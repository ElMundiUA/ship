"""Platform-level admin invite endpoints (E08 T03, T07).

Admin-only surface for issuing closed-beta invites. Separate from
workspace-scoped invites (in `routes/invites.py`); these grant platform-level
access (first step of onboarding).

Surface area:

- ``POST   /v1/admin/invites`` — issue invite for an email. Returns raw token
  + landing link + other metadata. Token shown raw exactly once.
- ``GET    /v1/admin/invites?status=pending|accepted|revoked|all`` — list
  invites filtered by status. Defaults to `pending`.
- ``POST   /v1/admin/invites/{id}/revoke`` — revoke an invite by setting
  ``revoked_at=now()``.
- ``GET    /v1/public/invites/{token}`` — public endpoint (no auth) to check
  invite status for the landing page. Returns pending/accepted/revoked/expired
  status + email + expires_at. Returns 404 if token unknown.

All admin actions are audit-logged.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import require_platform_admin
from backend.app.core.config import get_settings
from backend.app.db.models.tenancy import AuditLog, PlatformInvite, User
from backend.app.db.session import get_session


router = APIRouter(prefix="/admin/invites", tags=["admin.invites"])
public_router = APIRouter(prefix="/public/invites", tags=["public.invites"])


# --- Schemas ---------------------------------------------------------------


class PlatformInviteRequest(BaseModel):
    """Request to issue a new platform invite."""

    email: EmailStr
    expires_in_days: int = Field(default=14, ge=1, le=365)
    note: str | None = Field(default=None, max_length=1024)


class PlatformInviteOut(BaseModel):
    """Invite record returned to the admin (includes raw token once)."""

    id: uuid.UUID
    email: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_by_user_id: uuid.UUID | None
    revoked_at: datetime | None
    note: str | None
    token: str | None = None  # Raw token, shown exactly once at creation
    link: str | None = None  # Landing link, shown at creation


class PlatformInviteListOut(BaseModel):
    """Invite record for list endpoints (no raw token)."""

    id: uuid.UUID
    email: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_by_user_id: uuid.UUID | None
    revoked_at: datetime | None
    note: str | None


class PlatformInviteStatusOut(BaseModel):
    """Public invite status response (no auth required)."""

    email: str
    expires_at: datetime
    status: Literal["pending", "accepted", "revoked", "expired"]
    accepted_at: datetime | None = None


class BetaCapacityOut(BaseModel):
    """Public closed-beta capacity response."""

    accepted: int
    cap: int
    full: bool


# --- Helpers ---------------------------------------------------------------


def _generate_token() -> str:
    """Generate a 32-byte URL-safe invite token."""
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    """SHA-256 hash of the plaintext token (hex-encoded)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _build_invite_link(token: str) -> str:
    """Return the landing link for invitee."""
    return f"https://app.ship.elmundi.com/invite/{token}"


# --- Endpoints ---------------------------------------------------------------


@router.post(
    "",
    response_model=PlatformInviteOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(
    payload: PlatformInviteRequest,
    admin: User = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> PlatformInviteOut:
    """Issue a new platform invite for an email.

    Generates a fresh token (32-byte URL-safe), hashes it (SHA-256 hex),
    stores the PlatformInvite row, and audit-logs the action.

    The raw token is returned exactly once in the response; it is never
    shown again (only the hash is stored).
    """
    raw_token = _generate_token()
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)

    invite = PlatformInvite(
        email=payload.email.lower(),
        token_hash=token_hash,
        created_by_user_id=admin.id,
        expires_at=expires_at,
        note=payload.note,
    )
    session.add(invite)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="invite for this email+token already exists (token collision; retry)",
        ) from exc

    # Audit log the creation.
    session.add(
        AuditLog(
            actor_user_id=admin.id,
            action="admin.invite.create",
            target_kind="platform_invite",
            target_id=str(invite.id),
            payload={
                "email": invite.email,
                "expires_in_days": payload.expires_in_days,
                "note": payload.note,
            },
        )
    )
    await session.flush()

    return PlatformInviteOut(
        id=invite.id,
        email=invite.email,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        accepted_by_user_id=invite.accepted_by_user_id,
        revoked_at=invite.revoked_at,
        note=invite.note,
        token=raw_token,  # Raw token shown exactly once
        link=_build_invite_link(raw_token),
    )


