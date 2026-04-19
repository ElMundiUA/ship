"""Workspace CRUD (v1, minimal).

Only the operations needed to satisfy RFC-0006's acceptance criterion are
implemented here: list workspaces the caller belongs to, and create a new one
under the caller's personal org. Member management, project CRUD, and
artifact-repo wiring follow in subsequent slices.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.schemas import (
    WorkspaceCreate,
    WorkspaceDeleteRequest,
    WorkspaceOut,
    WorkspaceUpdate,
)
from backend.app.db.models.tenancy import (
    AuditLog,
    Org,
    OrgMember,
    Workspace,
    WorkspaceMember,
)
from backend.app.db.session import get_session


# Standard role tiers used across workspace-scoped routes.
ROLES_ADMIN = ("owner", "admin")
ROLES_MAINTAIN = ("owner", "admin", "maintainer")
ROLES_READ = ("owner", "admin", "maintainer", "member", "viewer")
VALID_CATALOG_KEYS = frozenset({"global", "workspace", "project"})


async def _require_membership(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    accept: tuple[str, ...],
) -> WorkspaceMember:
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id,
    )
    membership = (await session.execute(stmt)).scalar_one_or_none()
    if membership is None:
        # Hide the workspace from non-members entirely.
        raise HTTPException(status_code=404, detail="workspace not found")
    if membership.role not in accept:
        raise HTTPException(
            status_code=403, detail="insufficient role for this action"
        )
    return membership


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


async def _ensure_personal_org(session: AsyncSession, auth: AuthContext) -> Org:
    """Each user gets one auto-managed personal Org on first workspace create.

    The slug is derived from the user id so it never collides with another
    user's chosen slug, and so the Org is invisible until the user upgrades to
    a real team plan.
    """
    personal_slug = f"personal-{str(auth.user.id).replace('-', '')[:12]}"
    stmt = select(Org).where(Org.slug == personal_slug)
    org = (await session.execute(stmt)).scalar_one_or_none()
    if org is not None:
        return org
    org = Org(slug=personal_slug, name=f"{auth.user.email}'s personal org", plan="free")
    session.add(org)
    await session.flush()
    session.add(OrgMember(org_id=org.id, user_id=auth.user.id, role="org_owner"))
    return org


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceOut]:
    stmt = (
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == auth.user.id)
        .order_by(Workspace.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [WorkspaceOut.model_validate(row) for row in rows]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceOut:
    if payload.org_id is None:
        org = await _ensure_personal_org(session, auth)
    else:
        # Caller must already be an org_admin/org_owner in the requested org.
        org = await session.get(Org, payload.org_id)
        if org is None:
            raise HTTPException(status_code=404, detail="org not found")
        member_stmt = select(OrgMember).where(
            OrgMember.org_id == org.id,
            OrgMember.user_id == auth.user.id,
        )
        membership = (await session.execute(member_stmt)).scalar_one_or_none()
        if membership is None or membership.role not in ("org_owner", "org_admin"):
            raise HTTPException(status_code=403, detail="cannot create workspace in this org")

    workspace = Workspace(org_id=org.id, slug=payload.slug, name=payload.name)
    session.add(workspace)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="workspace slug already exists in this org"
        ) from exc

    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=auth.user.id, role="owner")
    )
    session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="workspace.create",
            target_kind="workspace",
            target_id=str(workspace.id),
            payload={"slug": workspace.slug, "name": workspace.name},
        )
    )
    await session.flush()
    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return WorkspaceOut.model_validate(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceUpdate,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")

    changed: dict[str, object] = {}
    if payload.name is not None:
        workspace.name = payload.name
        changed["name"] = payload.name
    if payload.catalog_sources is not None:
        unknown = set(payload.catalog_sources) - VALID_CATALOG_KEYS
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown catalog source keys: {sorted(unknown)}",
            )
        # Merge with the existing dict so partial updates are friendly.
        merged = {**workspace.catalog_sources, **payload.catalog_sources}
        workspace.catalog_sources = merged
        changed["catalog_sources"] = merged

    if changed:
        session.add(
            AuditLog(
                workspace_id=workspace.id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="workspace.update",
                target_kind="workspace",
                target_id=str(workspace.id),
                payload=changed,
            )
        )
    await session.flush()
    return WorkspaceOut.model_validate(workspace)


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceDeleteRequest,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Permanently delete a workspace.

    Owners only. Cascades drop members, integrations, artifact-repo rows,
    and audit-log entries via ``ON DELETE CASCADE`` / ``SET NULL`` on the
    foreign keys (see :mod:`backend.app.db.models.tenancy`). The remote
    Git repos and any cloned cache directories are **not** touched here —
    operator can rerun the git-sync worker garbage collector after the row
    is gone, or just nuke the cache volume.

    The caller must echo the workspace slug back in
    ``slug_confirmation`` — a deliberate "are you sure?" gate that protects
    against accidental DELETE calls from misconfigured CI scripts.
    """
    await _require_membership(session, workspace_id, auth.user.id, ("owner",))
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    if payload.slug_confirmation != workspace.slug:
        raise HTTPException(
            status_code=409,
            detail="slug_confirmation does not match workspace slug",
        )

    # Audit-log first so the row survives the cascade ON DELETE SET NULL
    # path below — once the workspace is gone, ``workspace_id`` becomes
    # NULL on the audit row but the action + target_id keep the trail.
    session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="workspace.delete",
            target_kind="workspace",
            target_id=str(workspace.id),
            payload={"slug": workspace.slug, "name": workspace.name},
        )
    )
    await session.flush()
    await session.delete(workspace)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
