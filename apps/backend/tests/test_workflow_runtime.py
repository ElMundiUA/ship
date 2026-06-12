"""W8.3 (ELS-258) — workflow runtime executor.

Fake leaf executors complete synchronously so the DAG mechanics are
testable without LLMs or CI: pipeline threading + schema validation,
parallel/barrier with partial failure, loop bounded at max_iters, the
single-chokepoint grep, and the finish-webhook lock release."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from backend.app.db.models.workflow import AgentWorkflowStepRun
from backend.app.services.workflow.leaves import (
    LeafExecutors,
    complete_coding_step,
)
from backend.app.services.workflow.runtime import (
    advance_workflow,
    run_workflow,
)
from backend.app.services.workflow.spec import load_spec


PIPELINE_SPEC = """
name: three-step
max_fanout: 4
steps:
  - id: enumerate
    kind: pipeline
    agent: {kind: reasoning}
    inputs: {target: "{{ inputs.target }}"}
  - id: implement
    kind: pipeline
    needs: [enumerate]
    agent: {kind: coding, provider: claude}
    inputs: {hotspots: "{{ steps.enumerate.output.hotspots }}"}
  - id: synth
    kind: synthesize
    needs: [implement]
    inputs: {result: "{{ steps.implement.output.result }}"}
    output_schema:
      type: object
      required: [summary]
      properties: {summary: {type: string}}
"""


def _executors(reasoning=None, coding=None) -> LeafExecutors:
    async def default_reasoning(_s, _st, _ws, step, inputs, run_id, **_kw):
        if step.kind == "synthesize":
            return {"summary": f"synth over {inputs.get('result')}"}
        return {"hotspots": ["a.py", "b.py"], "echo": inputs}

    async def default_coding(_s, _st, _ws, step, inputs, run_id, **_kw):
        return {"result": f"patched {len(inputs.get('hotspots') or [])} files"}

    return LeafExecutors(
        run_reasoning=reasoning or default_reasoning,
        run_coding=coding or default_coding,
    )


async def _locks(db_session, ws_id) -> int:
    return int(
        (
            await db_session.execute(
                text(
                    "SELECT COUNT(*)::int FROM agent_dispatch_locks "
                    "WHERE workspace_id = :ws AND key LIKE 'workflow:%' "
                    "AND expires_at > now()"
                ),
                {"ws": ws_id},
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_pipeline_threads_outputs_end_to_end(
    db_session, seed_workspace
) -> None:
    """AC: reasoning→coding→synthesize, outputs threaded, synthesize
    schema-validated, run completes bounded."""
    _, _, workspace = seed_workspace
    spec = load_spec(PIPELINE_SPEC)

    seen_inputs: dict[str, dict] = {}

    async def coding(_s, _st, _ws, step, inputs, run_id, **_kw):
        seen_inputs["implement"] = inputs
        return {"result": "patched"}

    run = await run_workflow(
        db_session,
        workspace_id=workspace.id,
        spec=spec,
        inputs={"target": "billing"},
        trigger_kind="chat",
        executors=_executors(coding=coding),
    )
    assert run.status == "completed"
    # Output threading: implement saw enumerate's hotspots.
    assert seen_inputs["implement"]["hotspots"] == ["a.py", "b.py"]
    # Final run output = last step (synthesize), schema-valid.
    assert run.output == {"summary": "synth over patched"}
    # All workflow locks released after completion.
    assert await _locks(db_session, workspace.id) == 0


@pytest.mark.asyncio
async def test_parallel_barrier_tolerates_branch_failure(
    db_session, seed_workspace
) -> None:
    """AC: a branch failure doesn't abort siblings; the barrier sees
    the partial set."""
    _, _, workspace = seed_workspace
    spec = load_spec(
        """
name: fan
max_fanout: 3
steps:
  - id: fan
    kind: parallel
    steps:
      - {id: ok.a, kind: pipeline, agent: {kind: reasoning}}
      - {id: boom, kind: pipeline, agent: {kind: reasoning}}
      - {id: ok.b, kind: pipeline, agent: {kind: reasoning}}
  - id: join
    kind: barrier
    needs: [fan]
