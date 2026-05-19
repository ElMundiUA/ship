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
from typing import Final

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


STALE_DISPATCH_WINDOW = timedelta(minutes=60)
# Was 20min — too short for project-lock-bound chains, where the
# original dispatch can hold the lock up to 60min waiting for /finish
# or sweep. Re-firing at 20min meant fsm_self_heal kept queuing fresh
# dispatches the picker had to refuse with ``dispatch.project_busy``,
# while runner-side crashes (no /finish, ever) racked up endlessly on
# the same ticket. Matching the 60min PROJECT_LOCK_TTL_S window in
# dispatcher.py makes the backstop genuinely "fire when prior attempt
# is dead", not "fire on top of an in-flight attempt".

# Threshold for "this runner is dying without finishing" — if a ticket
# has this many ``agent_run.dispatch`` rows in the lookback window with
# zero matching ``agent_run.finish`` rows after each, fsm_self_heal
# skips it and files an inbox blocker rather than racking up the
# (N+1)-th dead dispatch. Operator clears manually after looking at
# the GH Actions logs.
RUNNER_FAIL_THRESHOLD: Final[int] = 3
RUNNER_FAIL_WINDOW = timedelta(hours=4)
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

                # Runner-fail loop detection. Look back RUNNER_FAIL_WINDOW
                # and count dispatch rows without any subsequent finish.
                # If >= RUNNER_FAIL_THRESHOLD, the GH Actions workflow is
                # dying silently (network error, missing secret, OOM, etc.)
                # and re-firing produces yet another dead run. File a
                # blocker letter once and stop re-firing this ticket
                # until an operator clears it (resolve the letter).
                #
                # askslayer/PAC-32 + PAC-13 2026-05-19: 6 fsm_self_heal
                # dispatches in 5h, zero finishes, two project_locks
                # held continuously, 14 sibling PAC-* tickets gated on
                # ``dispatch.project_busy`` every 15min tick. Same
                # pattern caught earlier on askslayer/PAC-23 — root
                # cause is always runner-side (workflow file, secret,
                # cursor account), never something self-heal can fix
                # by re-dispatching.
                if await _looks_like_runner_fail_loop(
                    session, install.workspace_id, ref
                ):
                    await _file_runner_fail_blocker(
                        session, install.workspace_id, ref, stage
                    )
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


async def _looks_like_runner_fail_loop(
    session: AsyncSession,
    workspace_id,
    ticket_ref: str,
) -> bool:
    """Detect dispatched-without-finish loops on this ticket.

    Returns True when at least ``RUNNER_FAIL_THRESHOLD`` ``agent_run.dispatch``
    rows have landed for this ticket inside ``RUNNER_FAIL_WINDOW`` and the
    number of subsequent ``agent_run.finish`` rows is fewer than the
    dispatch count — i.e. at least one runner crashed without ``/finish``
    each time. Idempotent: returns True every tick while the operator
    has the inbox letter open, so we keep skipping.

    The signal we want: ``dispatches > finishes`` in the window, with
    ``dispatches >= 3``. We don't try to pair specific dispatches with
    specific finishes; the count delta is enough.

    **Refire-cap exclusion (2026-05-19, post first deploy).** False
    positive caught on Ship-on-Ship/ELS-117..146/155: the workflow
    *did* fire, shipctl *did* call ``_next_task``, but the picker
    returned null because the ticket's refire cap was already
    exhausted (``agent_run.refire_capped`` had fired earlier). The
    runner exited before ``/finish`` not because it crashed, but
    because there was no task to do. The pre-existing
    ``refire_capped`` blocker letter already carries the same three
    action_items; a duplicate ``runner_fail_loop`` letter from us is
    inbox noise. If we see ``agent_run.refire_capped`` for this
    ticket in the same window, that path owns the operator
    surfacing — bail.
    """
    cutoff = datetime.now(timezone.utc) - RUNNER_FAIL_WINDOW
    # Picker-null exclusion. The dispatcher's ``_next_task`` walks
    # several gates before handing the ticket to the runner; when one
    # of them rejects, it releases the lock and emits a
    # ``dispatch.project_lock_released`` audit row with
    # ``payload.via`` set to ``picker_<reason>``:
    #
    #   - ``picker_refire_capped`` — refire cap hit
    #   - ``picker_overlay_frozen`` — Linear project is still in the
    #     ``planning`` state (Drafts)
    #   - ``picker_priority_skipped`` — workspace priority filter
    #     excluded the ticket
    #
    # In all of these cases the runner DID start, called
    # ``_next_task``, got told "no ticket for you", and exited cleanly
    # without ``/finish``. C2's counter sees N dispatches + 0 finishes
    # and would file a duplicate ``runner_fail_loop`` letter — but the
    # refire-cap path / project-priority path / overlay path already
    # owns operator-side surfacing for those tickets. Bail when ANY
    # picker-null release happened for this ticket inside the C2
    # window.
    picker_null = (
        await session.execute(
            select(AuditLog.id)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "dispatch.project_lock_released",
                AuditLog.target_id == ticket_ref,
                AuditLog.created_at >= cutoff,
                AuditLog.payload["via"].astext.like("picker_%"),
            )
            .limit(1)
        )
    ).first()
    if picker_null is not None:
        return False
    dispatch_count = (
        await session.execute(
            select(AuditLog.id)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.dispatch",
                AuditLog.target_id == ticket_ref,
                AuditLog.created_at >= cutoff,
            )
        )
    ).all()
    if len(dispatch_count) < RUNNER_FAIL_THRESHOLD:
        return False
    finish_count = (
        await session.execute(
            select(AuditLog.id)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.finish",
                AuditLog.created_at >= cutoff,
                (AuditLog.target_id == ticket_ref)
                | (
                    AuditLog.payload["ticket_ref"].astext == ticket_ref
                ),
            )
        )
    ).all()
    return len(dispatch_count) - len(finish_count) >= RUNNER_FAIL_THRESHOLD


