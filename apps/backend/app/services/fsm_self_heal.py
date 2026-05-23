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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
    WorkspaceRepo,
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

# Workspace-wide runner-fail rollup (2026-05-21). A single root cause —
# GitHub Actions billing/spending-limit block, org Actions disabled, a
# revoked workspace-wide secret — kills *every* run at preflight, so the
# per-ticket detector fires a separate ``runner_fail_loop`` letter for
# each stuck ticket. Caught on askslayer/Visitor: one billing block →
# PAC-33/34/35/36 each got their own letter (inbox spam for one
# problem). When the workspace shows ``≥ROLLUP_THRESHOLD`` distinct
# ticket dispatches in the window AND **zero** finishes (nothing got
# past preflight), we file ONE workspace-level letter and skip the
# per-ticket scan entirely — re-dispatching into a dead workspace just
# burns more failed runs. Scheduled-routine dispatches (self-heal /
# daily-digest / weekly-audit) are excluded from the distinct-ticket
# count so a quiet-but-healthy workspace never trips this.
WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD: Final[int] = 3
_SCHEDULED_ROUTINE_TARGETS: Final[frozenset[str]] = frozenset(
    {"self-heal", "daily-digest", "weekly-audit"}
)

# ``dev_not_converging`` thresholds (post-mortem of Ship-on-Ship/
# ELS-117/ELS-7/ELS-111, 2026-05-19). When a reviewer-style gate
# (``code_review`` / ``validation`` / ``auto_merge``) blocks the same
# ticket repeatedly AND the developer agent does re-run between each
# block but the PR head SHA never changes (reviewer rejects the same
# logic flaw each pass), the chain is in a "dev keeps cycling but not
# pushing the fix" loop. Refire-cap catches the symptom (3 blocks
# in 24h) but the diagnosis it ships ("breadcrumb label not added")
# is wrong for this class — the breadcrumb IS being added, the
# developer just isn't moving the diff.
#
# Detection shape: in the same 4h window the runner-fail detector
# uses, look for ≥3 blocked finishes at reviewer-style stages AND
# ≥2 ready_next_step finishes at ``dev_implementation``. The dev
# successes prove the runner is alive (not a runner-fail loop) and
# that dev is iterating; the reviewer blocks prove the fix isn't
# landing. File a dedicated letter with action_items that point the
# operator at the actual PR instead of a generic refire-cap blurb.
DEV_NOT_CONVERGING_REVIEW_BLOCKS: Final[int] = 3
DEV_NOT_CONVERGING_DEV_CYCLES: Final[int] = 2
DEV_NOT_CONVERGING_REVIEW_STAGES: Final[frozenset[str]] = frozenset(
    {"code_review", "validation", "auto_merge", "pr_review",
     "qa_manual", "qa_automation"}
)
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

    # Workspace-wide runner-fail rollup. Check ONCE before scanning
    # tickets: if the whole workspace is dead at preflight (billing /
    # org Actions / revoked secret), file one workspace letter and bail
    # — re-dispatching into a dead workspace burns failed runs, and the
    # per-ticket scan would otherwise file a runner_fail_loop letter for
    # every stuck ticket (the inbox spam we're collapsing).
    if await _looks_like_workspace_runner_fail(session, install.workspace_id):
        ws_cutoff = datetime.now(timezone.utc) - RUNNER_FAIL_WINDOW
        dispatched = (
            await session.execute(
                select(AuditLog.target_id).where(
                    AuditLog.workspace_id == install.workspace_id,
                    AuditLog.action == "agent_run.dispatch",
                    AuditLog.created_at >= ws_cutoff,
                    AuditLog.target_id.isnot(None),
                ).distinct()
            )
        ).scalars().all()
        count = len({
            t for t in dispatched
            if t and t not in _SCHEDULED_ROUTINE_TARGETS
        })
        await _file_workspace_runner_fail_blocker(
            session, install.workspace_id, count
        )
        log.info(
            "fsm_self_heal: workspace-wide runner-fail ws=%s tickets=%d "
            "— filed rollup letter, skipping per-ticket scan",
            install.workspace_id, count,
        )
        return 0

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

                # Runner-fail cooldown — short-circuit BEFORE the
                # "recent dispatch" check. Pre-fix the scan had the
                # order reversed: any dispatch in the last 60min
                # caused us to skip even the detection, so a ticket
                # whose runner crashes every cron tick would never
                # trip the loop check (the crash counts as a "recent
                # dispatch" itself).
                #
                # Operator-facing letter exists in the last 24h →
                # operator already decided how to handle this. The
                # cron's job is to stay out of the way, not race the
                # operator. Also release any project_lock the
                # crashing ticket is currently holding so siblings
                # can dispatch into the freed slot.
                #
                # askslayer/Visitor 2026-05-19: PAC-32 + PAC-13
                # crash every cron tick, held two project_locks
                # continuously, blocked 12 sibling PAC-* tickets
                # entirely. C2 had filed letters but operator's
                # resolve flipped them to status=resolved; the scan
                # had no signal to stop re-firing. This cooldown
                # honours both open and recently-resolved letters.
                fail_letter_cutoff = (
                    datetime.now(timezone.utc) - timedelta(hours=24)
                )
                fail_letter = (
                    await session.execute(
                        select(AuditLog.id)
                        .where(
                            AuditLog.workspace_id == install.workspace_id,
                            AuditLog.action == "agent_run.dispatch",
                            AuditLog.target_id == ref,
                            AuditLog.created_at >= fail_letter_cutoff,
                        )
                        .limit(1)
                    )
                ).first()
                from backend.app.db.models.inbox import InboxItem as _Inbox
                runner_letter = (
                    await session.execute(
                        select(_Inbox.id, _Inbox.payload)
                        .where(
                            _Inbox.workspace_id == install.workspace_id,
                            _Inbox.intake_handle == f"runner-fail:{ref}",
                            _Inbox.created_at >= fail_letter_cutoff,
                        )
                        .limit(1)
                    )
                ).first()
                if runner_letter is not None:
                    # Free this ticket's project_lock if it's
                    # holding one — siblings should be free to use
                    # the slot. Find project_id from the most-recent
                    # dispatch audit row.
                    from sqlalchemy import text as _text
                    recent_dispatch = (
                        await session.execute(
                            select(AuditLog.payload).where(
                                AuditLog.workspace_id == install.workspace_id,
                                AuditLog.action == "agent_run.dispatch",
                                AuditLog.target_id == ref,
                            ).order_by(AuditLog.created_at.desc()).limit(1)
                        )
                    ).first()
                    if recent_dispatch is not None:
                        proj = (recent_dispatch[0] or {}).get("project_id")
                        if proj:
                            await session.execute(
                                _text(
                                    "DELETE FROM agent_dispatch_locks "
                                    "WHERE workspace_id=:ws AND key=:k"
                                ),
                                {
                                    "ws": install.workspace_id,
                                    "k": f"project:{proj}",
                                },
                            )
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
                # needs_clarification guard — if the ticket's most
                # recent finish was ``outcome=needs_clarification``,
                # the agent (reviewer / auto-merger) explicitly asked
                # the operator a question and the chain is correctly
                # parked. That's neither a runner crash nor a dev-not-
                # converging loop; the clarification letter is the
                # authoritative signal. Both detectors would otherwise
                # mis-fire on the dispatch-without-ready_next_step
                # shape. Caught on Ship-on-Ship/ELS-118/ELS-154/ELS-111
                # 2026-05-20: auto_merger stalled needs_clarification
                # (migration / scope / conflict), C2 piled a
                # runner_fail_loop + dev_not_converging letter on top
                # of the legit clarification.
                last_finish_outcome = (
                    await session.execute(
                        select(AuditLog.payload["outcome"].astext)
                        .where(
                            AuditLog.workspace_id == install.workspace_id,
                            AuditLog.action == "agent_run.finish",
                            (
                                (AuditLog.target_id == ref)
                                | (AuditLog.payload["ticket_ref"].astext == ref)
                            ),
                        )
                        .order_by(AuditLog.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if last_finish_outcome == "needs_clarification":
                    continue

                if await _looks_like_runner_fail_loop(
                    session, install.workspace_id, ref
                ):
                    await _file_runner_fail_blocker(
                        session, install.workspace_id, ref, stage
                    )
                    continue

                # Dev-not-converging detection. Distinct from runner-
                # fail: the runner IS calling /finish, the chain IS
                # cycling reviewer→dev→reviewer, but the developer
                # agent isn't pushing a fix that lands the reviewer's
                # blocker. Caught on Ship-on-Ship/ELS-117/ELS-7/ELS-
                # 111 2026-05-19 — 5 reviewer passes each on the same
                # commit SHA, refire-cap firing but with a misleading
                # "breadcrumb label not added" generic diagnosis. The
                # dedicated letter points the operator at the PR with
                # explicit "apply reviewer's fix manually / close +
                # comment recipe / override" action_items.
                if await _looks_like_dev_not_converging(
                    session, install.workspace_id, ref
                ):
                    await _file_dev_not_converging_blocker(
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


async def _looks_like_workspace_runner_fail(
    session: AsyncSession,
    workspace_id,
) -> bool:
    """Detect a TRUE workspace-wide runner death (billing block, org
    Actions disabled, revoked secret affecting every repo) vs one dead
    repo beside healthy/idle ones.

    Per-repo signal: a repo is "dead" when, in ``RUNNER_FAIL_WINDOW``, it
    dispatched ≥ ``WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD`` *distinct*
    tickets (scheduled routines excluded) and **none** of those tickets
    produced an ``agent_run.finish``. The workspace is only treated as a
    full kill — and the per-ticket scan skipped — when EVERY activated
    repo is dead.

    Why per-repo: the old workspace-wide test paused self-heal for the
    *whole* workspace the moment any one repo racked up failing
    dispatches. askslayer/visitor-web + visitor-mob were missing
    ``CURSOR_API_KEY`` (every run exited 7 before finish), which tripped
    the rollup and froze visitor-back's healthy code_review queue for a
    day (2026-05-24). A single dead repo must stay repo-local — its
    tickets get the per-ticket ``runner_fail_loop`` path — while the
    healthy/idle repos keep self-healing."""
    cutoff = datetime.now(timezone.utc) - RUNNER_FAIL_WINDOW
    disp_rows = (
        await session.execute(
            select(
                AuditLog.payload["repo"].astext.label("repo"),
                AuditLog.target_id,
            ).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.dispatch",
                AuditLog.created_at >= cutoff,
                AuditLog.target_id.isnot(None),
            )
        )
    ).all()
    tickets_by_repo: dict[str, set[str]] = {}
    for repo, target in disp_rows:
        if not repo or not target or target in _SCHEDULED_ROUTINE_TARGETS:
            continue
        tickets_by_repo.setdefault(repo, set()).add(target)
    if not tickets_by_repo:
        return False
    finished_tickets = {
        r[0]
        for r in (
            await session.execute(
                select(AuditLog.payload["ticket_ref"].astext).where(
                    AuditLog.workspace_id == workspace_id,
                    AuditLog.action == "agent_run.finish",
                    AuditLog.created_at >= cutoff,
                )
            )
        ).all()
        if r[0]
    }
    dead_repos = {
        repo
        for repo, tix in tickets_by_repo.items()
        if len(tix) >= WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD
        and not (tix & finished_tickets)
    }
    if not dead_repos:
        return False
    activated_repos = (
        await session.execute(
            select(func.count())
            .select_from(WorkspaceRepo)
            .where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.activated_at.isnot(None),
            )
        )
    ).scalar() or 0
    # Full kill only when every activated repo is dead. One dead repo
    # beside a healthy/idle one is repo-local — let the scan run so the
    # healthy repos keep flowing.
    return activated_repos > 0 and len(dead_repos) >= activated_repos


async def _file_workspace_runner_fail_blocker(
    session: AsyncSession,
    workspace_id,
    ticket_count: int,
) -> None:
    """File ONE workspace-level runner-fail letter (dedup by handle).

    Replaces the per-ticket ``runner_fail_loop`` spam when the whole
    workspace is down. The operator fixes one thing (usually billing /
    spending limit) and resumes; we don't re-file while it's open."""
    from backend.app.db.models.inbox import InboxItem

    handle = "runner-fail-workspace"
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
        return
    session.add(
        InboxItem(
            workspace_id=workspace_id,
            repo_id=None,
            type="blocker",
            title=(
                f"Workspace runners down — {ticket_count} tickets stalled "
                f"at preflight"
            )[:300],
            summary=(
                f"Every agent run in this workspace died before reporting "
                f"in the last {int(RUNNER_FAIL_WINDOW.total_seconds() // 3600)}h "
                f"({ticket_count} distinct tickets dispatched, 0 finishes). "
                f"That's a workspace-wide cause, not a per-ticket bug — "
                f"almost always a GitHub Actions billing / spending-limit "
                f"block, org Actions disabled, or a revoked workspace "
                f"secret. Check the org's Billing & plans and Actions "
                f"settings, then resume. fsm_self_heal has paused "
                f"re-dispatch for this workspace so it stops burning "
                f"failed runs."
            )[:2000],
            payload={
                "ticket_count": ticket_count,
                "window_hours": int(
                    RUNNER_FAIL_WINDOW.total_seconds() // 3600
                ),
                "resolution_mode": "single_choice",
                "action_items": [
                    {
                        "id": "fixed_resume",
                        "kind": "choice",
                        "label": "Fixed billing/secret — resume",
                        "hint": (
                            "Mark resolved; self-heal resumes dispatching "
                            "on the next tick."
                        ),
                    },
                    {
                        "id": "pause_workspace",
                        "kind": "choice",
                        "label": "Pause this workspace",
                        "hint": "Stop trying until I sort it out.",
                    },
                    {
                        "id": "already_handled",
                        "kind": "choice",
                        "label": "Already handled",
                    },
                ],
            },
            status="new",
            category="failure",
            priority=10,
            intake_handle=handle,
            intake_reason="runner_fail_workspace",
        )
    )


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


async def _looks_like_dev_not_converging(
    session: AsyncSession,
    workspace_id,
    ticket_ref: str,
) -> bool:
    """Detect "reviewer keeps blocking + dev keeps cycling but doesn't
    converge" loop.

    Three conditions must all hold in :data:`RUNNER_FAIL_WINDOW`:

    1. ≥ :data:`DEV_NOT_CONVERGING_REVIEW_BLOCKS` ``agent_run.finish``
       rows with ``outcome=blocked`` at a reviewer-style stage
       (:data:`DEV_NOT_CONVERGING_REVIEW_STAGES`) — reviewer keeps
       rejecting the work.
    2. ≥ :data:`DEV_NOT_CONVERGING_DEV_CYCLES` ``agent_run.finish``
       rows with ``outcome=ready_next_step`` at ``dev_implementation``
       — developer agent is alive and producing finishes (rules out
       runner-fail; that detector owns zero-finish cases).
    3. The picker-null exclusion (refire-cap / overlay / priority)
       inherited via the caller-side checks isn't tripped — that's
       handled before we reach the dev-not-converging check.

    Returns True when all three hold. The caller files
    :func:`_file_dev_not_converging_blocker` and skips re-dispatching
    the ticket until an operator clears it.
    """
    cutoff = datetime.now(timezone.utc) - RUNNER_FAIL_WINDOW
    # Picker-null exclusion — mirror the runner-fail detector. If the
    # picker is bailing the ticket cleanly (refire-cap / overlay-frozen
    # / priority-skipped), the operator already has a refire-cap letter
    # with the same intent. Don't double-file. Same window as the rest
    # of the check.
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
    # Cross-stage reviewer block count. Same JSONB-cast filter shape
    # the refire-cap counter uses.
    review_blocks = (
        await session.execute(
            select(AuditLog.id)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.finish",
                AuditLog.created_at >= cutoff,
                AuditLog.payload["outcome"].astext == "blocked",
                AuditLog.payload["fsm_stage"].astext.in_(
                    DEV_NOT_CONVERGING_REVIEW_STAGES
                ),
                (AuditLog.target_id == ticket_ref)
                | (AuditLog.payload["ticket_ref"].astext == ticket_ref),
            )
        )
    ).all()
    if len(review_blocks) < DEV_NOT_CONVERGING_REVIEW_BLOCKS:
        return False
    dev_successes = (
        await session.execute(
            select(AuditLog.id)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.finish",
                AuditLog.created_at >= cutoff,
                AuditLog.payload["outcome"].astext == "ready_next_step",
                AuditLog.payload["fsm_stage"].astext == "dev_implementation",
                (AuditLog.target_id == ticket_ref)
                | (AuditLog.payload["ticket_ref"].astext == ticket_ref),
            )
        )
    ).all()
    return len(dev_successes) >= DEV_NOT_CONVERGING_DEV_CYCLES


