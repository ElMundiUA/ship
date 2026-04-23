"""Workspace prose-rule policies API (Workspace policy injection).

CRUD over :class:`WorkspacePolicy` rows that get rendered into the
agent's system prompt at runtime (Navigator chat) and into the
``shipctl run`` stdout (GitHub-Actions agent step). Endpoints are
plain ``/workspaces/{ws}/policies``; admins author + toggle, all
members can read.

Naming history: the path was previously the home of mirror-lane
rules; that primitive moved to ``/workspaces/{ws}/fleet-lanes`` in
the Phase-1 rename. Net result is two distinct, well-named
surfaces — Fleet lanes for "wire pattern X as scheduled lane Y on
every repo" and Policies for "prose rule injected into every agent
turn".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.policies import WorkspacePolicy
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["policies"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PolicyOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    body: str
    enabled: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class PolicyListOut(BaseModel):
    policies: list[PolicyOut]


class PolicyCreateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=160)
    body: str = Field(..., min_length=1)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=-10_000, le=10_000)


class PolicyUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=-10_000, le=10_000)


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
    rows = (
        (
            await session.execute(
                select(WorkspacePolicy)
                .where(WorkspacePolicy.workspace_id == workspace_id)
                .order_by(
                    asc(WorkspacePolicy.sort_order),
                    asc(WorkspacePolicy.created_at),
                )
            )
        )
        .scalars()
        .all()
    )
    return PolicyListOut(policies=[_serialise(r) for r in rows])


@router.post(
    "/policies",
    response_model=PolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_policy(
    workspace_id: uuid.UUID,
    payload: PolicyCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = WorkspacePolicy(
        workspace_id=workspace_id,
        title=payload.title.strip(),
        body=payload.body,
        enabled=payload.enabled,
        sort_order=payload.sort_order,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row, attribute_names=["created_at", "updated_at"])
    return _serialise(row)


@router.patch("/policies/{policy_id}", response_model=PolicyOut)
async def update_policy(
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
    payload: PolicyUpdateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _require_policy(session, workspace_id, policy_id)
    if payload.title is not None:
        row.title = payload.title.strip()
    if payload.body is not None:
        row.body = payload.body
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    await session.flush()
    await session.refresh(row, attribute_names=["updated_at"])
    return _serialise(row)


@router.delete(
    "/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_policy(
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _require_policy(session, workspace_id, policy_id)
    await session.delete(row)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_policy(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
) -> WorkspacePolicy:
    row = (
        await session.execute(
            select(WorkspacePolicy).where(
                WorkspacePolicy.id == policy_id,
                WorkspacePolicy.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "policy_not_found", "message": "Unknown policy."},
        )
    return row


def _serialise(row: WorkspacePolicy) -> PolicyOut:
    return PolicyOut(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        body=row.body,
        enabled=row.enabled,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


__all__ = ["router"]
