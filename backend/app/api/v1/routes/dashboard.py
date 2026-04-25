"""Workspace dashboard summary endpoint (pilot Day 3).

One denormalised endpoint feeds the post-onboarding dashboard so the
console doesn't need to fan out to half a dozen others on every render.
We return:

- counts (active pipelines, activated repos, recent PRs/runs)
- the five default-pipeline cards
- the most-recent-N pull requests (write-through cache from the GitHub
  webhook)
- the most-recent-N workflow runs (same source)
- the most-recent-N pipeline runs (manual + future webhook triggers)

Members can read; admin-only verbs (run, toggle) live on the
``/pipelines`` router.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.notifications import NotificationOut, _to_out as _notif_to_out
from backend.app.api.v1.routes.pipelines import (
    PipelineOut,
    PipelineRunOut,
    _run_to_out,
    enrich_pipelines,
)
from backend.app.core.config import Settings, get_settings
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.notifications import WorkspaceNotification
from backend.app.db.models.pipelines import (
    Pipeline,
    PipelineRun,
    PullRequest,
    WorkflowRun,
)
from backend.app.db.session import get_session


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
_OPS_SUGGESTED_LIMIT = 5
_OPS_STUCK_PR_THRESHOLD = timedelta(hours=24)
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
    enabled_pipelines: int
    open_pull_requests: int
    runs_last_24h: int


class DashboardOut(BaseModel):
    counts: DashboardCounts
    pipelines: list[PipelineOut]
    pull_requests: list[PullRequestOut]
    workflow_runs: list[WorkflowRunOut]
    pipeline_runs: list[PipelineRunOut]
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


class DashboardWorkItemOut(BaseModel):
    name: str
    status: Literal["in_progress", "review", "blocked"]
    repo: str | None = None
    scope: str | None = None
    updated_at: datetime
    blocker_ref: str | None = None
    href: str | None = None


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


def _impact_rank(impact: Impact) -> int:
    return {"high": 0, "medium": 1, "low": 2}[impact]


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


@router.get("/ops", response_model=DashboardOpsOut)
async def get_ops_dashboard(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DashboardOpsOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(days=1)
    cutoff_wip = now - _OPS_WIP_WINDOW
    stuck_cutoff = now - _OPS_STUCK_PR_THRESHOLD
    stale_run_cutoff = now - _OPS_STALE_RUN_THRESHOLD

    failed_pipeline_runs = (
        await session.execute(
            select(PipelineRun, Pipeline)
            .join(Pipeline, PipelineRun.pipeline_id == Pipeline.id)
            .where(
                PipelineRun.workspace_id == workspace_id,
                PipelineRun.status == "failed",
                PipelineRun.created_at >= cutoff_24h,
            )
            .order_by(desc(PipelineRun.finished_at), desc(PipelineRun.created_at))
            .limit(25)
        )
    ).all()
    stale_pipeline_runs = (
        await session.execute(
            select(PipelineRun, Pipeline)
            .join(Pipeline, PipelineRun.pipeline_id == Pipeline.id)
            .where(
                PipelineRun.workspace_id == workspace_id,
                PipelineRun.status.in_(("running", "queued", "in_progress")),
                PipelineRun.created_at < stale_run_cutoff,
            )
            .order_by(PipelineRun.created_at)
            .limit(10)
        )
    ).all()
    failed_workflow_runs = (
        await session.execute(
            select(WorkflowRun)
            .where(
                WorkflowRun.workspace_id == workspace_id,
                WorkflowRun.conclusion.in_(
                    ("failure", "timed_out", "action_required", "cancelled")
                ),
                WorkflowRun.created_at >= cutoff_24h,
            )
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
    recent_pipeline_runs = (
        await session.execute(
            select(PipelineRun)
            .where(
                PipelineRun.workspace_id == workspace_id,
                PipelineRun.created_at >= cutoff_24h,
            )
            .order_by(desc(PipelineRun.created_at))
            .limit(500)
        )
    ).scalars().all()
    shipped_prs = (
        await session.execute(
            select(PullRequest)
            .where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.merged.is_(True),
                PullRequest.merged_at >= cutoff_24h,
            )
            .order_by(desc(PullRequest.merged_at))
            .limit(25)
        )
    ).scalars().all()

    stuck_prs = []
    for pr in open_prs:
        pr_updated_at = _event_time(pr.updated_at_external, pr.updated_at, pr.opened_at)
        if pr_updated_at is not None and pr_updated_at < stuck_cutoff:
            stuck_prs.append(pr)
    open_interventions = [
        item for item in open_inbox_items if item.type in {"failure", "exception", "approval"}
    ]
    broken_interventions = [
        item for item in open_inbox_items if item.type in {"failure", "exception"}
    ]
    recent_interventions = [
        item
        for item in open_inbox_items
        if _as_utc(item.created_at) >= cutoff_24h
        and item.type in {"failure", "exception", "approval", "clarification"}
    ]

    blockers: list[DashboardBlockerOut] = []
    for run, pipeline in failed_pipeline_runs:
        blockers.append(
            DashboardBlockerOut(
                type="pipeline",
                title=f"{pipeline.name} failed",
                scope=pipeline.lane_id,
                age_seconds=_age_seconds(
                    now, _event_time(run.finished_at, run.started_at, run.created_at)
                ),
                impact="high",
                href="/runs",
            )
        )
    for run, pipeline in stale_pipeline_runs:
        blockers.append(
            DashboardBlockerOut(
                type="pipeline",
                title=f"{pipeline.name} is still running",
                scope=pipeline.lane_id,
                age_seconds=_age_seconds(now, _event_time(run.started_at, run.created_at)),
                impact="medium",
                href="/runs",
            )
        )
    for run in failed_workflow_runs:
        blockers.append(
            DashboardBlockerOut(
                type="automation",
                title=f"{run.name} failed",
                repo=run.repo_full_name,
                scope=run.event,
                age_seconds=_age_seconds(
                    now, _event_time(run.finished_at, run.started_at, run.created_at)
                ),
                impact="high",
                href=run.html_url,
            )
        )
    for pr in stuck_prs:
        blockers.append(
            DashboardBlockerOut(
                type="pr",
                title=f"PR #{pr.number}: {pr.title}",
                repo=pr.repo_full_name,
                age_seconds=_age_seconds(
                    now, _event_time(pr.updated_at_external, pr.updated_at, pr.opened_at)
                ),
                impact="medium",
                href=pr.html_url,
            )
        )
    for item in open_interventions:
        impact: Impact = "high" if item.type in {"failure", "exception"} else "medium"
        blockers.append(
            DashboardBlockerOut(
                type="external",
                title=item.title,
                scope=item.type,
                age_seconds=_age_seconds(now, item.created_at),
                impact=impact,
                href=f"/inbox/{item.id}",
            )
        )

    blockers = sorted(
        blockers,
        key=lambda item: (_impact_rank(item.impact), -item.age_seconds),
    )[:_OPS_LIST_LIMIT]

    work_items: list[DashboardWorkItemOut] = []
    for pr in open_prs:
        updated_at = _event_time(pr.updated_at_external, pr.updated_at, pr.opened_at)
        if updated_at is None or updated_at < cutoff_wip:
            continue
        blocker_ref = f"pr:{pr.id}" if pr in stuck_prs else None
        work_items.append(
            DashboardWorkItemOut(
                name=f"PR #{pr.number}: {pr.title}",
                status="in_progress" if pr.draft else "review",
                repo=pr.repo_full_name,
                updated_at=updated_at,
                blocker_ref=blocker_ref,
                href=pr.html_url,
            )
        )
    for item in open_inbox_items:
        created_at = _as_utc(item.created_at)
        if created_at < cutoff_wip:
            continue
        is_blocked = item.type in {"failure", "exception", "approval"}
        work_items.append(
            DashboardWorkItemOut(
                name=item.title,
                status="blocked" if is_blocked else "in_progress",
                scope=item.type,
                updated_at=created_at,
                blocker_ref=f"inbox:{item.id}" if is_blocked else None,
                href=f"/inbox/{item.id}",
            )
        )
    work_items = sorted(work_items, key=lambda item: item.updated_at, reverse=True)[
        :_OPS_LIST_LIMIT
    ]

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

    bottlenecks: list[DashboardBottleneckOut] = []
    if stuck_prs:
        bottlenecks.append(
            DashboardBottleneckOut(
                metric="Stuck PRs",
                current_value=str(len(stuck_prs)),
                severity="high" if len(stuck_prs) >= 3 else "medium",
            )
        )
    failure_denominator = len(terminal_runs) + len(failed_workflow_runs)
    failure_rate = _pct(failed_runs_count + failed_workflows_count, failure_denominator)
    if failure_rate is not None and failure_denominator >= 3 and failure_rate >= 0.2:
        bottlenecks.append(
            DashboardBottleneckOut(
                metric="Automation failure rate",
                current_value=f"{round(failure_rate * 100)}%",
                severity="high" if failure_rate >= 0.5 else "medium",
            )
        )
    if recent_interventions:
        bottlenecks.append(
            DashboardBottleneckOut(
                metric="Manual interventions",
                current_value=str(len(recent_interventions)),
                severity="medium",
            )
        )
    bottlenecks = bottlenecks[:5]

    if failed_runs_count > 0 or broken_automations_count > 0:
        overall_status: OpsStatus = "critical"
    elif stuck_prs or stale_pipeline_runs or bottlenecks:
        overall_status = "degraded"
    else:
        overall_status = "ok"

    suggested_actions = [
        DashboardSuggestedActionOut(
            action=_suggest_action(blocker),
            reason=blocker.title,
            priority=blocker.impact,
            href=blocker.href,
        )
        for blocker in blockers[:_OPS_SUGGESTED_LIMIT]
    ]

    return DashboardOpsOut(
        system_status=DashboardSystemStatusOut(
            overall_status=overall_status,
            failing_pipelines_count=failed_runs_count,
            stuck_prs_count=len(stuck_prs),
            broken_automations_count=broken_automations_count,
            last_deploy=None,
        ),
        blockers=blockers,
        work_in_progress=work_items,
        shipped=DashboardShippedOut(
            features_shipped_count=features_count,
            fixes_count=fixes_count,
            rollbacks_count=rollbacks_count,
            items=shipped_items,
        ),
        bottlenecks=bottlenecks,
        automation_health=DashboardAutomationHealthOut(
            automation_coverage=None,
            success_rate=success_rate,
            manual_interventions_count=len(recent_interventions),
            failures_count=failed_runs_count + failed_workflows_count,
        ),
        suggested_actions=suggested_actions,
    )


def _suggest_action(blocker: DashboardBlockerOut) -> str:
    if blocker.type == "pipeline":
        return f"Inspect {blocker.title}"
    if blocker.type == "pr":
        return f"Review {blocker.title.split(':', 1)[0]}"
    if blocker.type == "automation":
        return f"Fix {blocker.title}"
    return f"Resolve {blocker.title}"


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
    enabled_count = (
        await session.execute(
            select(func.count(Pipeline.id)).where(
                Pipeline.workspace_id == workspace_id, Pipeline.enabled.is_(True)
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
    cutoff_24h = datetime.now(timezone.utc) - timedelta(days=1)
    runs_24h = (
        await session.execute(
            select(func.count(PipelineRun.id)).where(
                PipelineRun.workspace_id == workspace_id,
                PipelineRun.created_at >= cutoff_24h,
            )
        )
    ).scalar_one()

    pipelines = (
        await session.execute(
            select(Pipeline)
            .where(Pipeline.workspace_id == workspace_id)
            .order_by(Pipeline.created_at)
        )
    ).scalars().all()

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

    pipeline_runs = (
        await session.execute(
            select(PipelineRun)
            .where(PipelineRun.workspace_id == workspace_id)
            .order_by(desc(PipelineRun.started_at), desc(PipelineRun.created_at))
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
            enabled_pipelines=int(enabled_count or 0),
            open_pull_requests=int(open_pr_count or 0),
            runs_last_24h=int(runs_24h or 0),
        ),
        pipelines=await enrich_pipelines(session, list(pipelines), settings=settings),
        pull_requests=[_pr_to_out(p) for p in pulls],
        workflow_runs=[_wfrun_to_out(r) for r in runs],
        pipeline_runs=[_run_to_out(r) for r in pipeline_runs],
        notifications=[_notif_to_out(n) for n in notifications],
    )


__all__ = ["router"]
