"""Scope-filter tests for ``GET /v1/workspaces/{ws}/lanes`` (P1-09).

Pins the contract that lets the new ``/automations`` console route
consolidate the legacy ``/lanes`` + ``/fleet/lanes`` surfaces into
one endpoint via ``?scope=fleet|repo|all`` (+ ``?repo_id=``). The
``/lanes`` HTTP path stays the same — only the query params and the
unified ``LaneOut`` shape are new.

Covered:

- Default (no scope param) returns repo + fleet lanes (current
  behaviour for the legacy callers).
- ``scope=fleet`` returns only :class:`FleetLane` rows.
- ``scope=repo`` requires ``repo_id`` (422) and filters by it
  (rejects cross-workspace ids with 422).
- The XOR validator rejects ``scope=fleet&repo_id=...`` (422).
- ``scope=<garbage>`` is a 422 from FastAPI's Literal validator.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_lanes_environment(db_session, seed_workspace):
    """Two activated repos + a repo lane on each + one workspace fleet lane."""
    from backend.app.db.models.fleet_lanes import FleetLane
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.lanes import Lane

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=4_242_111,
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
        external_id=50_001,
        full_name="acme/widgets",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/widgets",
        activated_at=datetime.now(timezone.utc),
    )
    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=50_002,
        full_name="acme/cogs",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/cogs",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

    db_session.add_all(
        [
            Lane(
                workspace_id=workspace.id,
                repo_id=repo_a.id,
                lane_id="pr_review",
                kind="event",
                pattern="pr-and-ci-gate",
                config_blob={},
            ),
            Lane(
                workspace_id=workspace.id,
                repo_id=repo_b.id,
                lane_id="daily",
                kind="schedule",
                cron="0 9 * * *",
                pattern="scheduled-sdlc-lane",
                config_blob={},
            ),
        ]
    )
    db_session.add(
        FleetLane(
            workspace_id=workspace.id,
            kind="mirror_lane",
            name="Nightly retro",
            pattern_id="flow-daily-retro",
            lane_id="fleet_retro",
            cadence="@daily",
            inputs={"team": "platform"},
            enabled=True,
        )
    )
    await db_session.flush()

    return raw, workspace, repo_a, repo_b


@pytest.mark.asyncio
async def test_lanes_default_scope_returns_all(
    v1_client, seed_lanes_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_lanes_environment

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    scopes = {row["scope"] for row in body["lanes"]}
    assert scopes == {"repo", "fleet"}
    assert {row["lane_id"] for row in body["lanes"]} == {
        "pr_review",
        "daily",
        "fleet_retro",
    }


@pytest.mark.asyncio
async def test_lanes_scope_fleet_returns_only_fleet_lanes(
    v1_client, seed_lanes_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_lanes_environment

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "fleet"},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["lanes"]
    assert len(rows) == 1
    only = rows[0]
    assert only["scope"] == "fleet"
    assert only["lane_id"] == "fleet_retro"
    assert only["pattern_id"] == "flow-daily-retro"
    assert only["cadence"] == "@daily"
    # Repo-only fields stay None on a fleet row.
    assert only["repo_id"] is None
    assert only["repo_full_name"] is None
    assert only["pattern"] is None


@pytest.mark.asyncio
async def test_lanes_scope_repo_requires_repo_id(
    v1_client, seed_lanes_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_lanes_environment

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "repo"},
    )
    assert resp.status_code == 422
    assert "repo_id is required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_lanes_scope_repo_filters_by_repo_id(
    v1_client, seed_lanes_environment
) -> None:
    raw, workspace, repo_a, _repo_b = seed_lanes_environment

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "repo", "repo_id": str(repo_a.id)},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["lanes"]
    assert len(rows) == 1
    assert rows[0]["lane_id"] == "pr_review"
    assert rows[0]["scope"] == "repo"
    assert rows[0]["repo_id"] == str(repo_a.id)


@pytest.mark.asyncio
async def test_lanes_scope_repo_unknown_repo_id_returns_422(
    v1_client, seed_lanes_environment
) -> None:
    """Cross-workspace / unknown repo id → 422 (not silently empty)."""
    raw, workspace, _repo_a, _repo_b = seed_lanes_environment

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "repo", "repo_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422
    assert "workspace" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_lanes_scope_fleet_with_repo_id_returns_422(
    v1_client, seed_lanes_environment
) -> None:
    raw, workspace, repo_a, _repo_b = seed_lanes_environment

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "fleet", "repo_id": str(repo_a.id)},
    )
    assert resp.status_code == 422
    assert "scope=repo" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_lanes_scope_invalid_value_returns_422(
    v1_client, seed_lanes_environment
) -> None:
    raw, workspace, _repo_a, _repo_b = seed_lanes_environment

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
        params={"scope": "everything"},
    )
    assert resp.status_code == 422
