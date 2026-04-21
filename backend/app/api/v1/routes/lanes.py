"""Lanes API — list + sync customer-declared ``.ship/config.yml`` lanes (RFC-0007 Phase 7).

Three surfaces:

- ``GET /v1/workspaces/{ws}/lanes`` — workspace-wide list, optional
  ``?repo_id=`` filter. Powers the Console ``/lanes`` page.
- ``GET /v1/workspaces/{ws}/lanes/{lane_row_id}`` — detail including
  the 20 most recent :class:`PipelineRun` rows scoped to that lane
  (empty today; populated when "Trigger lane" lands).
- ``POST /v1/workspaces/{ws}/repos/{repo_id}/lanes/sync`` — admin
  trigger for a one-off re-pull of ``.ship/config.yml``. Idempotent.

Webhook-driven syncs (push to default branch touching
``.ship/config.yml``) are wired in :mod:`backend.app.api.v1.routes.github_app`;
this module is the human/Console-facing surface.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

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
from backend.app.db.models.lanes import Lane
from backend.app.db.models.pipelines import PipelineRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.services.lanes_sync import (
    CONFIG_PATH,
    SyncReport,
    sync_lanes_for_repo,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["lanes"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LaneOut(BaseModel):
    """One row on the ``/lanes`` list."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    repo_id: uuid.UUID
    repo_full_name: str
    lane_id: str
    kind: str = Field(..., description="``once`` | ``event`` | ``schedule``.")
    pattern: str | None
    cron: str | None
    idempotency_key: str | None
    enabled: bool
    config: dict
    last_run_at: datetime | None
    last_run_status: str | None
    synced_at: datetime
    sync_source: str | None
    created_at: datetime
    updated_at: datetime


class LaneListOut(BaseModel):
    """Envelope for ``GET /workspaces/{ws}/lanes``."""

    lanes: list[LaneOut]


class LaneRunOut(BaseModel):
    """Subset of :class:`PipelineRun` used on the lane detail view."""

    id: uuid.UUID
    pipeline_id: uuid.UUID
    status: str
    trigger: str
    summary: str | None
    started_at: datetime | None
    finished_at: datetime | None


class LaneDetailOut(LaneOut):
    """Envelope for ``GET /workspaces/{ws}/lanes/{id}``."""

    recent_runs: list[LaneRunOut] = Field(default_factory=list)


class LaneSyncOut(BaseModel):
    """Envelope for ``POST /repos/{repo_id}/lanes/sync``."""

    repo_id: uuid.UUID
    added: int
    updated: int
    removed: int
    unchanged: int
    errors: list[str]
    sync_source: str | None


def _row_to_out(row: Lane, *, repo_full_name: str) -> LaneOut:
    return LaneOut(
        id=row.id,
        workspace_id=row.workspace_id,
        repo_id=row.repo_id,
        repo_full_name=repo_full_name,
        lane_id=row.lane_id,
        kind=row.kind,
        pattern=row.pattern,
        cron=row.cron,
        idempotency_key=row.idempotency_key,
        enabled=row.enabled,
        config=dict(row.config_blob or {}),
        last_run_at=row.last_run_at,
        last_run_status=row.last_run_status,
        synced_at=row.synced_at,
        sync_source=row.sync_source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/lanes", response_model=LaneListOut)
async def list_lanes_route(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> LaneListOut:
    """List lanes for the workspace, optionally narrowed to one repo."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = select(Lane, WorkspaceRepo).join(
        WorkspaceRepo, WorkspaceRepo.id == Lane.repo_id
    ).where(Lane.workspace_id == workspace_id).order_by(
        WorkspaceRepo.full_name, Lane.lane_id
    )
    if repo_id is not None:
        stmt = stmt.where(Lane.repo_id == repo_id)

    rows = (await session.execute(stmt)).all()
    return LaneListOut(
        lanes=[
            _row_to_out(lane, repo_full_name=repo.full_name)
            for (lane, repo) in rows
        ]
    )


@router.get("/lanes/{lane_row_id}", response_model=LaneDetailOut)
async def get_lane_route(
    workspace_id: uuid.UUID,
    lane_row_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> LaneDetailOut:
    """Single lane + recent runs."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = select(Lane, WorkspaceRepo).join(
        WorkspaceRepo, WorkspaceRepo.id == Lane.repo_id
    ).where(
        Lane.workspace_id == workspace_id,
        Lane.id == lane_row_id,
    )
    result = (await session.execute(stmt)).first()
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    lane_row, repo_row = result

    runs_stmt = (
        select(PipelineRun)
        .where(PipelineRun.lane_id == lane_row.id)
        .order_by(desc(PipelineRun.started_at), desc(PipelineRun.created_at))
        .limit(20)
    )
    runs = (await session.execute(runs_stmt)).scalars().all()

    base = _row_to_out(lane_row, repo_full_name=repo_row.full_name)
    return LaneDetailOut(
        **base.model_dump(),
        recent_runs=[
            LaneRunOut(
                id=r.id,
                pipeline_id=r.pipeline_id,
                status=r.status,
                trigger=r.trigger,
                summary=r.summary,
                started_at=r.started_at,
                finished_at=r.finished_at,
            )
            for r in runs
        ],
    )


@router.post(
    "/repos/{repo_id}/lanes/sync",
    response_model=LaneSyncOut,
)
async def sync_lanes_route(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LaneSyncOut:
    """Re-pull ``.ship/config.yml`` for one repo and upsert lanes.

    Admin-only — the sync itself is idempotent but we treat it as a
    write (it drops lanes that were removed from the YAML) to keep
    the RBAC surface honest.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.id == repo_id,
            )
        )
    ).scalars().first()
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if repo.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Repo is not backed by a GitHub App installation; "
                "cannot pull .ship/config.yml."
            ),
        )

    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "GitHub App installation for this repo is missing or "
                "suspended. Reinstall the Ship app."
            ),
        )

    try:
        report: SyncReport = await sync_lanes_for_repo(
            session=session,
            repo=repo,
            install=install,
            settings=settings,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{CONFIG_PATH} not found on {repo.full_name}@"
                f"{repo.default_branch}. Run `shipctl init` first."
            ),
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "GitHub Contents API rejected the request "
                f"(HTTP {exc.response.status_code})."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action="lanes.sync",
            target_kind="workspace_repo",
            target_id=str(repo.id),
            payload={
                "added": report.added,
                "updated": report.updated,
                "removed": report.removed,
                "unchanged": report.unchanged,
                "errors": list(report.errors),
                "sync_source": report.sync_source,
            },
        )
    )
    await session.flush()

    return LaneSyncOut(
        repo_id=repo.id,
        added=report.added,
        updated=report.updated,
        removed=report.removed,
        unchanged=report.unchanged,
        errors=list(report.errors),
        sync_source=report.sync_source,
    )


__all__ = ["router"]