async def _file_dev_not_converging_blocker(
    session: AsyncSession,
    workspace_id,
    ticket_ref: str,
    stage: str,
) -> None:
    """File one ``dev_not_converging`` blocker letter (dedup by
    intake_handle).

    Operator-facing diagnosis: the developer agent is alive and the
    chain is cycling, but the reviewer's blocker isn't landing in the
    PR diff. Letter body embeds the reviewer's last anchor comment +
    PR url inline so the operator doesn't have to leave the inbox to
    make a decision. Action items use server-side **executors** so
    the operator's pick translates into Ship actually doing the
    thing (merge / cancel / re-dispatch with hint / snooze) instead
    of just leaving a Linear comment.
    """
    from backend.app.db.models.inbox import InboxItem
    from backend.app.db.models.pipelines import PullRequest

    handle = f"dev-stuck:{ticket_ref}"
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
        return

    # PR state guard — if the associated PR is closed or merged the
    # loop is over (chain has nowhere to go). The 4h-window finishes
    # are stale signals from before the PR closed. Caught on Ship-
    # on-Ship/ELS-111 2026-05-20: PR #275 closed at 12:50 UTC, dev_
    # not_converging fired at 21:00 UTC reading the pre-close
    # finishes. Skip filing in that case.
    pr_url: str | None = None
    pr_state: str = "unknown"
    pr_row = (
        await session.execute(
            select(PullRequest).where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.title.ilike(f"%{ticket_ref}%"),
            ).order_by(PullRequest.updated_at_external.desc()).limit(1)
        )
    ).scalars().first()
    if pr_row is not None:
        pr_url = pr_row.html_url
        pr_state = pr_row.state
        if pr_state in ("closed", "merged"):
            return  # detection was a false-positive; PR already done

    # Try to embed the reviewer's last CHANGES_REQUESTED anchor
    # comment so the operator can decide without leaving the inbox.
    # Best-effort — a fetch failure just leaves the body without it.
    reviewer_excerpt = await _fetch_reviewer_last_comment(
        session, workspace_id, ticket_ref, pr_row,
    )

    body_parts = [
        f"**Pattern:** Developer agent ran successfully "
        f"≥{DEV_NOT_CONVERGING_DEV_CYCLES}× between "
        f"≥{DEV_NOT_CONVERGING_REVIEW_BLOCKS} blocks at "
        f"`{stage}` in the last "
        f"{int(RUNNER_FAIL_WINDOW.total_seconds() // 3600)}h. The "
        f"reviewer's blocker isn't landing in the PR — dev is "
        f"iterating without converging."
    ]
    if pr_url:
        body_parts.append(f"\n**PR:** {pr_url}  _(state: {pr_state})_")
    if reviewer_excerpt:
        body_parts.append(
            "\n**Reviewer's last comment:**\n\n" + reviewer_excerpt
        )

    summary = "\n\n".join(body_parts)[:2000]

    session.add(
        InboxItem(
            workspace_id=workspace_id,
            repo_id=None,
            type="blocker",
            title=(
                f"{ticket_ref}: dev not converging at {stage}"
            )[:300],
            summary=summary,
            payload={
                "ticket_ref": ticket_ref,
                "fsm_stage": stage,
                "pr_url": pr_url,
                "body": summary,
                "resolution_mode": "single_choice",
                "action_items": [
                    {
                        "id": "redispatch_dev_with_hint",
                        "kind": "choice",
                        "label": "Re-dispatch dev with my hint below",
                        "hint": (
                            "Type a hint in the freeform field, then "
                            "pick this. Ship posts your hint as an "
                            "operator note on Linear and re-fires "
                            "dev_implementation on the next cron tick."
                        ),
                        "executor": "redispatch_dev",
                    },
                    {
                        "id": "force_merge",
                        "kind": "choice",
                        "label": "Force-merge — override the reviewer",
                        "hint": (
                            "Ship squash-merges the PR via the App "
                            "token and moves the Linear ticket to "
                            "Done. Optional freeform note is recorded "
                            "as the override rationale on the PR."
                        ),
                        "executor": "force_merge",
                    },
                    {
                        "id": "cancel_ticket",
                        "kind": "choice",
                        "label": "Cancel ticket — wrong direction",
                        "hint": (
                            "Linear ticket → Canceled, PR closed with "
                            "your freeform note (if any). Use when "
                            "the work shouldn't continue."
                        ),
                        "executor": "cancel_ticket",
                    },
                    {
                        "id": "snooze_24h",
                        "kind": "choice",
                        "label": "Snooze 24h — investigating",
                        "hint": (
                            "Pause fsm_self_heal on this ticket for "
                            "24h while you investigate. Re-surfaces "
                            "automatically if the loop is still live."
                        ),
                        "executor": "snooze_24h",
                    },
                ],
            },
            status="new",
            intake_handle=handle,
            intake_reason="dev_not_converging",
        )
    )


