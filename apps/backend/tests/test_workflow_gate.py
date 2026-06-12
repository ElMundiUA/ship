"""W8.2 (ELS-257) — WorkflowDispatchGate.

The control-plane chokepoint: lease + cap (walk-back) + cascade
(recursion edges) + durable idempotency, all reusing the dispatcher
primitives. The last test pins the FOUNDER DECISION that no autonomy
branch exists in gate.py.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from backend.app.db.models.tenancy import AuditLog
from backend.app.db.models.workflow import AgentWorkflowRun, AgentWorkflowStepRun
from backend.app.services.workflow.gate import (
    GateReason,
    gate_step_dispatch,
    release_step_lock,
    workflow_lock_key,
)


async def _make_run(db_session, workspace_id) -> AgentWorkflowRun:
    run = AgentWorkflowRun(
        workspace_id=workspace_id,
        spec_name="pr-review",
        trigger_kind="chat",
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _lock_count(db_session, workspace_id, prefix="workflow:") -> int:
    return int(
        (
            await db_session.execute(
                text(
                    """
                    SELECT COUNT(*)::int FROM agent_dispatch_locks
                    WHERE workspace_id = :ws AND key LIKE :p
                      AND expires_at > now()
                    """
                ),
                {"ws": workspace_id, "p": f"{prefix}%"},
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_fanout_acquires_locks_and_cap_walks_back(
    db_session, seed_workspace
) -> None:
    """AC: max_fanout leaves acquire exactly that many workflow:*
    locks; the over-cap acquire is walked back (lock released, row
    blocked) — verified against actual agent_dispatch_locks rows."""
    _, _, workspace = seed_workspace
    workspace.max_concurrent_dispatches = 3
    await db_session.flush()
    run = await _make_run(db_session, workspace.id)

    decisions = []
    for i in range(4):
        decisions.append(
            await gate_step_dispatch(
                db_session,
                workspace_id=workspace.id,
                workflow_run_id=run.id,
                step_id=f"axis.{i}",
                attempt=1,
                kind="parallel",
                agent_provider="reasoning",
            )
        )
    assert [d.granted for d in decisions] == [True, True, True, False]
    assert decisions[3].reason == GateReason.CAP_EXCEEDED
    # Exactly cap locks live — the 4th acquire was walked back.
    assert await _lock_count(db_session, workspace.id) == 3
    blocked = (
        await db_session.execute(
            select(AgentWorkflowStepRun).where(
                AgentWorkflowStepRun.workflow_run_id == run.id,
                AgentWorkflowStepRun.status == "blocked",
            )
        )
    ).scalars().all()
    assert len(blocked) == 1
    assert blocked[0].reason == GateReason.CAP_EXCEEDED


@pytest.mark.asyncio
async def test_cascade_blocks_recursive_ship_leaves(
    db_session, seed_workspace
) -> None:
    """AC: recursion edges (ship provider) are counted via
    _count_recent_dispatches; the 4th in the window is refused —
    regardless of autonomy profile (none is consulted)."""
    _, _, workspace = seed_workspace
    workspace.max_concurrent_dispatches = 10
    workspace.autonomy = "high"  # the dial must not matter
    await db_session.flush()
    run = await _make_run(db_session, workspace.id)
    cascade_key = f"workflow:{run.id}"

    outcomes = []
    for i in range(4):
        d = await gate_step_dispatch(
            db_session,
            workspace_id=workspace.id,
            workflow_run_id=run.id,
            step_id=f"nest.{i}",
            attempt=1,
            kind="pipeline",
            agent_provider="ship",
            cascade_key=cascade_key,
        )
        outcomes.append(d)
    assert [d.granted for d in outcomes] == [True, True, True, False]
    assert outcomes[3].reason == GateReason.CASCADE_BLOCKED


@pytest.mark.asyncio
async def test_reasoning_fanout_is_not_a_recursion_edge(
    db_session, seed_workspace
) -> None:
    """An ordinary 4-leaf reasoning fan-out must NOT trip the cascade
    guard (it counts recursion edges only)."""
    _, _, workspace = seed_workspace
    workspace.max_concurrent_dispatches = 10
    await db_session.flush()
    run = await _make_run(db_session, workspace.id)
    for i in range(4):
        d = await gate_step_dispatch(
            db_session,
            workspace_id=workspace.id,
            workflow_run_id=run.id,
            step_id=f"axis.{i}",
            attempt=1,
            kind="parallel",
            agent_provider="reasoning",
        )
        assert d.granted, d.reason


@pytest.mark.asyncio
async def test_same_attempt_is_idempotent(db_session, seed_workspace) -> None:
    """AC: re-invoking the gate for the same (run, step, attempt) is
    idempotent — no second dispatch, no second lock; a NEW attempt
    re-runs."""
    _, _, workspace = seed_workspace
    run = await _make_run(db_session, workspace.id)

    first = await gate_step_dispatch(
        db_session,
        workspace_id=workspace.id,
        workflow_run_id=run.id,
        step_id="synth",
        attempt=1,
        kind="synthesize",
        agent_provider="reasoning",
    )
    assert first.granted
    locks_after_first = await _lock_count(db_session, workspace.id)

    retry = await gate_step_dispatch(
        db_session,
        workspace_id=workspace.id,
        workflow_run_id=run.id,
        step_id="synth",
        attempt=1,
        kind="synthesize",
        agent_provider="reasoning",
    )
    assert retry.granted is False
    assert retry.reason == GateReason.DUPLICATE_ATTEMPT
    assert retry.run_id == first.run_id  # same durable row, no re-mint
    assert await _lock_count(db_session, workspace.id) == locks_after_first

    # New attempt after release → re-runs cleanly.
    await release_step_lock(
        db_session,
        workspace_id=workspace.id,
        workflow_run_id=run.id,
        step_id="synth",
    )
    second = await gate_step_dispatch(
        db_session,
        workspace_id=workspace.id,
        workflow_run_id=run.id,
        step_id="synth",
        attempt=2,
        kind="synthesize",
        agent_provider="reasoning",
    )
    assert second.granted


@pytest.mark.asyncio
async def test_every_decision_writes_audit(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    workspace.max_concurrent_dispatches = 1
    await db_session.flush()
    run = await _make_run(db_session, workspace.id)

    await gate_step_dispatch(
        db_session,
        workspace_id=workspace.id,
        workflow_run_id=run.id,
        step_id="a",
        attempt=1,
        kind="pipeline",
        agent_provider="reasoning",
    )
    await gate_step_dispatch(
        db_session,
        workspace_id=workspace.id,
        workflow_run_id=run.id,
        step_id="b",
        attempt=1,
        kind="pipeline",
        agent_provider="reasoning",
    )
    actions = [
        r.action
        for r in (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace.id,
                    AuditLog.action.like("workflow.step_%"),
                )
            )
        ).scalars()
    ]
    assert "workflow.step_dispatched" in actions
    assert "workflow.step_blocked" in actions


def test_lock_key_namespace_shape() -> None:
    rid = uuid.UUID(int=1)
    assert workflow_lock_key(rid, "synth") == f"workflow:{rid}:synth"


def test_gate_has_no_autonomy_branch() -> None:
    """FOUNDER DECISION pinned: the control plane is off-limits to
    the autonomy dial — gate.py must not consult it (the word may
    only appear in comments explaining exactly this)."""
    source = Path("apps/backend/app/services/workflow/gate.py").read_text()
    in_doc = False
    real_code = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.endswith('"""'):
            in_doc = not in_doc if stripped.count('"""') == 1 else in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        real_code.append(line)
    joined = "\n".join(real_code)
    assert "autonomy" not in joined, "gate.py must not branch on autonomy"
