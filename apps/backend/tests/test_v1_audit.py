"""Audit-log read-API tests (RFC-0006 phase 2.5 + D12 filter extensions).

Coverage:
- admin/owner can list, member/viewer cannot
- pagination via descending ``id`` cursor
- ``?action=`` filter accepts both prefix and fully-qualified values
- bogus action filter returns 422 instead of an empty page
- entries minted by the workspace mutation routes appear immediately
- **D12** — ``?actor=``, ``?target_kind=``, ``?since=``, ``?until=``
  filters all narrow the page correctly; invalid values return 422
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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


# ---------------------------------------------------------------------------
# D12 filter extensions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_target_kind_filter_narrows_rows(
    v1_client, seed_workspace
) -> None:
    """``?target_kind=user`` keeps invites, drops workspace-level rows."""
    _, raw, ws = seed_workspace
    invite = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "target-kind@example.com", "role": "member"},
    )
    assert invite.status_code == 201, invite.text
    # Workspace.update emits a workspace-kind audit row; use the same
    # fixture-owner PAT so both rows live in `ws`.
    upd = await v1_client.patch(
        f"/v1/workspaces/{ws.id}",
        headers=_auth(raw),
        json={"name": "Renamed for audit"},
    )
    assert upd.status_code == 200, upd.text

    # Baseline: no filter returns both kinds.
    baseline = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log", headers=_auth(raw)
    )
    assert baseline.status_code == 200
    kinds = {row["target_kind"] for row in baseline.json()["items"]}
    assert {"user", "workspace"} <= kinds

    # Narrow to target_kind=user.
    narrow = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?target_kind=user",
        headers=_auth(raw),
    )
    assert narrow.status_code == 200, narrow.text
    assert {row["target_kind"] for row in narrow.json()["items"]} == {"user"}

    # Bogus target_kind → 422.
    bogus = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?target_kind=banana",
        headers=_auth(raw),
    )
    assert bogus.status_code == 422, bogus.text


@pytest.mark.asyncio
async def test_audit_log_actor_filter_matches_email_substring(
    v1_client, seed_workspace
) -> None:
    """``?actor=`` is a case-insensitive substring on email OR token name."""
    _, raw, ws = seed_workspace
    r = await v1_client.post(
        f"/v1/workspaces/{ws.id}/members",
        headers=_auth(raw),
        json={"email": "actor-filter@example.com", "role": "member"},
    )
    assert r.status_code == 201

    # The owner email is generated by the fixture (`seed-…@example.com`); we
    # don't know the exact local-part, so grep for the domain substring that
    # we *do* know is present, then flip to an impossible one to check the
    # filter actually constrains.
    matches = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?actor=example.com",
        headers=_auth(raw),
    )
    assert matches.status_code == 200, matches.text
    assert len(matches.json()["items"]) >= 1

    empty = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?actor=does-not-exist",
        headers=_auth(raw),
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []


@pytest.mark.asyncio
async def test_audit_log_date_range_filter(
    v1_client, seed_workspace, db_session
) -> None:
    """``?since=`` / ``?until=`` bracket the time window."""
    from backend.app.db.models.tenancy import AuditLog

    _, raw, ws = seed_workspace

    # Plant two rows with known timestamps: one last week, one "now".
    now = datetime.now(timezone.utc)
    last_week = now - timedelta(days=7)
    db_session.add(
        AuditLog(
            workspace_id=ws.id,
            action="workspace.update",
            target_kind="workspace",
            target_id=str(ws.id),
            payload={"marker": "old"},
            created_at=last_week,
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=ws.id,
            action="workspace.update",
            target_kind="workspace",
            target_id=str(ws.id),
            payload={"marker": "new"},
            created_at=now,
        )
    )
    await db_session.flush()

    # since = 3 days ago → only the "new" row survives.
    three_days_ago = (now - timedelta(days=3)).date().isoformat()
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?since={three_days_ago}&action=workspace",
        headers=_auth(raw),
    )
    assert res.status_code == 200, res.text
    markers = [row["payload"].get("marker") for row in res.json()["items"]]
    assert "new" in markers
    assert "old" not in markers

    # until = 3 days ago → only the "old" row survives.
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?until={three_days_ago}&action=workspace",
        headers=_auth(raw),
    )
    assert res.status_code == 200, res.text
    markers = [row["payload"].get("marker") for row in res.json()["items"]]
    assert "old" in markers
    assert "new" not in markers

    # since > until → 422.
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?since=2030-01-01&until=2020-01-01",
        headers=_auth(raw),
    )
    assert res.status_code == 422, res.text

    # Bogus date → 422.
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/audit-log?since=not-a-date",
        headers=_auth(raw),
    )
    assert res.status_code == 422, res.text
