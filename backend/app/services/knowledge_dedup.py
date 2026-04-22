"""Dedup clustering for workspace knowledge (RFC-0008 §I, PR-7B).

Finds groups of repo-scope :class:`BucketArticle` rows that look like
duplicates of each other — same topic, written once in each repo's
``.ship/knowledge/`` tree — and caches them as
:class:`KnowledgePromotionCandidate` rows so the operator can promote
a single canonical copy into the workspace scope.

Algorithm (MVP, on-demand):

1. Load every non-archived, ``scope_kind='repo'`` article with a
   non-null embedding for the workspace.
2. Compute pairwise cosine similarity **in Python**. We keep the
   pgvector column in the DB, but for this MVP we pull vectors
   into memory and iterate — the hardware budget is "≤ 500 articles
   per workspace" and the Python path is simpler + faster to reason
   about than a self-join that builds an N² result set inside
   Postgres. If the corpus grows past a few thousand we switch to a
   pgvector KNN pre-filter + component join here.
3. Connect articles A, B when ``similarity(A, B) >= similarity_threshold``
   (default 0.85). Union-find the graph; keep connected components
   with ≥ ``min_cluster_size`` members drawn from ≥ 2 distinct repos.
   Single-repo duplicates are a *bucket hygiene* issue, not a
   promotion candidate, so we drop them here.
4. For each surviving cluster produce one
   :class:`KnowledgePromotionCandidate` row. ``fingerprint`` is a
   deterministic SHA-256 of the sorted article ids so a repeated
   rebuild upserts in place.
5. Upsert: rows whose fingerprint disappears from the new set are
   deleted; unchanged fingerprints get ``ttl_expires_at`` bumped;
   new ones are inserted.

Thresholds live as kwargs instead of config constants so the tests
can drive the edge cases without touching ``Settings``.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    KnowledgeBucket,
)
from backend.app.db.models.knowledge_promotion import KnowledgePromotionCandidate


_SLUG_RE = re.compile(r"[^a-z0-9\-]+")


def _slugify(value: str) -> str:
    """Lowercased, hyphenated fallback slug.

    Used only when none of the member buckets carry a slug (which is
    rare — repo_files buckets always have one). Kept tiny on purpose;
    we don't need collision-safety here because the operator edits
    the slug before hitting ``/promote``.
    """
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = _SLUG_RE.sub("-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:120] or "untitled"


def _as_float_list(value: object) -> list[float]:
    """Coerce a pgvector column value into a plain ``list[float]``.

    pgvector-python hands back a :class:`numpy.ndarray` in newer
    releases; direct truthiness checks (``if vec:``) on arrays raise
    ``ValueError``. Materialising to a Python list up front keeps the
    rest of the clustering code numpy-free.
    """
    if value is None:
        return []
    try:
        return [float(x) for x in value]  # type: ignore[arg-type]
    except TypeError:
        return []


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity. Guarded against zero-norm vectors.

    Both inputs come from pgvector so they're already fixed-width
    lists of floats; we don't bother with numpy to avoid a hard
    dependency on the runtime path of every worker that touches this
    module.
    """
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _connected_components(
    n: int, edges: Iterable[tuple[int, int]]
) -> list[list[int]]:
    """Union-find → list of index-lists, one per component."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for a, b in edges:
        union(a, b)

    buckets: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        buckets.setdefault(root, []).append(i)
    return list(buckets.values())


def _fingerprint(article_ids: list[uuid.UUID]) -> str:
    """SHA-256 over comma-joined sorted ids — deterministic."""
    sorted_ids = sorted(str(aid) for aid in article_ids)
    return hashlib.sha256(",".join(sorted_ids).encode("utf-8")).hexdigest()


def _pick_slug_hint(
    articles: list[BucketArticle],
    buckets: dict[uuid.UUID, KnowledgeBucket],
) -> str:
    """Majority-vote slug across member buckets; lex tie-break.

    Falls back to a ``slugify(first_article.title)`` when no member
    bucket row can be resolved (shouldn't happen for repo_files but
    keeps the function total for weird migration states).
    """
    slugs: list[str] = []
    for a in articles:
        bucket = buckets.get(a.bucket_id)
        if bucket is None or not bucket.slug:
            continue
        slugs.append(bucket.slug)
    if not slugs:
        head = articles[0]
        return _slugify(head.title or "untitled")
    counter = Counter(slugs)
    # ``most_common`` is count-desc; for ties we want the lexicographic
    # minimum so the operator's mental cache stays stable run-to-run.
    top_count = counter.most_common(1)[0][1]
    tied = sorted(s for s, c in counter.items() if c == top_count)
    return tied[0]


async def _load_repo_articles(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[list[BucketArticle], dict[uuid.UUID, KnowledgeBucket]]:
    """Pull candidate articles + their parent buckets in two queries.

    We filter on the DB side so Python only sees eligible rows:
    ``scope_kind='repo'`` (workspace-scope articles are already
    canonical, don't re-cluster them), non-archived, published
    status, non-null embedding. That keeps the in-memory pairwise
    pass bounded to "articles a human could plausibly want to
    promote".
    """
    stmt = (
        select(BucketArticle, KnowledgeBucket)
        .join(KnowledgeBucket, KnowledgeBucket.id == BucketArticle.bucket_id)
        .where(
            and_(
                KnowledgeBucket.workspace_id == workspace_id,
                KnowledgeBucket.scope_kind == BucketScope.REPO,
                KnowledgeBucket.archived_at.is_(None),
                BucketArticle.archived_at.is_(None),
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
                BucketArticle.embedding.is_not(None),
            )
        )
    )
    rows = (await session.execute(stmt)).all()
    articles: list[BucketArticle] = [row[0] for row in rows]
    bucket_map: dict[uuid.UUID, KnowledgeBucket] = {row[1].id: row[1] for row in rows}
    return articles, bucket_map


async def rebuild_candidates(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    similarity_threshold: float = 0.85,
    min_cluster_size: int = 2,
    ttl_seconds: int = 24 * 3600,
) -> list[KnowledgePromotionCandidate]:
    """Recompute the dedup candidate set for ``workspace_id``.

    Upserts :class:`KnowledgePromotionCandidate` rows in place:
    fingerprints that disappear from the fresh set are deleted,
    survivors get their ``ttl_expires_at`` bumped, and brand-new
    clusters are inserted. Always returns the *full* current list
    for the workspace (so the caller doesn't have to follow up with
    a select).
    """
    articles, bucket_map = await _load_repo_articles(session, workspace_id)
    embeddings: list[list[float]] = [
        _as_float_list(a.embedding) for a in articles
    ]

    edges: list[tuple[int, int]] = []
    n = len(articles)
    for i in range(n):
        emb_i = embeddings[i]
        if not emb_i:
            continue
        for j in range(i + 1, n):
            emb_j = embeddings[j]
            if not emb_j:
                continue
            if _cosine_similarity(emb_i, emb_j) >= similarity_threshold:
                edges.append((i, j))

    components = _connected_components(n, edges)

    # Pre-compute per-index repo_id so the "≥ 2 repos" filter doesn't
    # re-walk the bucket map for every cluster.
    repo_ids: list[uuid.UUID | None] = []
    for a in articles:
        bucket = bucket_map.get(a.bucket_id)
        repo_ids.append(bucket.repo_id if bucket is not None else None)

    fresh: list[tuple[str, list[uuid.UUID], str, float]] = []
    for comp in components:
        if len(comp) < min_cluster_size:
            continue
        distinct_repos = {
            repo_ids[idx] for idx in comp if repo_ids[idx] is not None
        }
        if len(distinct_repos) < 2:
            continue

        member_articles = [articles[idx] for idx in comp]
        sorted_ids = sorted(member_articles, key=lambda a: str(a.id))
        article_ids = [a.id for a in sorted_ids]
        fp = _fingerprint(article_ids)
        slug_hint = _pick_slug_hint(sorted_ids, bucket_map)

        if len(comp) >= 2:
            total = 0.0
            pairs = 0
            for a_idx in range(len(comp)):
                for b_idx in range(a_idx + 1, len(comp)):
                    ea = embeddings[comp[a_idx]]
                    eb = embeddings[comp[b_idx]]
                    if ea and eb:
                        total += _cosine_similarity(ea, eb)
                        pairs += 1
            centroid = total / pairs if pairs > 0 else 0.0
        else:
            centroid = 1.0

        fresh.append((fp, article_ids, slug_hint, centroid))

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)

    fresh_fps = {fp for fp, _, _, _ in fresh}

    existing_rows = (
        await session.execute(
            select(KnowledgePromotionCandidate).where(
                KnowledgePromotionCandidate.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    existing_map: dict[str, KnowledgePromotionCandidate] = {
        row.fingerprint: row for row in existing_rows
    }

    # 1. Delete stale rows (present before, absent now).
    stale_fps = [fp for fp in existing_map.keys() if fp not in fresh_fps]
    if stale_fps:
        await session.execute(
            delete(KnowledgePromotionCandidate).where(
                and_(
                    KnowledgePromotionCandidate.workspace_id == workspace_id,
                    KnowledgePromotionCandidate.fingerprint.in_(stale_fps),
                )
            )
        )

    # 2. Upsert survivors + new rows.
    for fp, article_ids, slug_hint, centroid in fresh:
        row = existing_map.get(fp)
        id_strings = [str(aid) for aid in article_ids]
        if row is None:
            row = KnowledgePromotionCandidate(
                workspace_id=workspace_id,
                fingerprint=fp,
                article_ids=id_strings,
                slug_hint=slug_hint,
                centroid_score=float(centroid),
                ttl_expires_at=expires,
            )
            session.add(row)
        else:
            row.article_ids = id_strings
            row.slug_hint = slug_hint
            row.centroid_score = float(centroid)
            row.ttl_expires_at = expires
    await session.flush()

    return list(
        (
            await session.execute(
                select(KnowledgePromotionCandidate)
                .where(
                    KnowledgePromotionCandidate.workspace_id == workspace_id
                )
                .order_by(KnowledgePromotionCandidate.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def list_fresh_candidates(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[KnowledgePromotionCandidate] | None:
    """Return cached rows iff any rows exist and none are expired.

    Returns ``None`` when the cache is empty *or* when at least one
    row has passed its TTL — the caller treats that as "recompute".
    We intentionally don't partially serve fresh rows while
    recomputing expired ones: the rebuild is cheap enough on our
    MVP-size corpus that a single recompute-everything pass is
    simpler than a read-through partial invalidation.
    """
    now = datetime.now(timezone.utc)
    rows = list(
        (
            await session.execute(
                select(KnowledgePromotionCandidate)
                .where(
                    KnowledgePromotionCandidate.workspace_id == workspace_id
                )
                .order_by(KnowledgePromotionCandidate.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    if any(r.ttl_expires_at <= now for r in rows):
        return None
    return rows


__all__ = [
    "rebuild_candidates",
    "list_fresh_candidates",
]
