"""Workspace dashboard summary endpoint.

One denormalised endpoint feeds the post-onboarding dashboard so the
console doesn't need to fan out to half a dozen others on every render.
We return:

- counts (active routines, activated repos, recent PRs/runs)
- the most-recent-N pull requests (write-through cache from the GitHub
  webhook)
- the most-recent-N workflow runs (same source)
- the most-recent-N routine runs (manual + future webhook triggers)

Members can read; admin-only verbs (run a routine) live on the
``/runs`` router.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.notifications import NotificationOut, _to_out as _notif_to_out
from backend.app.core.config import Settings, get_settings
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.lanes import Routine, RoutineRun
from backend.app.db.models.notifications import WorkspaceNotification
from backend.app.db.models.pipelines import (
    PullRequest,
    WorkflowRun,
)
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.services.dashboard_tracker_wip import (
    collect_tracker_wip_candidates,
    effective_tracker_for_repo,
    tracker_label,
    workspace_has_tracker_binding,
)
from backend.app.services.ops_report_window import (
    OpsReportWindow,
    ops_report_cutoff,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/dashboard",
    tags=["dashboard"],
)


_RECENT_LIMIT = 10
# A4 + A5: cap the dashboard's banner rail so a noisy webhook burst
# can't pile up a wall of "PR merged" callouts. The dismiss UX stays
# one-click; if a user hits that limit they clear a few and the next
# refresh reveals the rest.
_NOTIFICATION_LIMIT = 5
_OPS_LIST_LIMIT = 7
_OPS_STUCK_PR_THRESHOLD = timedelta(days=1)
_TICKET_REF_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,10}-\d+)\b")
_INBOX_OPEN = ("new", "snoozed")
_OPS_WIP_WINDOW = timedelta(days=7)
_OPS_STALE_RUN_THRESHOLD = timedelta(hours=2)

Impact = Literal["high", "medium", "low"]
OpsItemType = Literal["pipeline", "pr", "automation", "external"]
OpsStatus = Literal["ok", "degraded", "critical"]


class PullRequestOut(BaseModel):
    id: uuid.UUID
    repo_full_name: str
    number: int
    title: str
    state: str
    merged: bool
    draft: bool
    author: str | None
    html_url: str
    opened_at: datetime | None
    updated_at_external: datetime | None
    closed_at: datetime | None
    merged_at: datetime | None


class WorkflowRunOut(BaseModel):
    id: uuid.UUID
    repo_full_name: str
    name: str
    event: str | None
    status: str
    conclusion: str | None
    head_branch: str | None
    head_sha: str | None
    actor: str | None
    html_url: str | None
    started_at: datetime | None
    finished_at: datetime | None


class DashboardCounts(BaseModel):
    active_repos: int
    enabled_routines: int
    open_pull_requests: int
    runs_last_24h: int


class RecentAgentRunOut(BaseModel):
    """One row in the dashboard's "recent activity" strip.

    Sourced from ``audit_log`` rows where ``action='agent_run.finish'``
    rather than from the ``routine_runs`` table — the latter is the
    upcoming backend-driven scheduler's surface and stays empty until
    customer repos provision routines explicitly. The audit-log path
    is the canonical record of what the picker + Cursor session actually
    did, regardless of whether a Routine row exists.
    """

    id: str
    ticket_ref: str | None
    fsm_stage: str | None
    stage_next: str | None
    outcome: str | None
    tracker_kind: str | None
    actions: list[str]
    created_at: datetime


class DashboardOut(BaseModel):
    counts: DashboardCounts
    pull_requests: list[PullRequestOut]
    workflow_runs: list[WorkflowRunOut]
    recent_agent_runs: list[RecentAgentRunOut]
    # Dismissible banner rail populated by webhook handlers (A4 "PR
    # merged", A5 "self-heal dispatched"). Capped at `_NOTIFICATION_LIMIT`
    # — the full list lives at /notifications.
    notifications: list[NotificationOut]


class DashboardLastDeployOut(BaseModel):
    time: datetime | None = None
    status: str | None = None


class DashboardSystemStatusOut(BaseModel):
    overall_status: OpsStatus
    failing_pipelines_count: int
    stuck_prs_count: int
    broken_automations_count: int
    last_deploy: DashboardLastDeployOut | None = None


class DashboardBlockerOut(BaseModel):
    type: OpsItemType
    title: str
    repo: str | None = None
    scope: str | None = None
    age_seconds: int
    impact: Impact
    href: str | None = None


class DashboardWorkItemPrLinkOut(BaseModel):
    number: int
    href: str


class DashboardWorkItemOut(BaseModel):
    name: str
    status: Literal["in_progress", "review", "blocked"]
    repo: str | None = None
    scope: str | None = None
    updated_at: datetime
    blocker_ref: str | None = None
    href: str | None = None
    ticket_ref: str | None = None
    tracker: str | None = None
    board_column: str | None = None
    active_agent: str | None = None
    pull_request: DashboardWorkItemPrLinkOut | None = None


class DashboardShippedItemOut(BaseModel):
    name: str
    type: Literal["feature", "fix", "rollback"]
    repo: str | None = None
    href: str | None = None


class DashboardShippedOut(BaseModel):
    features_shipped_count: int
    fixes_count: int
    rollbacks_count: int
    items: list[DashboardShippedItemOut]


class DashboardBottleneckOut(BaseModel):
    metric: str
    current_value: str
    delta: str | None = None
    severity: Impact


class DashboardAutomationHealthOut(BaseModel):
    automation_coverage: float | None = None
    success_rate: float | None = None
    manual_interventions_count: int
    failures_count: int


class DashboardSuggestedActionOut(BaseModel):
    action: str
    reason: str
    priority: Impact
    href: str | None = None


class DashboardOpsOut(BaseModel):
    system_status: DashboardSystemStatusOut
    blockers: list[DashboardBlockerOut]
    work_in_progress: list[DashboardWorkItemOut]
    shipped: DashboardShippedOut
    bottlenecks: list[DashboardBottleneckOut]
    automation_health: DashboardAutomationHealthOut
    suggested_actions: list[DashboardSuggestedActionOut]


def _pr_to_out(row: PullRequest) -> PullRequestOut:
    return PullRequestOut(
        id=row.id,
        repo_full_name=row.repo_full_name,
        number=row.number,
        title=row.title,
        state=row.state,
        merged=row.merged,
        draft=row.draft,
        author=row.author,
        html_url=row.html_url,
        opened_at=row.opened_at,
        updated_at_external=row.updated_at_external,
        closed_at=row.closed_at,
        merged_at=row.merged_at,
    )


def _wfrun_to_out(row: WorkflowRun) -> WorkflowRunOut:
    return WorkflowRunOut(
        id=row.id,
        repo_full_name=row.repo_full_name,
        name=row.name,
        event=row.event,
        status=row.status,
        conclusion=row.conclusion,
        head_branch=row.head_branch,
        head_sha=row.head_sha,
        actor=row.actor,
        html_url=row.html_url,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _event_time(*values: datetime | None) -> datetime | None:
    for value in values:
        if value is not None:
            return _as_utc(value)
    return None


def _age_seconds(now: datetime, since: datetime | None) -> int:
    if since is None:
        return 0
    since = _as_utc(since)
    return max(0, int((now - since).total_seconds()))


def _title_type(title: str) -> Literal["feature", "fix", "rollback"]:
    lower = title.lower()
    if "rollback" in lower or "revert" in lower:
        return "rollback"
    if "fix" in lower or "bug" in lower or "hotfix" in lower:
        return "fix"
    return "feature"


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _ticket_ref_from_text(text: str | None) -> str | None:
    if not text:
        return None
    match = _TICKET_REF_RE.search(text)
    return match.group(1) if match else None


def _match_pr_for_ticket(
    ticket_ref: str | None,
    repo_full_name: str | None,
    open_prs: list[PullRequest],
) -> DashboardWorkItemPrLinkOut | None:
    if not ticket_ref:
        return None
    ref = ticket_ref.strip()
    pool = [
        pr
        for pr in open_prs
        if not repo_full_name or pr.repo_full_name == repo_full_name
    ]
    if not pool:
        pool = list(open_prs)
    for pr in pool:
        title = pr.title or ""
        if ref in title:
            return DashboardWorkItemPrLinkOut(number=pr.number, href=pr.html_url)
        if "#" in ref:
            suffix = ref.split("#")[-1].strip()
            if suffix.isdigit() and (f"#{suffix}" in title or f"#{suffix}:" in title):
                return DashboardWorkItemPrLinkOut(number=pr.number, href=pr.html_url)
    return None


async def _mirror_stuck_prs_to_inbox(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    stuck_prs: list[PullRequest],
    now: datetime,
) -> None:
    stuck_ids = {pr.id for pr in stuck_prs}
    reconciled = (
        await session.scalars(
            select(InboxItem).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.source_table == "stuck_pr",
                InboxItem.status.in_(_INBOX_OPEN),
            )
        )
    ).all()
    for item in reconciled:
        if item.source_id is None or item.source_id not in stuck_ids:
            item.status = "dismissed"
            item.resolution = "dismissed"
            item.resolved_at = now

    # Pre-fetch open blocker letters for THIS workspace so we can
    # skip mirroring stuck PRs whose ticket is already surfaced as a
    # higher-signal letter (refire_capped / runner_fail_loop /
    # dev_not_converging / blocked_cascade_exhausted / clarification).
    # Pre-fix the dashboard spammed "Stuck work: PR #N" on every
    # render in addition to the per-failure-class blocker letter the
    # FSM machinery already filed — operator saw the same situation
    # twice and couldn't tell which one to act on. The blocker
    # letter is authoritative; stuck_pr is the lower-signal
    # observation.
    open_blockers = (
        await session.execute(
            select(InboxItem.payload).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.status.in_(_INBOX_OPEN),
                # ``improvement`` covered because fsm_self_heal phase-1
                # files orphan-dispatch findings as improvements, not
                # blockers — same situation as a stuck_pr from the
                # operator's POV.
                InboxItem.type.in_(("blocker", "clarification", "improvement")),
            )
        )
    ).all()
    tickets_already_surfaced: set[str] = set()
    for (payload,) in open_blockers:
        if not isinstance(payload, dict):
            continue
        ticket = payload.get("ticket_ref")
        if isinstance(ticket, str) and ticket:
            tickets_already_surfaced.add(ticket)

    for pr in stuck_prs:
        # Skip mirror when the PR's source ticket already has an open
        # blocker / clarification letter. Operator already has the
        # actionable signal; stuck_pr would be duplicate noise.
        # We extract the ticket ref from the PR title (e.g.
        # ``feat(ELS-156): ...``) since stuck_pr rows don't carry a
        # ticket_ref column.
        ticket_ref = _ticket_ref_from_pr_title(pr.title or "")
        if ticket_ref and ticket_ref in tickets_already_surfaced:
            continue

        exists = await session.scalar(
            select(func.count(InboxItem.id)).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.source_table == "stuck_pr",
                InboxItem.source_id == pr.id,
                InboxItem.status.in_(_INBOX_OPEN),
            )
        )
        if int(exists or 0) > 0:
            continue
        session.add(
            InboxItem(
                workspace_id=workspace_id,
                repo_id=pr.repo_id,
                type="stuck",
                title=f"Stuck work: PR #{pr.number} — no activity 24h+",
                summary=pr.title[:500] if pr.title else None,
                payload={
                    "kind": "stuck_pr",
                    "pr_number": pr.number,
                    "html_url": pr.html_url,
                    "ticket_ref": ticket_ref,
                },
                status="new",
                source_table="stuck_pr",
                source_id=pr.id,
            )
        )


_TICKET_REF_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def _ticket_ref_from_pr_title(title: str) -> str | None:
    """Parse a Linear-style ticket ref (``ELS-156`` / ``PAC-23``) out
    of a PR title like ``feat(ELS-156): file overlap warning``.

    Returns the first match. PR titles from cursor-bot are templated
    so first-match is reliable. Used by the stuck-PR mirror to
    dedup against open blocker letters that carry the same ticket
    ref in their payload.
    """
    if not title:
        return None
    m = _TICKET_REF_RE.search(title)
    return m.group(1) if m else None


@router.get("/ops", response_model=DashboardOpsOut)
async def get_ops_dashboard(
    workspace_id: uuid.UUID,
    window: OpsReportWindow = Query(
        default="24h",
        description="Rolling UTC window for time-bounded ops aggregates.",
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DashboardOpsOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    now = datetime.now(timezone.utc)
    ops_cutoff = ops_report_cutoff(now, window)
    cutoff_wip = now - _OPS_WIP_WINDOW
    stuck_cutoff = now - _OPS_STUCK_PR_THRESHOLD
    stale_run_cutoff = now - _OPS_STALE_RUN_THRESHOLD

    failed_pipeline_conds = [
        RoutineRun.workspace_id == workspace_id,
        RoutineRun.status == "failed",
    ]
    if ops_cutoff is not None:
        failed_pipeline_conds.append(RoutineRun.created_at >= ops_cutoff)
    failed_pipeline_runs = (
        await session.execute(
            select(RoutineRun, Routine)
            .join(Routine, RoutineRun.routine_id == Routine.id)
            .where(*failed_pipeline_conds)
            .order_by(desc(RoutineRun.finished_at), desc(RoutineRun.created_at))
            .limit(25)
        )
    ).all()
    stale_pipeline_runs = (
        await session.execute(
            select(RoutineRun, Routine)
            .join(Routine, RoutineRun.routine_id == Routine.id)
            .where(
                RoutineRun.workspace_id == workspace_id,
                RoutineRun.status.in_(("running", "queued", "in_progress")),
                RoutineRun.created_at < stale_run_cutoff,
            )
            .order_by(RoutineRun.created_at)
            .limit(10)
        )
    ).all()
    # Failed workflows: only Ship-installed starter workflows
    # (``name: Ship · …`` in customer YAML) so tiles match operator
    # expectations — see companion issue on workflow scope.
    failed_workflow_conds = [
        WorkflowRun.workspace_id == workspace_id,
        WorkflowRun.conclusion.in_(
            ("failure", "timed_out", "action_required", "cancelled")
        ),
        WorkflowRun.name.like("Ship · %"),
    ]
    if ops_cutoff is not None:
        failed_workflow_conds.append(WorkflowRun.created_at >= ops_cutoff)
    failed_workflow_runs = (
        await session.execute(
            select(WorkflowRun)
            .where(*failed_workflow_conds)
            .order_by(desc(WorkflowRun.finished_at), desc(WorkflowRun.created_at))
            .limit(25)
        )
    ).scalars().all()
    open_prs = (
        await session.execute(
            select(PullRequest)
            .where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.state == "open",
            )
            .order_by(desc(PullRequest.updated_at_external), desc(PullRequest.updated_at))
            .limit(100)
        )
    ).scalars().all()
    open_inbox_items = (
        await session.execute(
            select(InboxItem)
            .where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.status.in_(("new", "snoozed")),
            )
            .order_by(desc(InboxItem.created_at))
            .limit(100)
        )
    ).scalars().all()
    recent_pipeline_conds = [RoutineRun.workspace_id == workspace_id]
    if ops_cutoff is not None:
        recent_pipeline_conds.append(RoutineRun.created_at >= ops_cutoff)
    recent_pipeline_runs = (
        await session.execute(
            select(RoutineRun)
            .where(*recent_pipeline_conds)
            .order_by(desc(RoutineRun.created_at))
            .limit(500)
        )
    ).scalars().all()
    shipped_conds = [
        PullRequest.workspace_id == workspace_id,
        PullRequest.merged.is_(True),
    ]
    if ops_cutoff is not None:
        shipped_conds.append(PullRequest.merged_at >= ops_cutoff)
    shipped_prs = (
        await session.execute(
            select(PullRequest)
            .where(*shipped_conds)
            .order_by(desc(PullRequest.merged_at))
            .limit(25)
        )
    ).scalars().all()

    stuck_prs = []
    for pr in open_prs:
        pr_updated_at = _event_time(pr.updated_at_external, pr.updated_at, pr.opened_at)
        if pr_updated_at is not None and pr_updated_at < stuck_cutoff:
            stuck_prs.append(pr)

    await _mirror_stuck_prs_to_inbox(session, workspace_id, stuck_prs, now)
    await session.flush()

    broken_interventions = [
        item for item in open_inbox_items if item.type in {"failure", "exception"}
    ]
    recent_interventions = [
        item
        for item in open_inbox_items
        if (
            ops_cutoff is None or _as_utc(item.created_at) >= ops_cutoff
        )
        and item.type in {"failure", "exception", "approval", "clarification"}
    ]

    work_items: list[DashboardWorkItemOut] = []
    if await workspace_has_tracker_binding(session, workspace_id):
        candidates = await collect_tracker_wip_candidates(
            session,
            settings,
            workspace_id,
            auth.user.id,
            cutoff_wip=cutoff_wip,
            listing_cap=15,
        )
        for c in candidates[:_OPS_LIST_LIMIT]:
            pr_link = _match_pr_for_ticket(c.ticket_ref, c.repo_full_name, open_prs)
            href = c.href or (pr_link.href if pr_link else None)
            work_items.append(
                DashboardWorkItemOut(
                    name=c.name,
                    status=c.status,
                    repo=c.repo_full_name,
                    updated_at=c.updated_at,
                    blocker_ref=None,
                    href=href,
                    ticket_ref=c.ticket_ref,
                    tracker=tracker_label(c.tracker_kind),
                    board_column=c.board_column,
                    active_agent=c.active_agent,
                    pull_request=pr_link,
                )
            )
    else:
        for pr in open_prs:
            updated_at = _event_time(
                pr.updated_at_external, pr.updated_at, pr.opened_at
            )
            if updated_at is None or updated_at < cutoff_wip:
                continue
            ticket_ref = _ticket_ref_from_text(pr.title)
            raw_title = (pr.title or "").strip()
            if ticket_ref and raw_title.startswith(ticket_ref):
                remainder = raw_title[len(ticket_ref) :].lstrip(" :-—\t")
                work_title = remainder or raw_title
            else:
                work_title = raw_title or f"PR #{pr.number}"
            display_name = f"{ticket_ref}: {work_title}" if ticket_ref else work_title
            rid = pr.repo_id
            eff_kind, _ = (
                await effective_tracker_for_repo(session, workspace_id, rid)
                if rid is not None
                else (None, {})
            )
            tr_lbl = tracker_label(eff_kind) if eff_kind else None
            work_items.append(
                DashboardWorkItemOut(
                    name=display_name,
                    status="in_progress" if pr.draft else "review",
                    repo=pr.repo_full_name,
                    updated_at=updated_at,
                    blocker_ref=None,
                    href=pr.html_url,
                    ticket_ref=ticket_ref,
                    tracker=tr_lbl,
                    board_column="Draft" if pr.draft else "In review",
                    active_agent=None,
                    pull_request=DashboardWorkItemPrLinkOut(
                        number=pr.number,
                        href=pr.html_url,
                    ),
                )
            )
        work_items = sorted(
            work_items, key=lambda item: item.updated_at, reverse=True
        )[:_OPS_LIST_LIMIT]

    shipped_items = [
        DashboardShippedItemOut(
            name=f"PR #{pr.number}: {pr.title}",
            type=_title_type(pr.title),
            repo=pr.repo_full_name,
            href=pr.html_url,
        )
        for pr in shipped_prs
    ]
    shipped_items = shipped_items[:3]
    features_count = sum(1 for pr in shipped_prs if _title_type(pr.title) == "feature")
    fixes_count = sum(1 for pr in shipped_prs if _title_type(pr.title) == "fix")
    rollbacks_count = sum(1 for pr in shipped_prs if _title_type(pr.title) == "rollback")

    terminal_runs = [
        run for run in recent_pipeline_runs if run.status in {"succeeded", "failed"}
    ]
    succeeded_runs = sum(1 for run in terminal_runs if run.status == "succeeded")
    failed_runs_count = len(failed_pipeline_runs)
    failed_workflows_count = len(failed_workflow_runs)
    broken_automations_count = failed_workflows_count + len(broken_interventions)
    success_rate = _pct(succeeded_runs, len(terminal_runs))

    if failed_runs_count > 0 or broken_automations_count > 0:
        overall_status: OpsStatus = "critical"
    elif stale_pipeline_runs:
        overall_status = "degraded"
    else:
        overall_status = "ok"

    return DashboardOpsOut(
        system_status=DashboardSystemStatusOut(
            overall_status=overall_status,
            failing_pipelines_count=failed_runs_count,
            stuck_prs_count=0,
            broken_automations_count=broken_automations_count,
            last_deploy=None,
        ),
        blockers=[],
        work_in_progress=work_items,
        shipped=DashboardShippedOut(
            features_shipped_count=features_count,
            fixes_count=fixes_count,
            rollbacks_count=rollbacks_count,
            items=shipped_items,
        ),
        bottlenecks=[],
        automation_health=DashboardAutomationHealthOut(
            automation_coverage=None,
            success_rate=success_rate,
            manual_interventions_count=len(recent_interventions),
            failures_count=failed_runs_count + failed_workflows_count,
        ),
        suggested_actions=[],
    )


@router.get("", response_model=DashboardOut)
async def get_dashboard(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DashboardOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    # Counts — small denormalised SELECTs rather than one giant CTE so
    # query plans stay obvious.
    repo_count = (
        await session.execute(
            select(func.count(WorkspaceRepo.id)).where(
                WorkspaceRepo.workspace_id == workspace_id
            )
        )
    ).scalar_one()
    # "Enabled routines" = distinct ``routine_id`` values claimed in the
    # last 7d via ``repo.routine_run_claim`` audit-log entries. The
    # ``routines`` table itself stays empty in prod because the
    # backend-driven scheduler that's supposed to populate it isn't
    # yet the source of truth (post-B1+B2 phase) — but the cron-driven
    # GHA trigger script DOES claim routines from the API each tick,
    # which leaves audit-log evidence of every routine the operator
    # has actively running. That's the real "enabled" set.
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
    enabled_count = (
        await session.execute(
            select(
                func.count(func.distinct(AuditLog.payload["routine_id"].astext))
            ).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "repo.routine_run_claim",
                AuditLog.created_at >= cutoff_7d,
            )
        )
    ).scalar_one()
    open_pr_count = (
        await session.execute(
            select(func.count(PullRequest.id)).where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.state == "open",
            )
        )
    ).scalar_one()
    # 24h window. Computed in Python so the query plan stays portable
    # across the Postgres prod backend and the SQLite-backed unit tests.
    # ``runs_last_24h`` reads ``agent_run.finish`` events (the canonical
    # record of "Cursor session completed") rather than the empty
    # ``routine_runs`` table.
    cutoff_24h = datetime.now(timezone.utc) - timedelta(days=1)
    runs_24h = (
        await session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.finish",
                AuditLog.created_at >= cutoff_24h,
            )
        )
    ).scalar_one()

    pulls = (
        await session.execute(
            select(PullRequest)
            .where(PullRequest.workspace_id == workspace_id)
            .order_by(desc(PullRequest.updated_at_external), desc(PullRequest.updated_at))
            .limit(_RECENT_LIMIT)
        )
    ).scalars().all()

    runs = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.workspace_id == workspace_id)
            .order_by(desc(WorkflowRun.started_at), desc(WorkflowRun.updated_at))
            .limit(_RECENT_LIMIT)
        )
    ).scalars().all()

    # Recent agent activity from audit_log (canonical record of what
    # the picker + Cursor session did). Beats reading ``routine_runs``
    # which stays empty until the backend-driven scheduler is wired
    # end-to-end. Both motion-bearing finishes (ticket advanced) and
    # noop finishes (no eligible ticket) surface here — the FE
    # filters by ``ticket_ref is not null`` for the motion-only view.
    recent_agent_run_rows = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.finish",
            )
            .order_by(desc(AuditLog.created_at))
            .limit(_RECENT_LIMIT)
        )
    ).scalars().all()

    notifications = (
        await session.execute(
            select(WorkspaceNotification)
            .where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.dismissed_at.is_(None),
            )
            .order_by(desc(WorkspaceNotification.created_at))
            .limit(_NOTIFICATION_LIMIT)
        )
    ).scalars().all()

    return DashboardOut(
        counts=DashboardCounts(
            active_repos=int(repo_count or 0),
            enabled_routines=int(enabled_count or 0),
            open_pull_requests=int(open_pr_count or 0),
            runs_last_24h=int(runs_24h or 0),
        ),
        pull_requests=[_pr_to_out(p) for p in pulls],
        workflow_runs=[_wfrun_to_out(r) for r in runs],
        recent_agent_runs=[_audit_to_recent_run(r) for r in recent_agent_run_rows],
        notifications=[_notif_to_out(n) for n in notifications],
    )


def _audit_to_recent_run(row: AuditLog) -> RecentAgentRunOut:
    p = row.payload or {}
    return RecentAgentRunOut(
        id=str(row.id),
        ticket_ref=p.get("ticket_ref"),
        fsm_stage=p.get("fsm_stage"),
        stage_next=p.get("stage_next"),
        outcome=p.get("outcome"),
        tracker_kind=p.get("tracker_kind"),
        actions=list(p.get("actions") or []),
        created_at=row.created_at,
    )


__all__ = ["router"]
