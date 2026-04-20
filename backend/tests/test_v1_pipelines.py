"""End-to-end tests for ``/v1/workspaces/{ws}/pipelines/*`` (Day-4 Phase-1).

Day 3 shipped a stub "Run now" that synthesised a ``succeeded`` row in
the database without any external calls. Day-4 Phase-1 turned that
into a real GitHub Actions ``workflow_dispatch``, so these tests
exercise the new contract:

- ``GET`` lists the seeded defaults with ``workflow_installed`` /
  ``supports_run`` flags resolved (probe is monkeypatched).
- ``PATCH`` toggling still flips ``enabled`` + audits.
- ``POST .../runs`` dispatches via the workflows module when the
  starter file is present (returns 202 + ``running``); returns 412
  with a structured ``code`` when not bound, not supported, or the
  workflow file is missing; returns 502 when GitHub rejects the
  dispatch.
- Disabled pipelines still 409.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Shared helpers
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
        installation_id=999_001,
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
        external_id=42_424_242,
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
    from backend.app.services.default_pipelines import seed_default_pipelines

    pipelines = await seed_default_pipelines(
        db_session, workspace_id, default_repo_id=repo_id
    )
    await db_session.flush()
    return {p.kind: p for p in pipelines}


# ---------------------------------------------------------------------------
# List + toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pipelines_returns_seeded_defaults(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.services.default_pipelines import DEFAULT_PIPELINES

    raw, workspace, _install, repo = seed_repo_and_install
    await _seed_bound_pipelines(db_session, workspace.id, repo.id)

    async def _stub_list_workflows(repo, install, *, settings, **_):
        return frozenset({"ship-pr-gate.yml"})

    monkeypatch.setattr(
        pipelines_route, "list_repo_workflows", _stub_list_workflows
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {p["kind"] for p in body} == {p.kind for p in DEFAULT_PIPELINES}
    assert [p["kind"] for p in body] == [p.kind for p in DEFAULT_PIPELINES]
    by_kind = {p["kind"]: p for p in body}
    # PR review is the one Phase-1 lane; the probe says it's installed.
    assert by_kind["pr_review"]["workflow_installed"] is True
    assert by_kind["pr_review"]["supports_run"] is True
    assert by_kind["pr_review"]["repo_full_name"] == repo.full_name
    # Self-heal still ships off, no executor yet → not_supported branch.
    assert by_kind["self_heal"]["enabled"] is False
    assert by_kind["self_heal"]["supports_run"] is False
    assert by_kind["self_heal"]["workflow_installed"] is None


@pytest.mark.asyncio
async def test_toggle_pipeline_flips_enabled_and_audits(
    v1_client, db_session, seed_repo_and_install
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.pipelines import Pipeline
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["self_heal"]

    response = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"enabled": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True

    refreshed = (
        await db_session.execute(
            select(Pipeline).where(Pipeline.id == target.id)
        )
    ).scalar_one()
    assert refreshed.enabled is True

    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "pipeline.toggle",
            )
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload == {"kind": "self_heal", "enabled": True}


# ---------------------------------------------------------------------------
# Real dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_pipeline_dispatches_when_workflow_installed(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from sqlalchemy import select

    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.db.models.pipelines import Pipeline, PipelineRun

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    captured: dict[str, object] = {}

    async def _probe(repo, install, *, settings, **_):
        return frozenset({"ship-pr-gate.yml"})

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured["workflow_file"] = workflow_file
        captured["inputs"] = dict(inputs)

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
        json={"note": "smoke"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["trigger"] == "manual"

    assert captured["workflow_file"] == "ship-pr-gate.yml"
    inputs = captured["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["ship_run_id"] == body["id"]
    assert inputs["ship_callback_url"].endswith(
        f"/v1/pipelines/runs/{body['id']}/result"
    )
    assert isinstance(inputs["ship_run_token"], str) and len(inputs["ship_run_token"]) > 20

    runs = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "running"
    assert runs[0].run_token_hash and len(runs[0].run_token_hash) == 64

    pipeline = (
        await db_session.execute(
            select(Pipeline).where(Pipeline.id == target.id)
        )
    ).scalar_one()
    assert pipeline.last_run_status == "running"


@pytest.mark.asyncio
async def test_run_pipeline_412_when_workflow_not_installed(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.api.v1.routes import pipelines as pipelines_route

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    async def _probe(repo, install, *, settings, **_):
        return frozenset()  # nothing installed

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 412, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "workflow_not_installed"
    assert detail["workflow_file"] == "ship-pr-gate.yml"
    assert detail["repo_full_name"] == "acme/widgets"
    assert detail["install_endpoint"].endswith("/install")


@pytest.mark.asyncio
async def test_run_pipeline_412_when_not_bound(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.services.default_pipelines import seed_default_pipelines

    _, raw, workspace = seed_workspace
    pipelines = await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()
    target = next(p for p in pipelines if p.kind == "pr_review")

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 412, response.text
    assert response.json()["detail"]["code"] == "pipeline_not_bound"


@pytest.mark.asyncio
async def test_run_pipeline_412_when_kind_not_supported(
    v1_client, db_session, seed_repo_and_install
) -> None:
    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["tech_debt"]

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 412, response.text
    assert response.json()["detail"]["code"] == "kind_not_supported_yet"


@pytest.mark.asyncio
async def test_run_pipeline_502_when_dispatch_fails(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from sqlalchemy import select

    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.db.models.pipelines import PipelineRun
    from backend.app.integrations.github.workflows import WorkflowDispatchError

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    async def _probe(repo, install, *, settings, **_):
        return frozenset({"ship-pr-gate.yml"})

    async def _dispatch(*_args, **_kwargs):
        raise WorkflowDispatchError(503, "GitHub had a bad day")

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "dispatch_failed"

    # Run row should be persisted as ``failed`` so the dashboard can
    # show the diagnostic instead of ghosting the click.
    runs = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "failed"


@pytest.mark.asyncio
async def test_run_pipeline_rejects_disabled(
    v1_client, db_session, seed_repo_and_install
) -> None:
    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["self_heal"]  # ships disabled

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 409
    assert "disabled" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Result callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_updates_run_with_valid_token(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from sqlalchemy import select

    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.db.models.pipelines import Pipeline, PipelineRun

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    captured_inputs: dict[str, str] = {}

    async def _probe(repo, install, *, settings, **_):
        return frozenset({"ship-pr-gate.yml"})

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured_inputs.update(inputs)

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    dispatch_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    run_id = dispatch_resp.json()["id"]
    token = captured_inputs["ship_run_token"]

    callback_resp = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "status": "succeeded",
            "summary": "Gate green",
            "metrics": {"gh_workflow_run_id": 12345, "gh_html_url": "https://gh"},
        },
    )
    assert callback_resp.status_code == 200, callback_resp.text
    body = callback_resp.json()
    assert body["status"] == "succeeded"
    assert body["summary"] == "Gate green"

    refreshed = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.id == uuid.UUID(run_id))
        )
    ).scalar_one()
    assert refreshed.status == "succeeded"
    assert refreshed.finished_at is not None
    assert refreshed.payload.get("metrics", {}).get("gh_workflow_run_id") == 12345

    pipeline = (
        await db_session.execute(
            select(Pipeline).where(Pipeline.id == target.id)
        )
    ).scalar_one()
    assert pipeline.last_run_status == "succeeded"


@pytest.mark.asyncio
async def test_callback_rejects_missing_bearer(v1_client) -> None:
    response = await v1_client.post(
        f"/v1/pipelines/runs/{uuid.uuid4()}/result",
        json={"status": "succeeded"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_callback_rejects_wrong_token(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.api.v1.routes import pipelines as pipelines_route

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    captured: dict[str, str] = {}

    async def _probe(repo, install, *, settings, **_):
        return frozenset({"ship-pr-gate.yml"})

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured.update(inputs)

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    dispatch_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    run_id = dispatch_resp.json()["id"]

    # Token issued for a *different* run id — JWT decodes fine but
    # ``rid`` mismatch should bounce.
    from backend.app.api.v1.routes.pipelines import _mint_run_token
    from backend.app.core.config import get_settings

    bad_token = _mint_run_token(uuid.uuid4(), get_settings())

    response = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {bad_token}"},
        json={"status": "succeeded"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_callback_idempotent_for_terminal_runs(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.api.v1.routes import pipelines as pipelines_route

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    captured: dict[str, str] = {}

    async def _probe(repo, install, *, settings, **_):
        return frozenset({"ship-pr-gate.yml"})

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured.update(inputs)

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    dispatch_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    run_id = dispatch_resp.json()["id"]
    token = captured["ship_run_token"]

    first = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "succeeded", "summary": "first"},
    )
    assert first.status_code == 200

    second = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "failed", "summary": "second should be ignored"},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "succeeded"
    assert second.json()["summary"] == "first"


# ---------------------------------------------------------------------------
# Install endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_pipeline_workflow_opens_pr(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from sqlalchemy import select

    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.db.models.tenancy import AuditLog
    from backend.app.integrations.github.workflows import StarterWorkflowPR

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    seen: dict[str, object] = {}

    async def _commit(repo, install, *, workflow_file, content, pipeline_kind, settings, **_):
        seen["workflow_file"] = workflow_file
        seen["pipeline_kind"] = pipeline_kind
        # Sanity: starter YAML loaded from disk should mention the
        # callback step we documented in the artifact.
        seen["content_loaded"] = "ship_callback_url" in content
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/widgets/pull/42",
            pr_number=42,
            branch="ship/install-pr_review-1",
        )

    monkeypatch.setattr(pipelines_route, "commit_starter_workflow", _commit)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/install",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["pr_number"] == 42
    assert body["pr_url"].endswith("/42")
    assert seen["workflow_file"] == "ship-pr-gate.yml"
    assert seen["pipeline_kind"] == "pr_review"
    assert seen["content_loaded"] is True

    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "pipeline.install",
            )
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload["pr_number"] == 42


@pytest.mark.asyncio
async def test_install_412_when_kind_not_supported(
    v1_client, db_session, seed_repo_and_install
) -> None:
    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["tech_debt"]

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/install",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 412
    assert response.json()["detail"]["code"] == "kind_not_supported_yet"


# ---------------------------------------------------------------------------
# 404 cross-tenant safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_pipeline_returns_404(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    response = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/pipelines/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"enabled": True},
    )
    assert response.status_code == 404
