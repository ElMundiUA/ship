"""Team invites API (B7).

Surface area:

- ``POST   /v1/workspaces/{ws}/invites`` — admin creates 1..N invites.
  The response returns the freshly-minted token string **once** — we
  never echo it again; the admin's job is to forward the accept URL
  out-of-band (Slack/email).
- ``GET    /v1/workspaces/{ws}/invites`` — admin lists pending + past
  invites for the workspace.
- ``DELETE /v1/workspaces/{ws}/invites/{id}`` — admin revokes a
  pending invite (expired / accepted are left alone).
- ``GET    /v1/invites/{token}`` — prospective member peeks at the
  invite before logging in (shows workspace name + role + inviter).
  Unauthenticated but token-bearing; leak-safe because the token is
  one-off and time-boxed.
- ``POST   /v1/invites/{token}/accept`` — authenticated invitee
  accepts and becomes a workspace member. Email match is enforced
  so you can't redeem an invite intended for someone else.

We deliberately don't ship an email sender in the pilot — the admin
bulk-pastes emails, copies the accept-URL once per invite, and
forwards them via their existing tools. A future iteration will add
SMTP + SendGrid.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    _require_membership,
)
from backend.app.core.config import get_settings
from backend.app.db.models.tenancy import (
    AuditLog,
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
)
from backend.app.db.session import get_session


INVITE_DEFAULT_TTL = timedelta(days=7)
VALID_INVITE_ROLES: frozenset[str] = frozenset(
    {"admin", "maintainer", "member", "viewer"}
)


def _hash_token(token: str) -> bytes:
    """SHA-256 the plaintext token once so we can store it safely.

    We don't salt — the token is already 256 bits of entropy.
    """
    return hashlib.sha256(token.encode("utf-8")).digest()


def _build_accept_url(token: str) -> str:
    """Return the console-facing accept URL for ``token``.

    Kept here (not on the frontend) because the admin forwards this
    to invitees and we want it stable across deploys.
    """
    base = get_settings().console_url.rstrip("/")
    return f"{base}/invite?token={token}"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class InviteCreate(BaseModel):
    """One invite in the bulk-create payload."""

    email: EmailStr
    role: Literal["admin", "maintainer", "member", "viewer"] = "member"


class InviteBulkCreateIn(BaseModel):
    """Bulk create — either a list of structured rows, or a newline/comma
    separated ``emails`` blob that we parse server-side so the UI can stay
    a humble ``<textarea>``.
    """

    invites: list[InviteCreate] = Field(default_factory=list)
    emails: str | None = None
    default_role: Literal["admin", "maintainer", "member", "viewer"] = "member"
    ttl_days: int = Field(default=7, ge=1, le=60)


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    invited_by_email: str | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    # Present only on the ``POST`` response — the one place we ever
    # emit plaintext tokens. ``None`` everywhere else.
    token: str | None = None
    accept_url: str | None = None


class InvitePeekOut(BaseModel):
    email: str
    role: str
    workspace_id: uuid.UUID
    workspace_name: str
    invited_by_email: str | None
    expires_at: datetime


class InviteAcceptOut(BaseModel):
    workspace_id: uuid.UUID
    role: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_EMAIL_SPLIT_RE = re.compile(r"[\s,;]+")


def _parse_bulk_emails(blob: str) -> list[str]:
    return [chunk.strip() for chunk in _EMAIL_SPLIT_RE.split(blob) if chunk.strip()]


def _row_to_out(row: WorkspaceInvite, inviter_email: str | None) -> InviteOut:
    return InviteOut(
        id=row.id,
        email=row.email,
        role=row.role,
        invited_by_email=inviter_email,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


async def _inviter_emails(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    unique = [uid for uid in {*user_ids} if uid is not None]
    if not unique:
        return {}
    rows = (
        await session.execute(select(User).where(User.id.in_(unique)))
    ).scalars().all()
    return {u.id: u.email for u in rows}


# ---------------------------------------------------------------------------
# Admin-scoped: list / create / revoke
# ---------------------------------------------------------------------------


workspace_router = APIRouter(
    prefix="/workspaces/{workspace_id}/invites", tags=["invites"]
)


@workspace_router.get("", response_model=list[InviteOut])
async def list_invites(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[InviteOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    rows = (
        await session.execute(
            select(WorkspaceInvite)
            .where(WorkspaceInvite.workspace_id == workspace_id)
            .order_by(WorkspaceInvite.created_at.desc())
        )
    ).scalars().all()
    inviter_map = await _inviter_emails(
        session, [r.invited_by_user_id for r in rows if r.invited_by_user_id]
    )
    return [
        _row_to_out(r, inviter_map.get(r.invited_by_user_id) if r.invited_by_user_id else None)
        for r in rows
    ]


@workspace_router.post(
    "", response_model=list[InviteOut], status_code=status.HTTP_201_CREATED
)
async def create_invites(
    workspace_id: uuid.UUID,
    payload: InviteBulkCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[InviteOut]:
    """Bulk create invites.

    Accepts either a structured ``invites: [{email, role}]`` list or a
    free-form ``emails`` blob (split on whitespace / commas / semicolons)
    with a shared ``default_role``. Duplicates within the same request
    and against already-pending invites for the same email get
    collapsed — the endpoint is idempotent per (workspace, email).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    # Compose the effective list.
    structured: list[InviteCreate] = list(payload.invites)
    if payload.emails:
        for email in _parse_bulk_emails(payload.emails):
            structured.append(
                InviteCreate(email=email, role=payload.default_role)
            )
    if not structured:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No invite rows supplied.",
        )

    # Collapse duplicates within the request, keeping the strongest role.
    role_rank = {"viewer": 0, "member": 1, "maintainer": 2, "admin": 3}
    effective: dict[str, str] = {}
    for row in structured:
        email = row.email.lower()
        prev = effective.get(email)
        if prev is None or role_rank.get(row.role, 0) > role_rank.get(prev, 0):
            effective[email] = row.role

    # Look up already-pending invites so we can reuse rows instead of
    # colliding on the unique (workspace, email) pattern — strictly a
    # convention today; we DB-enforce uniqueness on ``token_hash`` only.
    existing_stmt = select(WorkspaceInvite).where(
        WorkspaceInvite.workspace_id == workspace_id,
        WorkspaceInvite.accepted_at.is_(None),
        WorkspaceInvite.revoked_at.is_(None),
    )
    existing = (await session.execute(existing_stmt)).scalars().all()
    existing_by_email: dict[str, WorkspaceInvite] = {
        row.email.lower(): row for row in existing
    }

    created: list[tuple[WorkspaceInvite, str]] = []  # (row, plaintext_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=payload.ttl_days)
    for email, role in effective.items():
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        row = existing_by_email.get(email)
        if row is None:
            row = WorkspaceInvite(
                workspace_id=workspace_id,
                email=email,
                role=role,
                token_hash=token_hash,
                invited_by_user_id=auth.user.id,
                expires_at=expires_at,
            )
            session.add(row)
        else:
            # Refresh the token and extend the TTL; keep the stored
            # role in sync with the new request so the admin can
            # bump a pending viewer → maintainer without a revoke.
            row.token_hash = token_hash
            row.role = role
            row.expires_at = expires_at
            row.invited_by_user_id = auth.user.id
        created.append((row, token))

    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover — token collision is astronomically unlikely
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Token collision while minting invites; retry.",
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="invite.create",
            target_kind="workspace",
            target_id=str(workspace_id),
            payload={
                "count": len(created),
                "emails": [row.email for row, _ in created],
            },
        )
    )
    await session.flush()

    out: list[InviteOut] = []
    for row, token in created:
        entry = _row_to_out(row, auth.user.email)
        entry.token = token
        entry.accept_url = _build_accept_url(token)
        out.append(entry)
    return out


