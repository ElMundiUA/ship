"""Agent role registry API — Ship defaults + workspace CRUD (Phase 2.4).

Two surfaces:

* ``GET /v1/agent-roles`` / ``GET /v1/agent-roles/{slug}`` — read-only
  list + lookup of Ship-shipped defaults (file-backed under
  ``backend/app/resources/agent_roles/``). No auth membership check —
  the caller still needs a valid session, but defaults are not
  workspace-scoped data.
* ``GET|POST|PUT|DELETE /v1/workspaces/{ws}/agent-roles[/...]`` —
  workspace overrides + clones backed by the ``agent_roles`` table.
* ``GET /v1/workspaces/{ws}/agent-roles/{slug}/resolve`` — convenience
  for ``shipctl run``: returns the workspace row when present,
  otherwise the Ship default.

Slug grammar: lowercase kebab-case, 1–64 chars (``[a-z0-9][a-z0-9-]*``).
The same shape Ship default filenames use.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_roles import AgentRole
from backend.app.db.session import get_session
from backend.app.services import agent_roles as agent_roles_svc


public_router = APIRouter(prefix="/agent-roles", tags=["agent-roles"])
workspace_router = APIRouter(
    prefix="/workspaces/{workspace_id}/agent-roles",
    tags=["agent-roles"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AgentRoleDefaultOut(BaseModel):
    """One Ship-shipped default specialist (file-backed, read-only)."""

    slug: str
    name: str
    fsm_stage: str | None = None


class AgentRoleDefaultDetailOut(AgentRoleDefaultOut):
    """Default summary plus full prompt body."""

    prompt: str


class AgentRoleOut(BaseModel):
    """One workspace agent role row."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    slug: str
    name: str
    base_role_slug: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentRoleDetailOut(AgentRoleOut):
    """Workspace row plus the full prompt body."""

    prompt: str


class AgentRoleListOut(BaseModel):
    roles: list[AgentRoleOut]


class AgentRoleCreateIn(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1)
    # When set, the slug must NOT match a Ship default — ``base_role_slug``
    # is informational only and signals "this is a clone of <slug>". To
    # override a Ship default, post the row with ``slug == default_slug``
    # and leave ``base_role_slug`` null.
    base_role_slug: str | None = Field(default=None, max_length=64)


class AgentRoleUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1)


class AgentRoleResolvedOut(BaseModel):
    """Runtime resolution result for ``shipctl run``."""

    slug: str
    name: str
    prompt: str
    fsm_stage: str | None = None
    source: Literal["workspace", "ship_default"]


# ---------------------------------------------------------------------------
# Ship default surface (read-only)
# ---------------------------------------------------------------------------


@public_router.get("", response_model=list[AgentRoleDefaultOut])
async def list_ship_defaults(
    _: AuthContext = Depends(get_current_auth),
) -> list[AgentRoleDefaultOut]:
    """Every Ship-shipped default specialist, sorted by slug."""
    return [
        AgentRoleDefaultOut(slug=d.slug, name=d.name, fsm_stage=d.fsm_stage)
        for d in agent_roles_svc.list_defaults()
    ]


@public_router.get("/{slug}", response_model=AgentRoleDefaultDetailOut)
async def get_ship_default(
    slug: str,
    _: AuthContext = Depends(get_current_auth),
) -> AgentRoleDefaultDetailOut:
    if not agent_roles_svc.is_valid_slug(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid slug — kebab-case, 1–64 chars, [a-z0-9-]",
        )
    default = agent_roles_svc.get_default(slug)
    if default is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown Ship default: {slug}",
        )
    return AgentRoleDefaultDetailOut(
        slug=default.slug,
        name=default.name,
        fsm_stage=default.fsm_stage,
        prompt=default.prompt,
    )


# ---------------------------------------------------------------------------
# Workspace surface (CRUD)
# ---------------------------------------------------------------------------


