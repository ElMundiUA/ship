"""Workspace-wide knowledge search.

Hybrid retrieval: embedding-first with a Postgres ``tsvector`` keyword
fallback so the surface stays usable when the embedding provider isn't
configured (or is briefly down). Both paths read the same set of
articles — workspace-scope, published, non-archived — and return
:class:`KnowledgeSearchHit` rows sorted by score.

Why hybrid:

- Embedding mode is the happy path: cosine-distance over per-article
  vectors, ordered tightest-first.
- Keyword mode kicks in when ``embed_text`` raises (no API key, model
  outage). It uses ``to_tsvector('english', title || ' ' || body)``
  with ``plainto_tsquery`` and ranks by ``ts_rank_cd``. No GIN index
  is required at our beta volume — the table is small enough that
  Postgres scans without it. We can add the index later if recall
  latency starts to bite.

Callers:

- The HTTP route (``POST /v1/workspaces/{ws}/knowledge/search``)
  surfaces both modes: when keyword fallback is used it sets
  ``mode='keyword'`` on the response so the Console can show the
  "embeddings degraded" banner.
- The Navigator ``search_workspace_kb`` tool calls the same function
  directly so the model never round-trips through HTTP for context.

Removed in the bucket-consolidation cleanup (KB-5+):

- The repo-scope vs workspace-scope band re-ranker. After Pipeline C
  every bucket is workspace-scoped, so ``rank_bucket`` is always
  ``'workspace'`` — kept on the wire so existing Console code still
  groups by it (with one group).
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.services.agent.embedding import embed_text


_SNIPPET_MAX_CHARS = 400


class EmbeddingsUnavailable(RuntimeError):
    """Embedding provider is not configured.

    Internal sentinel — the search function catches it and falls back
    to keyword retrieval. Kept exported because the Navigator tool
    (and a few tests) still want to distinguish "no embedding" from a
    routine failure.
    """


class KnowledgeSearchHit(BaseModel):
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


def _first_paragraph(body: str) -> str:
    """Pick the first non-empty paragraph from markdown, truncated."""
    if not body:
        return ""
    stripped = body.strip()
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


async def _embed_query(query: str, *, settings: Settings | None) -> list[float]:
    try:
        return await embed_text(query, settings=settings or get_settings())
    except RuntimeError as exc:
        raise EmbeddingsUnavailable(str(exc)) from exc


async def _resolve_repo_full_names(
    session: AsyncSession, repo_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    if not repo_ids:
        return {}
    rows = (
        await session.execute(
            select(WorkspaceRepo.id, WorkspaceRepo.full_name).where(
                WorkspaceRepo.id.in_(repo_ids)
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _hit_from_row(
    article: BucketArticle,
    bucket: KnowledgeBucket,
    score: float,
    repo_full_names: dict[uuid.UUID, str | None],
) -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        id=article.id,
        source="bucket_article",
        bucket_slug=bucket.slug,
        bucket_id=bucket.id,
        repo_id=bucket.repo_id,
        scope_kind=bucket.scope_kind,
        score=round(score, 4),
        rank_bucket="workspace",
        snippet=_first_paragraph(article.body_md),
        title=article.title,
        repo_full_name=(
            repo_full_names.get(bucket.repo_id)
            if bucket.repo_id is not None
            else None
        ),
    )


def _base_filters(workspace_id: uuid.UUID, bucket_slug: str | None):
    clauses = [
        KnowledgeBucket.workspace_id == workspace_id,
        KnowledgeBucket.archived_at.is_(None),
        KnowledgeBucket.source_kind != BucketSource.REPO_FILES,
        BucketArticle.archived_at.is_(None),
        BucketArticle.status == BucketArticleStatus.PUBLISHED,
    ]
    if bucket_slug is not None:
        clauses.append(KnowledgeBucket.slug == bucket_slug)
    return clauses


async def _vector_search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    query_vec: list[float],
    bucket_slug: str | None,
    limit: int,
) -> list[tuple[BucketArticle, KnowledgeBucket, float]]:
    stmt = (
        select(
            BucketArticle,
            KnowledgeBucket,
            BucketArticle.embedding.cosine_distance(query_vec).label("dist"),
        )
        .join(KnowledgeBucket, KnowledgeBucket.id == BucketArticle.bucket_id)
        .where(
            and_(
                *_base_filters(workspace_id, bucket_slug),
                BucketArticle.embedding.is_not(None),
            )
        )
        .order_by("dist")
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(article, bucket, 1.0 - float(dist)) for article, bucket, dist in rows]


async def _keyword_search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    query: str,
    bucket_slug: str | None,
    limit: int,
) -> list[tuple[BucketArticle, KnowledgeBucket, float]]:
    """tsvector fallback. Builds the document on the fly (no stored
    column / index yet) — closed-beta volumes don't justify a GIN
    index until measured latency forces it.
    """
    document = func.to_tsvector(
        text("'english'"),
        func.coalesce(BucketArticle.title, text("''"))
        + text("' '")
        + func.coalesce(BucketArticle.body_md, text("''")),
    )
    tsquery = func.plainto_tsquery(text("'english'"), query)
    rank = func.ts_rank_cd(document, tsquery).label("rank")

    stmt = (
        select(BucketArticle, KnowledgeBucket, rank)
        .join(KnowledgeBucket, KnowledgeBucket.id == BucketArticle.bucket_id)
        .where(
            and_(
                *_base_filters(workspace_id, bucket_slug),
                document.op("@@")(tsquery),
            )
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        (article, bucket, float(rank_score))
        for article, bucket, rank_score in rows
    ]


async def search_workspace_knowledge(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    query: str,
    repo_id: uuid.UUID | None = None,  # noqa: ARG001 — kept for wire compat
    bucket_slug: str | None = None,
    limit: int = 20,
    settings: Settings | None = None,
) -> list[KnowledgeSearchHit]:
    """Run the workspace-wide knowledge search and return ranked hits.

    Returns an empty list for blank queries. ``repo_id`` is accepted
    for wire compatibility but no longer participates in ranking now
    that every bucket is workspace-scoped. ``EmbeddingsUnavailable``
    is no longer raised — callers see a keyword-fallback result set
    instead.
    """
    safe_query = (query or "").strip()
    if not safe_query:
        return []
    safe_limit = max(1, min(int(limit), 100))

    try:
        qvec = await _embed_query(safe_query, settings=settings)
    except EmbeddingsUnavailable:
        rows = await _keyword_search(
            session,
            workspace_id=workspace_id,
            query=safe_query,
            bucket_slug=bucket_slug,
            limit=safe_limit,
        )
    else:
        rows = await _vector_search(
            session,
            workspace_id=workspace_id,
            query_vec=qvec,
            bucket_slug=bucket_slug,
            limit=safe_limit,
        )

    repo_ids = {bucket.repo_id for _, bucket, _ in rows if bucket.repo_id is not None}
    repo_full_names = await _resolve_repo_full_names(session, repo_ids)

    return [_hit_from_row(article, bucket, score, repo_full_names) for article, bucket, score in rows]


__all__: list[Any] = [
    "EmbeddingsUnavailable",
    "KnowledgeSearchHit",
    "search_workspace_knowledge",
]
