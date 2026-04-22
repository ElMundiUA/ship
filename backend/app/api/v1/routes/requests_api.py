"""Ad-hoc agent runs ("Requests") — Phase 3 of RFC-0007 lanes/requests.

Two surfaces backing the Console's ``/requests`` page:

- ``POST /v1/workspaces/{ws}/repos/{repo_id}/requests`` — admin-only.
  Accepts a one-shot payload (``agent_slug``, optional ``context_ref``,
  ``prompt``), dispatches the ``adhoc-agent-run.yml`` GitHub Actions
  workflow on the repo's default branch, and inserts an
  :class:`AgentRequest` row as a dispatch receipt. Returns the row so
  the UI can redirect to the detail view.
- ``GET /v1/workspaces/{ws}/requests`` — workspace-wide list (newest
  first), optional ``?repo_id=`` filter.

There's no callback-accepting route here yet — the dispatched workflow
currently falls back to GitHub Actions as the run-log surface. When we
wire ``shipctl callback`` updates, the sink will live next to
``/pipeline-runs/{id}/callback`` and key off ``run_token_hash`` /
``gh_workflow_run_id``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import AgentRequest
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["requests"],
)


# Adhoc workflow filename — lives alongside the other starter YAMLs
# on the repo after the wizard seed lands. Kept in a constant so the
# test + dispatcher agree on the path (the starter_workflows catalog
# is the source of truth but requires a read; this is a cheap mirror).
_ADHOC_WORKFLOW_FILE = "adhoc-agent-run.yml"


# Allowed agent slugs — matches the ``catalog/agents`` surface. Kept
# tight on purpose: an unknown slug would dispatch a workflow that
# silently picks the wrong agent. Extending this is a product choice.
_ALLOWED_AGENT_SLUGS = {"claude", "gpt", "gemini", "custom"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AgentRequestIn(BaseModel):
    """Body for ``POST /{repo_id}/requests``.

    All fields echo what the Console's "New request" form collects
    (see ``console/src/app/requests/page.tsx``). ``context_ref`` is
    optional because not every prompt needs a PR / ticket / file to
    focus on; when set the workflow exposes it as ``inputs.context_ref``
    so ``shipctl run-adhoc`` can handle it agent-side.
    """

    agent_slug: str = Field(..., min_length=1, max_length=64)
    prompt: str = Field(..., min_length=1, max_length=4096)
    context_ref: str | None = Field(default=None, max_length=1024)


class AgentRequestOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    repo_id: uuid.UUID
    repo_full_name: str
    agent_slug: str
    context_ref: str | None
    prompt: str
    status: str
    summary: str | None
    gh_workflow_run_id: int | None
    gh_html_url: str | None
    requested_by_email: str | None
    finished_at: str | None
    created_at: str


class AgentRequestListOut(BaseModel):
    requests: list[AgentRequestOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_repo(
    session: AsyncSession, workspace_id: uuid.UUID, repo_id: uuid.UUID
) -> tuple[WorkspaceRepo, GitHubInstallation]:
    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )
    if repo_row.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App isn't installed for this repo. "
                    "Reconnect it before dispatching requests."
                ),
            },
        )
    install_row = await session.get(GitHubInstallation, repo_row.installation_id)
    if install_row is None or install_row.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App installation is missing or "
                    "suspended. Reinstall the Ship app."
                ),
            },
        )
    return repo_row, install_row


def _serialize(row: AgentRequest, repo_full_name: str, requester_email: str | None) -> AgentRequestOut:
    return AgentRequestOut(
        id=row.id,
        workspace_id=row.workspace_id,
        repo_id=row.repo_id,
        repo_full_name=repo_full_name,
        agent_slug=row.agent_slug,
        context_ref=row.context_ref,
        prompt=row.prompt,
        status=row.status,
        summary=row.summary,
        gh_workflow_run_id=row.gh_workflow_run_id,
        gh_html_url=row.gh_html_url,
        requested_by_email=requester_email,
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/repos/{repo_id}/requests",
    response_model=AgentRequestOut,
    status_code=status.HTTP_201_CREATED,
)
async def dispatch_request(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: AgentRequestIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AgentRequestOut:
    """Dispatch one ad-hoc agent run.

    Admin-only. Inserts :class:`AgentRequest` *before* the dispatch
    so a failed GitHub call leaves a visible "failed to dispatch" row
    in the Console rather than a silent loss.
    """
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        dispatch_workflow,
    )

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    if payload.agent_slug not in _ALLOWED_AGENT_SLUGS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unknown_agent",
                "message": (
                    f"agent_slug must be one of {sorted(_ALLOWED_AGENT_SLUGS)}"
                ),
            },
        )

    repo_row, install_row = await _load_repo(session, workspace_id, repo_id)

    row = AgentRequest(
        workspace_id=workspace_id,
        repo_id=repo_id,
        requested_by_user_id=auth.user.id,
        agent_slug=payload.agent_slug,
        context_ref=payload.context_ref or None,
        prompt=payload.prompt,
        status="dispatching",
    )
    session.add(row)
    await session.flush()

    inputs: dict[str, str] = {
        "agent": payload.agent_slug,
        "prompt": payload.prompt,
        "context_ref": payload.context_ref or "",
        # Keep the callback channel pre-wired so when the callback
        # sink ships, existing rows start reporting retroactively.
        "ship_run_id": str(row.id),
        # The callback sink isn't wired yet — see module docstring.
        # Pass an empty URL so the workflow skips the report step
        # rather than failing loudly.
        "ship_callback_url": "",
        "ship_run_token": "",
    }

    try:
        await dispatch_workflow(
            repo_row,
            install_row,
            _ADHOC_WORKFLOW_FILE,
            inputs=inputs,
            settings=settings,
        )
    except WorkflowDispatchError as exc:
        row.status = "dispatch_failed"
        row.summary = (exc.message or "GitHub rejected workflow_dispatch")[:512]
        await session.flush()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "dispatch_failed",
                "upstream_status": exc.status_code,
                "message": row.summary,
            },
        ) from exc
    except httpx.HTTPStatusError as exc:
        row.status = "dispatch_failed"
        row.summary = (
            f"GitHub HTTP {exc.response.status_code} on "
            f"workflow_dispatch({_ADHOC_WORKFLOW_FILE})"
        )[:512]
        await session.flush()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "dispatch_failed",
                "upstream_status": exc.response.status_code,
                "message": row.summary,
            },
        ) from exc

    row.status = "dispatched"
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="request.dispatch",
            target_kind="agent_request",
            target_id=str(row.id),
            payload={
                "repo_id": str(repo_id),
                "repo_full_name": repo_row.full_name,
                "agent_slug": payload.agent_slug,
                "workflow_file": _ADHOC_WORKFLOW_FILE,
            },
        )
    )
    await session.flush()

    return _serialize(row, repo_row.full_name, auth.user.email)


@router.get("/requests", response_model=AgentRequestListOut)
async def list_requests(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRequestListOut:
    """List recent ad-hoc requests (newest first)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = (
        select(AgentRequest, WorkspaceRepo.full_name)
        .join(WorkspaceRepo, AgentRequest.repo_id == WorkspaceRepo.id)
        .where(AgentRequest.workspace_id == workspace_id)
        .order_by(desc(AgentRequest.created_at))
        .limit(limit)
    )
    if repo_id is not None:
        stmt = stmt.where(AgentRequest.repo_id == repo_id)

    rows = (await session.execute(stmt)).all()

    out: list[AgentRequestOut] = []
    for row, repo_full_name in rows:
        # Requester email is looked up lazily per row; the dashboard
        # only shows the list tip so N<=200 queries is acceptable.
        requester_email: str | None = None
        if row.requested_by_user_id is not None:
            from backend.app.db.models.tenancy import User

            user = await session.get(User, row.requested_by_user_id)
            requester_email = user.email if user else None
        out.append(_serialize(row, repo_full_name, requester_email))
    return AgentRequestListOut(requests=out)


@router.get(
    "/requests/{request_id}",
    response_model=AgentRequestOut,
)
async def get_request(
    workspace_id: uuid.UUID,
    request_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRequestOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    row = (
        await session.execute(
            select(AgentRequest).where(
                AgentRequest.id == request_id,
                AgentRequest.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found.",
        )
    repo_row = await session.get(WorkspaceRepo, row.repo_id)
    repo_full_name = repo_row.full_name if repo_row else ""
    requester_email: str | None = None
    if row.requested_by_user_id is not None:
        from backend.app.db.models.tenancy import User

        user = await session.get(User, row.requested_by_user_id)
        requester_email = user.email if user else None
    return _serialize(row, repo_full_name, requester_email)


__all__ = ["router"]
