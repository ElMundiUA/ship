"""Scope-filter tests for ``GET /v1/workspaces/{ws}/pipelines`` (P1-09).

Mirrors ``test_v1_lanes_scope.py``. Pins the contract that the new
``/runs`` console route uses to fold the legacy
``/pipelines`` + ``/fleet/*`` surfaces into one query.

For pipelines, ``scope=fleet`` returns only :class:`Pipeline` rows
whose ``lane_id`` is materialised by a :class:`FleetLane` in the
workspace (i.e. the per-repo Pipelines a workspace fleet rule
generated). ``scope=repo`` requires ``repo_id`` and filters by it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_pipelines_environment(db_session, seed_workspace):
    """Two repos × two pipelines + one fleet lane that mirrors one lane id."""
    from backend.app.db.models.fleet_lanes import FleetLane
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.pipelines import Pipeline

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=8_300_001,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo_a = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=70_001,
        full_name="acme/api",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/api",
        activated_at=datetime.now(timezone.utc),
    )
    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=70_002,
        full_name="acme/web",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/web",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

    # ``Pipeline`` has a unique constraint on ``(workspace_id, lane_id)``,
    # so the per-repo materialisation of a "fleet" lane needs a distinct
    # lane_id per repo. Match that by suffixing — ``daily_retro_api`` /
    # ``daily_retro_web`` — and have the FleetLane mirror only the
    # ``api`` slot so we still test "scope=fleet returns the subset
    # whose lane_id is materialised by a FleetLane".
    db_session.add_all(
        [
            Pipeline(
                workspace_id=workspace.id,
                repo_id=repo_a.id,
                name="PR review (api)",
                workflow_id="pr-and-ci-gate",
                lane_id="pr_review_api",
                enabled=True,
                config={},
            ),
            Pipeline(
                workspace_id=workspace.id,
                repo_id=repo_a.id,
                name="Daily retro (api)",
                workflow_id="scheduled-sdlc-lane",
                lane_id="daily_retro_api",
                enabled=True,
                config={},
            ),
            Pipeline(
                workspace_id=workspace.id,
                repo_id=repo_b.id,
                name="Daily retro (web)",
                workflow_id="scheduled-sdlc-lane",
                lane_id="daily_retro_web",
                enabled=True,
                config={},
            ),
        ]
    )
    db_session.add(
        FleetLane(
            workspace_id=workspace.id,
            kind="mirror_lane",
            name="Daily retro (fleet)",
            pattern_id="flow-daily-retro",
            lane_id="daily_retro_api",
            cadence="@daily",
            inputs={},
            enabled=True,
        )
    )
    await db_session.flush()

    return raw, workspace, repo_a, repo_b


async def _stub_workflow_probe(monkeypatch) -> None:
    """Pipelines list calls GitHub to probe workflow availability —
    stub it so the suite never hits api.github.com.
    """
    from backend.app.api.v1.routes import pipelines as pipelines_route

    async def _probe(repo, install, *, settings, **_):
        return frozenset()

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)


@pytest.mark.asyncio
async def test_pipelines_default_scope_returns_all(
    monkeypatch, v1_client, seed_pipelines_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_pipelines_environment
    await _stub_workflow_probe(monkeypatch)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 3
    names = sorted(p["name"] for p in body)
    assert names == [
        "Daily retro (api)",
        "Daily retro (web)",
        "PR review (api)",
    ]


@pytest.mark.asyncio
async def test_pipelines_scope_fleet_returns_only_fleet_pipelines(
    monkeypatch, v1_client, seed_pipelines_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_pipelines_environment
    await _stub_workflow_probe(monkeypatch)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "fleet"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only the lane the FleetLane mirrors (``daily_retro_api``) — the
    # other repo's pipeline + the PR review aren't fleet-mirrored, so
    # they drop out.
    assert len(body) == 1
    assert body[0]["kind"] == "daily_retro_api"


@pytest.mark.asyncio
async def test_pipelines_scope_repo_requires_repo_id(
    monkeypatch, v1_client, seed_pipelines_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_pipelines_environment
    await _stub_workflow_probe(monkeypatch)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "repo"},
    )
    assert resp.status_code == 422
    assert "repo_id is required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_pipelines_scope_repo_filters_by_repo_id(
    monkeypatch, v1_client, seed_pipelines_environment
) -> None:
    raw, workspace, repo_a, _repo_b = seed_pipelines_environment
    await _stub_workflow_probe(monkeypatch)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "repo", "repo_id": str(repo_a.id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 2
    assert all(p["repo_id"] == str(repo_a.id) for p in body)


@pytest.mark.asyncio
async def test_pipelines_scope_repo_unknown_repo_id_returns_422(
    monkeypatch, v1_client, seed_pipelines_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_pipelines_environment
    await _stub_workflow_probe(monkeypatch)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "repo", "repo_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422
    assert "workspace" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_pipelines_scope_fleet_with_repo_id_returns_422(
    monkeypatch, v1_client, seed_pipelines_environment
) -> None:
    raw, workspace, repo_a, _repo_b = seed_pipelines_environment
    await _stub_workflow_probe(monkeypatch)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "fleet", "repo_id": str(repo_a.id)},
    )
    assert resp.status_code == 422
    assert "scope=repo" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_pipelines_scope_invalid_value_returns_422(
    monkeypatch, v1_client, seed_pipelines_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_pipelines_environment
    await _stub_workflow_probe(monkeypatch)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "everything"},
    )
    assert resp.status_code == 422
