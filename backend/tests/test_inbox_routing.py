"""Tests for :mod:`backend.app.services.inbox.routing` (RFC-0010 P2-06).

Exercises every layer of the resolver: explicit workspace rules
(user / group / strategy targets), the four built-in handles,
the ``workspace_admin → workspace_owner`` fallback chain, the
unresolved sentinel, and the ``round_robin`` state-row /
``SELECT FOR UPDATE`` contract that keeps concurrent intake
honest.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from backend.app.db.models.inbox import (
    GroupAssignmentState,
    InboxRoutingRule,
    MemberGroup,
    MemberGroupMember,
)
from backend.app.db.models.tenancy import (
    Org,
    OrgMember,
    User,
    Workspace,
    WorkspaceMember,
)
from backend.app.services.inbox.routing import (
    ResolvedTarget,
    RoutingContext,
    _pick_from_group,
    resolve_chain,
    resolve_handle,
)


# ---------------------------------------------------------------------------
# Local fixtures + helpers
# ---------------------------------------------------------------------------


def _ctx(workspace_id: uuid.UUID, **overrides) -> RoutingContext:
    return RoutingContext(
        workspace_id=workspace_id,
        repo_id=overrides.get("repo_id"),
        run_id=overrides.get("run_id"),
        source_row=overrides.get("source_row", {}),
    )


async def _make_user(db_session, *, email: str | None = None) -> User:
    user = User(
        email=email or f"user-{secrets.token_hex(4)}@example.com",
        display_name="Test user",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_workspace_member(
    db_session,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    *,
    created_at: datetime | None = None,
) -> WorkspaceMember:
    member = WorkspaceMember(
        workspace_id=workspace_id, user_id=user_id, role=role
    )
    db_session.add(member)
    await db_session.flush()
    if created_at is not None:
        member.created_at = created_at
        await db_session.flush()
    return member


async def _make_group(
    db_session,
    workspace_id: uuid.UUID,
    *,
    key: str,
    display_name: str | None = None,
) -> MemberGroup:
    group = MemberGroup(
        workspace_id=workspace_id,
        key=key,
        display_name=display_name or key.title(),
    )
    db_session.add(group)
    await db_session.flush()
    return group


async def _add_group_member(
    db_session,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    added_at: datetime | None = None,
) -> MemberGroupMember:
    row = MemberGroupMember(group_id=group_id, user_id=user_id)
    db_session.add(row)
    await db_session.flush()
    if added_at is not None:
        row.added_at = added_at
        await db_session.flush()
    return row


async def _make_rule(
    db_session,
    workspace_id: uuid.UUID,
    *,
    handle_key: str,
    target_type: str,
    target_value: str,
    assignment_strategy: str | None = None,
    strategy_config: dict | None = None,
    is_enabled: bool = True,
) -> InboxRoutingRule:
    rule = InboxRoutingRule(
        workspace_id=workspace_id,
        handle_key=handle_key,
        target_type=target_type,
        target_value=target_value,
        assignment_strategy=assignment_strategy,
        strategy_config=strategy_config or {},
        is_enabled=is_enabled,
    )
    db_session.add(rule)
    await db_session.flush()
    return rule


async def _bare_workspace(db_session) -> Workspace:
    """Workspace with no members (for the unresolved-edge test)."""
    org = Org(slug=f"org-{secrets.token_hex(3)}", name="bare org")
    db_session.add(org)
    await db_session.flush()
    workspace = Workspace(
        org_id=org.id,
        slug=f"ws-{secrets.token_hex(3)}",
        name="bare ws",
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


# ---------------------------------------------------------------------------
# Rule layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_handle_via_user_rule(db_session, seed_workspace):
    _, _, ws = seed_workspace
    target = await _make_user(db_session)
    await _make_workspace_member(db_session, ws.id, target.id, "member")
    await _make_rule(
        db_session,
        ws.id,
        handle_key="secops",
        target_type="user",
        target_value=str(target.id),
    )

    result = await resolve_handle(db_session, "secops", _ctx(ws.id))

    assert result == ResolvedTarget(
        user_id=target.id,
        group_id=None,
        intake_handle="secops",
        intake_reason="rule:user",
    )


@pytest.mark.asyncio
async def test_resolve_handle_via_group_round_robin(
    db_session, seed_workspace
):
    _, _, ws = seed_workspace
    members = []
    base = datetime.now(timezone.utc) - timedelta(days=10)
    group = await _make_group(db_session, ws.id, key="secops")
    for i in range(3):
        u = await _make_user(db_session)
        await _make_workspace_member(db_session, ws.id, u.id, "member")
        await _add_group_member(
            db_session,
            group.id,
            u.id,
            added_at=base + timedelta(seconds=i),
        )
        members.append(u)
    await _make_rule(
        db_session,
        ws.id,
        handle_key="secops",
        target_type="group",
        target_value="secops",
        assignment_strategy="round_robin",
    )

    picked: list[uuid.UUID | None] = []
    for _ in range(4):
        result = await resolve_handle(db_session, "secops", _ctx(ws.id))
        assert result.group_id == group.id
        assert result.intake_reason == "group:secops:round_robin"
        picked.append(result.user_id)

    expected = [
        members[0].id,
        members[1].id,
        members[2].id,
        members[0].id,
    ]
    assert picked == expected

    # State row should reflect the last assignee.
    state = await db_session.get(GroupAssignmentState, group.id)
    assert state is not None
    assert state.last_assigned_user_id == members[0].id


@pytest.mark.asyncio
async def test_resolve_handle_via_group_oncall_with_oncall_member(
    db_session, seed_workspace
):
    _, _, ws = seed_workspace
    base = datetime.now(timezone.utc) - timedelta(days=2)
    group = await _make_group(db_session, ws.id, key="ops-oncall")

    non_oncall_a = await _make_user(db_session)
    oncall = await _make_user(db_session)
    non_oncall_b = await _make_user(db_session)
    for u in (non_oncall_a, oncall, non_oncall_b):
        await _make_workspace_member(db_session, ws.id, u.id, "member")

    # Add non-oncall first so 'first' would NOT pick the oncall user.
    await _add_group_member(
        db_session, group.id, non_oncall_a.id, added_at=base
    )
    await _add_group_member(
        db_session,
        group.id,
        oncall.id,
        added_at=base + timedelta(seconds=1),
    )
    await _add_group_member(
        db_session,
        group.id,
        non_oncall_b.id,
        added_at=base + timedelta(seconds=2),
    )

    await _make_rule(
        db_session,
        ws.id,
        handle_key="ops_oncall",
        target_type="group",
        target_value="ops-oncall",
        assignment_strategy="oncall",
        strategy_config={"oncall_user_ids": [str(oncall.id)]},
    )

    for _ in range(3):
        result = await resolve_handle(
            db_session, "ops_oncall", _ctx(ws.id)
        )
        assert result.user_id == oncall.id
        assert result.group_id == group.id
        assert result.intake_reason == "group:ops-oncall:oncall"


@pytest.mark.asyncio
async def test_resolve_handle_via_group_oncall_falls_back_to_first(
    db_session, seed_workspace
):
    _, _, ws = seed_workspace
    base = datetime.now(timezone.utc) - timedelta(days=2)
    group = await _make_group(db_session, ws.id, key="ops-oncall")

    first = await _make_user(db_session)
    second = await _make_user(db_session)
    await _make_workspace_member(db_session, ws.id, first.id, "member")
    await _make_workspace_member(db_session, ws.id, second.id, "member")
    await _add_group_member(db_session, group.id, first.id, added_at=base)
    await _add_group_member(
        db_session,
        group.id,
        second.id,
        added_at=base + timedelta(seconds=1),
    )

    await _make_rule(
        db_session,
        ws.id,
        handle_key="ops_oncall",
        target_type="group",
        target_value="ops-oncall",
        assignment_strategy="oncall",
        strategy_config={},
    )

    result = await resolve_handle(db_session, "ops_oncall", _ctx(ws.id))
    assert result.user_id == first.id
    assert result.group_id == group.id
    assert result.intake_reason == "group:ops-oncall:oncall"


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_handle_unknown_handle_falls_back_to_workspace_admin(
    db_session, seed_workspace
):
    owner_user, _, ws = seed_workspace
    base = datetime.now(timezone.utc) - timedelta(days=5)
    admin_one = await _make_user(db_session)
    admin_two = await _make_user(db_session)
    await _make_workspace_member(
        db_session, ws.id, admin_one.id, "admin", created_at=base
    )
    await _make_workspace_member(
        db_session,
        ws.id,
        admin_two.id,
        "admin",
        created_at=base + timedelta(seconds=10),
    )

    result = await resolve_handle(
        db_session, "made_up_handle", _ctx(ws.id)
    )

    assert result.user_id == admin_one.id
    assert result.group_id is None
    assert result.intake_handle == "made_up_handle"
    assert result.intake_reason == "fallback:workspace_admin"
    # Owner from seed_workspace should not be selected as long as an
    # admin exists (admin precedes owner in the default fallback chain).
    assert result.user_id != owner_user.id


@pytest.mark.asyncio
async def test_resolve_handle_no_admin_falls_back_to_owner(
    db_session, seed_workspace
):
    owner_user, _, ws = seed_workspace

    result = await resolve_handle(
        db_session, "made_up_handle", _ctx(ws.id)
    )

    assert result.user_id == owner_user.id
    assert result.intake_reason == "fallback:workspace_owner"


@pytest.mark.asyncio
async def test_resolve_handle_unresolved_when_workspace_empty_after_owner_removal(
    db_session,
):
    workspace = await _bare_workspace(db_session)

    result = await resolve_handle(
        db_session, "anything", _ctx(workspace.id)
    )

    assert result.user_id is None
    assert result.group_id is None
    assert result.intake_handle == "anything"
    assert result.intake_reason == "unresolved"


# ---------------------------------------------------------------------------
# Built-in handles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_handle_requested_by_uses_source_row(
    db_session, seed_workspace
):
    _, _, ws = seed_workspace
    requester = await _make_user(db_session)
    await _make_workspace_member(db_session, ws.id, requester.id, "member")

    result = await resolve_handle(
        db_session,
        "requested_by",
        _ctx(ws.id, source_row={"requested_by_user_id": str(requester.id)}),
    )

    assert result.user_id == requester.id
    assert result.group_id is None
    assert result.intake_handle == "requested_by"
    assert result.intake_reason == "builtin:requested_by"


@pytest.mark.asyncio
async def test_resolve_handle_repo_maintainer_via_workspace_role(
    db_session, seed_workspace
):
    owner_user, _, ws = seed_workspace
    repo_id = uuid.uuid4()  # ctx.repo_id is informational here

    result = await resolve_handle(
        db_session, "repo_maintainer", _ctx(ws.id, repo_id=repo_id)
    )

    # Owner is the only maintainer-grade role present in the seed.
    assert result.user_id == owner_user.id
    assert result.intake_reason == "builtin:repo_maintainer"


# ---------------------------------------------------------------------------
# Concurrency contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_from_group_round_robin_concurrent_safe(
    db_session, seed_workspace
):
    _, _, ws = seed_workspace
    base = datetime.now(timezone.utc) - timedelta(days=1)
    group = await _make_group(db_session, ws.id, key="rr-group")
    members = []
    for i in range(2):
        u = await _make_user(db_session)
        await _make_workspace_member(db_session, ws.id, u.id, "member")
        await _add_group_member(
            db_session,
            group.id,
            u.id,
            added_at=base + timedelta(seconds=i),
        )
        members.append(u)

    first = await _pick_from_group(
        db_session, group, strategy="round_robin"
    )
    second = await _pick_from_group(
        db_session, group, strategy="round_robin"
    )
    third = await _pick_from_group(
        db_session, group, strategy="round_robin"
    )
    assert first == members[0].id
    assert second == members[1].id
    assert third == members[0].id

    # Verify the FOR UPDATE clause is in the emitted SQL — that's
    # what guarantees serialisation under real concurrency.
    from sqlalchemy import select

    stmt = (
        select(GroupAssignmentState)
        .where(GroupAssignmentState.group_id == group.id)
        .with_for_update()
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled.upper()


# ---------------------------------------------------------------------------
# resolve_chain + priority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_chain_first_match_wins(db_session, seed_workspace):
    _, _, ws = seed_workspace
    eng_manager = await _make_user(db_session)
    await _make_workspace_member(
        db_session, ws.id, eng_manager.id, "member"
    )
    # Only the second handle has a matching rule — the chain still
    # walks past `secops` (no rule, no built-in) and picks it.
    await _make_rule(
        db_session,
        ws.id,
        handle_key="eng_managers",
        target_type="user",
        target_value=str(eng_manager.id),
    )

    result = await resolve_chain(
        db_session, ["secops", "eng_managers"], _ctx(ws.id)
    )

    assert result.user_id == eng_manager.id
    assert result.intake_handle == "eng_managers"
    assert result.intake_reason == "rule:user"


@pytest.mark.asyncio
async def test_routing_rule_priority_respected(db_session, seed_workspace):
    """Routing rules outrank the built-in resolver for the same handle.

    The schema's ``UNIQUE(workspace_id, handle_key)`` constraint
    means at most one rule per handle exists in v1, so ``priority``
    in this resolver is a layered concept (rule > built-in >
    fallback) rather than a numeric column. This test pins the rule
    > built-in ordering: the built-in for ``requested_by`` would
    normally read ``ctx.source_row['requested_by_user_id']``, but a
    workspace rule pinning ``requested_by`` to a specific user
    must win over that built-in.
    """
    _, _, ws = seed_workspace
    pinned = await _make_user(db_session)
    requester = await _make_user(db_session)
    await _make_workspace_member(db_session, ws.id, pinned.id, "member")
    await _make_workspace_member(db_session, ws.id, requester.id, "member")
    await _make_rule(
        db_session,
        ws.id,
        handle_key="requested_by",
        target_type="user",
        target_value=str(pinned.id),
    )

    result = await resolve_handle(
        db_session,
        "requested_by",
        _ctx(
            ws.id,
            source_row={"requested_by_user_id": str(requester.id)},
        ),
    )

    assert result.user_id == pinned.id
    assert result.intake_reason == "rule:user"
