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

import logging
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
    ClaimStatus,
    KnowledgeBucket,
    KnowledgeClaim,
    KnowledgeTopicView,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.integrations.lighthouse import build_lighthouse_client
from backend.app.services.agent.embedding import embed_text

logger = logging.getLogger(__name__)

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


async def _vector_search_topic_views(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    query_vec: list[float],
    limit: int,
) -> list[tuple[KnowledgeTopicView, float]]:
    stmt = (
        select(
            KnowledgeTopicView,
            KnowledgeTopicView.embedding.cosine_distance(query_vec).label(
                "dist"
            ),
        )
        .where(KnowledgeTopicView.workspace_id == workspace_id)
        .where(KnowledgeTopicView.embedding.is_not(None))
        .order_by("dist")
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(view, 1.0 - float(dist)) for view, dist in rows]


async def _vector_search_claims(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    query_vec: list[float],
    limit: int,
) -> list[tuple[KnowledgeClaim, float]]:
    stmt = (
        select(
            KnowledgeClaim,
            KnowledgeClaim.embedding.cosine_distance(query_vec).label("dist"),
        )
        .where(KnowledgeClaim.workspace_id == workspace_id)
        .where(KnowledgeClaim.status == ClaimStatus.ACTIVE)
        .where(KnowledgeClaim.embedding.is_not(None))
        .order_by("dist")
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(claim, 1.0 - float(dist)) for claim, dist in rows]


def _topic_view_hit(
    view: KnowledgeTopicView, score: float
) -> KnowledgeSearchHit:
    """Adapter — fit a topic-view onto the legacy ``KnowledgeSearchHit``
    shape so the Console / CLI keep one parser. ``source='topic_view'``
    tells the consumer to show the rendered body and a "view N
    underlying claims" link. ``rank_bucket='canon'`` distinguishes the
    new tier from legacy ``'workspace'`` bucket articles."""
    return KnowledgeSearchHit(
        id=view.id,
        source="topic_view",
        bucket_slug=view.topic_tag,
        bucket_id=None,
        repo_id=None,
        scope_kind="topic_view",
        score=round(score, 4),
        rank_bucket="canon",
        snippet=_first_paragraph(view.body_md),
        title=view.title,
        repo_full_name=None,
    )


def _claim_hit(claim: KnowledgeClaim, score: float) -> KnowledgeSearchHit:
    """Adapter — atomic claim → search hit. Title is the first 80 chars
    of the claim text (claims don't have a discrete title field). The
    snippet is the first source_link's excerpt when present so the
    consumer immediately sees provenance, falling back to the claim
    text itself."""
    excerpt: str | None = None
    if isinstance(claim.source_links, list) and claim.source_links:
        first = claim.source_links[0]
        if isinstance(first, dict):
            raw = first.get("excerpt")
            if isinstance(raw, str) and raw.strip():
                excerpt = raw.strip()
    snippet = excerpt or claim.claim_md
    if len(snippet) > _SNIPPET_MAX_CHARS:
        snippet = snippet[: _SNIPPET_MAX_CHARS - 1].rstrip() + "…"
    title = claim.claim_md
    if len(title) > 120:
        title = title[:119].rstrip() + "…"
    return KnowledgeSearchHit(
        id=claim.id,
        source="claim",
        bucket_slug=(claim.topic_tags[0] if claim.topic_tags else None),
        bucket_id=None,
        repo_id=None,
        scope_kind="claim",
        score=round(score, 4),
        rank_bucket="canon",
        snippet=snippet,
        title=title,
        repo_full_name=None,
    )


