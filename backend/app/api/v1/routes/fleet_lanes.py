"""Workspace Fleet lanes (was: workspace policies, RFC-0008 §G — PR-5).

Mirror-lane Fleet lanes are the first kind the Console exposes:
"pattern X runs as lane Y with cadence Z on every activated repo
unless the repo has an explicit opt-out". The endpoint surface
covers CRUD on the Fleet lane plus toggle on per-repo exceptions,
and every read computes a compliance rollup (which repos satisfy
the rule, which are missing the lane, which opted out).

Compliance heuristic for PR-5: we look at ``Pipeline`` rows keyed on
``(repo_id, lane_id)`` — if an enabled row exists, the repo counts
as compliant. ``.ship/config.yml`` drift detection and one-click
autofix via Navigator are intentionally out of scope (they can slot
into this endpoint later without breaking the response shape).

Naming history: this module used to be ``policies.py`` exposing
``/workspaces/{ws}/policies`` with ``WorkspacePolicy`` rows. The
name "policy" is now reserved for free-text standing rules
("Always work via PR", etc.) injected into agent instructions, so
the mirror-lane primitive moved to "Fleet lanes" (sibling of "Fleet
requests" in the Console nav). Migration ``0029`` renamed the
underlying tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.fleet_lanes import (
    FleetLane,
    FleetLaneException,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.pipelines import Pipeline
from backend.app.db.session import get_session
from backend.app.services import catalog as catalog_service


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["fleet-lanes"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


ComplianceStatus = Literal["compliant", "missing", "excepted"]


class FleetLaneRepoCompliance(BaseModel):
    repo_id: uuid.UUID
    full_name: str
    status: ComplianceStatus
    exception_reason: str | None = None


class FleetLaneCompliance(BaseModel):
    total_repos: int
    compliant: int
    missing: int
    excepted: int
    repos: list[FleetLaneRepoCompliance] = Field(default_factory=list)


class FleetLaneOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    name: str
    pattern_id: str
    lane_id: str
    cadence: str
    agent_slug: str | None
    inputs: dict
    enabled: bool
    created_at: datetime
    updated_at: datetime
    compliance: FleetLaneCompliance


class FleetLaneListOut(BaseModel):
    fleet_lanes: list[FleetLaneOut]


class FleetLaneCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    pattern_id: str = Field(..., min_length=1, max_length=120)
    lane_id: str = Field(..., min_length=1, max_length=64)
    cadence: str = Field(..., min_length=1, max_length=120)
    agent_slug: str | None = Field(default=None, max_length=64)
    inputs: dict | None = None
    enabled: bool = True


class FleetLaneExceptionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=512)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/fleet-lanes", response_model=FleetLaneListOut)
async def list_fleet_lanes(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FleetLaneListOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    fleet_lanes = (
        (
            await session.execute(
                select(FleetLane)
                .where(FleetLane.workspace_id == workspace_id)
                .order_by(asc(FleetLane.created_at))
            )
        )
        .scalars()
        .all()
    )
    repos = await _load_activated_repos(session, workspace_id)

    out: list[FleetLaneOut] = []
    for fleet_lane in fleet_lanes:
        compliance = await _compute_compliance(session, fleet_lane, repos)
        out.append(_serialise_fleet_lane(fleet_lane, compliance))
    return FleetLaneListOut(fleet_lanes=out)


@router.post(
    "/fleet-lanes",
    response_model=FleetLaneOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_fleet_lane(
    workspace_id: uuid.UUID,
    payload: FleetLaneCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FleetLaneOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    pattern = next(
        (
            p
            for p in catalog_service.list_patterns()
            if p.id == payload.pattern_id
        ),
        None,
    )
    if pattern is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "pattern_not_found",
                "message": f"No catalog pattern with id={payload.pattern_id!r}.",
            },
        )
    if "lane" not in pattern.modes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "pattern_not_lane_mode",
                "message": (
                    f"Pattern {pattern.id!r} can't back a Fleet lane "
                    "(missing 'lane' in spec.modes)."
                ),
            },
        )

    row = FleetLane(
        workspace_id=workspace_id,
        kind="mirror_lane",
        name=payload.name,
        pattern_id=payload.pattern_id,
        lane_id=payload.lane_id,
        cadence=payload.cadence,
        agent_slug=(payload.agent_slug or None),
        inputs=payload.inputs or {},
        enabled=payload.enabled,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "fleet_lane_conflict",
                "message": (
                    f"A Fleet lane with lane_id={payload.lane_id!r} already "
                    "exists in this workspace."
                ),
            },
        ) from exc
    await session.refresh(row, attribute_names=["created_at", "updated_at"])

    repos = await _load_activated_repos(session, workspace_id)
    compliance = await _compute_compliance(session, row, repos)
    return _serialise_fleet_lane(row, compliance)


@router.delete(
    "/fleet-lanes/{fleet_lane_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_fleet_lane(
    workspace_id: uuid.UUID,
    fleet_lane_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    fleet_lane = await _require_fleet_lane(session, workspace_id, fleet_lane_id)
    await session.delete(fleet_lane)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/fleet-lanes/{fleet_lane_id}/exceptions/{repo_id}",
    response_model=FleetLaneOut,
)
async def add_fleet_lane_exception(
    workspace_id: uuid.UUID,
    fleet_lane_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: FleetLaneExceptionIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FleetLaneOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    fleet_lane = await _require_fleet_lane(session, workspace_id, fleet_lane_id)

    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "repo_not_found", "message": "Unknown repo."},
        )

    existing = (
        await session.execute(
            select(FleetLaneException).where(
                FleetLaneException.fleet_lane_id == fleet_lane.id,
                FleetLaneException.repo_id == repo.id,
            )
        )
    ).scalar_one_or_none()
    # Idempotent: calling twice just updates the reason in place
    # rather than surfacing a constraint error.
    reason = payload.reason if payload else None
    if existing is None:
        session.add(
            FleetLaneException(
                fleet_lane_id=fleet_lane.id,
                repo_id=repo.id,
                reason=reason,
            )
        )
    else:
        existing.reason = reason
    await session.flush()

    repos = await _load_activated_repos(session, workspace_id)
    compliance = await _compute_compliance(session, fleet_lane, repos)
    return _serialise_fleet_lane(fleet_lane, compliance)


@router.delete(
    "/fleet-lanes/{fleet_lane_id}/exceptions/{repo_id}",
    response_model=FleetLaneOut,
)
async def remove_fleet_lane_exception(
    workspace_id: uuid.UUID,
    fleet_lane_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FleetLaneOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    fleet_lane = await _require_fleet_lane(session, workspace_id, fleet_lane_id)

    existing = (
        await session.execute(
            select(FleetLaneException).where(
                FleetLaneException.fleet_lane_id == fleet_lane.id,
                FleetLaneException.repo_id == repo_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()

    repos = await _load_activated_repos(session, workspace_id)
    compliance = await _compute_compliance(session, fleet_lane, repos)
    return _serialise_fleet_lane(fleet_lane, compliance)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_fleet_lane(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    fleet_lane_id: uuid.UUID,
) -> FleetLane:
    fleet_lane = (
        await session.execute(
            select(FleetLane).where(
                FleetLane.id == fleet_lane_id,
                FleetLane.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if fleet_lane is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "fleet_lane_not_found",
                "message": "Unknown Fleet lane.",
            },
        )
    return fleet_lane


async def _load_activated_repos(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[WorkspaceRepo]:
    rows = (
        (
            await session.execute(
                select(WorkspaceRepo)
                .where(WorkspaceRepo.workspace_id == workspace_id)
                .order_by(asc(WorkspaceRepo.full_name))
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _compute_compliance(
    session: AsyncSession,
    fleet_lane: FleetLane,
    repos: list[WorkspaceRepo],
) -> FleetLaneCompliance:
    if not repos:
        return FleetLaneCompliance(
            total_repos=0, compliant=0, missing=0, excepted=0, repos=[]
        )

    # Exceptions for this Fleet lane — a set of repo_id for O(1) lookup.
    exc_rows = (
        (
            await session.execute(
                select(FleetLaneException).where(
                    FleetLaneException.fleet_lane_id == fleet_lane.id
                )
            )
        )
        .scalars()
        .all()
    )
    exceptions: dict[uuid.UUID, str | None] = {
        e.repo_id: e.reason for e in exc_rows
    }

    # Pipelines with matching lane_id across all activated repos
    # in the workspace.
    repo_ids = [r.id for r in repos]
    pipe_rows = (
        (
            await session.execute(
                select(Pipeline.repo_id).where(
                    Pipeline.lane_id == fleet_lane.lane_id,
                    Pipeline.repo_id.in_(repo_ids),
                    Pipeline.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    covered: set[uuid.UUID] = {rid for rid in pipe_rows if rid is not None}

    entries: list[FleetLaneRepoCompliance] = []
    compliant = missing = excepted = 0
    for repo in repos:
        if repo.id in exceptions:
            entries.append(
                FleetLaneRepoCompliance(
                    repo_id=repo.id,
                    full_name=repo.full_name,
                    status="excepted",
                    exception_reason=exceptions[repo.id],
                )
            )
            excepted += 1
        elif repo.id in covered:
            entries.append(
                FleetLaneRepoCompliance(
                    repo_id=repo.id,
                    full_name=repo.full_name,
                    status="compliant",
                )
            )
            compliant += 1
        else:
            entries.append(
                FleetLaneRepoCompliance(
                    repo_id=repo.id,
                    full_name=repo.full_name,
                    status="missing",
                )
            )
            missing += 1

    return FleetLaneCompliance(
        total_repos=len(repos),
        compliant=compliant,
        missing=missing,
        excepted=excepted,
        repos=entries,
    )


def _serialise_fleet_lane(
    fleet_lane: FleetLane, compliance: FleetLaneCompliance
) -> FleetLaneOut:
    return FleetLaneOut(
        id=fleet_lane.id,
        workspace_id=fleet_lane.workspace_id,
        kind=fleet_lane.kind,
        name=fleet_lane.name,
        pattern_id=fleet_lane.pattern_id,
        lane_id=fleet_lane.lane_id,
        cadence=fleet_lane.cadence,
        agent_slug=fleet_lane.agent_slug,
        inputs=dict(fleet_lane.inputs or {}),
        enabled=fleet_lane.enabled,
        created_at=fleet_lane.created_at,
        updated_at=fleet_lane.updated_at,
        compliance=compliance,
    )


__all__ = ["router"]
