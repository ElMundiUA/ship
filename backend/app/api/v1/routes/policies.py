"""Workspace policies (RFC-0008 §G — PR-5).

Mirror-lane policies are the first Policy kind the Console exposes:
"pattern X runs as lane Y with cadence Z on every activated repo
unless the repo has an explicit opt-out". The endpoint surface
covers CRUD on the policy plus toggle on per-repo exceptions, and
every read computes a compliance rollup (which repos satisfy the
rule, which are missing the lane, which opted out).

Compliance heuristic for PR-5: we look at ``Pipeline`` rows keyed on
``(repo_id, lane_id)`` — if an enabled row exists, the repo counts
as compliant. ``.ship/config.yml`` drift detection and one-click
autofix via Navigator are intentionally out of scope (they can slot
into this endpoint later without breaking the response shape).
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
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.pipelines import Pipeline
from backend.app.db.models.policies import (
    WorkspacePolicy,
    WorkspacePolicyException,
)
from backend.app.db.session import get_session
from backend.app.services import catalog as catalog_service


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["policies"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


ComplianceStatus = Literal["compliant", "missing", "excepted"]


class PolicyRepoCompliance(BaseModel):
    repo_id: uuid.UUID
    full_name: str
    status: ComplianceStatus
    exception_reason: str | None = None


class PolicyCompliance(BaseModel):
    total_repos: int
    compliant: int
    missing: int
    excepted: int
    repos: list[PolicyRepoCompliance] = Field(default_factory=list)


class PolicyOut(BaseModel):
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
    compliance: PolicyCompliance


class PolicyListOut(BaseModel):
    policies: list[PolicyOut]


class PolicyCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    pattern_id: str = Field(..., min_length=1, max_length=120)
    lane_id: str = Field(..., min_length=1, max_length=64)
    cadence: str = Field(..., min_length=1, max_length=120)
    agent_slug: str | None = Field(default=None, max_length=64)
    inputs: dict | None = None
    enabled: bool = True


class PolicyExceptionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=512)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/policies", response_model=PolicyListOut)
async def list_policies(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyListOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    policies = (
        (
            await session.execute(
                select(WorkspacePolicy)
                .where(WorkspacePolicy.workspace_id == workspace_id)
                .order_by(asc(WorkspacePolicy.created_at))
            )
        )
        .scalars()
        .all()
    )
    repos = await _load_activated_repos(session, workspace_id)

    out: list[PolicyOut] = []
    for policy in policies:
        compliance = await _compute_compliance(session, policy, repos)
        out.append(_serialise_policy(policy, compliance))
    return PolicyListOut(policies=out)


@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def create_policy(
    workspace_id: uuid.UUID,
    payload: PolicyCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
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
                    f"Pattern {pattern.id!r} can't back a mirror-lane policy "
                    "(missing 'lane' in spec.modes)."
                ),
            },
        )

    row = WorkspacePolicy(
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
                "code": "policy_lane_conflict",
                "message": (
                    f"A policy with lane_id={payload.lane_id!r} already "
                    "exists in this workspace."
                ),
            },
        ) from exc
    await session.refresh(row, attribute_names=["created_at", "updated_at"])

    repos = await _load_activated_repos(session, workspace_id)
    compliance = await _compute_compliance(session, row, repos)
    return _serialise_policy(row, compliance)


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    policy = await _require_policy(session, workspace_id, policy_id)
    await session.delete(policy)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/policies/{policy_id}/exceptions/{repo_id}",
    response_model=PolicyOut,
)
async def add_policy_exception(
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: PolicyExceptionIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    policy = await _require_policy(session, workspace_id, policy_id)

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
            select(WorkspacePolicyException).where(
                WorkspacePolicyException.policy_id == policy.id,
                WorkspacePolicyException.repo_id == repo.id,
            )
        )
    ).scalar_one_or_none()
    # Idempotent: calling twice just updates the reason in place
    # rather than surfacing a constraint error.
    reason = payload.reason if payload else None
    if existing is None:
        session.add(
            WorkspacePolicyException(
                policy_id=policy.id,
                repo_id=repo.id,
                reason=reason,
            )
        )
    else:
        existing.reason = reason
    await session.flush()

    repos = await _load_activated_repos(session, workspace_id)
    compliance = await _compute_compliance(session, policy, repos)
    return _serialise_policy(policy, compliance)


@router.delete(
    "/policies/{policy_id}/exceptions/{repo_id}",
    response_model=PolicyOut,
)
async def remove_policy_exception(
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    policy = await _require_policy(session, workspace_id, policy_id)

    existing = (
        await session.execute(
            select(WorkspacePolicyException).where(
                WorkspacePolicyException.policy_id == policy.id,
                WorkspacePolicyException.repo_id == repo_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()

    repos = await _load_activated_repos(session, workspace_id)
    compliance = await _compute_compliance(session, policy, repos)
    return _serialise_policy(policy, compliance)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_policy(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
) -> WorkspacePolicy:
    policy = (
        await session.execute(
            select(WorkspacePolicy).where(
                WorkspacePolicy.id == policy_id,
                WorkspacePolicy.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "policy_not_found", "message": "Unknown policy."},
        )
    return policy


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
    policy: WorkspacePolicy,
    repos: list[WorkspaceRepo],
) -> PolicyCompliance:
    if not repos:
        return PolicyCompliance(
            total_repos=0, compliant=0, missing=0, excepted=0, repos=[]
        )

    # Exceptions for this policy — a set of repo_id for O(1) lookup.
    exc_rows = (
        (
            await session.execute(
                select(WorkspacePolicyException).where(
                    WorkspacePolicyException.policy_id == policy.id
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
                    Pipeline.lane_id == policy.lane_id,
                    Pipeline.repo_id.in_(repo_ids),
                    Pipeline.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    covered: set[uuid.UUID] = {rid for rid in pipe_rows if rid is not None}

    entries: list[PolicyRepoCompliance] = []
    compliant = missing = excepted = 0
    for repo in repos:
        if repo.id in exceptions:
            entries.append(
                PolicyRepoCompliance(
                    repo_id=repo.id,
                    full_name=repo.full_name,
                    status="excepted",
                    exception_reason=exceptions[repo.id],
                )
            )
            excepted += 1
        elif repo.id in covered:
            entries.append(
                PolicyRepoCompliance(
                    repo_id=repo.id,
                    full_name=repo.full_name,
                    status="compliant",
                )
            )
            compliant += 1
        else:
            entries.append(
                PolicyRepoCompliance(
                    repo_id=repo.id,
                    full_name=repo.full_name,
                    status="missing",
                )
            )
            missing += 1

    return PolicyCompliance(
        total_repos=len(repos),
        compliant=compliant,
        missing=missing,
        excepted=excepted,
        repos=entries,
    )


def _serialise_policy(
    policy: WorkspacePolicy, compliance: PolicyCompliance
) -> PolicyOut:
    return PolicyOut(
        id=policy.id,
        workspace_id=policy.workspace_id,
        kind=policy.kind,
        name=policy.name,
        pattern_id=policy.pattern_id,
        lane_id=policy.lane_id,
        cadence=policy.cadence,
        agent_slug=policy.agent_slug,
        inputs=dict(policy.inputs or {}),
        enabled=policy.enabled,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
        compliance=compliance,
    )


__all__ = ["router"]
