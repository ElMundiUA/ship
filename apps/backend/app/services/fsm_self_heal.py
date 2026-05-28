"""FSM startup helper — keep workspaces' Linear FSM provisioning in
sync with code.

When a new stage is added to :data:`SHIP_FSM_STAGES` and the backend
redeploys, existing workspaces' ``Integration.config.label_id_by_stage``
is missing that stage's Linear label id. ``transition()`` silently
skips the breadcrumb (the label was never created on Linear), the next
picker can't match, the chain wedges. Caught on askslayer/PAC-17..22 +
Ship-on-Ship/ELS-7 2026-05-17 — adding ``auto_merge`` required ad-hoc
reprovision by hand. :func:`auto_reprovision_on_startup` walks every
workspace on lifespan startup and reprovisions if the FSM has grown.

The 15-min ``scan_eligible_tickets`` backstop + workspace/per-ticket
runner-fail detectors lived here until 2026-05-28 (Phase 2 of the
event-driven rearchitecture). Phase 1 (PR #341) made every
``outcome=blocked`` add a Linear label the picker drops via
OVERLAY_FREEZE_LABEL_PREFIXES; that means a re-dispatch on a stuck
ticket has no work to find. The cron was deleted as part of the same
push that removed the auto-cascade + refire_cap throttle; runner-fail
detectors went with it.
"""

from __future__ import annotations

import logging

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
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.security.encryption import safe_decrypt
from backend.app.services import linear_provisioner


log = logging.getLogger(__name__)


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



__all__ = [
    "auto_reprovision_on_startup",
]
