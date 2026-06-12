"""ELS-277 — workflow coding-leaf run_id correlation vs workspace bundles."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
    WorkspaceRepoRouting,
)
from backend.app.db.models.tenancy import AuditLog, Org, Workspace
from backend.app.db.models.workflow import AgentWorkflowRun, AgentWorkflowStepRun
from backend.app.services import dispatcher
from backend.app.services.dispatcher import (
    acquire_lock,
    maybe_dispatch_workspace_bundle,
    workflow_leaf_lock_key,
)
from backend.app.services.workflow.leaves import complete_coding_step


async def _make_workspace_with_repo(db_session) -> tuple[uuid.UUID, object]:
    org = Org(
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Test org",
        plan="free",
    )
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Test ws",
    )
    db_session.add(ws)
    await db_session.flush()
    install = GitHubInstallation(
        workspace_id=ws.id,
        installation_id=99001 + uuid.uuid4().int % 1000,
        account_login="acme",
        account_type="Organization",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=ws.id,
        installation_id=install.id,
        provider="github",
        external_id=9001,
        full_name="acme/ship",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/ship",
        installed_bundle_version="0.38",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    db_session.add(
        WorkspaceRepoRouting(
            workspace_id=ws.id, project_native_id=None, repo_id=repo.id
        )
    )
    await db_session.flush()
    return ws.id, repo


@pytest.mark.asyncio
async def test_workspace_bundle_skips_when_workflow_leaf_lock_held(
    db_session, monkeypatch
) -> None:
    """Scheduler yields when a workflow coding leaf owns the routine."""
    ws_id, _repo = await _make_workspace_with_repo(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(), "tracker_poll_fire", True, raising=False
    )
    await acquire_lock(
        db_session,
        workspace_id=ws_id,
        key=workflow_leaf_lock_key("weekly-audit"),
    )

    result = await maybe_dispatch_workspace_bundle(
        db_session,
        workspace_id=ws_id,
        bundle_id="weekly-audit",
        trigger_kind="weekly_tick",
    )
    assert result.fired is False
    assert result.reason == "workflow_leaf_collision"

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "workflow.coding_leaf.collision_skipped",
                AuditLog.target_id == "weekly-audit",
            )
        )
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.payload["routine_id"] == "weekly-audit"


@pytest.mark.asyncio
async def test_workspace_bundle_dispatch_passes_ship_run_id(
    db_session, monkeypatch
) -> None:
    """Standalone scheduler dispatches mint and forward ``ship_run_id``."""
    ws_id, _repo = await _make_workspace_with_repo(db_session)
    settings = dispatcher.get_settings()
    monkeypatch.setattr(settings, "tracker_poll_fire", True, raising=False)

    captured: dict[str, str] = {}

    async def _dispatch(*_args, **kwargs):
        captured.update(kwargs.get("inputs") or {})
        return None

    monkeypatch.setattr(dispatcher, "dispatch_workflow", _dispatch)

    result = await maybe_dispatch_workspace_bundle(
        db_session,
        workspace_id=ws_id,
        bundle_id="weekly-audit",
        trigger_kind="weekly_tick",
        settings=settings,
    )
    assert result.fired is True
    ship_run_id = captured.get("ship_run_id")
    assert ship_run_id
    assert ship_run_id.startswith("run_")


@pytest.mark.asyncio
async def test_coding_leaf_finish_correlates_gate_run_id(
    db_session, seed_workspace
) -> None:
    """Finish with the gate's ``run_id`` completes the dispatched step."""
    _, _, workspace = seed_workspace
    run = AgentWorkflowRun(
        workspace_id=workspace.id,
        spec_name="codebase-audit",
        spec_version="1",
        inputs={},
        trigger_kind="cron",
        status="running",
    )
    db_session.add(run)
    await db_session.flush()
    gate_run_id = "run_gate12345678"
    step = AgentWorkflowStepRun(
        workflow_run_id=run.id,
        step_id="enumerate",
        kind="pipeline",
        agent_provider="claude",
        status="dispatched",
        run_id=gate_run_id,
    )
    db_session.add(step)
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            action="workflow.coding_leaf.dispatched",
            target_kind="workflow_step",
            target_id=gate_run_id,
            payload={"routine_id": "weekly-audit", "step_id": "enumerate"},
        )
    )
    await acquire_lock(
        db_session,
        workspace_id=workspace.id,
        key=workflow_leaf_lock_key("weekly-audit"),
    )
    await db_session.flush()

    matched = await complete_coding_step(
        db_session,
        workspace_id=workspace.id,
        run_id=gate_run_id,
        success=True,
        output={"outcome": "ready_next_step"},
    )
    assert matched is True
    await db_session.refresh(step)
    assert step.status == "completed"

    held = await dispatcher.lock_is_held(
        db_session,
        workspace_id=workspace.id,
        key=workflow_leaf_lock_key("weekly-audit"),
    )
    assert held is False


@pytest.mark.asyncio
async def test_finish_mismatch_emits_audit_for_workspace_bundle(
    v1_client, db_session, seed_workspace
) -> None:
    """Scheduler-minted finish id that misses the gate row is audited."""
    _, raw, workspace = seed_workspace
    gate_run_id = "run_gateabcdef01"
    scheduler_run_id = "run_schedabcdef01"
    run = AgentWorkflowRun(
        workspace_id=workspace.id,
        spec_name="codebase-audit",
        spec_version="1",
        inputs={},
        trigger_kind="cron",
        status="running",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        AgentWorkflowStepRun(
            workflow_run_id=run.id,
            step_id="enumerate",
            kind="pipeline",
            agent_provider="claude",
            status="dispatched",
            run_id=gate_run_id,
        )
    )
    await db_session.flush()

    res = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "run_id": scheduler_run_id,
            "outcome": "noop",
            "fsm_stage": "workspace_weekly",
            "ticket_ref": "weekly-audit",
            "comment": "No findings.",
        },
    )
    assert res.status_code == 200, res.text

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "workflow.coding_leaf.finish_mismatch",
                AuditLog.payload["actual_run_id"].astext == scheduler_run_id,
            )
        )
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.payload["expected_run_id"] == gate_run_id