@router.get("", response_model=list[PlatformInviteListOut])
async def list_invites(
    status: Literal["pending", "accepted", "revoked", "all"] = "pending",
    admin: User = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> list[PlatformInviteListOut]:
    """List platform invites, optionally filtered by status.

    Status values:
    - `pending` (default) — not yet accepted, not revoked, not expired
    - `accepted` — invite was accepted (accepted_at is not null)
    - `revoked` — invite was revoked (revoked_at is not null)
    - `all` — no filter
    """
    stmt = select(PlatformInvite)

    if status == "pending":
        now = datetime.now(timezone.utc)
        stmt = stmt.where(
            and_(
                PlatformInvite.accepted_at.is_(None),
                PlatformInvite.revoked_at.is_(None),
                PlatformInvite.expires_at > now,
            )
        )
    elif status == "accepted":
        stmt = stmt.where(PlatformInvite.accepted_at.isnot(None))
    elif status == "revoked":
        stmt = stmt.where(PlatformInvite.revoked_at.isnot(None))
    # If status == "all", no additional filter

    stmt = stmt.order_by(PlatformInvite.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()

    return [
        PlatformInviteListOut(
            id=row.id,
            email=row.email,
            created_at=row.created_at,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
            accepted_by_user_id=row.accepted_by_user_id,
            revoked_at=row.revoked_at,
            note=row.note,
        )
        for row in rows
    ]


@router.post("/{invite_id}/revoke", response_model=PlatformInviteListOut)
async def revoke_invite(
    invite_id: uuid.UUID,
    admin: User = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> PlatformInviteListOut:
    """Revoke a platform invite by setting revoked_at=now()."""
    stmt = select(PlatformInvite).where(PlatformInvite.id == invite_id)
    invite = (await session.execute(stmt)).scalar_one_or_none()

    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="invite not found",
        )

    if invite.revoked_at is not None:
        # Already revoked; idempotent
        pass
    else:
        invite.revoked_at = datetime.now(timezone.utc)

        # Audit log the revocation.
        session.add(
            AuditLog(
                actor_user_id=admin.id,
                action="admin.invite.revoke",
                target_kind="platform_invite",
                target_id=str(invite.id),
                payload={"email": invite.email},
            )
        )

    await session.flush()

    return PlatformInviteListOut(
        id=invite.id,
        email=invite.email,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        accepted_by_user_id=invite.accepted_by_user_id,
        revoked_at=invite.revoked_at,
        note=invite.note,
    )


# --- Public endpoints (no auth required) ------------------------------------


@public_router.get("/{token}", response_model=PlatformInviteStatusOut)
async def get_invite_status(
    token: str,
    session: AsyncSession = Depends(get_session),
) -> PlatformInviteStatusOut:
    """Public endpoint to check invite status by token (no auth required).

    Returns the invite status (pending/accepted/revoked/expired) without
    exposing sensitive information. The token itself is the capability.

    Returns:
    - 200 with status if token found
    - 404 if token not found
    """
    token_hash = _hash_token(token)
    stmt = select(PlatformInvite).where(PlatformInvite.token_hash == token_hash)
    invite = (await session.execute(stmt)).scalar_one_or_none()

    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="invite not found",
        )

    # Determine status
    if invite.revoked_at is not None:
        invite_status = "revoked"
    elif invite.accepted_at is not None:
        invite_status = "accepted"
    else:
        now = datetime.now(timezone.utc)
        expires_at = invite.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            invite_status = "expired"
        else:
            invite_status = "pending"

    return PlatformInviteStatusOut(
        email=invite.email,
        expires_at=invite.expires_at,
        status=invite_status,
        accepted_at=invite.accepted_at,
    )


beta_capacity_router = APIRouter(prefix="/public", tags=["public"])


@beta_capacity_router.get("/beta-capacity", response_model=BetaCapacityOut)
async def get_beta_capacity(
    session: AsyncSession = Depends(get_session),
) -> BetaCapacityOut:
    """Return closed-beta capacity: accepted invites, cap, and full status.

    No authentication required. Reads the count of platform invites where
    ``accepted_at IS NOT NULL AND revoked_at IS NULL``, compares to the
    configured cap (from env ``CLOSED_BETA_CAP``), and returns the counts.
    """
    settings = get_settings()
    cap = settings.closed_beta_cap

    stmt = select(PlatformInvite).where(
        and_(
            PlatformInvite.accepted_at.isnot(None),
            PlatformInvite.revoked_at.is_(None),
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    accepted = len(rows)

    return BetaCapacityOut(
        accepted=accepted,
        cap=cap,
        full=accepted >= cap,
    )
