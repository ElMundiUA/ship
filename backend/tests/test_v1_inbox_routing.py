"""CRUD tests for ``/v1/workspaces/{ws}/inbox/routing`` (RFC-0010 P2-05).

Covers the 19 acceptance scenarios listed in the ticket: empty list,
admin-only create, per-target_type required-field validation,
cross-field validation (forbidden combos), handle pattern, workspace
scoping on user/group references, dup-handle 409, detail enrichment,
PATCH semantics (full update + partial), DELETE, the
bound/used/orphaned/unbound classifier on ``GET /handles``, the
side-effect-free /preview endpoint, cross-workspace isolation, and
audit-log emission.
"""

from __future__ import annotations

import secrets
import uuid

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


# ---------------------------------------------------------------------------
# Local helpers — copied from sibling test_v1_inbox_groups.py to keep this
# file self-contained (no shared fixture dependency on a sibling module).
# ---------------------------------------------------------------------------


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


async def _create_group(db_session, workspace, key: str, *, name: str | None = None):
    """Insert a :class:`MemberGroup` directly (no HTTP round trip)."""
    from backend.app.db.models.inbox import MemberGroup

    group = MemberGroup(
        workspace_id=workspace.id,
        key=key,
        display_name=name or key.title(),
    )
    db_session.add(group)
    await db_session.flush()
    return group


async def _add_group_member(db_session, group, user):
    from backend.app.db.models.inbox import MemberGroupMember

    db_session.add(MemberGroupMember(group_id=group.id, user_id=user.id))
    await db_session.flush()


async def _create_rule(client, ws_id, raw, **overrides):
    body = {
        "handle": "secops",
        "target_type": "user",
    }
    body.update(overrides)
    return await client.post(
        f"/v1/workspaces/{ws_id}/inbox/routing",
        headers=_auth(raw),
        json=body,
    )


# ---------------------------------------------------------------------------
# 1. Empty state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_routing_empty_initially(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/routing", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 2. Create (user target) admin-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_target_user_succeeds(
    v1_client, seed_workspace, db_session
) -> None:
    """Owner succeeds (201); viewer + member rejected (403)."""
    owner, owner_raw, ws = seed_workspace

    ok = await _create_rule(
        v1_client, ws.id, owner_raw,
        handle="secops", target_type="user", target_user_id=str(owner.id),
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["handle"] == "secops"
    assert body["target_type"] == "user"
    assert body["target_user_id"] == str(owner.id)
    assert body["target_group_id"] is None
    assert body["target_strategy"] is None
    assert body["assignment_strategy"] is None
    assert body["is_enabled"] is True

    _, viewer_raw = await _mint_role(db_session, ws, "viewer")
    blocked_v = await _create_rule(
        v1_client, ws.id, viewer_raw,
        handle="other_handle", target_type="user", target_user_id=str(owner.id),
    )
    assert blocked_v.status_code == 403, blocked_v.text

    _, member_raw = await _mint_role(db_session, ws, "member")
    blocked_m = await _create_rule(
        v1_client, ws.id, member_raw,
        handle="another_handle", target_type="user", target_user_id=str(owner.id),
    )
    assert blocked_m.status_code == 403, blocked_m.text


# ---------------------------------------------------------------------------
# 3. Group target requires target_group_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_target_group_requires_group_id(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    res = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops", target_type="group",
    )
    assert res.status_code == 422, res.text
    assert "target_group_id" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. Strategy target requires target_strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_target_strategy_requires_assignment_strategy(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    res = await _create_rule(
        v1_client, ws.id, raw,
        handle="code_owner", target_type="strategy",
    )
    assert res.status_code == 422, res.text
    assert "target_strategy" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5. Forbidden combo: target_type=user with group_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_target_user_with_group_id_returns_422(
    v1_client, seed_workspace, db_session
) -> None:
    owner, raw, ws = seed_workspace
    group = await _create_group(db_session, ws, key="secops")

    res = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops",
        target_type="user",
        target_user_id=str(owner.id),
        target_group_id=str(group.id),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 6. Handle pattern validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_handle_pattern_validation(
    v1_client, seed_workspace
) -> None:
    owner, raw, ws = seed_workspace
    bad = await _create_rule(
        v1_client, ws.id, raw,
        handle="Bad Handle",
        target_type="user", target_user_id=str(owner.id),
    )
    assert bad.status_code == 422, bad.text

    good = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops_team",
        target_type="user", target_user_id=str(owner.id),
    )
    assert good.status_code == 201, good.text


