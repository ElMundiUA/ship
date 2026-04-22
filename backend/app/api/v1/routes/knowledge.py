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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import get_settings
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KbChunk,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import Workspace
from backend.app.db.session import get_session
from backend.app.services.agent.embedding import embed_text
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


# ---------------------------------------------------------------------------
# Workspace vector search (PR-7A)
# ---------------------------------------------------------------------------
#
# Registered *before* the ``/{slug}`` detail route so FastAPI's
# first-match-wins dispatch doesn't send ``GET /canonical`` into the
# repo-files detail handler as ``slug="canonical"``.


class KnowledgeSearchIn(BaseModel):
    """Request body for ``POST /knowledge/search``.

    ``repo_id`` is optional — when set, the resolver promotes hits from
    that repo into the top ``rank_bucket`` so the caller's "current
    repo" context wins over pure semantic score.
    """

    query: str = Field(..., min_length=1, max_length=1000)
    repo_id: uuid.UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)


class KnowledgeSearchHit(BaseModel):
    """One result row. ``source`` distinguishes bucket-article hits
    (structured knowledge) from kb_chunk hits (raw ``.ship/knowledge``
    markdown). The frontend can group or style by ``rank_bucket``
    without re-running the ordering logic."""

    id: uuid.UUID
    source: str
    bucket_slug: str | None = None
    bucket_id: uuid.UUID | None = None
    repo_id: uuid.UUID | None = None
    scope_kind: str
    score: float
    rank_bucket: str
    snippet: str
    title: str | None = None
    repo_full_name: str | None = None


class KnowledgeSearchResponse(BaseModel):
    query: str
    hits: list[KnowledgeSearchHit]


