"""Explicit project→repo dispatch routing API.

Surfaces under ``/v1/workspaces/{workspace_id}/repo-routing``:

- ``GET``                         — current default + per-project bindings.
- ``PUT /default``                — set / clear the workspace default repo
  (where unbound / projectless tickets dispatch).
- ``PUT /projects/{pid}``         — bind a Linear project to a repo.
- ``DELETE /projects/{pid}``      — drop a project binding.

Replaces the retired name-heuristic + oldest-activated fallback in
``dispatcher._pick_dispatch_repo``. Writes are admin-only (routing
decides where every agent run lands — high blast radius); reads are
member-level. See model ``WorkspaceRepoRouting`` + migration 0079.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.integrations import (
    WorkspaceRepo,
    WorkspaceRepoRouting,
)
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repo-routing",
    tags=["repo-routing"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RepoRoutingBindingOut(BaseModel):
    project_native_id: str
    repo_id: uuid.UUID
    repo_full_name: str | None = None


class RepoRoutingOut(BaseModel):
    default_repo_id: uuid.UUID | None = None
    default_repo_full_name: str | None = None
    bindings: list[RepoRoutingBindingOut]


class SetDefaultIn(BaseModel):
    # ``None`` clears the default (back to unresolved → clarification).
    repo_id: uuid.UUID | None = None


class BindProjectIn(BaseModel):
    repo_id: uuid.UUID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _activated_repo_or_404(
    session: AsyncSession, workspace_id: uuid.UUID, repo_id: uuid.UUID
) -> WorkspaceRepo:
    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.activated_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="repo not found / not activated in this workspace",
        )
    return repo


async def _build_out(
    session: AsyncSession, workspace_id: uuid.UUID
) -> RepoRoutingOut:
    rows = (
        await session.execute(
            select(WorkspaceRepoRouting, WorkspaceRepo.full_name)
            .join(
                WorkspaceRepo,
                WorkspaceRepo.id == WorkspaceRepoRouting.repo_id,
                isouter=True,
            )
            .where(WorkspaceRepoRouting.workspace_id == workspace_id)
        )
    ).all()
    default_repo_id: uuid.UUID | None = None
    default_name: str | None = None
    bindings: list[RepoRoutingBindingOut] = []
    for routing, full_name in rows:
        if routing.project_native_id is None:
            default_repo_id = routing.repo_id
            default_name = full_name
        else:
            bindings.append(
                RepoRoutingBindingOut(
                    project_native_id=routing.project_native_id,
                    repo_id=routing.repo_id,
                    repo_full_name=full_name,
                )
            )
    bindings.sort(key=lambda b: b.project_native_id)
    return RepoRoutingOut(
        default_repo_id=default_repo_id,
        default_repo_full_name=default_name,
        bindings=bindings,
    )


async def _upsert_row(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_native_id: str | None,
    repo_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    row = (
        await session.execute(
            select(WorkspaceRepoRouting).where(
                WorkspaceRepoRouting.workspace_id == workspace_id,
                (
                    WorkspaceRepoRouting.project_native_id.is_(None)
                    if project_native_id is None
                    else WorkspaceRepoRouting.project_native_id
                    == project_native_id
                ),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(
            WorkspaceRepoRouting(
                workspace_id=workspace_id,
                project_native_id=project_native_id,
                repo_id=repo_id,
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
            )
        )
    else:
        row.repo_id = repo_id
        row.updated_by_user_id = user_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=RepoRoutingOut)
async def get_repo_routing(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RepoRoutingOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    return await _build_out(session, workspace_id)


@router.put("/default", response_model=RepoRoutingOut)
async def set_default_repo(
    workspace_id: uuid.UUID,
    body: SetDefaultIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RepoRoutingOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    if body.repo_id is None:
        # Clear the default.
        existing = (
            await session.execute(
                select(WorkspaceRepoRouting).where(
                    WorkspaceRepoRouting.workspace_id == workspace_id,
                    WorkspaceRepoRouting.project_native_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            await session.delete(existing)
    else:
        await _activated_repo_or_404(session, workspace_id, body.repo_id)
        await _upsert_row(
            session,
            workspace_id=workspace_id,
            project_native_id=None,
            repo_id=body.repo_id,
            user_id=auth.user.id,
        )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            action="repo_routing.default_set",
            target_kind="workspace_repo_routing",
            target_id=str(body.repo_id) if body.repo_id else None,
            payload={"repo_id": str(body.repo_id) if body.repo_id else None},
        )
    )
    await session.flush()
    return await _build_out(session, workspace_id)


@router.put("/projects/{project_native_id}", response_model=RepoRoutingOut)
async def bind_project_repo(
    workspace_id: uuid.UUID,
    project_native_id: str,
    body: BindProjectIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RepoRoutingOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    await _activated_repo_or_404(session, workspace_id, body.repo_id)
    await _upsert_row(
        session,
        workspace_id=workspace_id,
        project_native_id=project_native_id,
        repo_id=body.repo_id,
        user_id=auth.user.id,
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            action="repo_routing.project_bound",
            target_kind="project",
            target_id=project_native_id,
            payload={
                "project_native_id": project_native_id,
                "repo_id": str(body.repo_id),
            },
        )
    )
    await session.flush()
    return await _build_out(session, workspace_id)


@router.delete("/projects/{project_native_id}", response_model=RepoRoutingOut)
async def unbind_project_repo(
    workspace_id: uuid.UUID,
    project_native_id: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RepoRoutingOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    existing = (
        await session.execute(
            select(WorkspaceRepoRouting).where(
                WorkspaceRepoRouting.workspace_id == workspace_id,
                WorkspaceRepoRouting.project_native_id == project_native_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                action="repo_routing.project_unbound",
                target_kind="project",
                target_id=project_native_id,
                payload={"project_native_id": project_native_id},
            )
        )
    await session.flush()
    return await _build_out(session, workspace_id)