# ---------------------------------------------------------------------------
# 7. User must be a workspace member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_user_must_be_workspace_member(
    v1_client, seed_workspace, db_session
) -> None:
    from backend.app.db.models.tenancy import User

    _, raw, ws = seed_workspace
    stranger = User(
        email=f"stranger-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Stranger",
    )
    db_session.add(stranger)
    await db_session.flush()

    res = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops",
        target_type="user", target_user_id=str(stranger.id),
    )
    assert res.status_code == 422, res.text
    assert "workspace member" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 8. Group must belong to this workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_group_must_be_workspace_owned(
    v1_client, seed_workspace, db_session
) -> None:
    from backend.app.db.models.tenancy import (
        Org,
        OrgMember,
        Workspace,
        WorkspaceMember,
    )

    user, raw, ws_a = seed_workspace
    other_org = Org(
        slug=f"other-{uuid.uuid4().hex[:8]}", name="Other org", plan="free"
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
    db_session.add(WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner"))
    await db_session.flush()

    foreign_group = await _create_group(db_session, ws_b, key="secops_b")

    res = await _create_rule(
        v1_client, ws_a.id, raw,
        handle="secops", target_type="group", target_group_id=str(foreign_group.id),
    )
    assert res.status_code == 422, res.text
    assert "this workspace" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 9. Duplicate handle returns 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_duplicate_priority_returns_409(
    v1_client, seed_workspace
) -> None:
    """Schema enforces UNIQUE(workspace_id, handle_key); duplicate handle = 409.

    Note: the planning doc proposed a `priority` column for tie-breaking
    but the shipped migration (``0031_inbox_v1``) collapsed it to a
    single rule per handle. Test name retained for ticket traceability.
    """
    owner, raw, ws = seed_workspace

    first = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops", target_type="user", target_user_id=str(owner.id),
    )
    assert first.status_code == 201, first.text

    dup = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops", target_type="user", target_user_id=str(owner.id),
    )
    assert dup.status_code == 409, dup.text
    assert "handle" in dup.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 10. Detail resolves user email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rule_detail_resolves_target_user_email(
    v1_client, seed_workspace
) -> None:
    owner, raw, ws = seed_workspace
    created = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops", target_type="user", target_user_id=str(owner.id),
    )
    rule_id = created.json()["id"]

    detail = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/routing/{rule_id}",
        headers=_auth(raw),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["target_user_id"] == str(owner.id)
    assert body["target_user_email"] == owner.email
    assert body["target_group_key"] is None
    assert body["target_group_name"] is None


# ---------------------------------------------------------------------------
# 11. Detail resolves group key + name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rule_detail_resolves_target_group_key_and_name(
    v1_client, seed_workspace, db_session
) -> None:
    _, raw, ws = seed_workspace
    group = await _create_group(db_session, ws, key="secops", name="Security Operations")

    created = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops",
        target_type="group",
        target_group_id=str(group.id),
        assignment_strategy="round_robin",
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    detail = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/routing/{rule_id}",
        headers=_auth(raw),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["target_type"] == "group"
    assert body["target_group_id"] == str(group.id)
    assert body["target_group_key"] == "secops"
    assert body["target_group_name"] == "Security Operations"
    assert body["target_user_id"] is None
    assert body["target_user_email"] is None
    assert body["assignment_strategy"] == "round_robin"


