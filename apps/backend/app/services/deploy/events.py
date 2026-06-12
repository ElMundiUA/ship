"""Per-app activity events — the human-readable story shown on a card.

Events are keyed by the app (workspace + repo + provider) so they span
redeploys. Writers call :func:`record_event` at lifecycle points (deployed,
failed, deleted, removed-externally); the card's Activity tab reads them via
:func:`list_app_events`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.deploy import DeploymentEvent


async def record_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    provider: str,
    kind: str,
    message: str,
    deployment_id: uuid.UUID | None = None,
) -> DeploymentEvent:
    """Append one activity event. Caller commits/flushes."""
    ev = DeploymentEvent(
        workspace_id=workspace_id,
        repo_id=repo_id,
        provider=provider,
        kind=kind,
        message=message,
        deployment_id=deployment_id,
    )
    session.add(ev)
    return ev


async def list_app_events(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    provider: str,
    limit: int = 30,
) -> list[DeploymentEvent]:
    """Newest-first activity for one app."""
    rows = (
        await session.execute(
            select(DeploymentEvent)
            .where(
                DeploymentEvent.workspace_id == workspace_id,
                DeploymentEvent.repo_id == repo_id,
                DeploymentEvent.provider == provider,
            )
            .order_by(DeploymentEvent.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


__all__ = ["record_event", "list_app_events"]