def _map_lighthouse_hit(
    raw: dict[str, Any], *, rank: int, total: int
) -> KnowledgeSearchHit:
    """Adapter — one Lighthouse ``/v1/search`` hit → KnowledgeSearchHit.

    The engine returns ``summary`` as ``# <name>\\n<snippet>``; split it
    back into title + snippet. ``/v1/search`` doesn't expose a score, so
    we synthesise a descending rank score (the engine already returned
    hits best-first). ``node_id`` is the chunk uuid.
    """
    summary = str(raw.get("summary") or "")
    title: str | None = None
    snippet = summary
    if summary.startswith("# "):
        nl = summary.find("\n")
        if nl == -1:
            title, snippet = summary[2:].strip(), ""
        else:
            title, snippet = summary[2:nl].strip(), summary[nl + 1 :].strip()
    if len(snippet) > _SNIPPET_MAX_CHARS:
        snippet = snippet[: _SNIPPET_MAX_CHARS - 1].rstrip() + "…"

    node_id = str(raw.get("node_id") or "")
    try:
        hit_id = uuid.UUID(node_id)
    except (ValueError, TypeError):
        hit_id = uuid.uuid5(uuid.NAMESPACE_URL, f"lighthouse:{node_id}")

    return KnowledgeSearchHit(
        id=hit_id,
        source="lighthouse",
        bucket_slug=None,
        bucket_id=None,
        repo_id=None,
        scope_kind="lighthouse",
        score=round(1.0 - (rank / max(total, 1)), 4),
        rank_bucket="canon",
        snippet=snippet,
        title=title or None,
        repo_full_name=None,
    )


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
    """Workspace knowledge search — Lighthouse-first, internal fallback.

    The per-workspace Lighthouse engine is the target backend. We query
    it first (scoped by ``workspace_id``); on any failure or an empty
    result we fall back to Ship's internal index, which still holds
    everything until the K6 write cutover populates Lighthouse. So the
    surface degrades gracefully whether or not Lighthouse is wired or
    populated yet.

    Lighthouse is skipped when it isn't configured (``LIGHTHOUSE_BASE_URL``
    unset) or when ``bucket_slug`` pins a specific legacy bucket — the
    flat corpus has no bucket concept, so that filter only the internal
    index can honour.
    """
    safe_query = (query or "").strip()
    if not safe_query:
        return []
    safe_limit = max(1, min(int(limit), 100))

    if bucket_slug is None:
        client = build_lighthouse_client(settings or get_settings())
        if client is not None:
            try:
                raw_hits = await client.search(
                    workspace_id=workspace_id,
                    query=safe_query,
                    top_k=safe_limit,
                )
            except Exception:
                logger.warning(
                    "lighthouse search failed — falling back to internal index",
                    exc_info=True,
                )
                raw_hits = []
            if raw_hits:
                total = len(raw_hits)
                return [
                    _map_lighthouse_hit(h, rank=i, total=total)
                    for i, h in enumerate(raw_hits)
                ][:safe_limit]

    return await _search_internal(
        session,
        workspace_id=workspace_id,
        query=query,
        repo_id=repo_id,
        bucket_slug=bucket_slug,
        limit=limit,
        settings=settings,
    )


async def _search_internal(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    query: str,
    repo_id: uuid.UUID | None = None,  # noqa: ARG001 — kept for wire compat
    bucket_slug: str | None = None,
    limit: int = 20,
    settings: Settings | None = None,
) -> list[KnowledgeSearchHit]:
    """Ship's internal knowledge index (the pre-Lighthouse path).

    Returns an empty list for blank queries. ``repo_id`` is accepted
    for wire compatibility but no longer participates in ranking now
    that every bucket is workspace-scoped. ``EmbeddingsUnavailable``
    is no longer raised — callers see a keyword-fallback result set
    instead.

    Three sources are unioned into the response:

    - ``source='topic_view'`` — rendered canonical articles per
      ``topic_tag`` (the post-claim-store retrieval target).
    - ``source='claim'`` — atomic active claims, useful when the
      agent wants to cite specific facts.
    - ``source='bucket_article'`` — legacy synth-driven articles,
      retained while clients migrate.

    All three are scored on the same cosine-similarity scale so a
    cross-source merge by ``score`` produces a sensible ranking.
    Topic-view + claim retrieval is skipped on the keyword-fallback
    path (no embedding present in those tables yet means TS-vector
    ranking would mix awkwardly with cosine — easier to keep keyword
    mode pinned to the legacy article surface for now).
    """
    safe_query = (query or "").strip()
    if not safe_query:
        return []
    safe_limit = max(1, min(int(limit), 100))

    canon_hits: list[KnowledgeSearchHit] = []
    keyword_fallback = False
    try:
        qvec = await _embed_query(safe_query, settings=settings)
    except EmbeddingsUnavailable:
        keyword_fallback = True
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
        # Topic views + claims only ride along when ``bucket_slug`` is
        # not pinned — that filter is a legacy article-only knob, the
        # canon doesn't carry the same shape.
        if bucket_slug is None:
            view_rows = await _vector_search_topic_views(
                session,
                workspace_id=workspace_id,
                query_vec=qvec,
                limit=safe_limit,
            )
            claim_rows = await _vector_search_claims(
                session,
                workspace_id=workspace_id,
                query_vec=qvec,
                limit=safe_limit,
            )
            canon_hits = [_topic_view_hit(v, s) for v, s in view_rows] + [
                _claim_hit(c, s) for c, s in claim_rows
            ]

    repo_ids = {
        bucket.repo_id for _, bucket, _ in rows if bucket.repo_id is not None
    }
    repo_full_names = await _resolve_repo_full_names(session, repo_ids)

    article_hits = [
        _hit_from_row(article, bucket, score, repo_full_names)
        for article, bucket, score in rows
    ]

    merged = article_hits + canon_hits
    merged.sort(key=lambda hit: hit.score, reverse=True)
    return merged[:safe_limit] if not keyword_fallback else article_hits


__all__: list[Any] = [
    "EmbeddingsUnavailable",
    "KnowledgeSearchHit",
    "search_workspace_knowledge",
]
