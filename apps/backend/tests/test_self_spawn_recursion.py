"""Self-spawn recursion guard (ELS-242, thesis 6).

A nested ``shipctl run`` (trigger_kind='self_spawn') must pass THROUGH
the control plane, never around it: same cascade budget, same per-ws
cap, no project-lock carve-out, regardless of autonomy profile.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_dispatch import AgentDispatchLock
from backend.app.db.models.tenancy import AuditLog, Org, Workspace
from backend.app.services import dispatcher
from backend.app.services.dispatcher import (
    CASCADE_LIMIT,
    acquire_lock,
    count_active_locks,
    maybe_dispatch,
)


async def _make_workspace(db_session, *, autonomy: str = "balanced") -> uuid.UUID:
    org = Org(slug=f"o-{uuid.uuid4().hex[:8]}", name="o")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id, slug=f"w-{uuid.uuid4().hex[:8]}", name="w",
        autonomy=autonomy,
    )
    db_session.add(ws)
    await db_session.flush()
    return ws.id


def _fire_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        dispatcher.get_settings(), "tracker_poll_fire", True, raising=False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("autonomy", ["conservative", "balanced", "high"])
async def test_self_spawn_loop_terminates_via_cascade_blocked(
    db_session, monkeypatch, autonomy
) -> None:
    """4-deep self-spawn loop: every nested dispatch burns the same
    cascade budget; by depth CASCADE_LIMIT the engine refuses. The
    autonomy profile is irrelevant (control plane off-limits to the
    dial — founder decision)."""
    ws = await _make_workspace(db_session, autonomy=autonomy)
    _fire_mode(monkeypatch)

    refused_at = None
    for depth in range(CASCADE_LIMIT + 1):  # 0..3 → 4 attempts
        result = await maybe_dispatch(
            db_session,
            workspace_id=ws,
            ticket_ref="T-loop",
            trigger_kind="self_spawn",
            fsm_stage="planning",
        )
        if result.reason == "cascade_blocked":
            refused_at = depth
            break
        # Simulate the spawned run having fired (the audit row a real
        # dispatch writes) so the next nesting level sees it.
        db_session.add(
            AuditLog(
                workspace_id=ws,
                action="agent_run.dispatch",
                target_kind="ticket",
                target_id="T-loop",
                payload={"trigger_kind": "self_spawn"},
            )
        )
        # release the ticket lock the attempt took (simulating finish)
        from backend.app.services.dispatcher import release_lock

        await release_lock(db_session, workspace_id=ws, key="ticket:T-loop")
        await db_session.flush()

    assert refused_at is not None, "self-spawn loop never hit CASCADE_BLOCKED"
    assert refused_at <= CASCADE_LIMIT
    refusals = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == "dispatch.cascade_blocked",
            )
        )
    ).scalars().all()
    assert len(refusals) == 1
    assert refusals[0].payload["trigger_kind"] == "self_spawn"


@pytest.mark.asyncio
async def test_self_spawn_counts_against_cap_no_exemption(
    db_session, monkeypatch
) -> None:
    ws = await _make_workspace(db_session, autonomy="high")
    _fire_mode(monkeypatch)
    for n in range(4):  # default cap
        await acquire_lock(db_session, workspace_id=ws, key=f"ticket:T-{n}")

    result = await maybe_dispatch(
        db_session,
        workspace_id=ws,
        ticket_ref="T-new",
        trigger_kind="self_spawn",
        fsm_stage="planning",
    )
    assert result.fired is False
    assert result.reason == "cap_exceeded"
    assert await count_active_locks(db_session, workspace_id=ws) == 4


def test_self_spawn_excluded_from_cascade_carveout_in_source() -> None:
    """The project-lock bypass stays cascade-only. Pin the expression
    so 'self_spawn' can never quietly join it."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "dispatcher.py"
    ).read_text()
    assert 'is_cascade = trigger_kind == "cascade"' in src
    carve = src.index('is_cascade = trigger_kind == "cascade"')
    window = src[carve - 200 : carve + 200]
    assert "self_spawn\" ==" not in window
    assert 'trigger_kind in ("cascade", "self_spawn")' not in src


def test_dispatcher_never_reads_autonomy() -> None:
    """The dial lives at gates/prompts — the dispatch control plane
    must not branch on it (founder decision). AST-level: comments may
    mention the word; CODE may not."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "dispatcher.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.arg):
            name = node.arg
        if name is not None:
            assert "autonomy" not in name.lower(), (
                f"dispatcher.py code references {name!r} — the control "
                "plane is off-limits to the dial (thesis 7)"
            )
