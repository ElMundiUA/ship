"""Team invites (B7) — API contract.

Covers the admin create/list/revoke loop and the invitee
peek/accept loop, including edge cases: duplicate emails within a
single bulk request collapse to one row; expired/revoked/accepted
invites return 410; accepting as the wrong email returns 403.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def invited_user(db_session):
    """Second user who will redeem an invite."""
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import ApiToken, User

    user = User(email="invitee@example.com", display_name="Invitee")
    db_session.add(user)
    await db_session.flush()

    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=user.id,
            name="invitee-pat",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=["workspace:read", "workspace:write"],
        )
    )
    await db_session.flush()
    return user, raw


@pytest.mark.asyncio
async def test_bulk_create_invite_returns_tokens_once(
    v1_client, db_session, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/invites",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "emails": "alice@acme.dev, bob@acme.dev\nalice@acme.dev",
            "default_role": "member",
            "ttl_days": 7,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # Duplicate ``alice@acme.dev`` should collapse to one row.
    emails = {row["email"] for row in body}
    assert emails == {"alice@acme.dev", "bob@acme.dev"}
    assert all(row["token"] for row in body)
    assert all(row["accept_url"] for row in body)

    # Subsequent list call must NOT leak tokens.
    list_response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/invites",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert list_response.status_code == 200
    for row in list_response.json():
        assert row["token"] is None
        assert row["accept_url"] is None


@pytest.mark.asyncio
async def test_peek_returns_workspace_info(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/invites",
        headers={"Authorization": f"Bearer {raw}"},
        json={"emails": "peek@acme.dev"},
    )
    token = create.json()[0]["token"]

    peek = await v1_client.get(f"/v1/invites/{token}")
    assert peek.status_code == 200
    body = peek.json()
    assert body["email"] == "peek@acme.dev"
    assert body["workspace_id"] == str(workspace.id)
    assert body["workspace_name"]


@pytest.mark.asyncio
async def test_peek_rejects_revoked_invite(
    v1_client, db_session, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/invites",
        headers={"Authorization": f"Bearer {raw}"},
        json={"emails": "revoked@acme.dev"},
    )
    assert create.status_code == 201
    row = create.json()[0]
    revoke = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/invites/{row['id']}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert revoke.status_code == 204

    peek = await v1_client.get(f"/v1/invites/{row['token']}")
    assert peek.status_code == 410


@pytest.mark.asyncio
async def test_accept_creates_membership_and_marks_accepted(
    v1_client, db_session, seed_workspace, invited_user
) -> None:
    from backend.app.db.models.tenancy import (
        WorkspaceInvite,
        WorkspaceMember,
    )

    _, admin_raw, workspace = seed_workspace
    invitee, invitee_raw = invited_user
    workspace_id = workspace.id
    invitee_id = invitee.id

    create = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/invites",
        headers={"Authorization": f"Bearer {admin_raw}"},
        json={"invites": [{"email": invitee.email, "role": "maintainer"}]},
    )
    assert create.status_code == 201, create.text
    token = create.json()[0]["token"]
    invite_id = create.json()[0]["id"]

    accept = await v1_client.post(
        f"/v1/invites/{token}/accept",
        headers={"Authorization": f"Bearer {invitee_raw}"},
    )
    assert accept.status_code == 200, accept.text
    assert accept.json()["workspace_id"] == str(workspace_id)
    assert accept.json()["role"] == "maintainer"

    db_session.expire_all()
    membership = (
        await db_session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == invitee_id,
            )
        )
    ).scalars().first()
    assert membership is not None
    assert membership.role == "maintainer"

    row = (
        await db_session.execute(
            select(WorkspaceInvite).where(WorkspaceInvite.id == invite_id)
        )
    ).scalars().one()
    assert row.accepted_at is not None

    # Second accept attempt fails with 410 (already accepted).
    again = await v1_client.post(
        f"/v1/invites/{token}/accept",
        headers={"Authorization": f"Bearer {invitee_raw}"},
    )
    assert again.status_code == 410


@pytest.mark.asyncio
async def test_accept_rejects_mismatched_email(
    v1_client, seed_workspace, invited_user
) -> None:
    _, admin_raw, workspace = seed_workspace
    _, wrong_raw = invited_user

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/invites",
        headers={"Authorization": f"Bearer {admin_raw}"},
        json={"invites": [{"email": "someone-else@acme.dev", "role": "member"}]},
    )
    token = create.json()[0]["token"]

    accept = await v1_client.post(
        f"/v1/invites/{token}/accept",
        headers={"Authorization": f"Bearer {wrong_raw}"},
    )
    assert accept.status_code == 403


@pytest.mark.asyncio
async def test_expired_invite_returns_410(
    v1_client, db_session, seed_workspace, invited_user
) -> None:
    from backend.app.db.models.tenancy import WorkspaceInvite

    _, admin_raw, workspace = seed_workspace
    _, invitee_raw = invited_user

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/invites",
        headers={"Authorization": f"Bearer {admin_raw}"},
        json={"invites": [{"email": "invitee@example.com", "role": "member"}]},
    )
    token = create.json()[0]["token"]
    invite_id = create.json()[0]["id"]

    # Fast-forward expiry.
    row = (
        await db_session.execute(
            select(WorkspaceInvite).where(WorkspaceInvite.id == invite_id)
        )
    ).scalars().one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.flush()

    peek = await v1_client.get(f"/v1/invites/{token}")
    assert peek.status_code == 410
    accept = await v1_client.post(
        f"/v1/invites/{token}/accept",
        headers={"Authorization": f"Bearer {invitee_raw}"},
    )
    assert accept.status_code == 410