"""
    )

    async def reasoning(_s, _st, _ws, step, inputs, run_id, **_kw):
        if step.id == "boom":
            raise RuntimeError("branch exploded")
        return {"ok": step.id}

    run = await run_workflow(
        db_session,
        workspace_id=workspace.id,
        spec=spec,
        inputs={},
        trigger_kind="chat",
        executors=_executors(reasoning=reasoning),
    )
    rows = {
        r.step_id: r
        for r in (
            await db_session.execute(
                select(AgentWorkflowStepRun).where(
                    AgentWorkflowStepRun.workflow_run_id == run.id
                )
            )
        ).scalars()
    }
    assert rows["ok.a"].status == "completed"
    assert rows["ok.b"].status == "completed"
    assert rows["boom"].status == "failed"
    assert rows["join"].status == "completed"
    assert set(rows["join"].output["joined"]) == {"ok.a", "ok.b"}
    assert rows["join"].output["failed"] == ["boom"]
    assert run.status == "completed"  # partial failure recorded, bounded


@pytest.mark.asyncio
async def test_loop_terminates_at_max_iters(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    spec = load_spec(
        """
name: looper
steps:
  - id: spin
    kind: loop
    agent: {kind: reasoning}
    until: "output.done == true"
    max_iters: 3
"""
    )
    calls = {"n": 0}

    async def reasoning(_s, _st, _ws, step, inputs, run_id, **_kw):
        calls["n"] += 1
        return {"done": False}  # never satisfies the predicate

    run = await run_workflow(
        db_session,
        workspace_id=workspace.id,
        spec=spec,
        inputs={},
        trigger_kind="cron",
        executors=_executors(reasoning=reasoning),
    )
    assert calls["n"] == 3  # bounded — no infinite loop
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_loop_stops_when_until_satisfied(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    spec = load_spec(
        """
name: looper2
steps:
  - id: spin
    kind: loop
    agent: {kind: reasoning}
    until: "output.done == true"
    max_iters: 5
"""
    )
    calls = {"n": 0}

    async def reasoning(_s, _st, _ws, step, inputs, run_id, **_kw):
        calls["n"] += 1
        return {"done": calls["n"] >= 2}

    await run_workflow(
        db_session,
        workspace_id=workspace.id,
        spec=spec,
        inputs={},
        trigger_kind="cron",
        executors=_executors(reasoning=reasoning),
    )
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_async_coding_leaf_finish_webhook_releases_lock(
    db_session, seed_workspace
) -> None:
    """AC: a coding leaf left 'dispatched' is completed by the finish
    correlation (run_id) which releases its workflow:* lock; the
    reconcile advance then finishes the run."""
    _, _, workspace = seed_workspace
    spec = load_spec(
        """
name: ci-leaf
steps:
  - id: build
    kind: pipeline
    agent: {kind: coding, provider: claude}
"""
    )

    async def coding(_s, _st, _ws, step, inputs, run_id, **_kw):
        return None  # async: CI will report back

    run = await run_workflow(
        db_session,
        workspace_id=workspace.id,
        spec=spec,
        inputs={},
        trigger_kind="gate",
        executors=_executors(coding=coding),
    )
    assert run.status == "running"
    assert await _locks(db_session, workspace.id) == 1
    row = (
        await db_session.execute(
            select(AgentWorkflowStepRun).where(
                AgentWorkflowStepRun.workflow_run_id == run.id
            )
        )
    ).scalar_one()
    assert row.status == "dispatched"

    matched = await complete_coding_step(
        db_session,
        workspace_id=workspace.id,
        run_id=row.run_id,
        success=True,
        output={"outcome": "ready_next_step"},
    )
    assert matched is True
    assert await _locks(db_session, workspace.id) == 0

    await advance_workflow(
        db_session, run=run, spec=spec, executors=_executors()
    )
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_schema_invalid_output_fails_step(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    spec = load_spec(
        """
name: strict
steps:
  - id: synth
    kind: synthesize
    output_schema: {type: object, required: [summary], properties: {summary: {type: string}}}
"""
    )

    async def reasoning(_s, _st, _ws, step, inputs, run_id, **_kw):
        return {"wrong_key": 1}

    run = await run_workflow(
        db_session,
        workspace_id=workspace.id,
        spec=spec,
        inputs={},
        trigger_kind="chat",
        executors=_executors(reasoning=reasoning),
    )
    row = (
        await db_session.execute(
            select(AgentWorkflowStepRun).where(
                AgentWorkflowStepRun.workflow_run_id == run.id
            )
        )
    ).scalar_one()
    assert row.status == "failed"
    assert row.reason == "schema_invalid"
    assert run.status == "failed"


def test_runtime_has_single_chokepoint() -> None:
    """AC: runtime.py never calls runAgent / dispatch_workflow /
    _run_subagent_loop — every spawn goes through gate.py + the
    injected executors."""
    source = Path(
        "apps/backend/app/services/workflow/runtime.py"
    ).read_text()
    assert "runAgent" not in source
    assert "dispatch_workflow" not in source
    assert "_run_subagent_loop" not in source
    assert "gate_step_dispatch" in source
