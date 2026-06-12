"""W8.4/W8.5/W8.6/W8.8 (ELS-259..262) — triggers + dogfood specs.

- chat: run_workflow tool queues lock-free, admin-gated, lists
  available specs on unknown names, excluded from subagent toolsets;
- gate: a configured FSM stage fires the workflow (dedup +
  fail-closed + shadow);
- cron: nightly tick enqueues per opt-in workspace only;
- ship leaf: spec accepts the provider, the config surface does NOT
  offer it to customers (internal/dogfood);
- dogfood: pr-review + codebase-audit load and run end-to-end with
  fake executors; /process stays untouched by this workstream.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.workflow import AgentWorkflowRun
from backend.app.services import dispatcher as dispatcher_mod
from backend.app.services.workflow import runtime as runtime_mod
from backend.app.services.workflow.leaves import LeafExecutors
from backend.app.services.workflow.registry import (
    list_available_specs,
    resolve_spec,
)
from backend.app.services.workflow.runtime import run_workflow


async def _workflow_locks(db_session, ws_id) -> int:
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


# ---------------------------------------------------------------------------
# Chat trigger (ELS-260)
# ---------------------------------------------------------------------------


def _toolbox(db_session, workspace, user):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        db_session,
        settings=Settings(OPENAI_API_KEY="test"),  # type: ignore[call-arg]
        workspace_id=workspace.id,
        user_id=user.id,
    )


@pytest.mark.asyncio
async def test_run_workflow_tool_queues_lock_free(
    db_session, seed_workspace, monkeypatch
) -> None:
    user, _, workspace = seed_workspace
    spy = AsyncMock()
    monkeypatch.setattr(runtime_mod, "advance_run_by_id", spy)

    toolbox = _toolbox(db_session, workspace, user)
    result = await toolbox._tool_run_workflow(
        {"workflow_name": "pr-review", "inputs": {"pr_url": "https://x/pr/1"}}
    )
    assert "workflow_run_id" in result

    # AC: the chat process acquired NO dispatch lock at tool return.
    assert await _workflow_locks(db_session, workspace.id) == 0
    run = (
        await db_session.execute(
            select(AgentWorkflowRun).where(
                AgentWorkflowRun.workspace_id == workspace.id
            )
        )
    ).scalar_one()
    assert run.status == "queued"
    assert run.trigger_kind == "chat"
    await asyncio.sleep(0)  # let the created task start
    spy.assert_awaited()  # spawn deferred to the gated background path


@pytest.mark.asyncio
async def test_run_workflow_unknown_name_lists_available(
    db_session, seed_workspace, monkeypatch
) -> None:
    user, _, workspace = seed_workspace
    monkeypatch.setattr(runtime_mod, "advance_run_by_id", AsyncMock())
    toolbox = _toolbox(db_session, workspace, user)
    result = await toolbox._tool_run_workflow({"workflow_name": "nope"})
    assert "unknown_workflow" in result
    assert "pr-review" in result and "codebase-audit" in result


@pytest.mark.asyncio
async def test_run_workflow_filtered_inside_subagent(
    db_session, seed_workspace
) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)
    toolbox._subagent_active = True
    with pytest.raises(ToolInvocationError, match="nested run_workflow"):
        await toolbox._tool_run_workflow({"workflow_name": "pr-review"})


def test_run_workflow_registered_in_tool_specs() -> None:
    source = Path("apps/backend/app/services/agent/tools.py").read_text()
    assert '"run_workflow": self._tool_run_workflow' in source
    assert '("consult_specialist", "run_workflow")' in source


# ---------------------------------------------------------------------------
# Gate trigger (ELS-261, b)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_trigger_fires_configured_workflow(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    workspace.settings = {
        **(workspace.settings or {}),
        "workflows": {"stage_triggers": {"code_review": "pr-review"}},
    }
    await db_session.flush()

    spy = AsyncMock()
    monkeypatch.setattr(runtime_mod, "run_workflow", spy)

    fire_settings = Settings(  # type: ignore[call-arg]
        OPENAI_API_KEY="test", SHIP_TRACKER_POLL_FIRE=True
    )
    await dispatcher_mod._maybe_fire_stage_workflow(
        db_session,
        workspace_id=workspace.id,
        fsm_stage="code_review",
        ticket_ref="ELS-1",
        settings=fire_settings,
    )
    spy.assert_awaited_once()
    kwargs = spy.await_args.kwargs
    assert kwargs["trigger_kind"] == "gate"
    assert kwargs["triggered_by"] == "ticket:ELS-1"
    assert kwargs["spec"].name == "pr-review"


@pytest.mark.asyncio
async def test_stage_trigger_fail_closed_without_config(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    spy = AsyncMock()
    monkeypatch.setattr(runtime_mod, "run_workflow", spy)
    await dispatcher_mod._maybe_fire_stage_workflow(
        db_session,
        workspace_id=workspace.id,
        fsm_stage="code_review",
        ticket_ref="ELS-1",
        settings=get_settings(),
    )
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_stage_trigger_dedups_running_run(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    workspace.settings = {
        "workflows": {"stage_triggers": {"code_review": "pr-review"}}
    }
    db_session.add(
        AgentWorkflowRun(
            workspace_id=workspace.id,
            spec_name="pr-review",
            trigger_kind="gate",
            triggered_by="ticket:ELS-1",
            status="running",
        )
    )
    await db_session.flush()

    spy = AsyncMock()
    monkeypatch.setattr(runtime_mod, "run_workflow", spy)
    await dispatcher_mod._maybe_fire_stage_workflow(
        db_session,
        workspace_id=workspace.id,
        fsm_stage="code_review",
        ticket_ref="ELS-1",
        settings=get_settings(),
    )
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_stage_trigger_shadow_records_without_spawning(
    db_session, seed_workspace, monkeypatch
) -> None:
    """AC: shadow mode (tracker_poll_fire off) records a
    workflow.would_have_run audit row and spawns nothing."""
    from backend.app.db.models.tenancy import AuditLog

    _, _, workspace = seed_workspace
    workspace.settings = {
        "workflows": {"stage_triggers": {"code_review": "pr-review"}}
    }
    await db_session.flush()
    spy = AsyncMock()
    monkeypatch.setattr(runtime_mod, "run_workflow", spy)

    shadow_settings = Settings(  # type: ignore[call-arg]
        OPENAI_API_KEY="test", SHIP_TRACKER_POLL_FIRE=False
    )
    await dispatcher_mod._maybe_fire_stage_workflow(
        db_session,
        workspace_id=workspace.id,
        fsm_stage="code_review",
        ticket_ref="ELS-1",
        settings=shadow_settings,
    )
    spy.assert_not_awaited()
    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "workflow.would_have_run",
            )
        )
    ).scalar_one()
    assert row.payload["shadow"] is True
    assert row.payload["fsm_stage"] == "code_review"


# ---------------------------------------------------------------------------
# Ship leaf (ELS-259)
# ---------------------------------------------------------------------------


def test_spec_accepts_ship_provider() -> None:
    from backend.app.services.workflow.spec import load_spec

    spec = load_spec(
        """
