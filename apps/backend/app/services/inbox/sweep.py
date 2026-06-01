"""Inbox sweep helpers — auto-recover on ticket advance + stale cron."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_surface import Clarification
from backend.app.db.models.inbox import InboxItem, InboxItemEvent

logger = logging.getLogger(__name__)


async def sweep_auto_resolvable(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    fsm_stage: str | None,
) -> int:
    """Close open auto-resolvable rows for ``(ticket_ref, fsm_stage)``.

    Only ``status='new'`` rows with ``auto_resolvable=true`` are touched.
    Snoozed rows are intentionally excluded.
    """
    if not ticket_ref or not fsm_stage:
        return 0

    now = datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.status == "new",
                InboxItem.auto_resolvable.is_(True),
                InboxItem.payload["ticket_ref"].astext == ticket_ref,
                InboxItem.payload["fsm_stage"].astext == fsm_stage,
            )
        )
    ).scalars().all()

    for item in rows:
        item.status = "resolved"
        item.resolution = "auto_recovered"
        item.resolved_at = now
        session.add(
            InboxItemEvent(
                item_id=item.id,
                actor_user_id=None,
                actor_kind="system",
                action="auto_recovered",
                payload={
                    "ticket_ref": ticket_ref,
                    "fsm_stage": fsm_stage,
                },
            )
        )

    if rows:
        await session.flush()
        logger.info(
            "inbox sweep_auto_resolvable ws=%s ticket=%s stage=%s count=%d",
            workspace_id,
            ticket_ref,
            fsm_stage,
            len(rows),
        )
    return len(rows)


async def sweep_stale_inbox_items(session: AsyncSession) -> int:
    """Dismiss ``new`` rows past ``stale_after`` (global cron tick)."""
    now = datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(InboxItem).where(
                InboxItem.status == "new",
                InboxItem.stale_after.is_not(None),
                InboxItem.created_at + InboxItem.stale_after < now,
            )
        )
    ).scalars().all()

    stale_clarification_ids: list[uuid.UUID] = []
    for item in rows:
        item.status = "dismissed"
        item.resolution = "stale"
        item.resolved_at = now
        # Keep the legacy source row in step with its mirror so the
        # ``clarifications`` endpoint stops projecting a dismissed card
        # as ``open`` (the inbox sweep only touches inbox_items).
        if item.source_table == "clarifications" and item.source_id:
            stale_clarification_ids.append(item.source_id)
        session.add(
            InboxItemEvent(
                item_id=item.id,
                actor_user_id=None,
                actor_kind="system",
                action="dismissed",
                payload={"resolution": "stale"},
            )
        )

    if stale_clarification_ids:
        await session.execute(
            update(Clarification)
            .where(
                Clarification.id.in_(stale_clarification_ids),
                Clarification.status == "open",
            )
            .values(status="stale", updated_at=now)
        )

    if rows:
        await session.flush()
        logger.info("inbox sweep_stale count=%d", len(rows))
    return len(rows)


__all__ = ["sweep_auto_resolvable", "sweep_stale_inbox_items"]
