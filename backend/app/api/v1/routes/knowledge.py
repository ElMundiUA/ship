"""Workspace-scoped knowledge buckets.

This endpoint surfaces every ``.ship/knowledge/*.md`` file that has
been mirrored into ``knowledge_buckets`` as a repo-scoped,
``source_kind='repo_files'`` row. Mirroring happens in three places,
all pointing at the same sync service
(:mod:`backend.app.services.bucket_repo_files_sync`):

* push webhook (``_apply_push_event_for_kb``) on the repo's default
  branch,
* manual ``POST /repos/{id}/kb/reindex`` admin call,
* first-time repo activation.

Before Phase 2 this route scanned ``ArtifactRepo`` rows on disk via
:mod:`backend.app.services.knowledge_lister`. That surface only worked
for local-dev `file://` URLs and was effectively empty in SaaS. We
keep it wired as a **fallback** so self-hosted dev workflows still
list their buckets — DB rows win when both sources know about the
same slug (the DB row carries the vendor SHA + push trail, so it's
authoritative).

Wire shape (``buckets[i]``) is backward-compatible with the
disk-lister output: ``slug / title / visibility / repo_id / repo_url /
path / size / updated_at / excerpt``. Two additions for Phase 2
consumers that opt in:

* ``scope_kind`` — ``"repo"`` for DB-backed rows; ``"workspace"`` or
  ``"project"`` for legacy disk rows (mirrors the old ``visibility``
  field). Kept side-by-side so old clients aren't broken.
* ``source_kind`` — ``"repo_files"`` for DB-backed rows; ``"legacy"``
  for disk rows until we delete the fallback in Phase 5.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import Workspace
from backend.app.db.session import get_session
from backend.app.services.knowledge_lister import (
    bucket_to_dict as legacy_bucket_to_dict,
    get_bucket as legacy_get_bucket,
    list_buckets as legacy_list_buckets,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/knowledge",
    tags=["knowledge"],
)


async def _load_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> Workspace:
    await _require_membership(session, workspace_id, user_id, ROLES_READ)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return workspace


async def _load_repo_files_buckets(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[tuple[KnowledgeBucket, WorkspaceRepo | None]]:
    """Repo-scoped ``repo_files`` buckets joined to their carrier repo.

    Two passes on purpose: we LEFT JOIN so a bucket whose repo row was
    deleted out from under it still appears (with a null carrier) in
    the response — the alternative is silently hiding the row, which
    makes "where did my bucket go?" investigations needlessly hard.
    Archived rows are filtered out here; the resolver will surface
    them separately in Phase 3.
    """
    stmt = (
        select(KnowledgeBucket, WorkspaceRepo)
        .outerjoin(WorkspaceRepo, KnowledgeBucket.repo_id == WorkspaceRepo.id)
        .where(
            and_(
                KnowledgeBucket.workspace_id == workspace_id,
                KnowledgeBucket.scope_kind == BucketScope.REPO,
                KnowledgeBucket.source_kind == BucketSource.REPO_FILES,
                KnowledgeBucket.archived_at.is_(None),
            )
        )
        .order_by(KnowledgeBucket.slug)
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


def _db_bucket_to_dict(
    bucket: KnowledgeBucket,
    repo: WorkspaceRepo | None,
    *,
    include_body: bool = False,
) -> dict[str, Any]:
    """Project a DB bucket row into the wire shape.

    Legacy fields (``slug / title / visibility / repo_id / repo_url /
    path / size / updated_at / excerpt``) are preserved so old
    consumers don't regress. New consumers can branch on
    ``scope_kind`` / ``source_kind`` to tell the DB-backed rows from
    the legacy disk ones.
    """
    ref = bucket.source_ref or {}
    path = ref.get("path") if isinstance(ref, dict) else None
    size = ref.get("size") if isinstance(ref, dict) else None
    updated = bucket.updated_at
    out: dict[str, Any] = {
        "slug": bucket.slug,
        "title": bucket.name,
        # ``visibility`` kept for pre-Phase-2 clients that hard-coded the
        # ``project``/``workspace`` pair. For repo-scoped rows we say
        # ``"repo"`` — the console already renders unknown values as a
        # generic badge.
        "visibility": "repo",
        "repo_id": str(bucket.repo_id) if bucket.repo_id else None,
        "repo_url": repo.html_url if repo is not None else None,
        "repo_full_name": repo.full_name if repo is not None else None,
        "path": path,
        "size": size,
        "updated_at": (
            updated.isoformat().replace("+00:00", "Z") if updated else None
        ),
        "excerpt": bucket.description,
        # Phase 2 additions — additive, consumers can branch on them.
        "scope_kind": bucket.scope_kind,
        "source_kind": bucket.source_kind,
        "source_ref": bucket.source_ref,
    }
    if include_body:
        # Detail body is not cached in the bucket row — live-fetch via
        # the code host on the detail route. This dict-level shape
        # still carries a ``body`` key (potentially ``None``) so the
        # frontend doesn't have to branch on its presence.
        out["body"] = None
    return out


async def _fetch_body_for_bucket(
    session: AsyncSession,
    bucket: KnowledgeBucket,
    repo: WorkspaceRepo | None,
) -> str | None:
    """Live-fetch the markdown body from the code host.

    Returning ``None`` on any failure is fine — the detail page shows
    a "couldn't load preview" fallback in that case. We prefer the
    live fetch over caching the body in the bucket row because
    operators edit these files in git: caching would drift.
    """
    if repo is None or repo.installation_id is None:
        return None
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        return None
    path = _source_ref_path(bucket)
    if not path:
        return None

    # Lazy imports — the gateway pulls in httpx + GitHub auth and we
    # don't want to spin those up on the cheap list path.
    from backend.app.core.config import get_settings
    from backend.app.integrations.gateway.code_host import RepoRef
    from backend.app.integrations.github.code_host_adapter import GitHubCodeHost

    owner, _, name = (repo.full_name or "").partition("/")
    if not owner or not name:
        return None

    try:
        gw = GitHubCodeHost(install.installation_id, settings=get_settings())
        blob = await gw.get_blob(
            RepoRef(kind="github", owner=owner, repo=name),
            path=path,
            ref_sha=repo.default_branch or None,
        )
    except Exception:
        return None
    if blob.encoding != "utf-8":
        return None
    return blob.content


def _source_ref_path(bucket: KnowledgeBucket) -> str | None:
    ref = bucket.source_ref or {}
    if isinstance(ref, dict):
        path = ref.get("path")
        return path if isinstance(path, str) else None
    return None


def _legacy_to_dict_with_phase2(bucket: Any) -> dict[str, Any]:
    """Legacy disk-lister output + Phase 2 classifier fields."""
    out = legacy_bucket_to_dict(bucket)
    out.setdefault("scope_kind", bucket.visibility)  # project|workspace
    out.setdefault("source_kind", "legacy")
    return out


@router.get("")
async def list_workspace_knowledge(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _load_workspace(session, workspace_id, auth.user.id)

    db_rows = await _load_repo_files_buckets(session, workspace_id)
    db_entries = [_db_bucket_to_dict(b, r) for b, r in db_rows]
    db_slugs = {entry["slug"] for entry in db_entries}

    legacy_entries: list[dict[str, Any]] = []
    try:
        legacy_buckets = await legacy_list_buckets(session, workspace)
    except Exception:
        legacy_buckets = []
    for b in legacy_buckets:
        if b.slug in db_slugs:
            # DB row wins: it carries the vendor SHA + push trail, so
            # even if a local ArtifactRepo mirrors the same slug we
            # show the authoritative row.
            continue
        legacy_entries.append(_legacy_to_dict_with_phase2(b))

    buckets = sorted(db_entries + legacy_entries, key=lambda e: e["slug"])
    return {
        "version": 2,
        "workspace_id": str(workspace.id),
        "buckets": buckets,
    }


@router.get("/{slug}")
async def get_workspace_knowledge(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _load_workspace(session, workspace_id, auth.user.id)

    # DB first: the live-fetch of the body here is the headline of
    # this endpoint — list cards are fine with excerpts, detail pages
    # expect the full markdown.
    db_rows = await _load_repo_files_buckets(session, workspace_id)
    for bucket, repo in db_rows:
        if bucket.slug != slug:
            continue
        out = _db_bucket_to_dict(bucket, repo, include_body=True)
        body = await _fetch_body_for_bucket(session, bucket, repo)
        out["body"] = body
        return out

    # Fallback to the disk lister for local-dev `ArtifactRepo` rows.
    legacy = await legacy_get_bucket(session, workspace, slug)
    if legacy is not None:
        return _legacy_to_dict_with_phase2(legacy) | {"body": legacy.body}

    raise HTTPException(
        status_code=404,
        detail=f"knowledge bucket '{slug}' not found in any enabled repo",
    )
