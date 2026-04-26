"""Workspace-scoped knowledge buckets.

This endpoint surfaces every ``.ship/knowledge/*.md`` file that has
been mirrored into ``knowledge_buckets`` as a repo-scoped,
``source_kind='repo_files'`` row. Mirroring happens in three places,
all pointing at the same sync service
(:mod:`backend.app.services.bucket_repo_files_sync`):

* push webhook (``_apply_push_event_for_kb``) on the repo's default
  branch,
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

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import get_settings
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.knowledge_promotion import KnowledgePromotionCandidate
from backend.app.db.models.tenancy import Workspace
from backend.app.db.session import get_session
from backend.app.services.agent.client import pick_default_client
from backend.app.services.agent.embedding import embed_text
from backend.app.services.knowledge_dedup import (
    list_fresh_candidates,
    rebuild_candidates,
)
from backend.app.services.knowledge_search import (
    EmbeddingsUnavailable,
    KnowledgeSearchHit,
    search_workspace_knowledge as _run_workspace_knowledge_search,
)
from backend.app.services.knowledge_lister import (
    bucket_to_dict as legacy_bucket_to_dict,
    get_bucket as legacy_get_bucket,
    list_buckets as legacy_list_buckets,
)
from backend.app.services.knowledge_promotion import (
    PromotionDraft,
    draft_canonical,
)


logger = logging.getLogger(__name__)


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
    bucket_slug: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=20, ge=1, le=100)


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


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_workspace_knowledge(
    workspace_id: uuid.UUID,
    payload: KnowledgeSearchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeSearchResponse:
    """Vector search over workspace knowledge.

    Thin HTTP wrapper — all the vector union + re-rank work lives in
    :func:`backend.app.services.knowledge_search.search_workspace_knowledge`
    so the Navigator ``search_workspace_kb`` tool can call the same
    implementation directly (no cookie round-trip). The 412 on
    unconfigured embeddings is translated from the service's
    :class:`EmbeddingsUnavailable` so the Console contract doesn't
    change.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    try:
        hits = await _run_workspace_knowledge_search(
            session,
            workspace_id=workspace_id,
            query=payload.query,
            repo_id=payload.repo_id,
            bucket_slug=payload.bucket_slug,
            limit=payload.limit,
            settings=get_settings(),
        )
    except EmbeddingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "embeddings_unconfigured",
                "message": str(exc),
            },
        ) from exc

    return KnowledgeSearchResponse(query=payload.query, hits=hits)


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
# Promotion candidates + drafting + persist (PR-7B)
# ---------------------------------------------------------------------------


class KnowledgeCandidateMember(BaseModel):
    article_id: uuid.UUID
    bucket_id: uuid.UUID
    bucket_slug: str
    repo_id: uuid.UUID | None
    repo_full_name: str | None
    title: str | None
    preview: str


class KnowledgeCandidate(BaseModel):
    id: uuid.UUID
    fingerprint: str
    slug_hint: str
    centroid_score: float
    member_count: int
    repo_count: int
    members: list[KnowledgeCandidateMember]


class KnowledgeCandidatesResponse(BaseModel):
    workspace_id: uuid.UUID
    candidates: list[KnowledgeCandidate]
    computed_at: datetime
    is_fresh: bool


class PromotionDraftIn(BaseModel):
    article_ids: list[uuid.UUID] | None = None


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,119}$")
_PREVIEW_CHARS = 200
_MAX_BODY_CHARS = 64_000


class PromotionIn(BaseModel):
    slug: str
    title: str = Field(..., min_length=1, max_length=512)
    body: str = Field(..., min_length=1, max_length=_MAX_BODY_CHARS)
    summary: str | None = None
    source_article_ids: list[uuid.UUID] = Field(default_factory=list)
    mark_sources_as_overrides: bool = True

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                "slug must be kebab-case, start with [a-z0-9], and be ≤ 120 chars"
            )
        return v

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("title must be non-empty")
        return s

    @field_validator("body")
    @classmethod
    def _validate_body(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("body must be non-empty")
        return s


class PromotionOut(BaseModel):
    workspace_bucket_id: uuid.UUID
    workspace_article_id: uuid.UUID
    overridden_article_ids: list[uuid.UUID]


def _preview(text: str | None) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) > _PREVIEW_CHARS:
        return compact[: _PREVIEW_CHARS - 1].rstrip() + "…"
    return compact


