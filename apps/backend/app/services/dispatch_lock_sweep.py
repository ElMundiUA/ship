"""Background sweeper for dangling ``project:*`` dispatch locks (ELS-149).

The dispatcher's per-project WIP lock has a 24-hour TTL and only
auto-releases via :func:`_release_project_lock_for_ticket` calls from
the agent-finish handler (or PR merge webhook). Two failure modes
leave the lock dangling:

- **Agent CI crashes pre-`/finish`.** Cursor Free runtime errors,
  OOM, SIGKILL, container restarts — none of these trigger
  ``agent_run.finish``. The project_lock survives until 24h TTL.
  Caught on Askslayer/PAC-32 2026-05-18: lock held 6h11m.
- **Workflow_dispatch fires but workflow_run never reaches the
  agent.** GH API hiccup, action queue stall, workflow file
  rejected by GitHub — the audit log holds an ``agent_run.dispatch``
  row that's never followed by a ``finish``.

The picker-null path (overlay_frozen / orphan / priority /
refire_capped) is handled directly in ``_next_task`` (ELS-148) — by
the time we reach here the picker had a chance to release. This
sweeper covers the cases where the picker is never called at all.

Algorithm: every 5 minutes, find ``agent_dispatch_locks`` whose key
starts with ``project:`` and were claimed more than 60 min ago.
For each, look up the owning dispatch via ``lock.run_id`` to recover
the ``ticket_ref``. If there is no ``agent_run.dispatch`` or
``agent_run.finish`` for that ticket in the last 30 minutes, the
chain is dead — release the lock and write a
``dispatch.project_lock_swept`` audit row so the post-mortem trail
is preserved.

Conservative cadence + 60-minute floor + 30-min activity window
mean a legitimately slow chain (planning → dev → validation → etc.,
where each stage runs ~10-15 min) is never falsely cleared. The
lock is only released when no agent activity has touched the
ticket in 30 minutes AND the lock is at least 60 min old.

Cron registration lives in :mod:`backend.app.services.cron_jobs`
under :data:`CronLockId.AGENT_DISPATCH_LOCK_SWEEP`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_sessionmaker

log = logging.getLogger(__name__)

# Lock must be older than this before the sweeper even looks at it.
# Set to comfortably exceed the slowest legitimate stage time.
STALE_PROJECT_LOCK_AGE_MIN = 60

# If the lock's ticket has had any ``agent_run.*`` event newer than
# this, treat the chain as alive even if the lock itself is older
# than the floor.
ACTIVE_CHAIN_WINDOW_MIN = 30


async def sweep_dangling_project_locks(session: AsyncSession) -> int:
    """Release ``project:*`` locks whose owning chain is dead.

    Returns the count of locks released. Audit rows are added to the
    session but NOT committed — the cron wrapper handles commit so a
    test can roll back.
    """
    stale = (
        await session.execute(
            text(
                f"""
                SELECT id, workspace_id, key, run_id, claimed_at
                FROM agent_dispatch_locks
                WHERE key LIKE 'project:%'
                  AND claimed_at < NOW() - INTERVAL '{STALE_PROJECT_LOCK_AGE_MIN} minutes'
                """
            )
        )
    ).all()
    if not stale:
        return 0

    now = datetime.now(timezone.utc)
    released = 0
    for lock_row in stale:
        owning_ticket_ref: str | None = None
        owning_dispatch_id = lock_row.run_id
        if owning_dispatch_id is not None:
            owning = (
                await session.execute(
                    text(
                        """
                        SELECT target_id, payload->>'ticket_ref' AS tr
                        FROM audit_log
                        WHERE id = :rid
                        LIMIT 1
                        """
                    ),
                    {"rid": owning_dispatch_id},
                )
            ).first()
            if owning is not None:
                owning_ticket_ref = owning.tr or owning.target_id

        # Without a ticket_ref we can't tell whether the chain is
        # alive. Don't release — leave the lock for the 24h TTL.
        # This is the catastrophe-only fallback path.
        if not owning_ticket_ref:
            log.debug(
                "lock_sweep: skip lock=%s (no ticket_ref for run_id=%s)",
                lock_row.key, owning_dispatch_id,
            )
            continue

        recent_activity = (
            await session.execute(
                text(
                    f"""
                    SELECT 1
                    FROM audit_log
                    WHERE workspace_id = :ws
                      AND action IN ('agent_run.dispatch', 'agent_run.finish')
                      AND (
                        target_id = :tid
                        OR payload->>'ticket_ref' = :tid
                      )
                      AND created_at > NOW() - INTERVAL '{ACTIVE_CHAIN_WINDOW_MIN} minutes'
                    LIMIT 1
                    """
                ),
                {"ws": lock_row.workspace_id, "tid": owning_ticket_ref},
            )
        ).first()
        if recent_activity is not None:
            # Chain alive — leave the lock alone. Common case for a
            # multi-stage chain mid-flight.
            continue

        deleted = (
            await session.execute(
                text(
                    "DELETE FROM agent_dispatch_locks WHERE id = :lid "
                    "RETURNING 1"
                ),
                {"lid": lock_row.id},
            )
        ).first()
        if deleted is None:
            # Someone else (PR merge webhook, finish handler arriving
            # between our SELECT and DELETE) already released. Skip
            # the audit row so we don't claim credit for somebody
            # else's cleanup.
            continue

        age_seconds = int((now - lock_row.claimed_at).total_seconds())
        session.add(
            AuditLog(
                workspace_id=lock_row.workspace_id,
                action="dispatch.project_lock_swept",
                target_kind="ticket",
                target_id=owning_ticket_ref,
                payload={
                    "lock_key": lock_row.key,
                    "owning_run_id": (
                        str(owning_dispatch_id)
                        if owning_dispatch_id is not None
                        else None
                    ),
                    "claimed_at": lock_row.claimed_at.isoformat(),
                    "age_seconds": age_seconds,
                    "reason": "no_recent_activity",
                },
            )
        )
        released += 1
        log.info(
            "lock_sweep: released stale lock key=%s ws=%s ticket=%s age=%ds",
            lock_row.key, lock_row.workspace_id, owning_ticket_ref, age_seconds,
        )
    return released


async def sweep_dangling_project_locks_tick() -> None:
    """Cron entrypoint — wraps :func:`sweep_dangling_project_locks`
    with the sessionmaker boilerplate and a single commit at the end."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            released = await sweep_dangling_project_locks(session)
        except Exception:
            await session.rollback()
            raise
        await session.commit()
    if released:
        log.info("lock_sweep: tick released %d project lock(s)", released)


__all__ = [
    "STALE_PROJECT_LOCK_AGE_MIN",
    "ACTIVE_CHAIN_WINDOW_MIN",
    "sweep_dangling_project_locks",
    "sweep_dangling_project_locks_tick",
]
