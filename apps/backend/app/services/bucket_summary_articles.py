"""Mirror :class:`BucketSummary` rows into :class:`BucketArticle`.

Phase 5b companion to the (now-retired) repo-files-sync. Where that
module projected ``.ship/knowledge/*.md`` into articles, this one
projects agent-memory packed summaries.

The shapes are different enough to deserve their own module:

- A ``repo_files`` bucket has **one** file ⇢ **one** article, with
  version bumps on content changes and supersession on edits. Slug is
  fixed (``"main"``) because there's only ever one story per bucket.
- An ``agent_memory`` bucket has **many** summaries accumulated over
  time; each summary is a self-contained snapshot of a chat thread.
  Versioning / supersession aren't meaningful — you can't "edit" a
  pack; the user just packs a new thread. So each summary gets its
  own article with a unique, deterministic slug derived from the
  summary's UUID, and all of them stay ``published`` forever (until
  the bucket itself is archived).

What the mirror captures
------------------------

- ``slug`` — ``thread-<summary.id hex, dashless>`` so it's stable
  across re-runs and independent of title changes.
- ``title`` / ``body_md`` — summary's title + text.
- ``embedding`` — carried over unchanged so the retrieval path in
  Phase 5c doesn't have to re-embed.
- ``content_sha`` — sha256 of the summary text; not used for fast-path
  skip here (every summary is a new row), but kept present so the
  article invariants hold.
- ``provenance`` — ``{"source_kind": "agent_memory",
  "summary_id": "...", "thread_id": "..."|null,
  "created_by_user_id": "..."|null, "packed_at": "..."}`` so a future
  Distiller / audit pipeline can walk back to the originating chat.

Idempotency
-----------

Both the per-summary :func:`mirror_summary_to_article` and the bulk
:func:`backfill_missing_articles_for_workspace` check for an existing
article with the deterministic slug before writing. Calling either
twice is a no-op — the migration + the pack_topic dual-write can
race in practice and neither path should produce duplicates.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketSource,
    BucketSummary,
    KnowledgeBucket,
)


logger = logging.getLogger(__name__)


# Prefix keeps the slug namespace obviously distinct from repo_files'
# ``main`` (and from any future Distiller-chosen slugs). 32 hex chars
# from the UUID plus the 7-char prefix fit well inside ``slug(120)``.
_SLUG_PREFIX: str = "thread-"


@dataclass(slots=True)
class BackfillReport:
    """Outcome of a bulk mirror pass."""

    articles_created: int = 0
    summaries_scanned: int = 0
    summaries_skipped_existing: int = 0


def article_slug_for_summary(summary_id: uuid.UUID) -> str:
    """Deterministic slug for the article mirror of a summary.

    Kept hex-only so the slug is URL-safe and easy to eyeball in the
    DB. Using the UUID directly means the mapping is injective —
    there's never a collision between different summaries, and never a
    drift between the slug and its source row.
    """
    return f"{_SLUG_PREFIX}{summary_id.hex}"


def _content_sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _build_provenance(summary: BucketSummary) -> dict[str, str | None]:
    """Provenance pointer that lets Phase 5c read-path walk back to chat.

    ``thread_id`` can be NULL in the schema (packed threads that were
    later hard-deleted) — we preserve the NULL instead of dropping the
    key so downstream consumers can distinguish "no thread" from
    "thread id wasn't captured".
    """
    return {
        "source_kind": BucketSource.AGENT_MEMORY,
        "summary_id": str(summary.id),
        "thread_id": str(summary.thread_id) if summary.thread_id else None,
        "created_by_user_id": (
            str(summary.created_by_user_id)
            if summary.created_by_user_id
            else None
        ),
        "packed_at": (
            summary.created_at.astimezone(timezone.utc).isoformat()
            if summary.created_at is not None
            else None
        ),
    }


async def mirror_summary_to_article(
    session: AsyncSession, summary: BucketSummary
) -> BucketArticle | None:
    """Create the article mirror for ``summary`` if missing.

    Returns the new :class:`BucketArticle` (already attached to
    ``session``) or ``None`` if a mirror already exists. The caller
    is responsible for the flush — we avoid calling it here so the
    pack_topic path can keep its single end-of-transaction flush.
    """
    slug = article_slug_for_summary(summary.id)

    existing = (
        await session.execute(
            select(BucketArticle.id).where(
                BucketArticle.bucket_id == summary.bucket_id,
                BucketArticle.slug == slug,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    article = BucketArticle(
        id=uuid.uuid4(),
        bucket_id=summary.bucket_id,
        slug=slug,
        title=(summary.title or "Packed topic")[:512],
        body_md=summary.summary,
        content_sha=_content_sha(summary.summary),
        version=1,
        status=BucketArticleStatus.PUBLISHED,
        supersedes_id=None,
        provenance=_build_provenance(summary),
        embedding=summary.embedding,
    )
    session.add(article)
    return article


async def backfill_missing_articles_for_bucket(
    session: AsyncSession, bucket: KnowledgeBucket
) -> int:
    """Mirror every summary in ``bucket`` that doesn't yet have an article.

    Returns the count of articles inserted. Used from the pack_topic
    dual-write path (belt-and-braces when a concurrent packer may have
    raced ahead) and exposed for manual reconciliation runs.
    """
    summaries = (
        await session.execute(
            select(BucketSummary).where(
                BucketSummary.bucket_id == bucket.id
            )
        )
    ).scalars().all()

    created = 0
    for s in summaries:
        article = await mirror_summary_to_article(session, s)
        if article is not None:
            created += 1
    return created


async def backfill_missing_articles_for_workspace(
    session: AsyncSession, workspace_id: uuid.UUID
) -> BackfillReport:
    """Mirror every summary in ``workspace_id`` missing an article.

    Used by tests, the CLI reconcile command (future), and the Phase
    5b data migration's runtime path when Postgres-side backfill
    hasn't been run yet. Idempotent by construction.
    """
    report = BackfillReport()
    summaries = (
        await session.execute(
            select(BucketSummary)
            .join(
                KnowledgeBucket,
                BucketSummary.bucket_id == KnowledgeBucket.id,
            )
            .where(KnowledgeBucket.workspace_id == workspace_id)
        )
    ).scalars().all()

    for s in summaries:
        report.summaries_scanned += 1
        article = await mirror_summary_to_article(session, s)
        if article is None:
            report.summaries_skipped_existing += 1
        else:
            report.articles_created += 1
    return report


__all__ = [
    "BackfillReport",
    "article_slug_for_summary",
    "backfill_missing_articles_for_bucket",
    "backfill_missing_articles_for_workspace",
    "mirror_summary_to_article",
]
