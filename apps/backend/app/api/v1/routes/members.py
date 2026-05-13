"""Workspace member CRUD (RFC-0006, Phase 1.4).

Members are the join table between :class:`User` and :class:`Workspace`. Read
is open to any member (so the operator UI can render the team list to
viewers); writes (invite / change role / remove) require admin or owner.

The "invite" endpoint here is intentionally lightweight: we **pre-create** a
``User`` row keyed by email when the invitee has never logged in before, and
attach a ``WorkspaceMember`` row immediately. When that person eventually
authenticates against Auth0, :func:`backend.app.security.auth0.user_from_claims`
finds the row by email and binds ``external_subject`` — so the invitation
takes effect on first login without us needing the Auth0 Management API in
the hot path. (Operators send the actual invitation email out-of-band, or
wire up a Management-API m2m client later as a Phase-2 enhancement.)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.api.v1.schemas import (
    MemberInviteRequest,
    MemberOut,
    MemberPatch,
)
from backend.app.db.models.tenancy import (
    AuditLog,
    User,
    WorkspaceMember,
)
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/members",
    tags=["members"],
)


def _row_to_out(member: WorkspaceMember, user: User) -> MemberOut:
    return MemberOut(
        id=member.id,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        answer_specialist_slugs=list(member.answer_specialist_slugs or []),
        # "Pending" = pre-invited row that has never been bound to an Auth0
        # subject. Local-mode users skip this state because signup also sets
        # password_hash; we treat either signal as "active".
        pending=user.external_subject is None and user.password_hash is None,
        created_at=member.created_at,
    )


@router.get("", response_model=list[MemberOut])
async def list_members(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[MemberOut]:
    """Return the workspace roster, oldest member first.

    Visible to every member (including viewer) so the team page works for
    everyone — sensitive fields like ``external_subject`` are never serialised.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = (
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [_row_to_out(member, user) for member, user in rows]


@router.post(
    "",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    workspace_id: uuid.UUID,
    payload: MemberInviteRequest,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    """Add (or pre-invite) a user to the workspace.

    Idempotent on ``email`` — re-inviting an existing member updates their
    role instead of failing, so the UI can use the same form for "invite"
    and "promote-by-email" without two endpoints. Returns 409 only when the
    membership already exists at the **same** role (no-op).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    email_norm = payload.email.lower()

    user = (
        await session.execute(select(User).where(User.email == email_norm))
    ).scalar_one_or_none()
    created_user = False
    if user is None:
        # Pre-invite: row will be claimed on first Auth0 login when
        # ``user_from_claims`` matches by email and binds ``external_subject``.
        user = User(
            email=email_norm,
            display_name=payload.display_name,
        )
        session.add(user)
        await session.flush()
        created_user = True

    member_stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user.id,
    )
    membership = (await session.execute(member_stmt)).scalar_one_or_none()
    is_new_membership = membership is None
    if membership is None:
        membership = WorkspaceMember(
            workspace_id=workspace_id, user_id=user.id, role=payload.role
        )
        session.add(membership)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=409, detail="member already exists for this user"
            ) from exc
    else:
        if membership.role == payload.role:
            # Honest no-op rather than a fake 201 — the caller would otherwise
            # think the request did something.
            raise HTTPException(
                status_code=409,
                detail="member already exists with the requested role",
            )
        membership.role = payload.role

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="member.invite" if is_new_membership else "member.role_change",
            target_kind="user",
            target_id=str(user.id),
            payload={
                "email": email_norm,
                "role": payload.role,
                "user_created": created_user,
            },
        )
    )
    await session.flush()
    # Refresh to pick up server-side defaults (created_at).
    await session.refresh(membership)
    await session.refresh(user)
    return _row_to_out(membership, user)


async def _load_member(
    session: AsyncSession, workspace_id: uuid.UUID, member_id: uuid.UUID
) -> tuple[WorkspaceMember, User]:
    stmt = (
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(
            WorkspaceMember.id == member_id,
            WorkspaceMember.workspace_id == workspace_id,
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="member not found")
    return row[0], row[1]


@router.patch("/{member_id}", response_model=MemberOut)
async def update_member(
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    payload: MemberPatch,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    """Change a member's role and/or specialist-lane coverage.

    Refuses to demote the **last owner** — if the workspace has exactly one
    owner and the caller is asking to set them to anything else, we 409 so
    the UI can prompt the operator to promote someone first.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    membership, user = await _load_member(session, workspace_id, member_id)

    current_slugs = list(membership.answer_specialist_slugs or [])
    if payload.role is not None and payload.answer_specialist_slugs is None:
        if membership.role == payload.role:
            return _row_to_out(membership, user)
    if (
        payload.role is None
        and payload.answer_specialist_slugs is not None
        and payload.answer_specialist_slugs == current_slugs
    ):
        return _row_to_out(membership, user)

    if payload.role is not None and membership.role != payload.role:
        if membership.role == "owner" and payload.role != "owner":
            owner_count_stmt = select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == "owner",
            )
            owners = (await session.execute(owner_count_stmt)).scalars().all()
            if len(owners) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="cannot demote the last owner — promote another member first",
                )
        previous = membership.role
        membership.role = payload.role
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="member.role_change",
                target_kind="user",
                target_id=str(user.id),
                payload={"from": previous, "to": payload.role},
            )
        )

    if payload.answer_specialist_slugs is not None:
        membership.answer_specialist_slugs = list(payload.answer_specialist_slugs)
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="member.specialists_update",
                target_kind="user",
                target_id=str(user.id),
                payload={"answer_specialist_slugs": payload.answer_specialist_slugs},
            )
        )

    await session.flush()
    return _row_to_out(membership, user)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove a workspace membership.

    Same last-owner guard as :func:`update_member`: removing the only owner
    would orphan the workspace. The user row itself stays — they may belong
    to other workspaces or other Orgs.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    membership, user = await _load_member(session, workspace_id, member_id)

    if membership.role == "owner":
        owner_count_stmt = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == "owner",
        )
        owners = (await session.execute(owner_count_stmt)).scalars().all()
        if len(owners) <= 1:
            raise HTTPException(
                status_code=409,
                detail="cannot remove the last owner — promote another member first",
            )

    await session.delete(membership)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="member.remove",
            target_kind="user",
            target_id=str(user.id),
            payload={"email": user.email, "role": membership.role},
        )
    )
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
