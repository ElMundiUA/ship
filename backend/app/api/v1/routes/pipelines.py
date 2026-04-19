"""Pipelines API — list, toggle, run, run-history (pilot Day 3).

Backs the dashboard's five pipeline cards. The "run" verb is a stub
in the pilot: we insert a :class:`PipelineRun` row, mark it
``succeeded`` immediately, and return it. Real execution lives in the
worker that comes back in package #2; for the demo flow that's fine —
the user sees the click → row → toast → row in history.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.pipelines import Pipeline, PipelineRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/pipelines",
    tags=["pipelines"],
)


# Recent-runs panel on the dashboard caps at 20 — beyond that we'd need
# pagination, which the pilot scope doesn't include.
_RUNS_PAGE_LIMIT = 20
_RUNS_PAGE_DEFAULT = 10


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PipelineOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    workflow_id: str
    enabled: bool
    config: dict
    last_run_at: datetime | None
    last_run_status: str | None
    created_at: datetime
    updated_at: datetime


class PipelineToggleIn(BaseModel):
    enabled: bool


class PipelineRunOut(BaseModel):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    trigger: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    summary: str | None
    payload: dict
    created_at: datetime


class PipelineRunIn(BaseModel):
    """Optional client-supplied payload for a manual run.

    Mostly cosmetic in the pilot — the runner stub doesn't read the
    payload, but we record it so the audit log can show "user X ran
    pipeline Y with note Z".
    """

    note: str | None = Field(
        default=None,
        max_length=500,
        description="Optional human note shown in the run history.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_out(row: Pipeline) -> PipelineOut:
    return PipelineOut(
        id=row.id,
        kind=row.kind,
        name=row.name,
        workflow_id=row.workflow_id,
        enabled=row.enabled,
        config=row.config or {},
        last_run_at=row.last_run_at,
        last_run_status=row.last_run_status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_to_out(row: PipelineRun) -> PipelineRunOut:
    return PipelineRunOut(
        id=row.id,
        pipeline_id=row.pipeline_id,
        trigger=row.trigger,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        summary=row.summary,
        payload=row.payload or {},
        created_at=row.created_at,
    )


async def _load_pipeline(
    session: AsyncSession, workspace_id: uuid.UUID, pipeline_id: uuid.UUID
) -> Pipeline:
    stmt = select(Pipeline).where(
        Pipeline.workspace_id == workspace_id, Pipeline.id == pipeline_id
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        # 404 (not 403) — same shape used for unknown integration kinds;
        # avoids leaking pipeline ids across workspaces.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return row


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PipelineOut])
async def list_pipelines(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[PipelineOut]:
    """All pipelines for the workspace, ordered by ``created_at`` so the
    five baked-in defaults stay in seed order.

    Members read; only admins can toggle/run (separate endpoints).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(Pipeline)
        .where(Pipeline.workspace_id == workspace_id)
        .order_by(Pipeline.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_out(r) for r in rows]


@router.patch("/{pipeline_id}", response_model=PipelineOut)
async def toggle_pipeline(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: PipelineToggleIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PipelineOut:
    """Flip a pipeline on or off. Admin-only.

    The pilot only exposes ``enabled``; ``config`` mutations land in
    Day-4 (or later) once we have per-kind settings forms.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _load_pipeline(session, workspace_id, pipeline_id)
    if row.enabled == payload.enabled:
        # No-op writes still emit an audit row in some shops; here we
        # silently return the current state so toggle spam stays cheap.
        return _row_to_out(row)
    row.enabled = payload.enabled
    row.updated_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="pipeline.toggle",
            target_kind="pipeline",
            target_id=str(row.id),
            payload={"kind": row.kind, "enabled": row.enabled},
        )
    )
    await session.flush()
    return _row_to_out(row)


@router.post(
    "/{pipeline_id}/runs",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_pipeline(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: PipelineRunIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PipelineRunOut:
    """Execute a pipeline now (admin-only).

    The pilot's runner is a synchronous stub: insert ``running``,
    update to ``succeeded``, return. The endpoint shape matches what
    the real (Day-4) async runner will return so the dashboard's
    "Run now" UI doesn't need to change when execution becomes real.

    Disabled pipelines reject with 409 — the dashboard hides the
    button but anyone hitting the API directly should get a clear
    error rather than a no-op.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _load_pipeline(session, workspace_id, pipeline_id)
    if not row.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline is disabled. Enable it before running.",
        )

    note = (payload.note if payload else None) or None
    now = datetime.now(timezone.utc)

    run = PipelineRun(
        pipeline_id=row.id,
        workspace_id=workspace_id,
        trigger="manual",
        status="succeeded",
        started_at=now,
        finished_at=now,
        summary=note or f"Pilot stub run for {row.name}",
        payload={"note": note} if note else {},
    )
    session.add(run)

    # Mirror the latest run state on the pipeline so the dashboard
    # card can show "last run: 4m ago · ok" without joining.
    row.last_run_at = now
    row.last_run_status = "succeeded"
    row.updated_at = now

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="pipeline.run",
            target_kind="pipeline",
            target_id=str(row.id),
            payload={"kind": row.kind, "trigger": "manual", "note": note},
        )
    )
    await session.flush()
    return _run_to_out(run)


@router.get("/{pipeline_id}/runs", response_model=list[PipelineRunOut])
async def list_pipeline_runs(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    limit: int = _RUNS_PAGE_DEFAULT,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[PipelineRunOut]:
    """Most-recent-first page of run history. Members can read."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    # Make sure the pipeline belongs to this workspace before exposing
    # its runs (otherwise a cross-tenant id leak would be possible).
    await _load_pipeline(session, workspace_id, pipeline_id)
    capped = max(1, min(limit, _RUNS_PAGE_LIMIT))
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .order_by(desc(PipelineRun.started_at), desc(PipelineRun.created_at))
        .limit(capped)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_to_out(r) for r in rows]


__all__ = ["router"]
