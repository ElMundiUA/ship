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

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
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
    )


__all__ = ["router"]
