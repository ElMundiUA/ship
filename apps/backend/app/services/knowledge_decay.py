"""Knowledge GC — hard-delete archived bucket articles.

The synthesiser's ``action='archive'`` flow (commit c62dd21) flips
articles to ``status='archived'`` + ``archived_at=now()`` once an
operator accepts the inbox proposal. Operator-driven archive does the
same dual-flip via ``POST /buckets/{slug}/archive``.

Archive isn't deletion. The row stays so search can still surface it
when ``include_archived=True`` and so a bad archive call is reversible
via ``POST /buckets/{slug}/restore``. Without a sweep, though, the
archived rows accumulate forever — they keep their ``embedding``,
their ``provenance`` JSONB, and the ``BucketArticleSource`` rows that
link them back to harvested notes.

This module's ``gc_archived_articles`` runs daily and hard-deletes
articles whose ``archived_at`` is older than a configurable cutoff
(default 90 days). Cascades:

- ``BucketArticleSource`` rows: deleted via the FK ``ondelete='CASCADE'``
  on ``article_id``.
- ``Improvement(kind='knowledge_note')`` rows that fed the article via
  ``provenance.source_note_ids`` are NOT touched — those are observation
  events, not derived data, and are still useful for re-routing if a
  bucket comes back.
- ``BucketArticle`` rows whose ``supersedes_id`` points at a soon-to-be-
  deleted article: the FK is ``ondelete='SET NULL'`` so the surviving
  newer version simply loses its link to history. Acceptable — the
  history was already archived, and at 90 days the chain is too cold
  to matter.

Cutoff is a constant for now. If operators ask for per-workspace tuning
later, lift it onto a workspace settings JSONB field.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
)
from backend.app.db.models.tenancy import Workspace


log = logging.getLogger(__name__)


# 90 days = a quarter. Long enough that a regretful operator can still
# restore by hand from the DB if needed; short enough that the table
# doesn't bloat indefinitely.
ARCHIVE_TTL_DAYS = 90


@dataclass(frozen=True, slots=True)
class DecayReport:
    workspace_id: uuid.UUID | None
    deleted: int = 0
    inspected: int = 0


async def gc_archived_articles(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None = None,
    cutoff: datetime | None = None,
) -> DecayReport:
    """Hard-delete archived articles older than the cutoff.

    ``workspace_id`` is optional — when ``None`` the sweep runs across
    every workspace (used by the daily cron). The cron entry point
    iterates per workspace so a long sweep doesn't hold one transaction
    for the whole tenant set.
    """
    cutoff = cutoff or datetime.now(timezone.utc) - timedelta(days=ARCHIVE_TTL_DAYS)

    stmt = select(BucketArticle.id).where(
        BucketArticle.status == BucketArticleStatus.ARCHIVED,
        BucketArticle.archived_at.is_not(None),
        BucketArticle.archived_at < cutoff,
    )
    if workspace_id is not None:
        # Articles don't carry workspace_id directly; join through the
        # bucket carrier so the sweep stays scoped.
        from backend.app.db.models.agent_memory import KnowledgeBucket

        stmt = (
            select(BucketArticle.id)
            .join(KnowledgeBucket, KnowledgeBucket.id == BucketArticle.bucket_id)
            .where(
                BucketArticle.status == BucketArticleStatus.ARCHIVED,
                BucketArticle.archived_at.is_not(None),
                BucketArticle.archived_at < cutoff,
                KnowledgeBucket.workspace_id == workspace_id,
            )
        )

    article_ids = list((await session.execute(stmt)).scalars().all())
    if not article_ids:
        return DecayReport(workspace_id=workspace_id, deleted=0, inspected=0)

    result = await session.execute(
        delete(BucketArticle).where(BucketArticle.id.in_(article_ids))
    )
    deleted = int(result.rowcount or 0)
    log.info(
        "knowledge_decay: hard-deleted archived articles workspace_id=%s "
        "deleted=%d cutoff=%s",
        workspace_id,
        deleted,
        cutoff.isoformat(),
    )
    return DecayReport(
        workspace_id=workspace_id, deleted=deleted, inspected=len(article_ids)
    )


async def gc_all_workspaces(
    session: AsyncSession,
    *,
    cutoff: datetime | None = None,
) -> list[DecayReport]:
    """Cron entry point — sweep every workspace once."""
    workspace_ids = (
        await session.execute(select(Workspace.id))
    ).scalars().all()

    reports: list[DecayReport] = []
    for ws_id in workspace_ids:
        try:
            report = await gc_archived_articles(
                session, workspace_id=ws_id, cutoff=cutoff
            )
        except Exception as exc:
            log.exception("knowledge_decay sweep failed workspace=%s", ws_id)
            report = DecayReport(workspace_id=ws_id, deleted=0, inspected=0)
            _ = exc
        reports.append(report)
    return reports


__all__ = [
    "ARCHIVE_TTL_DAYS",
    "DecayReport",
    "gc_all_workspaces",
    "gc_archived_articles",
]