class KnowledgeCanonicalBucket(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    article_count: int
    override_count: int


class KnowledgeOrphanSlug(BaseModel):
    slug: str
    repo_count: int
    sample_repo_id: uuid.UUID
    sample_repo_full_name: str | None


class KnowledgeCanonicalResponse(BaseModel):
    workspace_id: uuid.UUID
    canonical: list[KnowledgeCanonicalBucket]
    orphan_slugs: list[KnowledgeOrphanSlug]


_SNIPPET_MAX_CHARS = 400


def _first_paragraph(text: str) -> str:
    """Pick the first non-empty paragraph from markdown, truncated.

    Best-effort: callers live in a search panel, not a reader, so the
    cheap ``\\n\\n`` split is fine — nobody cares if we clip mid-bullet
    when the body is markdown prose.
    """
    if not text:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    for chunk in stripped.split("\n\n"):
        candidate = chunk.strip()
        if candidate:
            if len(candidate) > _SNIPPET_MAX_CHARS:
                return candidate[: _SNIPPET_MAX_CHARS - 1].rstrip() + "…"
            return candidate
    trimmed = stripped[:_SNIPPET_MAX_CHARS]
    if len(stripped) > _SNIPPET_MAX_CHARS:
        trimmed = trimmed.rstrip() + "…"
    return trimmed


async def _embed_query(query: str) -> list[float]:
    """Call the shared embedding helper, translating its RuntimeError
    into a 412 the console can branch on (same contract the pattern-
    draft endpoint uses for unconfigured LLM keys)."""
    try:
        return await embed_text(query, settings=get_settings())
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "embeddings_unconfigured",
                "message": str(exc),
            },
        ) from exc


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_workspace_knowledge(
    workspace_id: uuid.UUID,
    payload: KnowledgeSearchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeSearchResponse:
    """Vector search over workspace knowledge.

    Unions two pgvector queries — structured :class:`BucketArticle`
    rows and raw :class:`KbChunk` markdown — then re-ranks by scope
    so the caller's current repo always wins over generic workspace
    hits and those over other repos. Keeps distance ordering inside
    each band so semantically stronger matches still bubble up.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    qvec = await _embed_query(payload.query.strip())
    over_fetch = payload.limit * 3

    article_stmt = (
        select(
            BucketArticle,
            KnowledgeBucket,
            BucketArticle.embedding.cosine_distance(qvec).label("dist"),
        )
        .join(KnowledgeBucket, KnowledgeBucket.id == BucketArticle.bucket_id)
        .where(
            and_(
                KnowledgeBucket.workspace_id == workspace_id,
                BucketArticle.archived_at.is_(None),
                BucketArticle.embedding.is_not(None),
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
        .order_by("dist")
        .limit(over_fetch)
    )

    chunk_stmt = (
        select(KbChunk, KbChunk.embedding.cosine_distance(qvec).label("dist"))
        .where(
            and_(
                KbChunk.workspace_id == workspace_id,
                KbChunk.embedding.is_not(None),
            )
        )
        .order_by("dist")
        .limit(over_fetch)
    )

    article_rows = (await session.execute(article_stmt)).all()
    chunk_rows = (await session.execute(chunk_stmt)).all()

    # Resolve repo full_names in a single batched query; both article
    # rows (via bucket.repo_id) and chunk rows reference ``workspace_repos``.
    repo_ids: set[uuid.UUID] = set()
    for _, bucket, _ in article_rows:
        if bucket.repo_id is not None:
            repo_ids.add(bucket.repo_id)
    for chunk, _ in chunk_rows:
        repo_ids.add(chunk.repo_id)

    repo_full_names: dict[uuid.UUID, str | None] = {}
    if repo_ids:
        repo_map_rows = (
            await session.execute(
                select(WorkspaceRepo.id, WorkspaceRepo.full_name).where(
                    WorkspaceRepo.id.in_(repo_ids)
                )
            )
        ).all()
        repo_full_names = {r[0]: r[1] for r in repo_map_rows}

    raw_hits: list[KnowledgeSearchHit] = []
    for article, bucket, dist in article_rows:
        hit_repo_id = bucket.repo_id
        raw_hits.append(
            KnowledgeSearchHit(
                id=article.id,
                source="bucket_article",
                bucket_slug=bucket.slug,
                bucket_id=bucket.id,
                repo_id=hit_repo_id,
                scope_kind=bucket.scope_kind,
                score=round(1.0 - float(dist), 4),
                rank_bucket="other_repo",
                snippet=_first_paragraph(article.body_md),
                title=article.title,
                repo_full_name=(
                    repo_full_names.get(hit_repo_id)
                    if hit_repo_id is not None
                    else None
                ),
            )
        )
    for chunk, dist in chunk_rows:
        raw_hits.append(
            KnowledgeSearchHit(
                id=chunk.id,
                source="kb_chunk",
                bucket_slug=None,
                bucket_id=None,
                repo_id=chunk.repo_id,
                scope_kind="repo",
                score=round(1.0 - float(dist), 4),
                rank_bucket="other_repo",
                snippet=_first_paragraph(chunk.content),
                title=chunk.source_path,
                repo_full_name=repo_full_names.get(chunk.repo_id),
            )
        )

    # Re-rank: current repo first, then workspace-scope, then every
    # other repo. Keep distance order inside each band.
    repo_match: list[KnowledgeSearchHit] = []
    workspace_hits: list[KnowledgeSearchHit] = []
    rest: list[KnowledgeSearchHit] = []
    for hit in raw_hits:
        if payload.repo_id is not None and hit.repo_id == payload.repo_id:
            hit.rank_bucket = "repo_match"
            repo_match.append(hit)
        elif hit.scope_kind == BucketScope.WORKSPACE:
            hit.rank_bucket = "workspace"
            workspace_hits.append(hit)
        else:
            hit.rank_bucket = "other_repo"
            rest.append(hit)

    for band in (repo_match, workspace_hits, rest):
        band.sort(key=lambda h: h.score, reverse=True)

    ordered = (repo_match + workspace_hits + rest)[: payload.limit]
    return KnowledgeSearchResponse(query=payload.query, hits=ordered)


@router.get("/canonical", response_model=KnowledgeCanonicalResponse)
async def list_canonical_knowledge(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeCanonicalResponse:
    """Workspace canonical buckets + orphan promotion candidates.

    Two lists in one payload:

    - ``canonical`` — every live workspace-scope bucket, with an
      article count and an override_count (how many narrower-scope
      articles currently point at one of this bucket's articles via
      ``overrides_workspace_article_id``).
    - ``orphan_slugs`` — slugs that show up at repo-scope in ≥2 repos
      without a workspace-scope copy; seeds for the future "promote
      to workspace" flow (7B).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    canonical_buckets = list(
        (
            await session.execute(
                select(KnowledgeBucket)
                .where(
                    and_(
                        KnowledgeBucket.workspace_id == workspace_id,
                        KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
                        KnowledgeBucket.archived_at.is_(None),
                    )
                )
                .order_by(KnowledgeBucket.slug)
            )
        )
        .scalars()
        .all()
    )

    canonical_out: list[KnowledgeCanonicalBucket] = []
    if canonical_buckets:
        bucket_ids = [b.id for b in canonical_buckets]

        # Published, non-archived article counts per canonical bucket.
        article_count_rows = (
            await session.execute(
                select(
                    BucketArticle.bucket_id,
                    func.count(BucketArticle.id).label("n"),
                )
                .where(
                    BucketArticle.bucket_id.in_(bucket_ids),
                    BucketArticle.status == BucketArticleStatus.PUBLISHED,
                    BucketArticle.archived_at.is_(None),
                )
                .group_by(BucketArticle.bucket_id)
            )
        ).all()
        article_counts = {row[0]: int(row[1]) for row in article_count_rows}

        # Override counts: each row is a narrower-scope article
        # whose ``overrides_workspace_article_id`` points at an
        # article inside a canonical bucket. Join the FK back through
        # ``bucket_articles`` to resolve the bucket ownership.
        from sqlalchemy.orm import aliased

        target = aliased(BucketArticle)
        override_count_rows = (
            await session.execute(
                select(
                    target.bucket_id,
                    func.count(BucketArticle.id).label("n"),
                )
                .join(
                    target,
                    target.id == BucketArticle.overrides_workspace_article_id,
                )
                .where(target.bucket_id.in_(bucket_ids))
                .group_by(target.bucket_id)
            )
        ).all()
        override_counts = {row[0]: int(row[1]) for row in override_count_rows}

        for bucket in canonical_buckets:
            canonical_out.append(
                KnowledgeCanonicalBucket(
                    id=bucket.id,
                    slug=bucket.slug,
                    name=bucket.name,
                    description=bucket.description,
                    article_count=article_counts.get(bucket.id, 0),
                    override_count=override_counts.get(bucket.id, 0),
                )
            )

    # Orphan slugs: same slug seen in ≥2 different repo-scope buckets,
    # *and* not present at workspace scope. We compute this in two
    # small queries instead of one giant one so the EXCEPT / NOT-EXISTS
    # clause stays legible.
    canonical_slugs = {b.slug for b in canonical_buckets}

    orphan_rows = (
        await session.execute(
            select(
                KnowledgeBucket.slug,
                func.count(func.distinct(KnowledgeBucket.repo_id)).label(
                    "repo_count"
                ),
                # Postgres has no ``min(uuid)`` — pick any representative
                # via array_agg + subscript. Ordering is stable-enough for
                # a "sample repo" column (we only use it to render an
                # illustrative link in the UI).
                func.array_agg(KnowledgeBucket.repo_id)[1].label(
                    "sample_repo_id"
                ),
            )
            .where(
                and_(
                    KnowledgeBucket.workspace_id == workspace_id,
                    KnowledgeBucket.scope_kind == BucketScope.REPO,
                    KnowledgeBucket.archived_at.is_(None),
                    KnowledgeBucket.repo_id.is_not(None),
                )
            )
            .group_by(KnowledgeBucket.slug)
            .having(func.count(func.distinct(KnowledgeBucket.repo_id)) >= 2)
        )
    ).all()

    sample_repo_ids = [row[2] for row in orphan_rows if row[2] is not None]
    repo_name_map: dict[uuid.UUID, str | None] = {}
    if sample_repo_ids:
        repo_name_map = {
            r[0]: r[1]
            for r in (
                await session.execute(
                    select(WorkspaceRepo.id, WorkspaceRepo.full_name).where(
                        WorkspaceRepo.id.in_(sample_repo_ids)
                    )
                )
            ).all()
        }

    orphans: list[KnowledgeOrphanSlug] = []
    for slug, repo_count, sample_repo_id in orphan_rows:
        if slug in canonical_slugs:
            continue
        if sample_repo_id is None:
            continue
        orphans.append(
            KnowledgeOrphanSlug(
                slug=slug,
                repo_count=int(repo_count),
                sample_repo_id=sample_repo_id,
                sample_repo_full_name=repo_name_map.get(sample_repo_id),
            )
        )
    orphans.sort(key=lambda o: (-o.repo_count, o.slug))

    return KnowledgeCanonicalResponse(
        workspace_id=workspace_id,
        canonical=canonical_out,
        orphan_slugs=orphans,
    )


# ---------------------------------------------------------------------------
# Legacy per-slug detail route (kept last so the PR-7A ``/search`` +
# ``/canonical`` routes above don't get shadowed by the catch-all
# ``/{slug}`` matcher).
# ---------------------------------------------------------------------------


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