async def _render_candidates(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rows: list[KnowledgePromotionCandidate],
) -> list[KnowledgeCandidate]:
    """Hydrate candidate rows with member article + repo context.

    One batch query each for articles, buckets, and repo full_names
    — no N+1 even for a workspace with a hundred candidates.
    """
    if not rows:
        return []

    all_article_ids: set[uuid.UUID] = set()
    for row in rows:
        for s in row.article_ids or []:
            try:
                all_article_ids.add(uuid.UUID(str(s)))
            except (ValueError, TypeError):
                continue
    if not all_article_ids:
        return []

    article_rows = (
        await session.execute(
            select(BucketArticle, KnowledgeBucket)
            .join(KnowledgeBucket, KnowledgeBucket.id == BucketArticle.bucket_id)
            .where(
                BucketArticle.id.in_(all_article_ids),
                KnowledgeBucket.workspace_id == workspace_id,
            )
        )
    ).all()

    article_map: dict[uuid.UUID, BucketArticle] = {
        r[0].id: r[0] for r in article_rows
    }
    bucket_map: dict[uuid.UUID, KnowledgeBucket] = {
        r[1].id: r[1] for r in article_rows
    }

    repo_ids = {
        b.repo_id
        for b in bucket_map.values()
        if b.repo_id is not None
    }
    repo_name_map: dict[uuid.UUID, str | None] = {}
    if repo_ids:
        repo_name_map = {
            r[0]: r[1]
            for r in (
                await session.execute(
                    select(WorkspaceRepo.id, WorkspaceRepo.full_name).where(
                        WorkspaceRepo.id.in_(repo_ids)
                    )
                )
            ).all()
        }

    out: list[KnowledgeCandidate] = []
    for row in rows:
        members: list[KnowledgeCandidateMember] = []
        repo_ids_in_cluster: set[uuid.UUID] = set()
        for s in row.article_ids or []:
            try:
                aid = uuid.UUID(str(s))
            except (ValueError, TypeError):
                continue
            article = article_map.get(aid)
            if article is None:
                continue
            bucket = bucket_map.get(article.bucket_id)
            if bucket is None:
                continue
            if bucket.repo_id is not None:
                repo_ids_in_cluster.add(bucket.repo_id)
            members.append(
                KnowledgeCandidateMember(
                    article_id=article.id,
                    bucket_id=bucket.id,
                    bucket_slug=bucket.slug,
                    repo_id=bucket.repo_id,
                    repo_full_name=(
                        repo_name_map.get(bucket.repo_id)
                        if bucket.repo_id is not None
                        else None
                    ),
                    title=article.title,
                    preview=_preview(article.body_md),
                )
            )
        if not members:
            continue
        out.append(
            KnowledgeCandidate(
                id=row.id,
                fingerprint=row.fingerprint,
                slug_hint=row.slug_hint,
                centroid_score=float(row.centroid_score or 0.0),
                member_count=len(members),
                repo_count=len(repo_ids_in_cluster),
                members=members,
            )
        )

    out.sort(key=lambda c: (-c.member_count, -c.centroid_score, c.slug_hint))
    return out