@workspace_router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    workspace_id: uuid.UUID,
    invite_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = (
        await session.execute(
            select(WorkspaceInvite).where(
                WorkspaceInvite.id == invite_id,
                WorkspaceInvite.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found."
        )
    if row.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invite was already accepted — remove the member instead.",
        )
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="invite.revoke",
                target_kind="workspace_invite",
                target_id=str(invite_id),
                payload={"email": row.email},
            )
        )
        await session.flush()


# ---------------------------------------------------------------------------
# Invitee-scoped: peek / accept
# ---------------------------------------------------------------------------


invite_router = APIRouter(prefix="/invites", tags=["invites"])


async def _lookup_invite(
    session: AsyncSession, token: str
) -> WorkspaceInvite:
    """Resolve a plaintext token into a live invite row (or 404/410)."""
    token_hash = _hash_token(token)
    row = (
        await session.execute(
            select(WorkspaceInvite).where(
                WorkspaceInvite.token_hash == token_hash
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found."
        )
    if row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Invite was revoked."
        )
    if row.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Invite was already accepted."
        )
    now = datetime.now(timezone.utc)
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Invite expired."
        )
    return row


@invite_router.get("/{token}", response_model=InvitePeekOut)
async def peek_invite(
    token: str,
    session: AsyncSession = Depends(get_session),
) -> InvitePeekOut:
    """Read-only peek — called before the invitee logs in so the
    console can show "Join ACME as Maintainer — expires Wed" copy
    without forcing an auth round-trip first.

    Returns ``410 Gone`` for revoked / accepted / expired invites so
    the UI can distinguish "bad link" from "no workspace".
    """
    row = await _lookup_invite(session, token)
    workspace = (
        await session.execute(
            select(Workspace).where(Workspace.id == row.workspace_id)
        )
    ).scalars().one()
    inviter_email: str | None = None
    if row.invited_by_user_id:
        user = (
            await session.execute(
                select(User).where(User.id == row.invited_by_user_id)
            )
        ).scalars().first()
        if user is not None:
            inviter_email = user.email
    return InvitePeekOut(
        email=row.email,
        role=row.role,
        workspace_id=row.workspace_id,
        workspace_name=workspace.name,
        invited_by_email=inviter_email,
        expires_at=row.expires_at,
    )