name: dogfood
steps:
  - id: nest
    kind: pipeline
    agent: {kind: coding, provider: ship}
"""
    )
    assert spec.steps[0].agent.provider == "ship"


def test_ship_provider_not_customer_selectable() -> None:
    """AC: internal/dogfood only — the config surface offers cursor/
    codex/claude, never ship."""
    from backend.app.services.config_registry import SCOPES

    enum = SCOPES["agent.provider"].schema["enum"]
    assert "ship" not in enum


# ---------------------------------------------------------------------------
# Dogfood specs (ELS-262)
# ---------------------------------------------------------------------------


def test_registry_lists_dogfood_specs() -> None:
    available = list_available_specs()
    assert "pr-review" in available
    assert "codebase-audit" in available
    assert resolve_spec("../etc/passwd") is None


@pytest.mark.asyncio
async def test_pr_review_runs_end_to_end(db_session, seed_workspace) -> None:
    """AC: pr-review loads via W8.1, runs via W8.3, synthesize emits a
    schema-valid findings object, verify consumes the top finding."""
    _, _, workspace = seed_workspace
    workspace.max_concurrent_dispatches = 8
    await db_session.flush()
    spec = resolve_spec("pr-review")
    assert spec is not None

    verify_inputs: dict = {}

    async def reasoning(_s, _st, _ws, step, inputs, run_id, **_kw):
        if step.kind == "synthesize":
            return {
                "findings": [
                    {"severity": "high", "title": "races on lock release"},
                    {"severity": "low", "title": "naming nit"},
                ]
            }
        if step.kind == "verify":
            verify_inputs.update(inputs)
            return {"verdict": "confirmed", "reason": "reproduced"}
        return {"axis": inputs.get("axis"), "notes": ["n1"]}

    async def coding(*_a, **_kw):  # pr-review has no coding leaves
        raise AssertionError("pr-review must not spawn coding leaves")

    run = await run_workflow(
        db_session,
        workspace_id=workspace.id,
        spec=spec,
        inputs={"pr_url": "https://github.com/x/y/pull/1"},
        trigger_kind="gate",
        executors=LeafExecutors(run_reasoning=reasoning, run_coding=coding),
    )
    assert run.status == "completed"
    # verify consumed the synthesized findings.
    assert verify_inputs["top_finding"][0]["severity"] == "high"
    assert await _workflow_locks(db_session, workspace.id) == 0


@pytest.mark.asyncio
async def test_codebase_audit_runs_as_pipeline(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    workspace.max_concurrent_dispatches = 8
    await db_session.flush()
    spec = resolve_spec("codebase-audit")
    assert spec is not None

    async def reasoning(_s, _st, _ws, step, inputs, run_id, **_kw):
        if step.kind == "judge":
            return {"items": [{"rank": 1, "title": "split dispatcher.py"}]}
        return {"lens": inputs.get("lens"), "items": ["x"]}

    async def coding(_s, _st, _ws, step, inputs, run_id, **_kw):
        return {"hotspots": ["dispatcher.py", "tools.py"]}

    run = await run_workflow(
        db_session,
        workspace_id=workspace.id,
        spec=spec,
        inputs={"focus": "nightly"},
        trigger_kind="cron",
        executors=LeafExecutors(run_reasoning=reasoning, run_coding=coding),
    )
    assert run.status == "completed"
    assert run.output == {"items": [{"rank": 1, "title": "split dispatcher.py"}]}


def test_process_editor_untouched_by_workflows() -> None:
    """Boundary doc AC: /process (reactive state machine) and the
    workflow primitive (imperative bounded pipeline) never merge —
    processes.py must not import the workflow runtime."""
    source = Path("apps/backend/app/api/v1/routes/processes.py").read_text()
    assert "services.workflow" not in source
