"""Member CRUD tests for ``/v1/workspaces/{id}/members`` (Phase 1.4)."""

from __future__ import annotations

import uuid

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_list_members_returns_owner_only_initially(
    v1_client, seed_workspace
) -> None:
    user, raw, ws = seed_workspace
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/members", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    row = body[0]
    assert row["user_id"] == str(user.id)
    assert row["role"] == "owner"
    assert row["email"] == user.email
    assert row["answer_specialist_slugs"] == ["*"]
    # Owner row was created via local-mode signup fixture (no Auth0 sub) but
    # also has no password — for fixture consistency, we treat that as
    # "pending". Real local-signup users have a password_hash set.
    assert "pending" in row


@pytest.mark.asyncio
async def test_invite_creates_pending_user_and_member(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "newbie@helio.dev", "role": "maintainer"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["email"] == "newbie@helio.dev"
    assert body["role"] == "maintainer"
    assert body["pending"] is True

    listed = await v1_client.get(
        f"/v1/workspaces/{ws.id}/members", headers=_auth(raw)
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert {r["email"] for r in rows} == {"newbie@helio.dev"} | {
        r["email"] for r in rows if r["role"] == "owner"
    }


@pytest.mark.asyncio
async def test_invite_existing_email_promotes_to_new_role(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    first = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "promote@helio.dev", "role": "viewer"},
    )
    assert first.status_code == 201
    again = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "promote@helio.dev", "role": "admin"},
    )
    assert again.status_code == 201
    assert again.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_invite_same_role_returns_409(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    a = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "noop@helio.dev", "role": "member"},
    )
    assert a.status_code == 201
    b = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "noop@helio.dev", "role": "member"},
    )
    assert b.status_code == 409


@pytest.mark.asyncio
async def test_patch_answer_specialist_slugs(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    invited = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "lanes@helio.dev", "role": "member"},
    )
    member_id = invited.json()["id"]

    patched = await v1_client.patch(
        f"/v1/workspaces/{ws.id}/members/{member_id}",
        headers=_auth(raw),
        json={"answer_specialist_slugs": ["ba", "qa"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["answer_specialist_slugs"] == ["ba", "qa"]


@pytest.mark.asyncio
async def test_patch_role_changes_member(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    invited = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "patch@helio.dev", "role": "viewer"},
    )
    member_id = invited.json()["id"]

    patched = await v1_client.patch(
        f"/v1/workspaces/{ws.id}/members/{member_id}",
        headers=_auth(raw),
        json={"role": "admin"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_cannot_demote_last_owner(v1_client, seed_workspace) -> None:
    user, raw, ws = seed_workspace
    listed = await v1_client.get(
        f"/v1/workspaces/{ws.id}/members", headers=_auth(raw)
    )
    owner_row = next(r for r in listed.json() if r["role"] == "owner")
    res = await v1_client.patch(
        f"/v1/workspaces/{ws.id}/members/{owner_row['id']}",
        headers=_auth(raw),
        json={"role": "admin"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_remove_member(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    invited = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "kick@helio.dev", "role": "member"},
    )
    member_id = invited.json()["id"]

    deleted = await v1_client.delete(
        f"/v1/workspaces/{ws.id}/members/{member_id}",
        headers=_auth(raw),
    )
    assert deleted.status_code == 204, deleted.text

    listed = await v1_client.get(
        f"/v1/workspaces/{ws.id}/members", headers=_auth(raw)
    )
    assert all(r["id"] != member_id for r in listed.json())


@pytest.mark.asyncio
async def test_cannot_remove_last_owner(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    listed = await v1_client.get(
        f"/v1/workspaces/{ws.id}/members", headers=_auth(raw)
    )
    owner_row = next(r for r in listed.json() if r["role"] == "owner")
    res = await v1_client.delete(
        f"/v1/workspaces/{ws.id}/members/{owner_row['id']}",
        headers=_auth(raw),
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_member_routes_require_admin(
    v1_client, seed_workspace, db_session
) -> None:
    """A viewer/member can list but cannot invite or remove others."""
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        User,
        WorkspaceMember,
    )

    _, owner_raw, ws = seed_workspace
    # Mint a "viewer" with their own token.
    viewer = User(
        email=f"viewer-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Viewer",
    )
    db_session.add(viewer)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=viewer.id, role="viewer")
    )
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=viewer.id,
            name="viewer-token",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=[],
        )
    )
    await db_session.flush()

    listed = await v1_client.get(
        f"/v1/workspaces/{ws.id}/members", headers=_auth(raw)
    )
    assert listed.status_code == 200

    blocked = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "denied@helio.dev", "role": "member"},
    )
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_strangers_get_404(v1_client, seed_user_with_token) -> None:
    _, raw = seed_user_with_token
    res = await v1_client.get(
        f"/v1/workspaces/{uuid.uuid4()}/members", headers=_auth(raw)
    )
    assert res.status_code == 404