async def _file_runner_fail_blocker(
    session: AsyncSession,
    workspace_id,
    ticket_ref: str,
    stage: str,
) -> None:
    """File one ``runner_fail_loop`` blocker letter (dedup by intake_handle).

    Stops fsm_self_heal from racking up additional dead dispatches while
    the operator looks at the GH Actions logs. Letter carries action_items
    so the operator can pick "Investigated — resume" / "Pause project" /
    "Already handled" in one click.
    """
    from backend.app.db.models.inbox import InboxItem

    handle = f"runner-fail:{ticket_ref}"
    existing = (
        await session.execute(
            select(InboxItem.id).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.intake_handle == handle,
                InboxItem.status == "new",
            )
        )
    ).first()
    if existing is not None:
        # Letter already open; don't spam re-files on every tick.
        return
    session.add(
        InboxItem(
            workspace_id=workspace_id,
            repo_id=None,
            type="blocker",
            title=(
                f"{ticket_ref}: {stage} runner crashing without /finish "
                f"(≥{RUNNER_FAIL_THRESHOLD} dead dispatches in "
                f"{int(RUNNER_FAIL_WINDOW.total_seconds() // 3600)}h)"
            )[:300],
            summary=(
                f"GitHub Actions runs for {ticket_ref} keep starting "
                f"(self-heal re-fires every {int(STALE_DISPATCH_WINDOW.total_seconds() // 60)}min) "
                f"but never call /finish — the chain holds the project "
                f"lock for 60min, sweeper clears, fsm_self_heal re-fires, "
                f"loop. Check the latest ship-agent-run.yml run on the "
                f"repo for the actual error (missing secret, OOM, network)."
            )[:2000],
            payload={
                "ticket_ref": ticket_ref,
                "fsm_stage": stage,
                "threshold": RUNNER_FAIL_THRESHOLD,
                "window_hours": int(
                    RUNNER_FAIL_WINDOW.total_seconds() // 3600
                ),
                "resolution_mode": "single_choice",
                "action_items": [
                    {
                        "id": "investigated_resume",
                        "kind": "choice",
                        "label": "Investigated — resume dispatching",
                        "hint": (
                            "Operator fixed the root cause (secret, "
                            "branch, etc.); fsm_self_heal will pick "
                            "this up on the next tick."
                        ),
                    },
                    {
                        "id": "pause_project",
                        "kind": "choice",
                        "label": "Pause this project's dispatching",
                        "hint": (
                            "Stops auto-dispatch on every ticket in "
                            "the same Linear project until manually "
                            "resumed."
                        ),
                    },
                    {
                        "id": "already_handled",
                        "kind": "choice",
                        "label": "Already handled",
                        "hint": (
                            "Operator merged / cancelled / re-assigned "
                            "the ticket manually."
                        ),
                    },
                ],
            },
            status="new",
            intake_handle=handle,
            intake_reason="runner_fail_loop",
        )
    )


__all__ = [
    "auto_reprovision_on_startup",
    "scan_eligible_tickets",
    "SCAN_STAGES",
    "STALE_DISPATCH_WINDOW",
]
