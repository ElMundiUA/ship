"""Dev-only Console surface for the Memory adapters (E19 step 5).

A single ``/local-tracker`` REST surface that the Console renders as
a control panel for the laptop-offline profile. Lets the developer
see every memory ticket / project / repo / PR / CI-run in one place
and bump them around without going through the normal product UI.

Gated on ``SHIP_USE_MEMORY_ADAPTERS=true``. When the flag is off
(production) every route returns 404, same way the e2e sandbox
seed endpoint is invisible on prod workspaces.

Endpoints:

- ``GET  /v1/workspaces/{ws}/local-tracker/dashboard`` — one bundle
  with everything the page renders (tickets, projects, repos with
  PRs, recent CI runs). Cheap — no pagination because the local
  workspace has at most a few dozen rows of anything.
- ``POST /v1/workspaces/{ws}/local-tracker/tickets/{display_id}/transition``
  — move a ticket. Body: ``{"to_state": "ba_requirements"}``.
- ``POST /v1/workspaces/{ws}/local-tracker/tickets/{display_id}/comment``
  — append a comment.
- ``POST /v1/workspaces/{ws}/local-tracker/prs/{number}/merge``
  — flip a PR to merged (also fires a new CI run on the base
  branch, so the dashboard re-paints with a fresh queued run).
- ``POST /v1/workspaces/{ws}/local-tracker/runs/{run_id}/rerun``
  — re-queue a completed run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.memory_adapters import (
    MemoryCiRun,
    MemoryGitPullRequest,
    MemoryGitRepo,
    MemoryTrackerProject,
    MemoryTrackerTicket,
)
from backend.app.db.session import get_session
from backend.app.integrations.gateway.code_host import RepoRef
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.local.ci import MemoryCi
from backend.app.integrations.local.code_host import MemoryCodeHost
from backend.app.integrations.local.tracker import MemoryTracker


router = APIRouter(prefix="/workspaces/{workspace_id}/local-tracker")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TicketOut(BaseModel):
    display_id: str
    title: str
    body: str
    state: str
    labels: list[str]
    stage: str | None
    project_id: uuid.UUID | None
    ticket_type: str | None
    created_at: datetime
    updated_at: datetime
    url: str


class TicketDetailOut(TicketOut):
    comments: list[dict[str, Any]]


class ProjectOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    state: str
    description: str | None
    body: str
    ticket_count: int
    url: str


class PullRequestOut(BaseModel):
    number: int
    title: str
    body: str
    head: str
    base: str
    state: str
    merged: bool
    draft: bool
    url: str
    created_at: datetime


class CiRunOut(BaseModel):
    id: uuid.UUID
    workflow_name: str
    status: str
    conclusion: str | None
    branch: str | None
    commit_sha: str | None
    created_at: datetime
    url: str


class RepoOut(BaseModel):
    id: uuid.UUID
    owner: str
    name: str
    default_branch: str
    description: str | None
    url: str
    pull_requests: list[PullRequestOut]
    recent_runs: list[CiRunOut]


class DashboardOut(BaseModel):
    enabled: bool
    tickets: list[TicketOut]
    projects: list[ProjectOut]
    repos: list[RepoOut]


class TransitionIn(BaseModel):
    to_state: str = Field(..., min_length=1, max_length=64)


class CommentIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _require_memory_enabled(settings: Settings) -> None:
    if not getattr(settings, "use_memory_adapters", False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@router.get("/dashboard", response_model=DashboardOut)
async def get_local_tracker_dashboard(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DashboardOut:
    """Single-shot bundle the Console renders for ``/local-tracker``."""
    _require_memory_enabled(settings)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    # ---- Tickets ----
    ticket_rows = (
        (
            await session.execute(
                select(MemoryTrackerTicket)
                .where(MemoryTrackerTicket.workspace_id == workspace_id)
                .order_by(MemoryTrackerTicket.serial.asc())
            )
        )
        .scalars()
        .all()
    )
    tickets = [_ticket_to_out(r, settings.console_url) for r in ticket_rows]

    # ---- Projects ----
    project_rows = (
        (
            await session.execute(
                select(MemoryTrackerProject)
                .where(MemoryTrackerProject.workspace_id == workspace_id)
                .order_by(MemoryTrackerProject.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    # Count tickets per project in one pass to avoid an N+1.
    counts_by_project: dict[uuid.UUID, int] = {}
    for t in ticket_rows:
        if t.project_id is not None:
            counts_by_project[t.project_id] = (
                counts_by_project.get(t.project_id, 0) + 1
            )
    projects = [
        ProjectOut(
            id=p.id,
            slug=p.slug,
            name=p.name,
            state=p.state,
            description=p.description,
            body=p.body,
            ticket_count=counts_by_project.get(p.id, 0),
            url=f"{settings.console_url.rstrip('/')}/local-tracker/projects/{p.slug}",
        )
        for p in project_rows
    ]

    # ---- Repos + PRs + recent runs ----
    repo_rows = (
        (
            await session.execute(
                select(MemoryGitRepo)
                .where(MemoryGitRepo.workspace_id == workspace_id)
                .order_by(MemoryGitRepo.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    repos: list[RepoOut] = []
    for repo in repo_rows:
        pr_rows = (
            (
                await session.execute(
                    select(MemoryGitPullRequest)
                    .where(MemoryGitPullRequest.repo_id == repo.id)
                    .order_by(MemoryGitPullRequest.number.desc())
                )
            )
            .scalars()
            .all()
        )
        # Calling the gateway flushes any ripe CI transitions before
        # we serialise — the dashboard ought to render the latest
        # state on every poll.
        ci = MemoryCi(
            session=session,
            workspace_id=workspace_id,
            console_origin=settings.console_url,
        )
        run_dicts = await ci.list_runs(
            RepoRef(kind="github", owner=repo.owner, repo=repo.name),
            limit=10,
        )
        repos.append(
            RepoOut(
                id=repo.id,
                owner=repo.owner,
                name=repo.name,
                default_branch=repo.default_branch,
                description=repo.description,
                url=f"{settings.console_url.rstrip('/')}/local-tracker/repos/{repo.owner}/{repo.name}",
                pull_requests=[
                    _pr_to_out(repo, p, settings.console_url) for p in pr_rows
                ],
                recent_runs=[_run_dict_to_out(d) for d in run_dicts],
            )
        )

    return DashboardOut(
        enabled=True,
        tickets=tickets,
        projects=projects,
        repos=repos,
    )


@router.get(
    "/tickets/{display_id}",
    response_model=TicketDetailOut,
)
async def get_local_tracker_ticket(
    workspace_id: uuid.UUID,
    display_id: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TicketDetailOut:
    _require_memory_enabled(settings)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    row = await _fetch_ticket(session, workspace_id, display_id)
    base = _ticket_to_out(row, settings.console_url)

    tracker = MemoryTracker(
        session=session,
        workspace_id=workspace_id,
        console_origin=settings.console_url,
    )
    comments = await tracker.list_comments(
        TicketRef(
            kind="linear",
            workspace_hint=str(workspace_id),
            id=row.display_id,
        )
    )
    return TicketDetailOut(
        **base.model_dump(),
        comments=[
            {
                "id": c.id,
                "body": c.body,
                "author": c.author,
                "created_at": c.created_at,
            }
            for c in comments
        ],
    )


@router.post(
    "/tickets/{display_id}/transition",
    response_model=TicketOut,
)
async def transition_local_tracker_ticket(
    workspace_id: uuid.UUID,
    display_id: str,
    payload: TransitionIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TicketOut:
    _require_memory_enabled(settings)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    row = await _fetch_ticket(session, workspace_id, display_id)
    tracker = MemoryTracker(
        session=session,
        workspace_id=workspace_id,
        console_origin=settings.console_url,
    )
    await tracker.transition(
        TicketRef(
            kind="linear",
            workspace_hint=str(workspace_id),
            id=row.display_id,
        ),
        to_state=payload.to_state,
    )
    await session.flush()
    await session.refresh(row)
    return _ticket_to_out(row, settings.console_url)


@router.post(
    "/tickets/{display_id}/comment",
    response_model=TicketDetailOut,
)
async def comment_local_tracker_ticket(
    workspace_id: uuid.UUID,
    display_id: str,
    payload: CommentIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TicketDetailOut:
    _require_memory_enabled(settings)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    row = await _fetch_ticket(session, workspace_id, display_id)
    tracker = MemoryTracker(
        session=session,
        workspace_id=workspace_id,
        console_origin=settings.console_url,
    )
    await tracker.comment(
        TicketRef(
            kind="linear",
            workspace_hint=str(workspace_id),
            id=row.display_id,
        ),
        body=payload.body,
    )
    await session.flush()
    comments = await tracker.list_comments(
        TicketRef(
            kind="linear",
            workspace_hint=str(workspace_id),
            id=row.display_id,
        )
    )
    base = _ticket_to_out(row, settings.console_url)
    return TicketDetailOut(
        **base.model_dump(),
        comments=[
            {
                "id": c.id,
                "body": c.body,
                "author": c.author,
                "created_at": c.created_at,
            }
            for c in comments
        ],
    )


@router.post(
    "/repos/{owner}/{name}/pulls/{number}/merge",
    response_model=PullRequestOut,
)
async def merge_local_tracker_pr(
    workspace_id: uuid.UUID,
    owner: str,
    name: str,
    number: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PullRequestOut:
    _require_memory_enabled(settings)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    code_host = MemoryCodeHost(
        session=session,
        workspace_id=workspace_id,
        console_origin=settings.console_url,
    )
    repo = await code_host._fetch_repo(owner=owner, name=name)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    pr = await code_host.mark_pr_merged(repo, number=number)
    if pr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Kick a fresh "deploy" run on the base branch so the dashboard
    # feels like a real merge happened.
    ci = MemoryCi(
        session=session,
        workspace_id=workspace_id,
        console_origin=settings.console_url,
    )
    await ci.dispatch(
        repo,
        workflow_name="deploy.yml",
        branch=pr.base,
        commit_sha="merged",
        outcome="success",
    )
    await session.flush()
    return _pr_to_out(repo, pr, settings.console_url)


@router.post(
    "/repos/{owner}/{name}/runs/{run_id}/rerun",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def rerun_local_tracker_run(
    workspace_id: uuid.UUID,
    owner: str,
    name: str,
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    _require_memory_enabled(settings)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    ci = MemoryCi(
        session=session,
        workspace_id=workspace_id,
        console_origin=settings.console_url,
    )
    await ci.rerun(
        RepoRef(kind="github", owner=owner, repo=name),
        run_id=str(run_id),
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_ticket(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    display_id: str,
) -> MemoryTrackerTicket:
    row = (
        await session.execute(
            select(MemoryTrackerTicket).where(
                MemoryTrackerTicket.workspace_id == workspace_id,
                MemoryTrackerTicket.display_id == display_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return row


def _ticket_to_out(
    row: MemoryTrackerTicket, console_origin: str
) -> TicketOut:
    stage = _extract_stage(list(row.labels or []))
    origin = console_origin.rstrip("/")
    return TicketOut(
        display_id=row.display_id,
        title=row.title,
        body=row.body,
        state=row.state,
        labels=list(row.labels or []),
        stage=stage,
        project_id=row.project_id,
        ticket_type=row.ticket_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
        url=f"{origin}/local-tracker/tickets/{row.display_id}",
    )


def _pr_to_out(
    repo: MemoryGitRepo,
    pr: MemoryGitPullRequest,
    console_origin: str,
) -> PullRequestOut:
    origin = console_origin.rstrip("/")
    return PullRequestOut(
        number=pr.number,
        title=pr.title,
        body=pr.body,
        head=pr.head,
        base=pr.base,
        state=pr.state,
        merged=pr.merged,
        draft=pr.draft,
        url=f"{origin}/local-tracker/repos/{repo.owner}/{repo.name}/pull/{pr.number}",
        created_at=pr.created_at,
    )


def _run_dict_to_out(d: dict[str, Any]) -> CiRunOut:
    return CiRunOut(
        id=uuid.UUID(d["id"]),
        workflow_name=d["name"],
        status=d["status"],
        conclusion=d.get("conclusion"),
        branch=d.get("branch"),
        commit_sha=d.get("commit_sha"),
        created_at=datetime.fromisoformat(d["created_at"]),
        url=d["url"],
    )


def _extract_stage(labels: list[str]) -> str | None:
    for label in labels:
        if label.startswith("stage:"):
            return label[len("stage:") :]
    return None


__all__ = ["router"]
