"""Chat-thread lifecycle helpers (Wave C — idle-thread sweeper).

The Wave C cron archives chat threads that have sat idle for more
than :data:`THRESHOLD_DAYS_DEFAULT` days. The selection / mutation
logic lives in this service module rather than directly in the
worker file so:

- It is straightforwardly unit-testable against the existing
  ``db_session`` fixture without spinning up arq or Redis.
- A future "archive on demand" admin route or one-off script can
  reuse the same primitive.

Idempotency is guaranteed by the SQL filter — we only touch rows
where ``status = 'active'``, so a second pass over the same
batch finds nothing left to flip and writes nothing. Already-
archived rows therefore keep their original ``archived_at``.

Bounded work per call: at most :data:`BATCH_LIMIT` rows are
processed, ordered by ``last_user_activity_at`` ASC so a backlog
drains oldest-first across multiple cron ticks instead of
shuffling random pages of the same workspace.

TODO(wave-c): once ``workspaces.feature_flags`` lands as a JSONB
column we can read ``feature_flags->>'chat_archive_idle_days'`` to
let an admin override the threshold per workspace. Today the
``Workspace`` model only carries ``settings`` (a generic bag) and
nothing else reads ``chat_archive_idle_days`` from it, so we keep
the threshold a single global constant for now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_surface import ChatThread


THRESHOLD_DAYS_DEFAULT: int = 7
BATCH_LIMIT: int = 500


@dataclass(frozen=True)
class ArchiveSweepResult:
    """Counters returned by :func:`archive_idle_chat_threads_once`.

    - ``scanned``: rows the SELECT returned this tick (≤ ``BATCH_LIMIT``).
    - ``archived``: rows actually flipped from ``active`` → ``archived``.
    - ``skipped``: ``scanned`` minus ``archived`` — reserved for future
      eligibility checks (e.g. workspace opt-out flag); always zero
      today because the SQL already filters everything we skip.
    """

    scanned: int
    archived: int
    skipped: int

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "archived": self.archived,
            "skipped": self.skipped,
        }


async def archive_idle_chat_threads_once(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    threshold_days: int = THRESHOLD_DAYS_DEFAULT,
    batch_limit: int = BATCH_LIMIT,
) -> ArchiveSweepResult:
    """Archive one batch of idle chat threads.

    Caller owns the transaction — this helper flushes its writes but
    does not commit, so the worker can wrap the whole tick in one
    transaction (and tests can roll it back via the ``db_session``
    fixture's SAVEPOINT).

    Selection criteria (must all hold):

    - ``status == 'active'`` (resolved / archived rows are out by
      definition; this is also what makes the function idempotent).
    - ``last_user_activity_at IS NOT NULL`` — never-started zombies
      are left for a human to look at.
    - ``last_user_activity_at < now - threshold_days``.
    """
    cutoff_now = now or datetime.now(timezone.utc)
    cutoff = cutoff_now - timedelta(days=threshold_days)

    rows = (
        await session.execute(
            select(ChatThread)
            .where(
                ChatThread.status == "active",
                ChatThread.last_user_activity_at.is_not(None),
                ChatThread.last_user_activity_at < cutoff,
            )
            .order_by(ChatThread.last_user_activity_at.asc())
            .limit(batch_limit)
        )
    ).scalars().all()

    archived = 0
    for row in rows:
        # Defensive re-check — between SELECT and the in-process flip
        # nothing else mutates these rows in the same transaction, but
        # the explicit guard keeps the intent obvious to readers and
        # makes future "select FOR UPDATE SKIP LOCKED" retrofits safe.
        if row.status != "active":
            continue
        row.status = "archived"
        row.archived_at = cutoff_now
        archived += 1

    if archived:
        await session.flush()

    return ArchiveSweepResult(
        scanned=len(rows),
        archived=archived,
        skipped=len(rows) - archived,
    )


__all__ = [
    "BATCH_LIMIT",
    "THRESHOLD_DAYS_DEFAULT",
    "ArchiveSweepResult",
    "archive_idle_chat_threads_once",
]
