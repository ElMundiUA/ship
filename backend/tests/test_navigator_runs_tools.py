"""Navigator Phase-6 runs tools (Wave A read-side).

- ``runs_query`` filters across play_key, repo, status (with alias
  expansion ok/fail/error), trigger, and ``has_escalations``.
- ``run_detail`` returns the canonical projection — findings,
  artifacts, escalations with deeplink-friendly fields — and
  enforces workspace tenancy.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


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


async def _seed_repo(db_session, workspace, *, external_id: int, full_name: str):
    from backend.app.db.models.integrations import WorkspaceRepo

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=None,
        provider="github",
        external_id=external_id,
        full_name=full_name,
        default_branch="main",
        private=False,
        html_url=f"https://github.com/{full_name}",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def _seed_pipeline(
    db_session,
    *,
    workspace_id,
    repo_id=None,
    lane_id: str,
    name: str | None = None,
    enabled: bool = True,
):
    from backend.app.db.models.pipelines import Pipeline

    p = Pipeline(
        workspace_id=workspace_id,
        repo_id=repo_id,
        lane_id=lane_id,
        name=name or lane_id,
        workflow_id="pr-and-ci-gate",
        enabled=enabled,
    )
    db_session.add(p)
    await db_session.flush()
    return p


async def _seed_run(
    db_session,
    *,
    pipeline_id,
    workspace_id,
    trigger: str = "manual",
    status: str = "succeeded",
    outcome: dict | None = None,
    started_at: datetime | None = None,
):
    from backend.app.db.models.pipelines import PipelineRun

    run = PipelineRun(
        pipeline_id=pipeline_id,
        workspace_id=workspace_id,
        trigger=trigger,
        status=status,
        started_at=started_at or datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        summary=f"{status} run",
        outcome=outcome or {},
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _seed_inbox_item(db_session, *, workspace_id, owner_user_id):
    from backend.app.db.models.inbox import InboxItem

    item = InboxItem(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        type="failure",
        status="new",
        title="failure",
        payload={},
    )
    db_session.add(item)
    await db_session.flush()
    return item


async def _seed_escalation(db_session, *, run, inbox_item, reason="play_failed_repeatedly"):
    from backend.app.db.models.inbox import RunEscalation

    esc = RunEscalation(
        run_id=run.id,
        inbox_item_id=inbox_item.id,
        escalation_reason=reason,
    )
    db_session.add(esc)
    await db_session.flush()
    return esc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def runs_world(db_session, seed_workspace):
    """Seed a small heterogenous fleet of runs for the query tests.

    Layout:

    - ``repo_a / pipeline_a (lane_id=flow-pr-self-review)``
        * 1 succeeded ``manual`` run with NO escalations
        * 1 failed ``webhook`` run WITH 1 escalation
    - ``repo_b / pipeline_b (lane_id=scan-test-coverage)``
        * 1 succeeded ``cron`` run
    """
    user, _, ws = seed_workspace

    repo_a = await _seed_repo(
        db_session, ws, external_id=200_001, full_name="acme/runs-a"
    )
    repo_b = await _seed_repo(
        db_session, ws, external_id=200_002, full_name="acme/runs-b"
    )

    pipeline_a = await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo_a.id,
        lane_id="flow-pr-self-review",
    )
    pipeline_b = await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo_b.id,
        lane_id="scan-test-coverage",
    )

    run_ok = await _seed_run(
        db_session,
        pipeline_id=pipeline_a.id,
        workspace_id=ws.id,
        trigger="manual",
        status="succeeded",
        outcome={
            "headline": "All checks passed",
            "outcome_text": "OK",
            "findings_count": 0,
            "findings_by_severity": {},
            "findings": [],
            "artifacts": [],
        },
    )
    run_fail = await _seed_run(
        db_session,
        pipeline_id=pipeline_a.id,
        workspace_id=ws.id,
        trigger="webhook",
        status="failed",
        outcome={
            "headline": "Two CVEs",
            "outcome_text": "Two high CVEs found",
            "findings_count": 2,
            "findings_by_severity": {"high": 2},
            "findings": [
                {"id": "f-1", "severity": "high", "title": "CVE-1"},
                {"id": "f-2", "severity": "high", "title": "CVE-2"},
            ],
            "artifacts": [
                {"name": "scan-report.json", "url": "https://x/y"},
            ],
        },
    )
    run_b = await _seed_run(
        db_session,
        pipeline_id=pipeline_b.id,
        workspace_id=ws.id,
        trigger="cron",
        status="succeeded",
    )

    item = await _seed_inbox_item(
        db_session, workspace_id=ws.id, owner_user_id=user.id
    )
    await _seed_escalation(db_session, run=run_fail, inbox_item=item)

    return {
        "user": user,
        "workspace": ws,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "pipeline_a": pipeline_a,
        "pipeline_b": pipeline_b,
        "run_ok": run_ok,
        "run_fail": run_fail,
        "run_b": run_b,
        "inbox_item": item,
    }


# ---------------------------------------------------------------------------
# runs_query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_query_by_play_key(db_session, runs_world) -> None:
    box = _toolbox(
        db_session,
        workspace_id=runs_world["workspace"].id,
        user_id=runs_world["user"].id,
    )
    out = json.loads(
        await box.invoke(
            "runs_query", {"play_key": "scan-test-coverage"}
        )
    )
    ids = [r["id"] for r in out["runs"]]
    assert str(runs_world["run_b"].id) in ids
    assert str(runs_world["run_ok"].id) not in ids


@pytest.mark.asyncio
async def test_runs_query_by_repo(db_session, runs_world) -> None:
    box = _toolbox(
        db_session,
        workspace_id=runs_world["workspace"].id,
        user_id=runs_world["user"].id,
    )
    out = json.loads(
        await box.invoke(
            "runs_query", {"repo_id": str(runs_world["repo_b"].id)}
        )
    )
    ids = {r["id"] for r in out["runs"]}
    assert ids == {str(runs_world["run_b"].id)}


@pytest.mark.asyncio
async def test_runs_query_by_status_alias(db_session, runs_world) -> None:
    """``status='ok'`` expands to ``['succeeded']`` per the alias map."""
    box = _toolbox(
        db_session,
        workspace_id=runs_world["workspace"].id,
        user_id=runs_world["user"].id,
    )
    out = json.loads(
        await box.invoke("runs_query", {"status": "ok"})
    )
    ids = {r["id"] for r in out["runs"]}
    # Both ``run_ok`` and ``run_b`` succeeded.
    assert str(runs_world["run_ok"].id) in ids
    assert str(runs_world["run_b"].id) in ids
    assert str(runs_world["run_fail"].id) not in ids


@pytest.mark.asyncio
async def test_runs_query_by_has_escalations(
    db_session, runs_world
) -> None:
    """``has_escalations=true`` keeps only runs with linked inbox items."""
    box = _toolbox(
        db_session,
        workspace_id=runs_world["workspace"].id,
        user_id=runs_world["user"].id,
    )
    out = json.loads(
        await box.invoke("runs_query", {"has_escalations": True})
    )
    ids = {r["id"] for r in out["runs"]}
    assert ids == {str(runs_world["run_fail"].id)}
    assert out["runs"][0]["escalations_count"] == 1


@pytest.mark.asyncio
async def test_runs_query_invalid_repo_id(db_session, seed_workspace) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("runs_query", {"repo_id": "not-a-uuid"})
    )
    assert out["error"] == "invalid_repo_id"


# ---------------------------------------------------------------------------
# run_detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_detail_includes_findings_and_escalations(
    db_session, runs_world
) -> None:
    box = _toolbox(
        db_session,
        workspace_id=runs_world["workspace"].id,
        user_id=runs_world["user"].id,
    )
    out = json.loads(
        await box.invoke(
            "run_detail", {"run_id": str(runs_world["run_fail"].id)}
        )
    )
    assert out["id"] == str(runs_world["run_fail"].id)
    assert out["status"] == "failed"
    assert out["play_key"] == "flow-pr-self-review"
    assert len(out["findings"]) == 2
    assert {f["severity"] for f in out["findings"]} == {"high"}
    assert len(out["artifacts"]) == 1
    assert len(out["escalations"]) == 1
    esc = out["escalations"][0]
    # The deeplink-friendly projection carries inbox_item_id +
    # title/status/type so a chat citation can render directly.
    assert esc["inbox_item_id"] == str(runs_world["inbox_item"].id)
    assert esc["item_status"] == "new"
    assert esc["item_type"] == "failure"
    assert esc["escalation_reason"] == "play_failed_repeatedly"


@pytest.mark.asyncio
async def test_run_detail_cross_workspace_returns_not_found(
    db_session, runs_world
) -> None:
    """Run lookup is workspace-scoped; foreign run → ``not_found``."""
    from backend.app.db.models.tenancy import Workspace, WorkspaceMember

    user = runs_world["user"]
    ws_a = runs_world["workspace"]

    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-{uuid.uuid4().hex[:6]}", name="Other"
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    await db_session.flush()

    foreign_pipeline = await _seed_pipeline(
        db_session, workspace_id=ws_b.id, lane_id="flow-other"
    )
    foreign_run = await _seed_run(
        db_session,
        pipeline_id=foreign_pipeline.id,
        workspace_id=ws_b.id,
    )

    # ToolBox is bound to ws_a — looking up a ws_b run must miss.
    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke("run_detail", {"run_id": str(foreign_run.id)})
    )
    assert out["error"] == "not_found"


@pytest.mark.asyncio
async def test_run_detail_invalid_run_id(db_session, seed_workspace) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("run_detail", {"run_id": "not-a-uuid"})
    )
    assert out["error"] == "invalid_run_id"
