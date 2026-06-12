"""Stall notifier (ELS-231) — engine-stalled signal through notify().

Closes the silent-failure gap: when :func:`engine_health.assess_engine_health`
reports a stalled ticket / dead engine, forward a structured BLOCKER
through the Phase-1 notification seam. No new transport, no resurrected
self-heal: this module REPORTS — it never moves a ticket status and
never touches a lock.

Safety properties:

* Behind ``SHIP_STALL_NOTIFY`` (default off).
* Idempotent per ``(lock_key, reason)`` within ``cooldown_minutes`` —
  deduped via ``audit_log action='engine.stall_notified'`` (the same
  pattern the cascade counter uses). Scheduler interval must be >= the
  cooldown to keep the dedup meaningful.
* Pure consumer of engine_health + notify(); the only row it writes
  itself is the dedup audit marker.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import AuditLog
from backend.app.services.engine_health import (
    StalledTicket,
    assess_engine_health,
)

logger = logging.getLogger("ship.stall_notifier")

STALL_NOTIFIED_ACTION = "engine.stall_notified"
DEFAULT_COOLDOWN_MINUTES = 360  # 6h — conservative, avoid letter storms


async def _already_notified(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    lock_key: str,
    reason: str,
    cooldown_minutes: int,
    now: datetime,
) -> bool:
    cutoff = now - timedelta(minutes=cooldown_minutes)
    count = await session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.action == STALL_NOTIFIED_ACTION,
            AuditLog.target_id == f"{lock_key}|{reason}",
            AuditLog.created_at >= cutoff,
        )
    )
    return bool(count)


async def notify_stalls_for_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    now: datetime | None = None,
) -> int:
    """Assess one workspace and forward fresh stalls. Returns the
    number of notifications emitted (post-dedup)."""
    from backend.app.services.notify import NotifyLevel, notify

    now = now or datetime.now(timezone.utc)
    liveness = await assess_engine_health(
        session, workspace_id=workspace_id, now=now
    )
    emitted = 0
    for stall in liveness.stalled:
        if await _already_notified(
            session,
            workspace_id=workspace_id,
            lock_key=stall.lock_key,
            reason=stall.reason,
            cooldown_minutes=cooldown_minutes,
            now=now,
        ):
            continue
        await notify(
            session,
            workspace_id=workspace_id,
            ticket_ref=_ticket_ref_for(stall),
            title=f"Engine stalled: {stall.lock_key} ({stall.reason})"[:250],
            body=(
                f"The dispatch engine looks stuck on `{stall.lock_key}`: "
                f"{_reason_text(stall)} Lock claimed "
                f"{stall.age_minutes:.0f} minutes ago"
                f" (expires {stall.expires_at.isoformat()}). Ship will not "
                "auto-act — inspect /engine-health and release or unblock "
                "manually."
            ),
            level=NotifyLevel.BLOCKER,
            dedup_key=f"stall:{stall.lock_key}:{stall.reason}",
            payload={
                "kind": "engine_stall",
                "lock_key": stall.lock_key,
                "reason": stall.reason,
                "age_minutes": stall.age_minutes,
                "claimed_at": stall.claimed_at.isoformat(),
                "expires_at": stall.expires_at.isoformat(),
                "run_id": stall.run_id,
            },
            inbox_overrides={"intake_reason": "engine_stall"},
        )
        # Dedup marker — the cooldown anchor.
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_token_id=None,
                action=STALL_NOTIFIED_ACTION,
                target_kind="lock",
                target_id=f"{stall.lock_key}|{stall.reason}",
                payload={"reason": stall.reason, "age_minutes": stall.age_minutes},
            )
        )
        emitted += 1
    if emitted:
        await session.flush()
    return emitted


def _ticket_ref_for(stall: StalledTicket) -> str | None:
    if stall.lock_key.startswith("ticket:"):
        return stall.lock_key[len("ticket:"):]
    return None


def _reason_text(stall: StalledTicket) -> str:
    if stall.reason == "expired_not_swept":
        return (
            "the lease is past its TTL but was never swept — the lock "
            "sweeper may not be running."
        )
    return (
        "the lock is held but no dispatch/finish progress landed in the "
        "audit window."
    )


__all__ = [
    "DEFAULT_COOLDOWN_MINUTES",
    "STALL_NOTIFIED_ACTION",
    "notify_stalls_for_workspace",
]