def _row_to_out(row: AgentRole) -> AgentRoleOut:
    return AgentRoleOut(
        id=row.id,
        workspace_id=row.workspace_id,
        slug=row.slug,
        name=row.name,
        base_role_slug=row.base_role_slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_detail(row: AgentRole) -> AgentRoleDetailOut:
    return AgentRoleDetailOut(
        id=row.id,
        workspace_id=row.workspace_id,
        slug=row.slug,
        name=row.name,
        prompt=row.prompt,
        base_role_slug=row.base_role_slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _load_row(
    session: AsyncSession, workspace_id: uuid.UUID, slug: str
) -> AgentRole | None:
    res = await session.execute(
        select(AgentRole).where(
            AgentRole.workspace_id == workspace_id,
            AgentRole.slug == slug,
        )
    )
    return res.scalar_one_or_none()


@workspace_router.get("", response_model=AgentRoleListOut)
async def list_workspace_roles(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRoleListOut:
    """Workspace-scoped agent role rows (overrides + clones)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    res = await session.execute(
        select(AgentRole)
        .where(AgentRole.workspace_id == workspace_id)
        .order_by(asc(AgentRole.slug))
    )
    rows = res.scalars().all()
    return AgentRoleListOut(roles=[_row_to_out(r) for r in rows])


@workspace_router.get("/{slug}", response_model=AgentRoleDetailOut)
async def get_workspace_role(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRoleDetailOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    if not agent_roles_svc.is_valid_slug(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid slug — kebab-case, 1–64 chars, [a-z0-9-]",
        )
    row = await _load_row(session, workspace_id, slug)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workspace agent role not found: {slug}",
        )
    return _row_to_detail(row)


@workspace_router.post(
    "",
    response_model=AgentRoleDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_role(
    workspace_id: uuid.UUID,
    payload: AgentRoleCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRoleDetailOut:
    """Create a workspace override (slug == Ship default) or clone (new slug)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    if not agent_roles_svc.is_valid_slug(payload.slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid slug — kebab-case, 1–64 chars, [a-z0-9-]",
        )

    if payload.base_role_slug is not None:
        if not agent_roles_svc.is_valid_slug(payload.base_role_slug):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid base_role_slug",
            )
        if agent_roles_svc.get_default(payload.base_role_slug) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown base Ship default: {payload.base_role_slug}",
            )
        # Clone semantics — slug must NOT shadow a Ship default. If the
        # caller wants to override, they should post with
        # ``base_role_slug=None`` and a slug that matches the default.
        if agent_roles_svc.get_default(payload.slug) is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "slug clashes with a Ship default — drop "
                    "base_role_slug to create an override instead, "
                    "or pick a different slug for the clone"
                ),
            )

    existing = await _load_row(session, workspace_id, payload.slug)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"workspace already has an agent role with slug '{payload.slug}'",
        )

    row = AgentRole(
        workspace_id=workspace_id,
        slug=payload.slug,
        name=payload.name,
        prompt=payload.prompt,
        base_role_slug=payload.base_role_slug,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row_to_detail(row)


@workspace_router.put("/{slug}", response_model=AgentRoleDetailOut)
async def update_workspace_role(
    workspace_id: uuid.UUID,
    slug: str,
    payload: AgentRoleUpdateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRoleDetailOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    if not agent_roles_svc.is_valid_slug(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid slug — kebab-case, 1–64 chars, [a-z0-9-]",
        )
    row = await _load_row(session, workspace_id, slug)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workspace agent role not found: {slug}",
        )
    if payload.name is not None:
        row.name = payload.name
    if payload.prompt is not None:
        row.prompt = payload.prompt
    if payload.name is None and payload.prompt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="nothing to update — supply at least one of name, prompt",
        )
    await session.commit()
    await session.refresh(row)
    return _row_to_detail(row)


@workspace_router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_role(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a workspace row.

    For an override (slug matches a Ship default) the runtime resolver
    falls back to the default after delete. For a clone (custom slug)
    the row goes away outright; routines referencing the clone will
    fail on resolve until they're rewired.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    if not agent_roles_svc.is_valid_slug(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid slug — kebab-case, 1–64 chars, [a-z0-9-]",
        )
    row = await _load_row(session, workspace_id, slug)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workspace agent role not found: {slug}",
        )
    await session.delete(row)
    await session.commit()


@workspace_router.get(
    "/{slug}/resolve", response_model=AgentRoleResolvedOut
)
async def resolve_workspace_role(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRoleResolvedOut:
    """Workspace row if present; otherwise Ship default; else 404.

    Hot path for ``shipctl run`` — saves a round trip vs. probe-then-fetch.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    if not agent_roles_svc.is_valid_slug(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid slug — kebab-case, 1–64 chars, [a-z0-9-]",
        )
    row = await _load_row(session, workspace_id, slug)
    if row is not None:
        # Workspace overrides + clones may declare a different
        # fsm_stage in the future. For now we mirror the Ship default's
        # fsm_stage when the slug shadows a default; clones inherit it
        # from the named base; brand-new roles get None.
        default = agent_roles_svc.get_default(slug)
        if default is not None:
            fsm_stage = default.fsm_stage
        elif row.base_role_slug:
            base = agent_roles_svc.get_default(row.base_role_slug)
            fsm_stage = base.fsm_stage if base is not None else None
        else:
            fsm_stage = None
        return AgentRoleResolvedOut(
            slug=row.slug,
            name=row.name,
            prompt=row.prompt,
            fsm_stage=fsm_stage,
            source="workspace",
        )

    default = agent_roles_svc.get_default(slug)
    if default is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"agent role not found: {slug}",
        )
    return AgentRoleResolvedOut(
        slug=default.slug,
        name=default.name,
        prompt=default.prompt,
        fsm_stage=default.fsm_stage,
        source="ship_default",
    )
