"""HTTP tests for ``/v1/workspaces/{ws}/adoption`` (RFC-0008 §E).

Seeds four repos that between them cover every funnel stage and
flag, then pins the rollup the Console's /fleet/adoption page
consumes. The funnel is cumulative by design (a ``steady`` repo is
also counted in ``activated``) and flags are orthogonal (a
``steady`` repo can still be ``bundle_out_of_date``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_adoption_repos(db_session, seed_workspace):
    """Four repos across all stages + the interesting flags."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.pipelines import (
        AgentRequest,
        Pipeline,
        PipelineRun,
    )
    from backend.app.services.seed_bundle import BUNDLE_VERSION

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=8_888_001,
        account_login="adoption",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    far_past = now - timedelta(days=60)
    recent = now - timedelta(days=2)

    # --- Repo A: steady. Recent successful PipelineRun + bundle
    # on an outdated version → also ``bundle_out_of_date``.
    repo_steady = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=20001,
        full_name="ad/steady",
        default_branch="main",
        private=False,
        html_url="https://github.com/ad/steady",
        activated_at=far_past,
        preset="web-app",
        installed_bundle_version=max(BUNDLE_VERSION - 1, 1),
    )
    db_session.add(repo_steady)
    await db_session.flush()

    pipeline_steady = Pipeline(
        workspace_id=workspace.id,
        repo_id=repo_steady.id,
        name="Daily standup",
        workflow_id="scheduled-sdlc-lane",
        lane_id="daily_standup",
        enabled=True,
        config={},
    )
    db_session.add(pipeline_steady)
    await db_session.flush()
    db_session.add(
        PipelineRun(
            pipeline_id=pipeline_steady.id,
            workspace_id=workspace.id,
            trigger="manual",
            status="succeeded",
            started_at=recent,
        )
    )

    # --- Repo B: first_run. Had a dispatched AgentRequest but no
    # successes yet.
    repo_first = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=20002,
        full_name="ad/first-run",
        default_branch="main",
        private=False,
        html_url="https://github.com/ad/first-run",
        activated_at=far_past,
        preset="web-app",
        installed_bundle_version=BUNDLE_VERSION,
    )
    db_session.add(repo_first)
    await db_session.flush()
    db_session.add(
        AgentRequest(
            workspace_id=workspace.id,
            repo_id=repo_first.id,
            agent_slug="claude",
            prompt="check",
            status="dispatched",
        )
    )

    # --- Repo C: activated only (no seed, no runs) + old enough to
    # qualify as ``stuck`` (activated_at > window_days ago).
    repo_stuck = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=20003,
        full_name="ad/stuck",
        default_branch="main",
        private=False,
        html_url="https://github.com/ad/stuck",
        activated_at=far_past,
        preset="api-backend",
        installed_bundle_version=None,
    )
    db_session.add(repo_stuck)
    await db_session.flush()

    # --- Repo D: installed but not activated, and the install is
    # suspended → ``install_missing`` flag.
    install.suspended_at = now
    repo_installed_only = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=20004,
        full_name="ad/dormant",
        default_branch="main",
        private=False,
        html_url="https://github.com/ad/dormant",
        activated_at=None,
        preset=None,
        installed_bundle_version=None,
    )
    db_session.add(repo_installed_only)
    await db_session.flush()

    return raw, workspace, {
        "steady": repo_steady,
        "first_run": repo_first,
        "stuck": repo_stuck,
        "installed_only": repo_installed_only,
    }


@pytest.mark.asyncio
async def test_adoption_funnel_aggregates_all_stages_and_flags(
    v1_client, seed_adoption_repos
) -> None:
    raw, workspace, repos = seed_adoption_repos

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/adoption",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["window_days"] == 14
    assert body["current_bundle_version"] >= 1

    totals = body["totals"]
    # Cumulative funnel: 4 installed, 3 activated (not dormant), 2
    # seeded (steady + first_run), 2 first_run (steady + first_run),
    # 1 steady.
    assert totals == {
        "installed": 4,
        "activated": 3,
        "seeded": 2,
        "first_run": 2,
        "steady": 1,
        # Flags
        "stuck": 1,  # the 'stuck' repo
        "install_missing": 4,  # whole workspace — we suspended the shared install
        "bundle_out_of_date": 1,  # steady repo has N-1
        "cold": 0,
    }

    repos_out = {r["full_name"]: r for r in body["repos"]}
    assert set(repos_out) == {
        "ad/steady",
        "ad/first-run",
        "ad/stuck",
        "ad/dormant",
    }

    steady = repos_out["ad/steady"]
    assert steady["stage"] == "steady"
    assert steady["runs_in_window"] == 1
    assert steady["successes_in_window"] == 1
    assert steady["success_rate_in_window"] == 1.0
    assert "bundle_out_of_date" in steady["flags"]
    assert "install_missing" in steady["flags"]

    first_run = repos_out["ad/first-run"]
    assert first_run["stage"] == "first_run"
    # 1 agent request in window, 0 successful → success_rate is 0.
    assert first_run["runs_in_window"] == 1
    assert first_run["successes_in_window"] == 0
    assert first_run["success_rate_in_window"] == 0.0

    stuck = repos_out["ad/stuck"]
    assert stuck["stage"] == "activated"
    assert "stuck" in stuck["flags"]

    dormant = repos_out["ad/dormant"]
    assert dormant["stage"] == "installed"
    assert dormant["activated_at"] is None


@pytest.mark.asyncio
async def test_adoption_empty_workspace_returns_zero_counts(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/adoption",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["repos"] == []
    assert all(v == 0 for v in body["totals"].values())


@pytest.mark.asyncio
async def test_adoption_custom_window(
    v1_client, seed_adoption_repos
) -> None:
    """``window_days=60`` shouldn't change stages (stage is based on
    has-any-run + successes-in-window), but the ``stuck`` count
    should drop because the 'stuck' repo's ``activated_at`` is
    exactly 60 days ago."""
    raw, workspace, _ = seed_adoption_repos
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/adoption?window_days=120",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window_days"] == 120
    # Stuck threshold uses the same window, so 60-day-old activation
    # with zero runs is no longer "overdue" at 120d.
    assert body["totals"]["stuck"] == 0
