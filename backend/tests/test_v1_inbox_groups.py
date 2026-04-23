"""CRUD tests for ``/v1/workspaces/{ws}/inbox/groups`` (RFC-0010 P2-04).

Covers the 11 acceptance scenarios listed in the ticket: empty state,
admin-only create, key-pattern validation, dup-key 409, member detail
join, strategy patch, cascade delete, stranger-user rejection,
double-add 409, member removal, and cross-workspace isolation.
"""

from __future__ import annotations

import secrets
import uuid

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


async def _mint_role(db_session, workspace, role: str):
    """Create a fresh user + PAT bound to ``workspace`` with ``role``."""
    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        User,
        WorkspaceMember,
    )

    user = User(
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.com",
        display_name=role.title(),
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role)
    )
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=f"{role}-token",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=[],
        )
    )
    await db_session.flush()
    return user, raw


async def _create_group(client, ws_id, raw, **overrides):
    body = {
        "key": "secops",
        "name": "Security Operations",
        "description": "Owns security incidents",
        "assignment_strategy": "round_robin",
    }
    body.update(overrides)
    res = await client.post(
        f"/v1/workspaces/{ws_id}/inbox/groups",
        headers=_auth(raw),
        json=body,
    )
    return res


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_groups_empty_initially(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/groups", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


@pytest.mark.asyncio
async def test_create_group_admin_only(
    v1_client, seed_workspace, db_session
) -> None:
    """Owner succeeds (201); viewer is rejected (403)."""
    _, owner_raw, ws = seed_workspace

    ok = await _create_group(v1_client, ws.id, owner_raw, key="secops")
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["key"] == "secops"
    assert body["name"] == "Security Operations"
    assert body["assignment_strategy"] == "round_robin"
    assert body["member_count"] == 0
    assert body["description"] == "Owns security incidents"

    _, viewer_raw = await _mint_role(db_session, ws, "viewer")
    blocked = await _create_group(
        v1_client, ws.id, viewer_raw, key="eng_managers"
    )
    assert blocked.status_code == 403, blocked.text


@pytest.mark.asyncio
async def test_create_group_validates_key_pattern(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    bad = await _create_group(v1_client, ws.id, raw, key="Key With Spaces")
    assert bad.status_code == 422, bad.text
    good = await _create_group(v1_client, ws.id, raw, key="secops")
    assert good.status_code == 201, good.text


@pytest.mark.asyncio
async def test_create_group_duplicate_key_returns_409(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    first = await _create_group(v1_client, ws.id, raw, key="oncall")
    assert first.status_code == 201
    dup = await _create_group(v1_client, ws.id, raw, key="oncall")
    assert dup.status_code == 409
    assert "key" in dup.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_group_detail_includes_members(
    v1_client, seed_workspace, db_session
) -> None:
    owner, raw, ws = seed_workspace
    created = await _create_group(v1_client, ws.id, raw, key="secops")
    group_id = created.json()["id"]

    # Two extra workspace members to assign into the group.
    user_a, _ = await _mint_role(db_session, ws, "member")
    user_b, _ = await _mint_role(db_session, ws, "maintainer")

    for u in (user_a, user_b):
        added = await v1_client.post(
            f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}/members",
            headers=_auth(raw),
            json={"user_id": str(u.id)},
        )
        assert added.status_code == 201, added.text

    detail = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}",
        headers=_auth(raw),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["member_count"] == 2
    emails = {m["email"] for m in body["members"]}
    assert emails == {user_a.email, user_b.email}
    for m in body["members"]:
        assert m["display_name"]
        assert m["on_call"] is False


@pytest.mark.asyncio
async def test_patch_group_updates_strategy(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    created = await _create_group(
        v1_client, ws.id, raw, key="oncall", assignment_strategy="round_robin"
    )
    group_id = created.json()["id"]

    patched = await v1_client.patch(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}",
        headers=_auth(raw),
        json={"assignment_strategy": "oncall"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["assignment_strategy"] == "oncall"

    # Persistence check: the next GET reflects the flip.
    re_fetched = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}",
        headers=_auth(raw),
    )
    assert re_fetched.json()["assignment_strategy"] == "oncall"
    # Description is preserved across the strategy flip.
    assert re_fetched.json()["description"] == "Owns security incidents"


@pytest.mark.asyncio
async def test_delete_group_cascade_member_rows(
    v1_client, seed_workspace, db_session
) -> None:
    """After DELETE the group is gone and its membership rows go too."""
    from sqlalchemy import select

    from backend.app.db.models.inbox import MemberGroup, MemberGroupMember

    _, raw, ws = seed_workspace
    created = await _create_group(v1_client, ws.id, raw, key="secops")
    group_id = uuid.UUID(created.json()["id"])

    user_a, _ = await _mint_role(db_session, ws, "member")
    added = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}/members",
        headers=_auth(raw),
        json={"user_id": str(user_a.id)},
    )
    assert added.status_code == 201

    deleted = await v1_client.delete(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}",
        headers=_auth(raw),
    )
    assert deleted.status_code == 204, deleted.text

    listed = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/groups", headers=_auth(raw)
    )
    assert listed.json() == []

    # Drill into the DB to confirm the cascade actually fired.
    remaining_groups = (
        await db_session.execute(
            select(MemberGroup).where(MemberGroup.id == group_id)
        )
    ).scalars().all()
    assert remaining_groups == []
    remaining_members = (
        await db_session.execute(
            select(MemberGroupMember).where(
                MemberGroupMember.group_id == group_id
            )
        )
    ).scalars().all()
    assert remaining_members == []


