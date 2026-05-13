"""HTTP tests for ``/v1/workspaces/{ws}/repos/{repo_id}/home``.

Seeds one repo with activity across all three sources (pipeline runs,
workflow runs, agent requests) and pins both the "Now" tiles and the
"Trends" histogram. The endpoint is stateless — we don't test
idempotency / caching — but we do guard against shape drift because
the Console tabs read these field names directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_repo_home(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.lanes import Routine, RoutineRun
    from backend.app.db.models.pipelines import (
        AgentRequest,
        WorkflowRun,
    )
    from backend.app.services.seed_bundle import BUNDLE_VERSION

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=9_000_001,
        account_login="repohome",
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
        external_id=30001,
        full_name="rh/app",
        default_branch="main",
        private=False,
        html_url="https://github.com/rh/app",
        activated_at=datetime.now(timezone.utc) - timedelta(days=45),
        preset="web-app",
        installed_bundle_version=BUNDLE_VERSION,
    )
    db_session.add(repo)
    await db_session.flush()

    pipeline = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        pattern="scheduled-sdlc-lane",
        lane_id="daily_standup",
        kind="schedule",
        enabled=True,
    )
    dormant_pipeline = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        pattern="scheduled-sdlc-lane",
        lane_id="housekeeping",
        kind="schedule",
        enabled=False,
    )
    db_session.add_all([pipeline, dormant_pipeline])
    await db_session.flush()

    now = datetime.now(timezone.utc)

    # --- Pipeline runs: success 2h ago, failure 20h ago, in-flight
    # right now (running), and one outside the 30d window (should be
    # ignored in trends but still folded into "last run" only if it's
    # the latest — which it isn't here).
    db_session.add_all(
        [
            RoutineRun(
                routine_id=pipeline.id,
                workspace_id=workspace.id,
                trigger="cron",
                status="succeeded",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1, minutes=50),
            ),
            RoutineRun(
                routine_id=pipeline.id,
                workspace_id=workspace.id,
                trigger="cron",
                status="failed",
                started_at=now - timedelta(hours=20),
                finished_at=now - timedelta(hours=19),
            ),
            RoutineRun(
                routine_id=pipeline.id,
                workspace_id=workspace.id,
                trigger="manual",
                status="running",
                started_at=now - timedelta(minutes=5),
            ),
        ]
    )

    # --- Workflow run: one GitHub-observed success yesterday. Unique
    # kind so we can assert the 3-source merge.
    db_session.add(
        WorkflowRun(
            workspace_id=workspace.id,
            repo_id=repo.id,
            external_id=777,
            repo_full_name=repo.full_name,
            name="CI",
            event="push",
            status="completed",
            conclusion="success",
            html_url="https://github.com/rh/app/actions/runs/777",
            created_at=now - timedelta(days=1),
        )
    )

    # --- Agent request: one still dispatched (running), one clean
    # success.
    db_session.add_all(
        [
            AgentRequest(
                workspace_id=workspace.id,
                repo_id=repo.id,
                agent_slug="claude",
                pattern_id="role-ba",
                prompt="hi",
                status="dispatched",
                gh_html_url="https://github.com/rh/app/actions/runs/1",
            ),
            AgentRequest(
                workspace_id=workspace.id,
                repo_id=repo.id,
                agent_slug="claude",
                pattern_id="role-reviewer",
                prompt="hi",
                status="succeeded",
                gh_html_url="https://github.com/rh/app/actions/runs/2",
            ),
        ]
    )
    await db_session.flush()

    return raw, workspace, repo


@pytest.mark.asyncio
async def test_repo_home_now_counters(v1_client, seed_repo_home) -> None:
    raw, workspace, repo = seed_repo_home
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/home",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["repo_id"] == str(repo.id)
    assert body["full_name"] == "rh/app"
    assert body["window_days"] == 30

    now_tile = body["now"]
    # In-flight: pipeline running + agent dispatched. Workflow "push"
    # already completed, so shouldn't show up.
    assert now_tile["runs_in_flight"] == 2
    assert now_tile["dispatches_in_flight"] == 1

    # Last 24h: success (2h) + failure (20h) + running (5min) +
    # agent dispatched (just created, no timestamp override) +
    # agent succeeded (just created). That's 5 total, 2 ok, 1 fail
    # (the 20h-ago failure).
    assert now_tile["runs_last_24h"] == 5
    assert now_tile["successes_last_24h"] == 2
    assert now_tile["failures_last_24h"] == 1

    assert now_tile["lanes_total"] == 2
    assert now_tile["lanes_enabled"] == 1
    assert now_tile["bundle_drift"] is False
    assert now_tile["install_suspended"] is False
    assert now_tile["install_missing"] is False

    # Recent activity feed should be newest-first, capped at 10. The
    # agent rows get ``created_at`` stamped server-side on flush, so
    # they land after the test's ``now`` anchor (slightly in the
    # future relative to the pipeline runs we back-dated). We don't
    # pin exact ordering — we just assert newest-first + cap.
    acts = now_tile["recent_activity"]
    assert 1 <= len(acts) <= 10
    timestamps = [a["at"] for a in acts]
    assert timestamps == sorted(timestamps, reverse=True)
    # The freshly-dispatched pipeline run and dispatched agent are
    # both "running" — one of them must be near the top.
    assert any(a["status"] == "running" for a in acts[:3])
    assert {a["kind"] for a in acts} >= {"pipeline", "agent", "workflow"}


@pytest.mark.asyncio
async def test_repo_home_trends_histogram(v1_client, seed_repo_home) -> None:
    raw, workspace, repo = seed_repo_home
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/home?window_days=7",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    trends = body["trends"]
    assert trends["window_days"] == 7
    # 7 buckets, one per day, zeros filled.
    assert len(trends["buckets"]) == 7
    # Bucket days sorted ascending.
    days = [b["day"] for b in trends["buckets"]]
    assert days == sorted(days)

    # Totals: 3 pipeline + 1 workflow + 2 agent = 6. Successes:
    # pipeline-success + workflow-success + agent-success = 3.
    # Failures: pipeline-failed = 1. Other: pipeline-running +
    # agent-dispatched = 2.
    totals = trends["totals"]
    assert totals == {
        "runs": 6,
        "successes": 3,
        "failures": 1,
        "other": 2,
        "success_rate": 0.5,
    }

    # Lane breakdown must include the daily_standup lane and order by
    # runs desc.
    lane_ids = [l["lane_id"] for l in trends["lanes"]]
    assert "daily_standup" in lane_ids
    daily = next(l for l in trends["lanes"] if l["lane_id"] == "daily_standup")
    assert daily["runs"] == 3
    assert daily["successes"] == 1
    assert daily["failures"] == 1


@pytest.mark.asyncio
async def test_repo_home_404_for_unknown_repo(v1_client, seed_workspace) -> None:
    import uuid as _uuid

    _, raw, workspace = seed_workspace
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{_uuid.uuid4()}/home",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 404
