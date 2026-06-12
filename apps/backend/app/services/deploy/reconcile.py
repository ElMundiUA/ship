"""Detect drift between our deployment rows and the provider's real apps.

For each ACTIVE deployment with a recorded provider ``app_id``, ask the
provider whether the app still exists. If it's gone (404) the app was removed
outside Ship (DO dashboard, crash, etc.) → flip our row to a red/failed state,
clear the live URL + health, and record a ``removed_externally`` activity
event so the user sees *why* it stopped working. The card then shows red +
Redeploy instead of a stale green.

Safety: we only ever query ``app_id``s WE recorded. We never enumerate or
delete arbitrary apps the user owns on the provider.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.deploy import (
    Deployment,
    DeploymentEventKind,
    DeploymentStatus as DS,
)
from backend.app.integrations.digitalocean import client as do_client
from backend.app.services.deploy.credentials import get_do_token
from backend.app.services.deploy.events import record_event


log = logging.getLogger(__name__)


async def reconcile_active_deployments(session: AsyncSession) -> tuple[int, int]:
    """Check every ACTIVE DigitalOcean deployment still exists on DO.

    Returns ``(checked, drifted)``. Caller commits.
    """
    rows = (
        await session.execute(
            select(Deployment).where(
                Deployment.status == DS.ACTIVE,
                Deployment.provider == "digitalocean",
            )
        )
    ).scalars().all()

    by_ws: dict[uuid.UUID, list[Deployment]] = {}
    for d in rows:
        if (d.provider_ref or {}).get("app_id"):
            by_ws.setdefault(d.workspace_id, []).append(d)

    checked = drifted = 0
    now = datetime.now(timezone.utc)
    for ws_id, deps in by_ws.items():
        token = await get_do_token(session, ws_id)
        if not token:
            continue
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
            for d in deps:
                app_id = (d.provider_ref or {}).get("app_id")
                checked += 1
                try:
                    await do_client.get_app(app_id, token=token, client=http)
                    continue  # still exists — nothing to do
                except do_client.DigitalOceanAPIError as exc:
                    if exc.status != 404:
                        continue  # transient/auth — don't touch the row
                except httpx.HTTPError:
                    continue  # network blip — skip this tick
                # 404 ⇒ the app is gone from DigitalOcean.
                d.status = DS.FAILED
                d.error_message = "Removed on DigitalOcean (detected by Ship)"
                d.live_url = None
                d.healthy = None
                d.updated_at = now
                await record_event(
                    session,
                    workspace_id=ws_id,
                    repo_id=d.repo_id,
                    provider=d.provider,
                    kind=DeploymentEventKind.REMOVED_EXTERNALLY,
                    message="App was removed on DigitalOcean — detected by Ship.",
                    deployment_id=d.id,
                )
                drifted += 1
    return checked, drifted


__all__ = ["reconcile_active_deployments"]
