"""Audit-log read-API tests (RFC-0006 phase 2.5).

Coverage:
- admin/owner can list, member/viewer cannot
- pagination via descending ``id`` cursor
- ``?action=`` filter accepts both prefix and fully-qualified values
- bogus action filter returns 422 instead of an empty page
- entries minted by the workspace mutation routes appear immediately
"""

from __future__ import annotations

import uuid

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_owner_can_list_audit_log_after_invite(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    invite = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "audit-target@example.com", "role": "member"},
    )
    assert invite.status_code == 201, invite.text

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "items" in body
    actions = [row["action"] for row in body["items"]]
    assert "member.invite" in actions

    invite_row = next(r for r in body["items"] if r["action"] == "member.invite")
    assert invite_row["target_kind"] == "user"
    assert invite_row["payload"]["email"] == "audit-target@example.com"
    assert invite_row["actor"]["user_email"] is not None


@pytest.mark.asyncio
async def test_audit_log_pagination_uses_id_cursor(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    # Mint several audit entries via repeated invites.
    for idx in range(5):
        res = await v1_client.post(
            f"/v1/workspaces/{ws.id}/members",
            headers=_auth(raw),
            json={"email": f"u{idx}@example.com", "role": "member"},
        )
        assert res.status_code == 201, res.text

    page1 = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?limit=2", headers=_auth(raw)
    )
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None

    page2 = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?limit=2&before={body1['next_cursor']}",
        headers=_auth(raw),
    )
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    # Strictly older rows.
    assert all(item["id"] < body1["next_cursor"] for item in body2["items"])
    # No overlap.
    overlap = {row["id"] for row in body1["items"]} & {row["id"] for row in body2["items"]}
    assert overlap == set()


@pytest.mark.asyncio
async def test_audit_log_action_filter_accepts_prefix_and_fq(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    invite = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "filter-target@example.com", "role": "member"},
    )
    assert invite.status_code == 201

    prefix = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?action=member", headers=_auth(raw)
    )
    fq = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?action=member.invite",
        headers=_auth(raw),
    )
    assert prefix.status_code == 200
    assert fq.status_code == 200
    prefix_actions = {r["action"] for r in prefix.json()["items"]}
    fq_actions = {r["action"] for r in fq.json()["items"]}
    assert "member.invite" in prefix_actions
    assert fq_actions == {"member.invite"}


@pytest.mark.asyncio
async def test_audit_log_rejects_unknown_action_filter(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?action=bogus.kind",
        headers=_auth(raw),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_member_role_cannot_read_audit_log(
    v1_client, seed_workspace, db_session
) -> None:
    """A plain workspace member must get 403 — audit reveals PAT activity."""
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        User,
        WorkspaceMember,
    )

    _, owner_raw, ws = seed_workspace

    # Create a second user, give them workspace membership at "member" tier,
    # mint a PAT for them.
    member_user = User(
        email=f"member-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Member",
    )
    db_session.add(member_user)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=member_user.id, role="member")
    )
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=member_user.id,
            name="member-pat",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=["workspace:read"],
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log", headers=_auth(raw)
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_audit_log_404_for_non_member(
    v1_client, seed_workspace, db_session
) -> None:
    """Non-members must see a 404 (not 403) so workspace existence is hidden."""
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import ApiToken, User

    _, _owner_raw, ws = seed_workspace

    outsider = User(email=f"outsider-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(outsider)
    await db_session.flush()
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=outsider.id,
            name="outsider-pat",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=[],
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log", headers=_auth(raw)
    )
    assert res.status_code == 404, res.text
