"""Workspace-wide knowledge vector search (PR-7A extracted in 7C).

PR-7A landed the ``POST /v1/workspaces/{ws}/knowledge/search`` surface
with the heavy lifting baked into the HTTP route. PR-7C factors that
into this service module so the
Navigator ``search_workspace_kb`` tool can reuse the same
implementation without another HTTP round-trip (and its attached
cookie / CSRF dance).

Contract for callers:

- :func:`search_workspace_knowledge` takes an explicit ``AsyncSession``
  plus the workspace/query/repo/limit inputs that the HTTP body used
  to carry, and returns a list of :class:`KnowledgeSearchHit`.
- When the embedding provider isn't configured the function raises
  :class:`EmbeddingsUnavailable`. HTTP callers translate that into
  the existing ``412 embeddings_unconfigured`` response; the
  Navigator tool catches it and returns a structured
  ``{"error": "embeddings_unavailable", ...}`` payload so the model
  doesn't see a tool-call failure.

Everything else about the re-rank (``repo_match > workspace >
other_repo``, distance-ordered inside each band) matches PR-7A
exactly — the HTTP tests from that PR are the invariant.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.services.agent.embedding import embed_text


_SNIPPET_MAX_CHARS = 400


class EmbeddingsUnavailable(RuntimeError):
    """Embedding provider is not configured.

    Raised from :func:`search_workspace_knowledge` instead of bubbling
    the raw ``RuntimeError`` from :func:`embed_text` so callers can
    branch on a named exception type. The HTTP route catches this and
    returns ``412 embeddings_unconfigured``; the Navigator tool
    catches it and returns an ``{"error": "embeddings_unavailable"}``
    payload without raising.
    """


class KnowledgeSearchHit(BaseModel):
    """One row of the search response.

    ``source`` is kept for wire compatibility and is now always
    ``bucket_article``. ``rank_bucket`` is assigned after retrieval so the
    Console (and the Navigator tool) can group or style by band
    without re-running the ordering logic.
    """

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


async def _embed_query(
    query: str, *, settings: Settings | None
) -> list[float]:
    try:
        return await embed_text(query, settings=settings or get_settings())
    except RuntimeError as exc:
        raise EmbeddingsUnavailable(str(exc)) from exc


async def search_workspace_knowledge(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    query: str,
    repo_id: uuid.UUID | None = None,
    bucket_slug: str | None = None,
    limit: int = 20,
    settings: Settings | None = None,
) -> list[KnowledgeSearchHit]:
    """Run the workspace-wide knowledge search and return ranked hits.

    The ranking policy, in order of preference:

    1. ``repo_match`` — ``repo_id`` is set *and* the hit belongs to
       that repo.
    2. ``workspace`` — the hit's bucket is workspace-scoped
       (canonical knowledge).
    3. ``other_repo`` — anything else, including repo-scoped hits
       from a different repo than ``repo_id``.

    Inside each band we keep the semantic distance ordering so the
    best semantic match still wins. We over-fetch by 3× the caller's
    ``limit`` before re-ranking so a strong repo-match doesn't get
    lost behind a pile of unrelated-but-semantically-close hits from
    other repos.
    """
    safe_query = (query or "").strip()
    if not safe_query:
        return []
    safe_limit = max(1, min(int(limit), 100))

    qvec = await _embed_query(safe_query, settings=settings)
    over_fetch = safe_limit * 3

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
                KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
                KnowledgeBucket.source_kind != BucketSource.REPO_FILES,
                BucketArticle.archived_at.is_(None),
                BucketArticle.embedding.is_not(None),
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
        .order_by("dist")
        .limit(over_fetch)
    )
    if bucket_slug is not None:
        article_stmt = article_stmt.where(KnowledgeBucket.slug == bucket_slug)

    article_rows = (await session.execute(article_stmt)).all()

    # Resolve repo full_names in a single batched query; both article
    # rows reference ``workspace_repos`` through their bucket carrier.
    repo_ids: set[uuid.UUID] = set()
    for _, bucket, _ in article_rows:
        if bucket.repo_id is not None:
            repo_ids.add(bucket.repo_id)

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
    repo_match: list[KnowledgeSearchHit] = []
    workspace_hits: list[KnowledgeSearchHit] = []
    rest: list[KnowledgeSearchHit] = []
    for hit in raw_hits:
        if repo_id is not None and hit.repo_id == repo_id:
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

    return (repo_match + workspace_hits + rest)[:safe_limit]


__all__: list[Any] = [
    "EmbeddingsUnavailable",
    "KnowledgeSearchHit",
    "search_workspace_knowledge",
]
