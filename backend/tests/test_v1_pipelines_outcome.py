"""End-to-end tests for the RFC-0010 §RunSummary contract (P3-01).

Covers the new ``pipeline_runs.outcome`` JSONB column + Pydantic
schema:

- The result callback persists a full :class:`RunSummary` payload.
- Pre-P3-01 callers (no ``outcome`` key) still 200 and the column
  defaults to ``{}``.
- Strict validation: extras forbidden, fields with constraints
  (``ge=0`` etc.) reject 422 at the API boundary.
- The list endpoint surfaces the column on every run row.
- Direct SQL inserts (i.e. legacy rows that pre-date the migration)
  read back as ``outcome={}`` thanks to the ``DEFAULT '{}'::jsonb``.

Tests piggyback on the existing dispatch fixtures in
``test_v1_pipelines.py`` so the wire format matches the real
callback path (per-run JWT, pipeline rebinding, etc.).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Shared fixtures (mirror the ones in test_v1_pipelines.py — kept inline so
# this file is self-contained; the test_v1_pipelines fixtures aren't
# re-exported via conftest.)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_repo_and_install(db_session, seed_workspace):
    """Insert a GitHub install + activated repo for the seeded workspace."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=999_002,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_424_244,
        full_name="acme/widgets",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/widgets",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return raw, workspace, install, repo


async def _seed_bound_pipelines(db_session, workspace_id, repo_id):
    from backend.app.services.lane_recipes import seed_default_pipelines

    pipelines = await seed_default_pipelines(
        db_session, workspace_id, default_repo_id=repo_id
    )
    await db_session.flush()
    return {p.lane_id: p for p in pipelines}


