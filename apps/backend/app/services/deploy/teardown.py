"""Tear down deployed apps — really delete them from the provider so they
stop running and billing.

Used by:
* the deploy ``DELETE`` route (explicit per-app delete), and
* orphan-billing hooks: repo disconnect and workspace deletion, which must
  confirm provider teardown before cascading away rows/tokens.

DigitalOcean is the only provider today. Deletes are idempotent (a 404 means
the app is already gone). Returns the set of app ids deleted vs failed so the
caller can block destructive local deletion if teardown cannot be confirmed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.deploy import Deployment, DeploymentStatus as DS
from backend.app.services.deploy.providers.operations import (
    ProviderOperationUnsupported,
    delete_provider_app,
    get_provider_token,
)


log = logging.getLogger(__name__)


@dataclass(slots=True)
class TeardownResult:
    deleted_app_ids: list[str] = field(default_factory=list)
    failed_app_ids: list[str] = field(default_factory=list)
    rows_removed: int = 0
    rows_soft_deleted: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed_app_ids


async def _delete_provider_app(provider: str, app_id: str, token: str) -> bool:
    """Delete one provider app. True if gone (incl. provider 404)."""
    try:
        ok = await delete_provider_app(provider=provider, token=token, app_id=app_id)
    except ProviderOperationUnsupported as exc:
        log.warning("teardown: %s", exc)
        return False
    if not ok:
        log.warning("teardown: provider delete app %s/%s failed", provider, app_id)
    return ok


async def _teardown(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rows: list[Deployment],
    *,
    delete_rows: bool,
) -> TeardownResult:
    result = TeardownResult()
    app_refs = {
        (d.provider, aid)
        for d in rows
        if (aid := (d.provider_ref or {}).get("app_id"))
    }
    if app_refs:
        by_provider: dict[str, set[str]] = {}
        for provider, aid in app_refs:
            by_provider.setdefault(provider, set()).add(aid)
        for provider, ids in by_provider.items():
            try:
                token = await get_provider_token(session, workspace_id, provider)
            except ProviderOperationUnsupported as exc:
                log.warning("teardown: %s", exc)
                result.failed_app_ids.extend(ids)
                continue
            if token:
                for aid in ids:
                    if await _delete_provider_app(provider, aid, token):
                        result.deleted_app_ids.append(aid)
                    else:
                        result.failed_app_ids.append(aid)
            else:
                # No token → we can't delete; treat as failed so callers don't
                # assume the cloud app is gone.
                result.failed_app_ids.extend(ids)

    # Only mark rows deleted when the cloud side is fully gone (else we'd lose
    # the app_id we need to retry / reconcile). We keep provider_ref/app_id for
    # billing auditability instead of hard-deleting the deployment history.
    if delete_rows and result.ok:
        now = datetime.now(timezone.utc)
        for d in rows:
            ref = dict(d.provider_ref or {})
            ref["deleted_at"] = now.isoformat()
            d.provider_ref = ref
            d.status = DS.DELETED
            d.status_detail = "DELETED"
            d.live_url = None
            d.healthy = None
            d.error_message = None
            d.finished_at = d.finished_at or now
            d.updated_at = now
        result.rows_soft_deleted = len(rows)
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
