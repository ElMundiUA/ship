"""Engine health / stall detection (ELS-230 — the thesis-2 residue).

Phase 2 of the FSM rearchitecture deleted both self-heal crons, the
``scan_eligible_tickets`` backstop and the runner-fail detectors — so
the only health surface left was a DB ping, and a stalled FSM went
silent. Per thesis 2 "is the engine alive / where is it stuck" is the
ONE piece of state with no home in Linear/GitHub/email, so it lives
here, in Ship.

READ-ONLY by design: everything below derives from the existing
Postgres control state (``agent_dispatch_locks`` + ``audit_log``) at
request time. No new authoritative store, no sweeping, no dispatching,
no lock mutation — this module reports, the human (or the stall
notifier's notify() emission) decides. Thresholds are config-driven
and deliberately conservative to avoid re-creating the deleted
self-heal crons' false positives.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_dispatch import AgentDispatchLock
from backend.app.db.models.tenancy import AuditLog

# Conservative defaults; overridable per call (the route reads them
# from query params / settings later if needed).
DEFAULT_LOCK_STALL_MINUTES = 45
DEFAULT_DISPATCH_AUDIT_WINDOW_MINUTES = 90


@dataclass(frozen=True)
class StalledTicket:
    lock_key: str
    claimed_at: datetime
    expires_at: datetime
    age_minutes: float
    reason: str  # 'expired_not_swept' | 'lock_held_no_progress'
    run_id: int | None


@dataclass(frozen=True)
class EngineLiveness:
    last_dispatch_at: datetime | None
    last_finish_at: datetime | None
    active_locks: int
    expired_unswept_locks: int
    stalled: list[StalledTicket]

    @property
    def healthy(self) -> bool:
        return not self.stalled and self.expired_unswept_locks == 0


async def assess_engine_health(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    lock_stall_minutes: int = DEFAULT_LOCK_STALL_MINUTES,
    dispatch_window_minutes: int = DEFAULT_DISPATCH_AUDIT_WINDOW_MINUTES,
    now: datetime | None = None,
) -> EngineLiveness:
    """Compute liveness + stalls for one workspace. Zero writes."""
    now = now or datetime.now(timezone.utc)

    async def _last(action: str) -> datetime | None:
        return await session.scalar(
            select(func.max(AuditLog.created_at)).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == action,
            )
        )

    last_dispatch = await _last("agent_run.dispatch")
    last_finish = await _last("agent_run.finish")

    locks = (
        await session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == workspace_id
            )
        )
    ).scalars().all()

    stalled: list[StalledTicket] = []
    expired_unswept = 0
    active = 0
    stall_cutoff = now - timedelta(minutes=lock_stall_minutes)
    window_start = now - timedelta(minutes=dispatch_window_minutes)

    for lock in locks:
        expires = lock.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        claimed = lock.claimed_at
        if claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=timezone.utc)
        age_min = (now - claimed).total_seconds() / 60.0

        if expires < now:
            # (a) lease past expires_at but still present — the sweeper
            # is not running (or died). This is the project_lock-leak
            # class made visible.
            expired_unswept += 1
            stalled.append(
                StalledTicket(
                    lock_key=lock.key,
                    claimed_at=claimed,
                    expires_at=expires,
                    age_minutes=round(age_min, 1),
                    reason="expired_not_swept",
                    run_id=lock.run_id,
                )
            )
            continue

        active += 1
        if claimed < stall_cutoff:
            # (b) long-held lock with no recent dispatch progress in
            # the audit feed → stuck in flight.
            recent_progress = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.workspace_id == workspace_id,
                    AuditLog.action.in_(
                        ("agent_run.dispatch", "agent_run.finish")
                    ),
                    AuditLog.created_at >= window_start,
                    AuditLog.target_id == _ticket_from_key(lock.key),
                )
            )
            if not recent_progress:
                stalled.append(
                    StalledTicket(
                        lock_key=lock.key,
                        claimed_at=claimed,
                        expires_at=expires,
                        age_minutes=round(age_min, 1),
                        reason="lock_held_no_progress",
                        run_id=lock.run_id,
                    )
                )

    return EngineLiveness(
        last_dispatch_at=last_dispatch,
        last_finish_at=last_finish,
        active_locks=active,
        expired_unswept_locks=expired_unswept,
        stalled=stalled,
    )


def _ticket_from_key(key: str) -> str:
    """``ticket:ELS-42`` -> ``ELS-42``; project locks keep their id."""
    for prefix in ("ticket:", "project:"):
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


__all__ = [
    "EngineLiveness",
    "StalledTicket",
    "assess_engine_health",
    "DEFAULT_LOCK_STALL_MINUTES",
    "DEFAULT_DISPATCH_AUDIT_WINDOW_MINUTES",
]