# ---------------------------------------------------------------------------
# 12. PATCH changes target (priority not in schema; assignment_strategy stands in)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rule_changes_priority_and_target(
    v1_client, seed_workspace, db_session
) -> None:
    """Schema has no `priority` column; we exercise the analogous
    "change strategy + target" PATCH path that admins use for the
    same operational outcome (re-pointing a handle and toggling
    its dispatch knob).
    """
    owner, raw, ws = seed_workspace
    group = await _create_group(db_session, ws, key="secops")

    created = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops", target_type="user", target_user_id=str(owner.id),
    )
    rule_id = created.json()["id"]

    patched = await v1_client.patch(
        f"/v1/workspaces/{ws.id}/inbox/routing/{rule_id}",
        headers=_auth(raw),
        json={
            "target_type": "group",
            "target_group_id": str(group.id),
            "assignment_strategy": "round_robin",
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["target_type"] == "group"
    assert body["target_group_id"] == str(group.id)
    assert body["target_user_id"] is None
    assert body["assignment_strategy"] == "round_robin"


# ---------------------------------------------------------------------------
# 13. PATCH partial keeps other fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rule_partial_payload_keeps_other_fields(
    v1_client, seed_workspace, db_session
) -> None:
    _, raw, ws = seed_workspace
    group = await _create_group(db_session, ws, key="secops")

    created = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops",
        target_type="group",
        target_group_id=str(group.id),
        assignment_strategy="round_robin",
        strategy_config={"max_per_day": 5},
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    # Only flip is_enabled — every other field must survive untouched.
    patched = await v1_client.patch(
        f"/v1/workspaces/{ws.id}/inbox/routing/{rule_id}",
        headers=_auth(raw),
        json={"is_enabled": False},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["is_enabled"] is False
    assert body["target_type"] == "group"
    assert body["target_group_id"] == str(group.id)
    assert body["assignment_strategy"] == "round_robin"
    assert body["strategy_config"] == {"max_per_day": 5}
    assert body["handle"] == "secops"


# ---------------------------------------------------------------------------
# 14. DELETE returns 204
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_rule_returns_204(v1_client, seed_workspace) -> None:
    owner, raw, ws = seed_workspace
    created = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops", target_type="user", target_user_id=str(owner.id),
    )
    rule_id = created.json()["id"]

    deleted = await v1_client.delete(
        f"/v1/workspaces/{ws.id}/inbox/routing/{rule_id}",
        headers=_auth(raw),
    )
    assert deleted.status_code == 204, deleted.text

    fetched = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/routing/{rule_id}",
        headers=_auth(raw),
    )
    assert fetched.status_code == 404


# ---------------------------------------------------------------------------
# 15. /handles classifier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handles_endpoint_classifies_bound_used_orphaned_unbound(
    v1_client, seed_workspace
) -> None:
    """Bind ``code_owner`` (used by catalog → bound + not orphaned).
    Bind ``made_up_handle`` (not used → orphaned).
    Don't bind ``incident_commander`` (used by flow_incident → unbound).
    """
    owner, raw, ws = seed_workspace

    # ``code_owner`` IS referenced by the catalog (flow_pr profile).
    res1 = await _create_rule(
        v1_client, ws.id, raw,
        handle="code_owner", target_type="user", target_user_id=str(owner.id),
    )
    assert res1.status_code == 201, res1.text

    res2 = await _create_rule(
        v1_client, ws.id, raw,
        handle="made_up_handle", target_type="user", target_user_id=str(owner.id),
    )
    assert res2.status_code == 201, res2.text

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/routing/handles",
        headers=_auth(raw),
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert "code_owner" in body["bound_handles"]
    assert "made_up_handle" in body["bound_handles"]
    assert "code_owner" in body["used_handles"]
    assert "incident_commander" in body["used_handles"]
    # made_up_handle is bound but not in catalog → orphaned
    assert "made_up_handle" in body["orphaned_handles"]
    assert "code_owner" not in body["orphaned_handles"]
    # incident_commander used by flow_incident profile but no rule → unbound
    assert "incident_commander" in body["unbound_handles"]
    assert "code_owner" not in body["unbound_handles"]


