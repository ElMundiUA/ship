"""Tear down deployed apps — really delete them from the provider so they
stop running and billing.

Used by:
* the deploy ``DELETE`` route (explicit per-app delete), and
* orphan-billing hooks: repo disconnect and workspace deletion, which would
  otherwise cascade away our rows while leaving the cloud app live and
  billing.

DigitalOcean is the only provider today. Deletes are idempotent (a 404 means
the app is already gone). Returns the set of app ids deleted vs failed so the
caller can decide whether to surface an error or just log (the reconcile cron
is the backstop for any that failed transiently).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.deploy import Deployment
from backend.app.integrations.digitalocean import client as do_client
from backend.app.services.deploy.credentials import get_do_token


log = logging.getLogger(__name__)


@dataclass(slots=True)
class TeardownResult:
    deleted_app_ids: list[str] = field(default_factory=list)
    failed_app_ids: list[str] = field(default_factory=list)
    rows_removed: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed_app_ids


async def _delete_do_app(app_id: str, token: str) -> bool:
    """Delete one DO app. True if gone (incl. already-404); False on real error."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
            await do_client.delete_app(app_id, token=token, client=http)
        return True
    except do_client.DigitalOceanAPIError as exc:
        if exc.status == 404:
            return True
        log.warning("teardown: DO delete app %s failed: %s", app_id, exc)
        return False
    except httpx.HTTPError as exc:
        log.warning("teardown: DO delete app %s network error: %s", app_id, exc)
        return False


async def _teardown(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rows: list[Deployment],
    *,
    delete_rows: bool,
) -> TeardownResult:
    result = TeardownResult()
    app_ids = {
        aid
        for d in rows
        if (aid := (d.provider_ref or {}).get("app_id"))
    }
    if app_ids:
        token = await get_do_token(session, workspace_id)
        if token:
            for aid in app_ids:
                if await _delete_do_app(aid, token):
                    result.deleted_app_ids.append(aid)
                else:
                    result.failed_app_ids.append(aid)
        else:
            # No token → we can't delete; treat as failed so callers don't
            # assume the cloud app is gone.
            result.failed_app_ids.extend(app_ids)

    # Only drop our rows when the cloud side is fully gone (else we'd lose
    # the app_id we need to retry / reconcile).
    if delete_rows and result.ok:
        for d in rows:
            await session.delete(d)
        result.rows_removed = len(rows)
        await session.flush()
    return result


async def teardown_repo_app(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    *,
    delete_rows: bool = True,
) -> TeardownResult:
    """Delete the DO app(s) for one repo in a workspace."""
    rows = (
        await session.execute(
            select(Deployment).where(
                Deployment.workspace_id == workspace_id,
                Deployment.repo_id == repo_id,
                Deployment.provider == "digitalocean",
            )
        )
    ).scalars().all()
    return await _teardown(session, workspace_id, list(rows), delete_rows=delete_rows)


async def teardown_workspace_apps(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    delete_rows: bool = False,
) -> TeardownResult:
    """Delete ALL DO apps in a workspace. Call BEFORE deleting the workspace
    row (the DO token + deployment rows cascade away with it). ``delete_rows``
    defaults False because the workspace cascade removes them anyway."""
    rows = (
        await session.execute(
            select(Deployment).where(
                Deployment.workspace_id == workspace_id,
                Deployment.provider == "digitalocean",
            )
        )
    ).scalars().all()
    return await _teardown(session, workspace_id, list(rows), delete_rows=delete_rows)


__all__ = ["TeardownResult", "teardown_repo_app", "teardown_workspace_apps"]
