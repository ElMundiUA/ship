"""Tests for platform-level admin invite endpoints (E08 T03)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import AuditLog, PlatformInvite, User


@pytest.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """Create a platform admin user."""
    admin = User(
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Admin User",
        is_platform_admin=True,
    )
    db_session.add(admin)
    await db_session.flush()
    return admin


@pytest.fixture
async def regular_user(db_session: AsyncSession) -> User:
    """Create a regular non-admin user."""
    user = User(
        email=f"regular-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Regular User",
        is_platform_admin=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def admin_token(admin_user: User) -> str:
    """Generate a session JWT for the admin user."""
    from backend.app.security.tokens import mint_session_jwt
    from backend.app.core.config import get_settings

    settings = get_settings()
    token, _ = mint_session_jwt(
        user_id=admin_user.id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.jwt_ttl_seconds,
    )
    return token


@pytest.fixture
async def regular_user_token(regular_user: User) -> str:
    """Generate a session JWT for the regular user."""
    from backend.app.security.tokens import mint_session_jwt
    from backend.app.core.config import get_settings

    settings = get_settings()
    token, _ = mint_session_jwt(
        user_id=regular_user.id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.jwt_ttl_seconds,
    )
    return token


@pytest.mark.asyncio
async def test_admin_issues_invite(
    v1_client,
    db_session: AsyncSession,
    admin_user: User,
    admin_token: str,
) -> None:
    """Admin can issue an invite and receives raw token."""
    response = await v1_client.post(
        "/v1/admin/invites",
        json={
            "email": "invitee@example.com",
            "expires_in_days": 14,
            "note": "Test invite",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "invitee@example.com"
    assert data["token"] is not None  # Raw token shown once
    assert data["link"].startswith("https://app.ship.elmundi.com/invite/")
    assert data["note"] == "Test invite"
    assert data["accepted_at"] is None
    assert data["revoked_at"] is None

    # Verify the invite was stored in the DB.
    stmt = select(PlatformInvite).where(PlatformInvite.id == data["id"])
    invite = (await db_session.execute(stmt)).scalar_one_or_none()
    assert invite is not None
    assert invite.email == "invitee@example.com"
    # Token hash is stored, not the raw token.
    assert invite.token_hash != data["token"]

    # Verify audit log entry was created.
    stmt = (
        select(AuditLog)
        .where(AuditLog.action == "admin.invite.create")
        .order_by(AuditLog.created_at.desc())
    )
    audit = (await db_session.execute(stmt)).scalars().first()
    assert audit is not None
    assert audit.target_kind == "platform_invite"
    assert audit.payload["email"] == "invitee@example.com"


@pytest.mark.asyncio
async def test_non_admin_cannot_issue_invite(
    v1_client,
    regular_user: User,
    regular_user_token: str,
) -> None:
    """Non-admin user gets 403 when trying to issue invite."""
    response = await v1_client.post(
        "/v1/admin/invites",
        json={
            "email": "invitee@example.com",
            "expires_in_days": 14,
        },
        headers={"Authorization": f"Bearer {regular_user_token}"},
    )

    assert response.status_code == 403
    assert "platform admin" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_pending_invites(
    v1_client,
    db_session: AsyncSession,
    admin_user: User,
    admin_token: str,
) -> None:
    """Admin can list pending invites (not expired, not accepted, not revoked)."""
    now = datetime.now(timezone.utc)

    # Create a pending invite.
    pending_invite = PlatformInvite(
        email="pending@example.com",
        token_hash="hash1",
        created_by_user_id=admin_user.id,
        expires_at=now + timedelta(days=7),
    )
    db_session.add(pending_invite)

    # Create an accepted invite.
    accepted_invite = PlatformInvite(
        email="accepted@example.com",
        token_hash="hash2",
        created_by_user_id=admin_user.id,
        expires_at=now + timedelta(days=7),
        accepted_at=now - timedelta(hours=1),
    )
    db_session.add(accepted_invite)

    # Create a revoked invite.
    revoked_invite = PlatformInvite(
        email="revoked@example.com",
        token_hash="hash3",
        created_by_user_id=admin_user.id,
        expires_at=now + timedelta(days=7),
        revoked_at=now - timedelta(hours=1),
    )
    db_session.add(revoked_invite)

    # Create an expired invite.
    expired_invite = PlatformInvite(
        email="expired@example.com",
        token_hash="hash4",
        created_by_user_id=admin_user.id,
        expires_at=now - timedelta(hours=1),
    )
    db_session.add(expired_invite)

    await db_session.flush()

    # List pending (default).
    response = await v1_client.get(
        "/v1/admin/invites",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    emails = [invite["email"] for invite in data]
    assert "pending@example.com" in emails
    assert "accepted@example.com" not in emails
    assert "revoked@example.com" not in emails
    assert "expired@example.com" not in emails

    # No raw token in list response.
    for invite in data:
        assert "token" not in invite or invite.get("token") is None


@pytest.mark.asyncio
async def test_revoke_invite(
    v1_client,
    db_session: AsyncSession,
    admin_user: User,
    admin_token: str,
) -> None:
    """Admin can revoke an invite."""
    now = datetime.now(timezone.utc)

    invite = PlatformInvite(
        email="revokee@example.com",
        token_hash="hash_revoke",
        created_by_user_id=admin_user.id,
        expires_at=now + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.flush()
    invite_id = invite.id

    # Revoke it.
    response = await v1_client.post(
        f"/v1/admin/invites/{invite_id}/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["revoked_at"] is not None

    # Verify it was updated in DB.
    stmt = select(PlatformInvite).where(PlatformInvite.id == invite_id)
    updated = (await db_session.execute(stmt)).scalar_one_or_none()
    assert updated is not None
    assert updated.revoked_at is not None

    # Verify audit log.
    stmt = (
        select(AuditLog)
        .where(AuditLog.action == "admin.invite.revoke")
        .order_by(AuditLog.created_at.desc())
    )
    audit = (await db_session.execute(stmt)).scalars().first()
    assert audit is not None
    assert audit.target_kind == "platform_invite"


@pytest.mark.asyncio
async def test_same_email_multiple_invites(
    v1_client,
    db_session: AsyncSession,
    admin_user: User,
    admin_token: str,
) -> None:
    """Same email can be invited multiple times (allowed)."""
    email = "multi@example.com"

    # Issue first invite.
    response1 = await v1_client.post(
        "/v1/admin/invites",
        json={"email": email, "expires_in_days": 7},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response1.status_code == 201
    id1 = response1.json()["id"]

    # Issue second invite for same email.
    response2 = await v1_client.post(
        "/v1/admin/invites",
        json={"email": email, "expires_in_days": 7},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response2.status_code == 201
    id2 = response2.json()["id"]

    # Both should exist in DB.
    stmt = select(PlatformInvite).where(PlatformInvite.email == email)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) >= 2
    assert id1 != id2
