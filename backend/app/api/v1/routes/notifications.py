"""Workspace notification read + dismiss API (A4 + A5).

The writer lives in :mod:`backend.app.services.notifications` and is
invoked from webhook handlers; this module is the *reader* + the
one-click dismiss surface for the dashboard. Members (not just
admins) can read and dismiss because these banners are the operator's
inbox — a viewer who just merged the onboarding PR should see the
"welcome back" banner without needing to bother an admin.

Volume is tiny (a dozen open banners per workspace at peak) so we
use offset-style pagination instead of a cursor. The dashboard only
ever renders the top 5 anyway.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.notifications import WorkspaceNotification
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/notifications",
    tags=["notifications"],
)


_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str | None
    href: str | None
    payload: dict
    dedupe_key: str | None
    dismissed_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]


def _to_out(row: WorkspaceNotification) -> NotificationOut:
    return NotificationOut(
        id=row.id,
        kind=row.kind,
        title=row.title,
        body=row.body,
        href=row.href,
        payload=row.payload or {},
        dedupe_key=row.dedupe_key,
        dismissed_at=row.dismissed_at,
        created_at=row.created_at,
    )


@router.get("", response_model=NotificationPage)
async def list_notifications(
    workspace_id: uuid.UUID,
    include_dismissed: bool = Query(
        default=False,
        description=(
            "By default only open (undismissed) banners are returned. "
            "Set true to include previously-dismissed history (capped "
            "at `limit`)."
        ),
    ),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> NotificationPage:
    """Return newest-first notifications for the workspace."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(WorkspaceNotification)
        .where(WorkspaceNotification.workspace_id == workspace_id)
        .order_by(desc(WorkspaceNotification.created_at))
        .limit(limit)
    )
    if not include_dismissed:
        stmt = stmt.where(WorkspaceNotification.dismissed_at.is_(None))
    rows = (await session.execute(stmt)).scalars().all()
    return NotificationPage(items=[_to_out(r) for r in rows])


@router.post("/{notification_id}/dismiss", status_code=204)
async def dismiss_notification(
    workspace_id: uuid.UUID,
    notification_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Mark a single notification dismissed (idempotent)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    row = (
        await session.execute(
            select(WorkspaceNotification).where(
                WorkspaceNotification.id == notification_id,
                WorkspaceNotification.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    # Idempotent: re-dismissing a dismissed row is a no-op so the
    # dashboard can fire the POST without debouncing.
    if row.dismissed_at is None:
        row.dismissed_at = datetime.now(timezone.utc)
        await session.flush()


@router.post("/dismiss-all", status_code=204)
async def dismiss_all(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    """One-click "clear everything" — useful when the inbox backs up."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    rows = (
        await session.execute(
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.dismissed_at.is_(None),
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.dismissed_at = now
    if rows:
        await session.flush()


__all__ = ["router"]