# ---------------------------------------------------------------------------
# 16. /preview is side-effect free
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_endpoint_returns_resolved_user_without_persisting(
    v1_client, seed_workspace, db_session
) -> None:
    """Round-robin dispatch normally writes group_assignment_state.
    The preview path must NOT persist that side-effect.
    """
    from sqlalchemy import select

    from backend.app.db.models.inbox import GroupAssignmentState

    owner, raw, ws = seed_workspace
    member_user, _ = await _mint_role(db_session, ws, "member")
    group = await _create_group(db_session, ws, key="secops")
    await _add_group_member(db_session, group, owner)
    await _add_group_member(db_session, group, member_user)

    created = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops",
        target_type="group",
        target_group_id=str(group.id),
        assignment_strategy="round_robin",
    )
    assert created.status_code == 201, created.text

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/routing/preview",
        headers=_auth(raw),
        json={"handle": "secops"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["handle"] == "secops"
    assert body["resolved_user_id"] is not None
    assert body["resolved_user_email"] in {owner.email, member_user.email}
    assert body["intake_handle"] == "secops"
    assert body["intake_reason"].startswith("group:secops:round_robin")

    # Critical: the savepoint rollback in the route must have wiped
    # any group_assignment_state UPSERT the resolver attempted.
    state_rows = (
        await db_session.execute(
            select(GroupAssignmentState).where(
                GroupAssignmentState.group_id == group.id
            )
        )
    ).scalars().all()
    assert state_rows == [], (
        "preview must be side-effect free; "
        "group_assignment_state should not have been persisted"
    )


# ---------------------------------------------------------------------------
# 17. /preview unresolved handle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_endpoint_unresolved_handle_returns_intake_reason_unresolved(
    v1_client, seed_workspace
) -> None:
    """A handle with no matching rule surfaces a non-``rule:*`` reason.

    The resolver's contract for an unmatched handle is to walk the
    built-in chain (``workspace_admin → workspace_owner``) before
    declaring the request ``unresolved``. The seed fixture creates
    the caller as workspace owner, so for a totally-unknown handle
    that has no rule and no built-in handler the resolver falls
    through to ``fallback:workspace_owner``. ``unresolved`` is the
    *terminal* reason only when the workspace has neither admin
    nor owner — which the seed fixture, by construction, does have.

    Either way, the assertion that matters is "no rule matched, so
    the reason is NOT ``rule:*`` and the handle echoes back".
    """
    _, raw, ws = seed_workspace

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/routing/preview",
        headers=_auth(raw),
        json={"handle": "totally_unknown_handle"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["intake_handle"] == "totally_unknown_handle"
    assert not body["intake_reason"].startswith("rule:")
    assert body["intake_reason"] in {
        "unresolved",
        "fallback:workspace_admin",
        "fallback:workspace_owner",
    }


# ---------------------------------------------------------------------------
# 18. Workspace isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_isolation_on_get_and_patch(
    v1_client, seed_workspace, db_session
) -> None:
    from backend.app.db.models.tenancy import (
        Org,
        OrgMember,
        Workspace,
        WorkspaceMember,
    )

    owner, raw, ws_a = seed_workspace
    created = await _create_rule(
        v1_client, ws_a.id, raw,
        handle="secops", target_type="user", target_user_id=str(owner.id),
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]

    other_org = Org(
        slug=f"other-{uuid.uuid4().hex[:8]}", name="Other org", plan="free"
    )
    db_session.add(other_org)
    await db_session.flush()
    db_session.add(OrgMember(org_id=other_org.id, user_id=owner.id, role="org_owner"))
    ws_b = Workspace(
        org_id=other_org.id,
        slug=f"ws-b-{uuid.uuid4().hex[:6]}",
        name="Workspace B",
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(WorkspaceMember(workspace_id=ws_b.id, user_id=owner.id, role="owner"))
    await db_session.flush()

    listed_b = await v1_client.get(
        f"/v1/workspaces/{ws_b.id}/inbox/routing", headers=_auth(raw)
    )
    assert listed_b.status_code == 200
    assert listed_b.json() == []

    direct = await v1_client.get(
        f"/v1/workspaces/{ws_b.id}/inbox/routing/{rule_id}",
        headers=_auth(raw),
    )
    assert direct.status_code == 404, direct.text

    patched = await v1_client.patch(
        f"/v1/workspaces/{ws_b.id}/inbox/routing/{rule_id}",
        headers=_auth(raw),
        json={"is_enabled": False},
    )
    assert patched.status_code == 404, patched.text


# ---------------------------------------------------------------------------
# 19. Audit log on create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_recorded_on_create(
    v1_client, seed_workspace, db_session
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog

    owner, raw, ws = seed_workspace
    created = await _create_rule(
        v1_client, ws.id, raw,
        handle="secops", target_type="user", target_user_id=str(owner.id),
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]

    rows = (
        await db_session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == ws.id,
                AuditLog.action == "inbox_routing.create",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    audit = rows[0]
    assert audit.actor_user_id == owner.id
    assert audit.target_kind == "inbox_routing_rule"
    assert audit.target_id == rule_id
    assert audit.payload["handle"] == "secops"
    assert audit.payload["target_type"] == "user"
    assert audit.payload["target_value"] == str(owner.id)