async def _dispatch_run(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> tuple[str, str, object, object]:
    """Dispatch a run and return ``(run_id, run_token, raw, workspace)``.

    Centralises the boilerplate so each test reads as
    "dispatch -> callback with X -> assert"; the dispatch wiring
    matches the real flow (JWT minted via the ``ship_run_token``
    workflow input).
    """
    from backend.app.api.v1.routes import pipelines as pipelines_route

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    captured: dict[str, str] = {}

    async def _probe(repo, install, *, settings, **_):
        return frozenset({"pr-and-ci-gate.yml"})

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured.update(inputs)

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    dispatch_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert dispatch_resp.status_code == 202, dispatch_resp.text
    run_id = dispatch_resp.json()["id"]
    return run_id, captured["ship_run_token"], raw, workspace, target


# Sample outcome the pattern reporter would emit. Exercises every
# branch of the RunSummary contract (text, severity buckets,
# artifacts, approval payload, escalations[]).
_FULL_OUTCOME = {
    "outcome_text": "3 issues found · 1 PR opened",
    "headline": "Security audit complete",
    "findings_count": 3,
    "findings_by_severity": {
        "low": 1,
        "medium": 1,
        "high": 1,
        "critical": 0,
    },
    "artifacts": [
        {
            "type": "pr",
            "title": "Bump openssl to 3.2.2",
            "ref": "https://github.com/acme/widgets/pull/42",
        },
        {
            "type": "issue",
            "title": "Track flaky test in payments",
            "ref": "GH-101",
        },
    ],
    "requires_approval": True,
    "approval_payload": {"reason": "license downgrade"},
    "escalations": [
        {"type": "approval", "reason": "autofix_proposed"},
    ],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_stores_outcome_summary(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """Full RunSummary payload round-trips through callback + GET."""
    from sqlalchemy import select

    from backend.app.db.models.pipelines import PipelineRun

    run_id, token, raw, workspace, target = await _dispatch_run(
        monkeypatch, v1_client, db_session, seed_repo_and_install
    )

    callback_resp = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "status": "succeeded",
            "summary": "Audit ok",
            "outcome": _FULL_OUTCOME,
        },
    )
    assert callback_resp.status_code == 200, callback_resp.text
    body = callback_resp.json()
    assert body["status"] == "succeeded"

    # Returned model surfaces the full outcome.
    assert body["outcome"]["outcome_text"] == "3 issues found · 1 PR opened"
    assert body["outcome"]["headline"] == "Security audit complete"
    assert body["outcome"]["findings_count"] == 3
    assert body["outcome"]["findings_by_severity"] == {
        "low": 1,
        "medium": 1,
        "high": 1,
        "critical": 0,
    }
    assert len(body["outcome"]["artifacts"]) == 2
    assert body["outcome"]["artifacts"][0]["type"] == "pr"
    assert body["outcome"]["requires_approval"] is True
    assert body["outcome"]["approval_payload"] == {"reason": "license downgrade"}
    assert body["outcome"]["escalations"] == [
        {"type": "approval", "reason": "autofix_proposed"}
    ]

    # Persisted in the column verbatim.
    refreshed = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.id == uuid.UUID(run_id))
        )
    ).scalar_one()
    assert refreshed.outcome["outcome_text"] == "3 issues found · 1 PR opened"
    assert refreshed.outcome["findings_count"] == 3
    assert refreshed.outcome["requires_approval"] is True

    # Re-read via the per-run GET path to exercise PipelineRunOut.
    detail_resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs/{run_id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail_body = detail_resp.json()
    assert detail_body["outcome"]["outcome_text"] == "3 issues found · 1 PR opened"
    assert detail_body["outcome"]["findings_by_severity"]["high"] == 1


@pytest.mark.asyncio
async def test_callback_without_outcome_is_backwards_compatible(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """Pre-P3-01 callback shape (no ``outcome`` key) still 200s."""
    from sqlalchemy import select

    from backend.app.db.models.pipelines import PipelineRun

    run_id, token, _raw, _workspace, _target = await _dispatch_run(
        monkeypatch, v1_client, db_session, seed_repo_and_install
    )

    callback_resp = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "succeeded", "summary": "legacy ok"},
    )
    assert callback_resp.status_code == 200, callback_resp.text
    body = callback_resp.json()
    # Out shape always includes ``outcome``; defaults to empty
    # RunSummary (every field at its model default).
    assert body["outcome"] == {
        "outcome_text": None,
        "headline": None,
        "findings_count": None,
        "findings_by_severity": None,
        "artifacts": [],
        "requires_approval": False,
        "approval_payload": {},
        "escalations": [],
    }

    refreshed = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.id == uuid.UUID(run_id))
        )
    ).scalar_one()
    # Migration default kept the column as an empty dict.
    assert refreshed.outcome == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_payload",
    [
        # Extras forbidden at the top level.
        {
            "status": "succeeded",
            "outcome": {"outcome_text": "ok", "unknown_field": 1},
        },
        # Negative findings_count (ge=0 violation).
        {
            "status": "succeeded",
            "outcome": {"findings_count": -1},
        },
        # Severity bucket negative.
        {
            "status": "succeeded",
            "outcome": {"findings_by_severity": {"high": -2}},
        },
        # Extras forbidden inside a sub-model.
        {
            "status": "succeeded",
            "outcome": {
                "artifacts": [
                    {
                        "type": "pr",
                        "title": "x",
                        "ref": "y",
                        "weird": True,
                    }
                ]
            },
        },
        # Escalation type outside the Literal.
        {
            "status": "succeeded",
            "outcome": {
                "escalations": [{"type": "nope", "reason": "bad"}]
            },
        },
    ],
    ids=[
        "extra_top_level",
        "negative_findings_count",
        "negative_severity_bucket",
        "extra_artifact_field",
        "bad_escalation_type",
    ],
)
async def test_callback_with_invalid_outcome_returns_422(
    monkeypatch, v1_client, db_session, seed_repo_and_install, bad_payload
) -> None:
    """Schema drift caught at the API boundary (extras forbidden, etc.)."""
    run_id, token, _raw, _workspace, _target = await _dispatch_run(
        monkeypatch, v1_client, db_session, seed_repo_and_install
    )
    response = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json=bad_payload,
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_pipeline_runs_list_includes_outcome(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """List endpoint surfaces the persisted outcome on every row."""
    run_id, token, raw, workspace, target = await _dispatch_run(
        monkeypatch, v1_client, db_session, seed_repo_and_install
    )

    callback_resp = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "succeeded", "outcome": _FULL_OUTCOME},
    )
    assert callback_resp.status_code == 200, callback_resp.text

    list_resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert list_resp.status_code == 200, list_resp.text
    rows = list_resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == run_id
    assert rows[0]["outcome"]["outcome_text"] == "3 issues found · 1 PR opened"
    assert rows[0]["outcome"]["findings_count"] == 3
    assert rows[0]["outcome"]["requires_approval"] is True


@pytest.mark.asyncio
async def test_outcome_default_empty_for_legacy_rows(
    v1_client, db_session, seed_workspace
) -> None:
    """Direct INSERT without ``outcome`` lands ``{}`` via the migration default.

    Simulates a row that pre-dates the P3-01 migration: we never set
    ``outcome`` on the ORM object, the column default kicks in, and
    GET returns an empty :class:`RunSummary`.
    """
    from backend.app.db.models.pipelines import Pipeline, PipelineRun

    user, raw, workspace = seed_workspace

    pipeline = Pipeline(
        workspace_id=workspace.id,
        repo_id=None,
        lane_id=f"legacy_test_{uuid.uuid4().hex[:8]}",
        name="legacy run lane",
        workflow_id="pr-and-ci-gate",
    )
    db_session.add(pipeline)
    await db_session.flush()
    run = PipelineRun(
        pipeline_id=pipeline.id,
        workspace_id=workspace.id,
        trigger="manual",
        status="succeeded",
    )
    db_session.add(run)
    await db_session.flush()
    # Force the server-side default to materialise on the in-memory row.
    await db_session.refresh(run)

    assert run.outcome == {}

    detail_resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines/{pipeline.id}/runs/{run.id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert detail_resp.status_code == 200, detail_resp.text
    body = detail_resp.json()
    # Empty dict round-trips as the model defaults.
    assert body["outcome"]["outcome_text"] is None
    assert body["outcome"]["findings_count"] is None
    assert body["outcome"]["artifacts"] == []
    assert body["outcome"]["requires_approval"] is False