async def _fetch_reviewer_last_comment(
    session: AsyncSession,
    workspace_id,
    ticket_ref: str,
    pr_row,
) -> str | None:
    """Best-effort: pull the last ``CHANGES_REQUESTED`` review body
    from GitHub for ``pr_row``. Returns ``None`` when there's no PR,
    no install, no reviews, or the API call fails — the letter
    still lands without the embedded excerpt.
    """
    if pr_row is None or not pr_row.repo_full_name:
        return None
    if "/" not in pr_row.repo_full_name:
        return None
    from backend.app.db.models.integrations import GitHubInstallation
    install = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.workspace_id == workspace_id,
                GitHubInstallation.suspended_at.is_(None),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if install is None:
        return None
    owner, repo = pr_row.repo_full_name.split("/", 1)
    try:
        from backend.app.integrations.github.app_auth import (
            get_installation_token,
        )
        import httpx
        from backend.app.core.config import get_settings
        settings = get_settings()
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            token = await get_installation_token(
                install.installation_id, settings=settings, client=client,
            )
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/"
                f"{pr_row.number}/reviews",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if resp.status_code >= 400:
                return None
            reviews = resp.json() or []
            cr = [
                r for r in reviews
                if r.get("state") == "CHANGES_REQUESTED" and (r.get("body") or "").strip()
            ]
            if not cr:
                return None
            body = (cr[-1].get("body") or "").strip()
            # Cap to keep the inbox letter readable; full review is
            # still on GitHub for deep dives.
            return body[:1200] + ("\n\n…(truncated)" if len(body) > 1200 else "")
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info(
            "fetch_reviewer_last_comment: failed ws=%s ticket=%s: %s",
            workspace_id, ticket_ref, exc,
        )
        return None


__all__ = [
    "auto_reprovision_on_startup",
    "scan_eligible_tickets",
    "SCAN_STAGES",
    "STALE_DISPATCH_WINDOW",
]