@invite_router.post("/{token}/accept", response_model=InviteAcceptOut)
async def accept_invite(
    token: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InviteAcceptOut:
    """Redeem ``token`` as the currently-authenticated user.

    The invitee's session email must match the one the invite was
    issued for (case-insensitive). Matches prevent a hijack where a
    forwarded link gets redeemed by someone else who happens to
    share an inbox with the intended invitee.

    Idempotency: if the user is already a member of the workspace,
    we swap the invite into ``accepted_at`` without touching the
    existing membership row. If the existing role is weaker than
    the invited role, we promote — matches the bulk-create
    "strongest wins" rule.
    """
    row = await _lookup_invite(session, token)
    caller_email = auth.user.email.strip().lower()
    invite_email = row.email.strip().lower()
    if caller_email != invite_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This invite was issued to {row.email}; you are signed in"
                f" as {auth.user.email}. Sign in with the invited account"
                f" or ask the admin for a fresh invite."
            ),
        )

    membership = (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == row.workspace_id,
                WorkspaceMember.user_id == auth.user.id,
            )
        )
    ).scalars().first()

    role_rank = {"viewer": 0, "member": 1, "maintainer": 2, "admin": 3, "owner": 4}
    if membership is None:
        membership = WorkspaceMember(
            workspace_id=row.workspace_id,
            user_id=auth.user.id,
            role=row.role,
        )
        session.add(membership)
    else:
        if role_rank.get(row.role, 0) > role_rank.get(membership.role, 0):
            membership.role = row.role

    now = datetime.now(timezone.utc)
    row.accepted_at = now
    row.accepted_by_user_id = auth.user.id
    session.add(
        AuditLog(
            workspace_id=row.workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="invite.accept",
            target_kind="workspace_invite",
            target_id=str(row.id),
            payload={"email": row.email, "role": row.role},
        )
    )
    await session.flush()
    return InviteAcceptOut(workspace_id=row.workspace_id, role=membership.role)


__all__ = [
    "invite_router",
    "workspace_router",
    "VALID_INVITE_ROLES",
    "INVITE_DEFAULT_TTL",
]
