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
    from backend.app.services.lane_recipes import seed_default_pipelines

    pipelines = await seed_default_pipelines(
        db_session, workspace_id, default_repo_id=repo_id
    )
    await db_session.flush()
    return {p.lane_id: p for p in pipelines}


# ---------------------------------------------------------------------------
# List + toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pipelines_returns_seeded_defaults(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.services.lane_recipes import list_lane_recipes

    raw, workspace, _install, repo = seed_repo_and_install
    await _seed_bound_pipelines(db_session, workspace.id, repo.id)

    async def _stub_list_workflows(repo, install, *, settings, **_):
        return frozenset({"pr-and-ci-gate.yml"})

    monkeypatch.setattr(
        pipelines_route, "list_repo_workflows", _stub_list_workflows
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    expected_ids = [r.lane_id for r in list_lane_recipes()]
    assert {p["kind"] for p in body} == set(expected_ids)
    assert [p["kind"] for p in body] == expected_ids
    by_kind = {p["kind"]: p for p in body}
    # PR review is installed (probe stub says so).
    assert by_kind["pr_review"]["workflow_installed"] is True
    assert by_kind["pr_review"]["supports_run"] is True
    assert by_kind["pr_review"]["repo_full_name"] == repo.full_name
    # Phase-2 added catalog starters for daily-standup / tech-debt /
    # self-heal — they now advertise ``supports_run`` but the probe
    # only reported ``pr-and-ci-gate.yml``, so their ``workflow_installed``
    # flag is False (dashboard renders Install-workflow CTA).
    assert by_kind["self_heal"]["enabled"] is False
    assert by_kind["self_heal"]["supports_run"] is True
    assert by_kind["self_heal"]["workflow_installed"] is False
    assert by_kind["daily_standup"]["supports_run"] is True
    assert by_kind["daily_standup"]["workflow_installed"] is False
    assert by_kind["tech_debt"]["supports_run"] is True
    assert by_kind["tech_debt"]["workflow_installed"] is False
    # ``code_map`` is still resolver-only (no catalog workflow.yml).
    assert by_kind["code_map"]["supports_run"] is False
    assert by_kind["code_map"]["workflow_installed"] is None


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
        return frozenset({"pr-and-ci-gate.yml"})

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

    assert captured["workflow_file"] == "pr-and-ci-gate.yml"
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
async def test_get_pipeline_run(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """GET …/pipelines/{id}/runs/{run_id} returns the row for the console."""
    from backend.app.api.v1.routes import pipelines as pipelines_route

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    async def _probe(repo, install, *, settings, **_):
        return frozenset({"pr-and-ci-gate.yml"})

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        pass

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    post = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert post.status_code == 202, post.text
    run_id = post.json()["id"]

    ok = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs/{run_id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["id"] == run_id
    assert body["pipeline_id"] == str(target.id)
    assert body["status"] == "running"
    assert "created_at" in body

    missing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert missing.status_code == 404


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
    assert detail["workflow_file"] == "pr-and-ci-gate.yml"
    assert detail["repo_full_name"] == "acme/widgets"
    assert detail["install_endpoint"].endswith("/install")


@pytest.mark.asyncio
async def test_run_pipeline_412_when_not_bound(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.services.lane_recipes import seed_default_pipelines

    _, raw, workspace = seed_workspace
    pipelines = await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()
    target = next(p for p in pipelines if p.lane_id == "pr_review")

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 412, response.text
    assert response.json()["detail"]["code"] == "pipeline_not_bound"


@pytest.mark.asyncio
async def test_run_pipeline_rebinds_when_explicit_repo_id(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """When the card posts a ``repo_id`` from a specific swimlane, the
    backend dispatches against that repo and rebinds the pipeline —
    even if it was previously bound to a different repo."""
    from datetime import datetime, timezone

    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.integrations.github import workflows as workflows_mod

    raw, workspace, install, first_repo = seed_repo_and_install
    # Seed a second activated repo in the same workspace — this is
    # the one the user will target explicitly via the swimlane card.
    second_repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=7_000_000,
        full_name="acme/second",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/second",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(second_repo)
    await db_session.flush()

    pipelines = await _seed_bound_pipelines(db_session, workspace.id, first_repo.id)
    target = pipelines["pr_review"]

    captured: dict[str, object] = {}

    async def _probe(repo, install, *, settings, **_):
        captured["probe_repo"] = repo.full_name
        return frozenset({"pr-and-ci-gate.yml"})

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured["dispatch_repo"] = repo.full_name

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)
    monkeypatch.setattr(workflows_mod, "list_repo_workflows", _probe)
    monkeypatch.setattr(workflows_mod, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
        json={"repo_id": str(second_repo.id)},
    )
    assert response.status_code == 202, response.text
    assert captured["dispatch_repo"] == "acme/second"
    assert captured["probe_repo"] == "acme/second"

    # Rebind persisted: next Run-now without ``repo_id`` would target
    # the second repo, not the original.
    await db_session.refresh(target)
    assert target.repo_id == second_repo.id


@pytest.mark.asyncio
async def test_run_pipeline_rejects_unknown_explicit_repo_id(
    v1_client, db_session, seed_repo_and_install
) -> None:
    """Explicit ``repo_id`` must resolve to a repo in the same
    workspace; passing an unknown UUID 412s instead of silently
    rebinding to a stored repo or to the default."""
    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    phantom = uuid.uuid4()
    assert phantom != repo.id

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
        json={"repo_id": str(phantom)},
    )
    assert response.status_code == 412, response.text
    assert response.json()["detail"]["code"] == "pipeline_not_bound"


@pytest.mark.asyncio
async def test_install_pipeline_workflow_rebinds_when_explicit_repo_id(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """Install PR targets the repo the user posted from the swimlane,
    not the pipeline's pre-existing binding."""
    from datetime import datetime, timezone

    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.integrations.github.workflows import StarterWorkflowPR

    raw, workspace, install, first_repo = seed_repo_and_install
    second_repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=7_100_000,
        full_name="acme/install-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/install-target",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(second_repo)
    await db_session.flush()

    pipelines = await _seed_bound_pipelines(db_session, workspace.id, first_repo.id)
    target = pipelines["pr_review"]

    captured: dict[str, object] = {}

    async def _commit(repo, install, *, workflow_file, content, pipeline_kind, settings, **_):
        captured["repo"] = repo.full_name
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/install-target/pull/7",
            pr_number=7,
            branch="ship/install-pr_review-1",
        )

    monkeypatch.setattr(pipelines_route, "commit_starter_workflow", _commit)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/install",
        headers={"Authorization": f"Bearer {raw}"},
        json={"repo_id": str(second_repo.id)},
    )
    assert response.status_code == 201, response.text
    assert captured["repo"] == "acme/install-target"

    await db_session.refresh(target)
    assert target.repo_id == second_repo.id


@pytest.mark.asyncio
async def test_run_pipeline_auto_binds_when_single_repo(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """Phase-3 convenience: legacy pipelines with ``repo_id=None``
    auto-bind to the workspace's sole activated repo instead of
    surfacing the cryptic ``pipeline_not_bound`` error. The dispatcher
    then proceeds as if the pipeline had been bound from day one."""
    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.integrations.github import workflows as workflows_mod
    from backend.app.services.lane_recipes import seed_default_pipelines

    raw, workspace, _install, repo = seed_repo_and_install
    # Seed without ``default_repo_id`` so rows come out unbound — this
    # is the exact shape legacy pilots observed on Day 3.
    pipelines = await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()
    target = next(p for p in pipelines if p.lane_id == "pr_review")
    assert target.repo_id is None

    async def _probe(*args, **kwargs):
        return frozenset({"pr-and-ci-gate.yml"})

    async def _dispatch(*args, **kwargs):
        return None

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)
    monkeypatch.setattr(workflows_mod, "list_repo_workflows", _probe)
    monkeypatch.setattr(workflows_mod, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 202, response.text
    # DB row now carries the binding — repeat runs don't need the
    # auto-bind heuristic anymore.
    await db_session.refresh(target)
    assert target.repo_id == repo.id


@pytest.mark.asyncio
async def test_run_pipeline_412_when_kind_not_supported(
    v1_client, db_session, seed_repo_and_install
) -> None:
    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    # ``code_map`` has no catalog workflow (resolver-only), so dispatch
    # still 412s with ``kind_not_supported_yet`` — the rest of the
    # DEFAULT_PIPELINES are catalog-backed as of Phase 2.
    target = pipelines["code_map"]

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
        return frozenset({"pr-and-ci-gate.yml"})

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
        return frozenset({"pr-and-ci-gate.yml"})

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
        return frozenset({"pr-and-ci-gate.yml"})

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
        return frozenset({"pr-and-ci-gate.yml"})

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
# Callback via long-lived repo token (RFC-0007 lane path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_accepts_long_lived_repo_token(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """Lane-triggered runners (cron / push / PR) have no JWT — they
    auth via ``secrets.SHIP_RUN_TOKEN``. Prove the callback endpoint
    accepts that path, maps the bearer to the repo, and lands the
    same audit trail with ``auth_mode: "repo"`` so operators can
    tell the two flows apart.
    """
    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.core.config import get_settings
    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services import repo_tokens

    raw, workspace, install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]
    settings = get_settings()

    # Mint a ``SHIP_RUN_TOKEN`` without touching the real GitHub API.
    async def _fake_put(*args, **kwargs):
        return "keyid-stub"

    monkeypatch.setattr(repo_tokens, "put_repo_secret", _fake_put)
    shipctoken = await repo_tokens.mint_repo_callback_token(
        db_session, repo, install, settings=settings
    )
    await db_session.commit()

    # Dispatch a run through the normal flow so we have a concrete
    # ``run_id`` bound to this repo's pipeline.
    async def _probe(repo, install, *, settings, **_):
        return frozenset({"pr-and-ci-gate.yml"})

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        return None

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    dispatch_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    run_id = dispatch_resp.json()["id"]

    # Callback under the long-lived token. The opaque bearer can't
    # carry ``rid``, so the endpoint's cross-check is
    # ``pipeline.repo_id == repo.id`` — that's what we're proving.
    callback_resp = await v1_client.post(
        f"/v1/pipelines/runs/{run_id}/result",
        headers={"Authorization": f"Bearer {shipctoken}"},
        json={"status": "succeeded", "summary": "lane ok"},
    )
    assert callback_resp.status_code == 200, callback_resp.text
    assert callback_resp.json()["status"] == "succeeded"

    from sqlalchemy import select as _sa_select

    audit = (
        await db_session.execute(
            _sa_select(AuditLog).where(
                AuditLog.action == "pipeline.run.callback",
                AuditLog.target_id == run_id,
            )
        )
    ).scalar_one()
    assert audit.payload.get("auth_mode") == "repo"


@pytest.mark.asyncio
async def test_callback_rejects_repo_token_for_foreign_run(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """Cross-repo forgery guard: a valid ``SHIP_RUN_TOKEN`` for repo A
    must not be able to land results against a run belonging to
    repo B. We insert a sibling repo + its own pipeline, mint a
    token for repo A, then target repo B's run. The endpoint must
    401 without leaking which check failed.
    """
    from datetime import datetime, timezone

    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.core.config import get_settings
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.services import repo_tokens

    raw, workspace, install, repo_a = seed_repo_and_install

    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_424_243,
        full_name="acme/other",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/other",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo_b)
    await db_session.flush()

    pipelines_b = await _seed_bound_pipelines(db_session, workspace.id, repo_b.id)
    target_b = pipelines_b["pr_review"]

    settings = get_settings()

    async def _fake_put(*args, **kwargs):
        return "keyid-stub"

    monkeypatch.setattr(repo_tokens, "put_repo_secret", _fake_put)
    # Token for repo A.
    shipctoken_a = await repo_tokens.mint_repo_callback_token(
        db_session, repo_a, install, settings=settings
    )
    await db_session.commit()

    async def _probe(repo, install, *, settings, **_):
        return frozenset({"pr-and-ci-gate.yml"})

    async def _dispatch(*args, **kwargs):
        return None

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    # Dispatch against repo B so the resulting run is bound to B.
    dispatch_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target_b.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    run_id_b = dispatch_resp.json()["id"]

    # Present repo A's token against repo B's run → 401.
    resp = await v1_client.post(
        f"/v1/pipelines/runs/{run_id_b}/result",
        headers={"Authorization": f"Bearer {shipctoken_a}"},
        json={"status": "succeeded"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Policies preamble (Workspace policy injection — shipctl side)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policies_preamble_returns_null_when_empty(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
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
    run_id = dispatch_resp.json()["id"]
    token = captured["ship_run_token"]

    resp = await v1_client.get(
        f"/v1/pipelines/runs/{run_id}/policies-preamble",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"preamble": None}


@pytest.mark.asyncio
async def test_policies_preamble_renders_enabled_only(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.db.models.policies import WorkspacePolicy

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["pr_review"]

    db_session.add_all(
        [
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Always work via PR",
                body="Never push directly to main.",
                enabled=True,
                sort_order=0,
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Disabled rule",
                body="Should not render.",
                enabled=False,
                sort_order=-1,
            ),
        ]
    )
    await db_session.flush()

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
    run_id = dispatch_resp.json()["id"]
    token = captured["ship_run_token"]

    resp = await v1_client.get(
        f"/v1/pipelines/runs/{run_id}/policies-preamble",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    preamble = resp.json()["preamble"]
    assert preamble is not None
    assert preamble.startswith("# Workspace policies")
    assert "## Always work via PR" in preamble
    assert "Never push directly to main." in preamble
    assert "Disabled rule" not in preamble


@pytest.mark.asyncio
async def test_policies_preamble_rejects_missing_bearer(v1_client) -> None:
    resp = await v1_client.get(
        f"/v1/pipelines/runs/{uuid.uuid4()}/policies-preamble",
    )
    assert resp.status_code == 401


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
    assert seen["workflow_file"] == "pr-and-ci-gate.yml"
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
    # ``code_map`` has no catalog workflow → install 412s.
    target = pipelines["code_map"]

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/install",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 412
    assert response.json()["detail"]["code"] == "kind_not_supported_yet"


@pytest.mark.asyncio
async def test_install_pipeline_workflow_daily_standup(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """Phase-2 lanes (daily_standup, tech_debt, self_heal) install too."""
    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.integrations.github.workflows import StarterWorkflowPR

    raw, workspace, _install, repo = seed_repo_and_install
    pipelines = await _seed_bound_pipelines(db_session, workspace.id, repo.id)
    target = pipelines["daily_standup"]

    seen: dict[str, object] = {}

    async def _commit(repo, install, *, workflow_file, content, pipeline_kind, settings, **_):
        seen["workflow_file"] = workflow_file
        seen["pipeline_kind"] = pipeline_kind
        seen["has_callback"] = "ship_callback_url" in content
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/widgets/pull/43",
            pr_number=43,
            branch="ship/install-daily_standup-1",
        )

    monkeypatch.setattr(pipelines_route, "commit_starter_workflow", _commit)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/install",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 201, response.text
    assert seen["workflow_file"] == "scheduled-sdlc-lane.yml"
    assert seen["pipeline_kind"] == "daily_standup"
    assert seen["has_callback"] is True


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
