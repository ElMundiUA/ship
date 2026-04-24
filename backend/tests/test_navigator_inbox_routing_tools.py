"""Navigator Phase-6 inbox-routing tools (Wave A read + Wave B mutate).

- ``inbox_routing_list`` — returns enabled + disabled rules with the
  handle bound/used/orphaned/unbound summary.
- ``inbox_routing_preview`` — side-effect free, resolves the
  configured rule via the same routing service the live path uses.
- ``inbox_routing_upsert`` — admin-gated insert + update (action
  flips between ``created`` and ``updated``); validates the
  ``name → handle_key`` character class.
"""

from __future__ import annotations

import json
import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toolbox(session, *, workspace_id, user_id):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def _make_user(db_session, *, email: str | None = None):
    from backend.app.db.models.tenancy import User

    u = User(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Routing tester",
    )
    db_session.add(u)
    await db_session.flush()
    return u


async def _make_member(db_session, *, workspace_id, user_id, role="member"):
    from backend.app.db.models.tenancy import WorkspaceMember

    db_session.add(
        WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
    )
    await db_session.flush()


async def _make_routing_rule(
    db_session,
    *,
    workspace_id,
    handle_key: str,
    target_type: str = "user",
    target_value: str,
    is_enabled: bool = True,
    assignment_strategy: str | None = None,
    strategy_config: dict | None = None,
):
    from backend.app.db.models.inbox import InboxRoutingRule

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


# ---------------------------------------------------------------------------
# inbox_routing_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_routing_list_includes_enabled_and_disabled(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    target = await _make_user(db_session)
    await _make_member(db_session, workspace_id=ws.id, user_id=target.id)

    on_rule = await _make_routing_rule(
        db_session,
        workspace_id=ws.id,
        handle_key="security_officer",
        target_value=str(target.id),
        is_enabled=True,
    )
    off_rule = await _make_routing_rule(
        db_session,
        workspace_id=ws.id,
        handle_key="legacy_handle",
        target_value=str(target.id),
        is_enabled=False,
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(await box.invoke("inbox_routing_list", {}))
    rule_ids = {r["id"] for r in out["rules"]}
    assert str(on_rule.id) in rule_ids
    assert str(off_rule.id) in rule_ids
    by_id = {r["id"]: r for r in out["rules"]}
    assert by_id[str(on_rule.id)]["enabled"] is True
    assert by_id[str(off_rule.id)]["enabled"] is False
    # Handles summary always present even if catalog is empty.
    assert "handles" in out and "bound" in out["handles"]
    assert "security_officer" in out["handles"]["bound"]


# ---------------------------------------------------------------------------
# inbox_routing_preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_routing_preview_with_explicit_handle(
    db_session, seed_workspace
) -> None:
    """``handle='...'`` skips catalog lookup; admin role NOT required."""
    user, _, ws = seed_workspace
    # Caller is a plain member, not admin — preview is read-only and
    # explicitly admin-not-required per the spec.
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )

    target = await _make_user(db_session, email="routed@example.com")
    await _make_member(db_session, workspace_id=ws.id, user_id=target.id)
    rule = await _make_routing_rule(
        db_session,
        workspace_id=ws.id,
        handle_key="my_owner",
        target_value=str(target.id),
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "inbox_routing_preview",
            {
                "item_type": "improvement",
                "handle": "my_owner",
            },
        )
    )
    assert out["handle"] == "my_owner"
    assert out["matched_rule_id"] == str(rule.id)
    assert out["resolved_owner"]["user_id"] == str(target.id)
    assert out["resolved_owner"]["email"] == "routed@example.com"
    assert out["resolved_owner"]["fallback_used"] is False
    # ``argument`` is the source we recorded (not catalog/profile).
    assert any(a["source"] == "argument" for a in out["attempted_strategies"])


@pytest.mark.asyncio
async def test_inbox_routing_preview_invalid_item_type(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_routing_preview",
            {"item_type": "bogus", "handle": "anything"},
        )
    )
    assert out["error"] == "invalid_item_type"


@pytest.mark.asyncio
async def test_inbox_routing_preview_missing_handle_source(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_routing_preview", {"item_type": "improvement"}
        )
    )
    assert out["error"] == "missing_handle_source"


# ---------------------------------------------------------------------------
# inbox_routing_upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_routing_upsert_create_new_rule(
    db_session, seed_workspace
) -> None:
    """Creating a fresh rule returns ``action='created'`` and persists."""
    from sqlalchemy import select

    from backend.app.db.models.inbox import InboxRoutingRule

    user, _, ws = seed_workspace
    target = await _make_user(db_session)
    await _make_member(db_session, workspace_id=ws.id, user_id=target.id)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_routing_upsert",
            {
                "name": "ml_reviewer",
                "then_assign_to": {
                    "strategy": "user",
                    "user_id": str(target.id),
                },
            },
        )
    )
    assert out["action"] == "created"
    assert out["name"] == "ml_reviewer"

    persisted = (
        await db_session.execute(
            select(InboxRoutingRule).where(
                InboxRoutingRule.workspace_id == ws.id,
                InboxRoutingRule.handle_key == "ml_reviewer",
            )
        )
    ).scalar_one()
    assert persisted.target_type == "user"
    assert persisted.target_value == str(target.id)


@pytest.mark.asyncio
async def test_inbox_routing_upsert_update_existing_rule(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    target_a = await _make_user(db_session)
    target_b = await _make_user(db_session)
    await _make_member(db_session, workspace_id=ws.id, user_id=target_a.id)
    await _make_member(db_session, workspace_id=ws.id, user_id=target_b.id)

    rule = await _make_routing_rule(
        db_session,
        workspace_id=ws.id,
        handle_key="rotating_owner",
        target_value=str(target_a.id),
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_routing_upsert",
            {
                "rule_id": str(rule.id),
                "name": "rotating_owner",
                "then_assign_to": {
                    "strategy": "user",
                    "user_id": str(target_b.id),
                },
                "enabled": False,
            },
        )
    )
    assert out["action"] == "updated"
    assert out["rule_id"] == str(rule.id)
    assert out["enabled"] is False

    await db_session.refresh(rule)
    assert rule.target_value == str(target_b.id)
    assert rule.is_enabled is False


@pytest.mark.asyncio
async def test_inbox_routing_upsert_invalid_handle_validation_failed(
    db_session, seed_workspace
) -> None:
    """Names that don't match ``^[a-z][a-z0-9_]*$`` get rejected."""
    user, _, ws = seed_workspace
    target = await _make_user(db_session)
    await _make_member(db_session, workspace_id=ws.id, user_id=target.id)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_routing_upsert",
            {
                # CamelCase + dash both violate the handle character class.
                "name": "Bad-Handle",
                "then_assign_to": {
                    "strategy": "user",
                    "user_id": str(target.id),
                },
            },
        )
    )
    assert out["error"] == "validation_failed"
    assert "handle_key" in out["message"]


@pytest.mark.asyncio
async def test_inbox_routing_upsert_non_admin_forbidden(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "inbox_routing_upsert",
            {
                "name": "any_handle",
                "then_assign_to": {
                    "strategy": "user",
                    "user_id": str(member.id),
                },
            },
        )
    )
    assert out["error"] == "forbidden"