@router.get("/candidates", response_model=KnowledgeCandidatesResponse)
async def list_promotion_candidates(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeCandidatesResponse:
    """List cached promotion candidates, recomputing on cache miss.

    ``is_fresh=True`` means we served purely from cache; ``False``
    means we just rebuilt. Either way the response is the same shape
    so the Console can render without branching.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    rows = await list_fresh_candidates(session, workspace_id)
    is_fresh = True
    if rows is None:
        rows = await rebuild_candidates(session, workspace_id)
        is_fresh = False
    candidates = await _render_candidates(session, workspace_id, list(rows))
    return KnowledgeCandidatesResponse(
        workspace_id=workspace_id,
        candidates=candidates,
        computed_at=datetime.now(timezone.utc),
        is_fresh=is_fresh,
    )


@router.post(
    "/candidates/refresh", response_model=KnowledgeCandidatesResponse
)
async def refresh_promotion_candidates(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeCandidatesResponse:
    """Force a rebuild of the candidate cache.

    Admin-gated because recompute walks every repo-scope article in
    the workspace; we don't want a read-only viewer triggering it.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    rows = await rebuild_candidates(session, workspace_id)
    candidates = await _render_candidates(session, workspace_id, list(rows))
    return KnowledgeCandidatesResponse(
        workspace_id=workspace_id,
        candidates=candidates,
        computed_at=datetime.now(timezone.utc),
        is_fresh=False,
    )


@router.post(
    "/candidates/{candidate_id}/draft", response_model=PromotionDraft
)
async def draft_promotion_candidate(
    workspace_id: uuid.UUID,
    candidate_id: uuid.UUID,
    payload: PromotionDraftIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PromotionDraft:
    """LLM-backed canonical draft for a candidate cluster.

    404 if the candidate doesn't belong to this workspace; 400 if
    the provided ``article_ids`` aren't a subset of the cluster; 412
    if the LLM isn't configured (mirrors the custom-pattern draft
    endpoint so the Console can surface the same banner).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    candidate = await session.get(KnowledgePromotionCandidate, candidate_id)
    if candidate is None or candidate.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="candidate not found")

    cluster_ids: list[uuid.UUID] = []
    for s in candidate.article_ids or []:
        try:
            cluster_ids.append(uuid.UUID(str(s)))
        except (ValueError, TypeError):
            continue
    if not cluster_ids:
        raise HTTPException(
            status_code=422, detail="candidate has no resolvable articles"
        )

    if payload.article_ids is None:
        chosen = cluster_ids
    else:
        if not payload.article_ids:
            raise HTTPException(
                status_code=400, detail="article_ids must be non-empty"
            )
        cluster_set = set(cluster_ids)
        extras = [a for a in payload.article_ids if a not in cluster_set]
        if extras:
            raise HTTPException(
                status_code=400,
                detail="article_ids must be a subset of the candidate cluster",
            )
        # Preserve caller-provided order so "representative" stays
        # first when the Console passes the cluster in its own order.
        seen: set[uuid.UUID] = set()
        chosen = []
        for a in payload.article_ids:
            if a in seen:
                continue
            seen.add(a)
            chosen.append(a)

    try:
        client = pick_default_client(get_settings())
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "llm_unconfigured", "message": str(exc)},
        ) from exc

    try:
        draft = await draft_canonical(
            session, workspace_id, chosen, llm_client=client
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "llm_unparseable", "message": str(exc)},
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover — upstream network errors
        logger.exception("knowledge-promotion draft failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "llm_failed", "message": str(exc)},
        ) from exc

    return draft


@router.post("/promote", response_model=PromotionOut)
async def promote_knowledge(
    workspace_id: uuid.UUID,
    payload: PromotionIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PromotionOut:
    """Persist the operator's canonical draft to workspace scope.

    Creates (or reuses) the workspace-scope :class:`KnowledgeBucket`
    for ``slug``, writes one :class:`BucketArticle` with ``title`` /
    ``body`` / ``summary``, best-effort embedding, and (optionally)
    marks each ``source_article_ids`` as an override pointing at the
    new canonical. Source articles are NEVER archived — promotion is
    additive.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    bucket = (
        await session.execute(
            select(KnowledgeBucket).where(
                and_(
                    KnowledgeBucket.workspace_id == workspace_id,
                    KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
                    KnowledgeBucket.slug == payload.slug,
                    KnowledgeBucket.archived_at.is_(None),
                )
            )
        )
    ).scalar_one_or_none()

    if bucket is None:
        bucket = KnowledgeBucket(
            workspace_id=workspace_id,
            slug=payload.slug,
            name=payload.title,
            description=payload.summary,
            scope_kind=BucketScope.WORKSPACE,
            source_kind=BucketSource.PROMOTED,
            project_id=None,
            repo_id=None,
            user_id=None,
        )
        session.add(bucket)
        await session.flush()

    # Best-effort embedding: ``embed_text`` raises when unconfigured;
    # we swallow that — the article still persists, it just won't
    # rank in ``/search`` until a later reindex.
    embedding: list[float] | None = None
    try:
        embedding = await embed_text(payload.body, settings=get_settings())
    except RuntimeError:
        embedding = None
    except Exception:  # pragma: no cover — network failures
        logger.exception("promotion embedding failed; proceeding without")
        embedding = None

    import hashlib as _hashlib

    content_sha = _hashlib.sha256(payload.body.encode("utf-8")).hexdigest()
    provenance: dict[str, Any] = {"kind": "promoted"}
    if payload.summary:
        provenance["summary"] = payload.summary
    if payload.source_article_ids:
        provenance["source_article_ids"] = [
            str(a) for a in payload.source_article_ids
        ]
    article = BucketArticle(
        bucket_id=bucket.id,
        slug=payload.slug,
        title=payload.title,
        body_md=payload.body,
        content_sha=content_sha,
        status=BucketArticleStatus.PUBLISHED,
        version=1,
        embedding=embedding,
        provenance=provenance,
    )
    session.add(article)
    await session.flush()

    overridden: list[uuid.UUID] = []
    if payload.mark_sources_as_overrides and payload.source_article_ids:
        sources = (
            await session.execute(
                select(BucketArticle, KnowledgeBucket)
                .join(
                    KnowledgeBucket,
                    KnowledgeBucket.id == BucketArticle.bucket_id,
                )
                .where(
                    BucketArticle.id.in_(payload.source_article_ids),
                    KnowledgeBucket.workspace_id == workspace_id,
                )
            )
        ).all()
        for src_article, _src_bucket in sources:
            # Don't silently clobber an operator-authored override — a
            # source already pointing elsewhere stays unchanged, and
            # the caller sees it absent from the response list.
            if src_article.overrides_workspace_article_id is not None:
                continue
            src_article.overrides_workspace_article_id = article.id
            overridden.append(src_article.id)

    # Invalidate any cached candidate whose cluster included a
    # promoted source. Overlap check is in-Python because
    # ``article_ids`` is a JSONB list of strings; Postgres array
    # overlap would need a cast + GIN index we haven't created.
    if payload.source_article_ids:
        source_strs = {str(a) for a in payload.source_article_ids}
        cached = (
            await session.execute(
                select(KnowledgePromotionCandidate).where(
                    KnowledgePromotionCandidate.workspace_id == workspace_id
                )
            )
        ).scalars().all()
        stale_ids = [
            c.id
            for c in cached
            if any(str(s) in source_strs for s in (c.article_ids or []))
        ]
        if stale_ids:
            await session.execute(
                delete(KnowledgePromotionCandidate).where(
                    KnowledgePromotionCandidate.id.in_(stale_ids)
                )
            )

    await session.flush()
    return PromotionOut(
        workspace_bucket_id=bucket.id,
        workspace_article_id=article.id,
        overridden_article_ids=overridden,
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
