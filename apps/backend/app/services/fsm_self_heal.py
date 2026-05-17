"""FSM self-heal — closes the gaps that event-driven dispatch leaves open.

Two side-band loops the main path (`tracker_poller` → `dispatcher`)
relies on but doesn't itself run:

1. :func:`auto_reprovision_on_startup` — when a new FSM stage is
   added to :data:`SHIP_FSM_STAGES` and the backend redeploys, the
   existing workspaces' ``Integration.config.label_id_by_stage`` is
   missing that stage's Linear label id. ``transition()`` silently
   skips the breadcrumb (the label was never created on Linear),
   the next picker can't match, the chain wedges. Caught on
   askslayer/PAC-17..22 + Ship-on-Ship/ELS-7 2026-05-17 — adding
   ``auto_merge`` required ad-hoc reprovision against two
   workspaces by hand. Now we walk every workspace on startup and
   reprovision if the FSM has grown.

2. :func:`scan_eligible_tickets` — periodic backstop for
   event-driven dispatch. The poller only fires events on STATE
   CHANGES; if a maybe_dispatch was dropped (lock, refire-cap,
   transient error), the ticket sits idle until something else
   moves its state. This cron scans the Linear FSM filter for each
   live stage and re-fires dispatch on tickets that have no
   ``agent_run.dispatch`` in the last :data:`STALE_DISPATCH_WINDOW`.
   Conservative cadence (every 15 min) to avoid running roughshod
   over the poller's view.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.db.session import get_sessionmaker
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.security.encryption import safe_decrypt
from backend.app.services import linear_provisioner


log = logging.getLogger(__name__)


STALE_DISPATCH_WINDOW = timedelta(minutes=20)
# Stages the backstop scan considers. We exclude ``self_heal`` /
# decomposition (special chains) and the legacy intake-substage
# names — those are handled by the labels-as-breadcrumb path.
SCAN_STAGES: tuple[str, ...] = (
    "planning",
    "dev_implementation",
    "validation",
    "code_review",
    "auto_merge",
)


async def auto_reprovision_on_startup() -> None:
    """Walk every workspace with a Linear integration and reprovision
    its FSM if ``SHIP_FSM_STAGES`` has grown since the last
    provisioning. Idempotent: existing labels stay; only missing
    stages get fresh ones. Audit-logged on add.

    Fires from the FastAPI lifespan; errors are swallowed (logged)
    so a single workspace failing doesn't block the rest of startup.
    """
    settings = get_settings()
    Session = get_sessionmaker()
    target_stages = set(linear_provisioner.SHIP_FSM_STAGES)
    log.info(
        "fsm_self_heal: auto-reprovision check on %d expected stages",
        len(target_stages),
    )
    async with Session() as session:
        # Walk Linear installs; each links to a workspace, each
        # workspace has at most one workspace-scope Integration row
        # with the FSM config.
        installs = (
            await session.execute(
                select(NativeIntegrationInstallation).where(
                    NativeIntegrationInstallation.provider
                    == NativeIntegrationProvider.LINEAR,
                    NativeIntegrationInstallation.status
                    == NativeIntegrationStatus.READY,
                    NativeIntegrationInstallation.disabled_at.is_(None),
                )
            )
        ).scalars().all()
        for install in installs:
            try:
                await _reprovision_one_workspace(
                    session, install, target_stages, settings
                )
            except Exception as exc:  # noqa: BLE001 — per-ws best-effort
                log.warning(
                    "fsm_self_heal: reprovision failed ws=%s err=%s",
                    install.workspace_id, exc,
                )
                await session.rollback()
            else:
                await session.commit()


async def _reprovision_one_workspace(
    session: AsyncSession,
    install: NativeIntegrationInstallation,
    target_stages: set[str],
    settings,
) -> None:
    legacy = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == install.workspace_id,
                Integration.kind == "linear",
                Integration.repo_id.is_(None),
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if legacy is None or not legacy.config:
        log.debug(
            "fsm_self_heal: skip ws=%s — no legacy linear config",
            install.workspace_id,
        )
        return
    cfg = legacy.config
    existing_stages = set((cfg.get("label_id_by_stage") or {}).keys())
    missing = sorted(target_stages - existing_stages)
    if not missing:
        return
    team_key = cfg.get("team_key")
    if not team_key:
        log.debug(
            "fsm_self_heal: skip ws=%s — no team_key bound",
            install.workspace_id,
        )
        return
    cred = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == install.id,
                NativeIntegrationCredential.kind == "access_token",
                NativeIntegrationCredential.revoked_at.is_(None),
            )
            .order_by(NativeIntegrationCredential.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if cred is None:
        return
    token = safe_decrypt(cred.secret_ciphertext)
    if not token:
        return
    log.info(
        "fsm_self_heal: reprovisioning ws=%s — missing stages: %s",
        install.workspace_id, missing,
    )
    live = LinearTracker(token)
    result = await linear_provisioner.provision_team(
        tracker=live, team_key=team_key, settings=settings
    )
    new_cfg = dict(cfg)
    new_cfg.update(
        {
            "team_id": result.team_id,
            "team_key": result.team_key,
            "state_id_by_name": result.state_id_by_name,
            "label_id_by_stage": result.label_id_by_stage,
            "signal_label_ids": result.signal_label_ids,
            "canonical_to_native": result.canonical_to_native,
            "fsm_provisioned": True,
        }
    )
    legacy.config = new_cfg
    legacy.updated_at = datetime.now(timezone.utc)
    session.add(
        AuditLog(
            workspace_id=install.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="tracker.reprovision_fsm.auto",
            target_kind="workspace",
            target_id=str(install.workspace_id),
            payload={
                "team_key": result.team_key,
                "added_stages": missing,
            },
        )
    )


async def scan_eligible_tickets() -> None:
    """Backstop scan for tickets that the event-driven dispatcher
    might have missed.

    For each workspace with a live Linear integration, for each
    stage in :data:`SCAN_STAGES`, query the picker's Linear filter
    (``list_tickets(state)``). Any ticket without a recent
    ``agent_run.dispatch`` (within :data:`STALE_DISPATCH_WINDOW`)
    gets re-dispatched via :func:`maybe_dispatch`. That's the same
    code path tracker_poller uses; the only difference is what
    triggers it.

    Conservative — runs every 15 minutes from
    :class:`CronLockId.FSM_SCAN_BACKSTOP`.
    """
    settings = get_settings()
    if not settings.tracker_poll_fire:
        log.debug("fsm_self_heal: scan skipped — SHIP_TRACKER_POLL_FIRE off")
        return
    Session = get_sessionmaker()
    fired = 0
    async with Session() as session:
        installs = (
            await session.execute(
                select(NativeIntegrationInstallation).where(
                    NativeIntegrationInstallation.provider
                    == NativeIntegrationProvider.LINEAR,
                    NativeIntegrationInstallation.status
                    == NativeIntegrationStatus.READY,
                    NativeIntegrationInstallation.disabled_at.is_(None),
                )
            )
        ).scalars().all()
        for install in installs:
            try:
                fired += await _scan_one_workspace(session, install)
            except Exception as exc:  # noqa: BLE001 — per-ws best-effort
                log.warning(
                    "fsm_self_heal: scan failed ws=%s err=%s",
                    install.workspace_id, exc,
                )
                await session.rollback()
                continue
            await session.commit()
    if fired:
        log.info("fsm_self_heal: backstop fired %d dispatches", fired)


async def _scan_one_workspace(
    session: AsyncSession,
    install: NativeIntegrationInstallation,
) -> int:
    legacy = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == install.workspace_id,
                Integration.kind == "linear",
                Integration.repo_id.is_(None),
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if legacy is None or not legacy.config:
        return 0
    cfg = legacy.config
    cred = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == install.id,
                NativeIntegrationCredential.kind == "access_token",
                NativeIntegrationCredential.revoked_at.is_(None),
            )
            .order_by(NativeIntegrationCredential.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if cred is None:
        return 0
    token = safe_decrypt(cred.secret_ciphertext)
    if not token:
        return 0
    tracker = LinearTracker(
        token,
        team_id=cfg.get("team_id"),
        team_key=cfg.get("team_key"),
        state_id_by_name=cfg.get("state_id_by_name") or {},
        label_id_by_stage=cfg.get("label_id_by_stage") or {},
        signal_label_ids=cfg.get("signal_label_ids") or {},
        fsm_to_linear_state=linear_provisioner.FSM_TO_LINEAR_STATE,
    )
    from backend.app.services.dispatcher import maybe_dispatch
    fired = 0
    cutoff = datetime.now(timezone.utc) - STALE_DISPATCH_WINDOW
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for stage in SCAN_STAGES:
            try:
                rows = await tracker.list_tickets(state=stage, limit=20)
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "fsm_self_heal: list_tickets(%s) failed ws=%s err=%s",
                    stage, install.workspace_id, exc,
                )
                continue
            for row in rows or []:
                ref = row.get("id") or row.get("identifier")
                if not ref:
                    continue
                recent = (
                    await session.execute(
                        select(AuditLog.id)
                        .where(
                            AuditLog.workspace_id == install.workspace_id,
                            AuditLog.action == "agent_run.dispatch",
                            AuditLog.target_id == ref,
                            AuditLog.created_at >= cutoff,
                        )
                        .limit(1)
                    )
                ).first()
                if recent is not None:
                    continue
                # No fresh dispatch on this (ws, ticket) — re-fire.
                await maybe_dispatch(
                    session,
                    workspace_id=install.workspace_id,
                    ticket_ref=ref,
                    trigger_kind="fsm_self_heal",
                    fsm_stage=stage,
                    client=client,
                )
                fired += 1
    return fired


__all__ = [
    "auto_reprovision_on_startup",
    "scan_eligible_tickets",
    "SCAN_STAGES",
    "STALE_DISPATCH_WINDOW",
]