@pytest.mark.asyncio
async def test_add_member_must_be_workspace_member(
    v1_client, seed_workspace, db_session
) -> None:
    """A user that does not belong to the workspace cannot join its groups."""
    from backend.app.db.models.tenancy import User

    _, raw, ws = seed_workspace
    created = await _create_group(v1_client, ws.id, raw, key="secops")
    group_id = created.json()["id"]

    stranger = User(
        email=f"stranger-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Stranger",
    )
    db_session.add(stranger)
    await db_session.flush()

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}/members",
        headers=_auth(raw),
        json={"user_id": str(stranger.id)},
    )
    assert res.status_code == 422, res.text
    assert "workspace member" in res.json()["detail"]


@pytest.mark.asyncio
async def test_add_member_idempotent_on_oncall_flip(
    v1_client, seed_workspace, db_session
) -> None:
    """Re-POST same (group, user) returns 409.

    Decision: ``on_call`` is currently not stored per-row (see
    ``GroupMemberOut`` doc), so a second POST cannot meaningfully
    "flip" it via the same endpoint. We chose to reject the second
    POST with 409 (preserving non-idempotent POST semantics) and
    leave the on-call toggle to a future PATCH endpoint once the
    column lands. Until then, callers DELETE + POST to flip.
    """
    _, raw, ws = seed_workspace
    created = await _create_group(v1_client, ws.id, raw, key="oncall")
    group_id = created.json()["id"]

    user, _ = await _mint_role(db_session, ws, "member")
    first = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}/members",
        headers=_auth(raw),
        json={"user_id": str(user.id), "on_call": False},
    )
    assert first.status_code == 201
    second = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}/members",
        headers=_auth(raw),
        json={"user_id": str(user.id), "on_call": True},
    )
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_remove_member_returns_204_and_drops_row(
    v1_client, seed_workspace, db_session
) -> None:
    _, raw, ws = seed_workspace
    created = await _create_group(v1_client, ws.id, raw, key="secops")
    group_id = created.json()["id"]
    user, _ = await _mint_role(db_session, ws, "member")
    added = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}/members",
        headers=_auth(raw),
        json={"user_id": str(user.id)},
    )
    assert added.status_code == 201

    removed = await v1_client.delete(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}/members/{user.id}",
        headers=_auth(raw),
    )
    assert removed.status_code == 204, removed.text

    detail = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/groups/{group_id}",
        headers=_auth(raw),
    )
    assert detail.json()["member_count"] == 0
    assert detail.json()["members"] == []


@pytest.mark.asyncio
async def test_workspace_isolation(
    v1_client, seed_workspace, db_session
) -> None:
    """A group in workspace A is invisible from workspace B."""
    from backend.app.db.models.tenancy import (
        Org,
        OrgMember,
        Workspace,
        WorkspaceMember,
    )

    user, raw, ws_a = seed_workspace
    created = await _create_group(v1_client, ws_a.id, raw, key="secops")
    assert created.status_code == 201
    group_id = created.json()["id"]

    # Spin up a second workspace under a fresh org so there's no
    # accidental cross-org membership.
    other_org = Org(
        slug=f"other-{uuid.uuid4().hex[:8]}",
        name="Other org",
        plan="free",
    )
    db_session.add(other_org)
    await db_session.flush()
    db_session.add(OrgMember(org_id=other_org.id, user_id=user.id, role="org_owner"))
    ws_b = Workspace(
        org_id=other_org.id,
        slug=f"ws-b-{uuid.uuid4().hex[:6]}",
        name="Workspace B",
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    await db_session.flush()

    # B's listing must not include A's group.
    listed_b = await v1_client.get(
        f"/v1/workspaces/{ws_b.id}/inbox/groups", headers=_auth(raw)
    )
    assert listed_b.status_code == 200
    assert listed_b.json() == []

    # And a direct GET by id from B must 404 (no cross-tenant peek).
    direct = await v1_client.get(
        f"/v1/workspaces/{ws_b.id}/inbox/groups/{group_id}",
        headers=_auth(raw),
    )
    assert direct.status_code == 404, direct.text
