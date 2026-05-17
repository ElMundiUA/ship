"""End-to-end tests for ``GET /v1/workspaces/{ws}/dashboard``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_dashboard_returns_empty_state_for_fresh_workspace(
    v1_client, db_session, seed_workspace
) -> None:
    user, raw, workspace = seed_workspace
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/dashboard",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"] == {
        "active_repos": 0,
        "enabled_routines": 0,
        "open_pull_requests": 0,
        "runs_last_24h": 0,
    }
    assert body["pull_requests"] == []
    assert body["workflow_runs"] == []
    assert body["recent_agent_runs"] == []


@pytest.mark.asyncio
async def test_dashboard_aggregates_counts_and_recent_strips(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.lanes import Routine, RoutineRun
    from backend.app.db.models.pipelines import (
        PullRequest,
        WorkflowRun,
    )

    user, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=42,
        account_id=1,
        account_login="acme",
        account_type="Organization",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=1001,
        full_name="acme/alpha",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/alpha",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()

    pr_review = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id="pr_review",
        kind="event",
        pattern="pr-and-ci-gate",
        enabled=True,
    )
    db_session.add(pr_review)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(
        PullRequest(
            workspace_id=workspace.id,
            repo_id=repo.id,
            external_id=1,
            number=7,
            repo_full_name=repo.full_name,
            title="Add caching",
            state="open",
            html_url="https://github.com/acme/alpha/pull/7",
            opened_at=now,
            updated_at_external=now,
        )
    )
    db_session.add(
        WorkflowRun(
            workspace_id=workspace.id,
            repo_id=repo.id,
            external_id=99,
            repo_full_name=repo.full_name,
            name="CI",
            event="pull_request",
            status="completed",
            conclusion="success",
            head_branch="main",
            head_sha="deadbeef" * 5,
            actor="ci-bot",
            html_url="https://github.com/acme/alpha/actions/runs/99",
            started_at=now,
            finished_at=now,
        )
    )
    db_session.add(
        RoutineRun(
            routine_id=pr_review.id,
            workspace_id=workspace.id,
            trigger="manual",
            status="succeeded",
            started_at=now,
            finished_at=now,
            summary="ok",
        )
    )
    old = now - timedelta(days=2)
    db_session.add(
        RoutineRun(
            routine_id=pr_review.id,
            workspace_id=workspace.id,
            trigger="manual",
            status="succeeded",
            started_at=old,
            finished_at=old,
            summary="ancient",
            created_at=old,
        )
    )
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/dashboard",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["counts"]["active_repos"] == 1
    # enabled_routines now counts distinct routine_ids from audit_log
    # repo.routine_run_claim events in last 7d, not Routine table rows.
    # Fixture doesn't seed claim events → 0.
    assert body["counts"]["enabled_routines"] == 0
    assert body["counts"]["open_pull_requests"] == 1
    # runs_last_24h now reads audit_log.agent_run.finish events, not
    # the legacy RoutineRun table — fixture doesn't seed those, so 0.
    assert body["counts"]["runs_last_24h"] == 0
    assert {p["repo_full_name"] for p in body["pull_requests"]} == {"acme/alpha"}
    assert {r["repo_full_name"] for r in body["workflow_runs"]} == {"acme/alpha"}
    # recent_agent_runs reads audit_log; fixture doesn't seed finishes.
    assert body["recent_agent_runs"] == []


@pytest.mark.asyncio
async def test_ops_dashboard_returns_empty_ok_snapshot(
    v1_client, db_session, seed_workspace
) -> None:
    user, raw, workspace = seed_workspace
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/dashboard/ops",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["system_status"] == {
        "overall_status": "ok",
        "failing_pipelines_count": 0,
        "stuck_prs_count": 0,
        "broken_automations_count": 0,
        "last_deploy": None,
    }
    assert body["blockers"] == []
    assert body["work_in_progress"] == []
    assert body["shipped"] == {
        "features_shipped_count": 0,
        "fixes_count": 0,
        "rollbacks_count": 0,
        "items": [],
    }
    assert body["suggested_actions"] == []


@pytest.mark.asyncio
async def test_ops_dashboard_prioritizes_blockers_and_shipped_24h(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.lanes import Routine, RoutineRun
    from backend.app.db.models.pipelines import (
        PullRequest,
        WorkflowRun,
    )

    user, raw, workspace = seed_workspace

    now = datetime.now(timezone.utc)
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=42,
        account_id=1,
        account_login="acme",
        account_type="Organization",
        installed_at=now,
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=1001,
        full_name="acme/alpha",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/alpha",
        activated_at=now,
    )
    db_session.add(repo)
    await db_session.flush()

    pr_review = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id="pr_review",
        kind="event",
        pattern="pr-and-ci-gate",
        enabled=True,
    )
    db_session.add(pr_review)
    await db_session.flush()

    stale = now - timedelta(hours=25)
    db_session.add(
        PullRequest(
            workspace_id=workspace.id,
            repo_id=repo.id,
            external_id=1,
            number=7,
            repo_full_name=repo.full_name,
            title="Add caching",
            state="open",
            html_url="https://github.com/acme/alpha/pull/7",
            opened_at=stale,
            updated_at_external=stale,
        )
    )
    db_session.add(
        PullRequest(
            workspace_id=workspace.id,
            repo_id=repo.id,
            external_id=2,
            number=8,
            repo_full_name=repo.full_name,
            title="Fix checkout bug",
            state="closed",
            merged=True,
            html_url="https://github.com/acme/alpha/pull/8",
            opened_at=now - timedelta(hours=3),
            updated_at_external=now - timedelta(hours=1),
            closed_at=now - timedelta(hours=1),
            merged_at=now - timedelta(hours=1),
        )
    )
    db_session.add(
        RoutineRun(
            routine_id=pr_review.id,
            workspace_id=workspace.id,
            trigger="manual",
            status="failed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1),
            summary="tests failed",
            created_at=now - timedelta(hours=2),
        )
    )
    db_session.add(
        WorkflowRun(
            workspace_id=workspace.id,
            repo_id=repo.id,
            external_id=99,
            repo_full_name=repo.full_name,
            name="Ship · CI",
            event="pull_request",
            status="completed",
            conclusion="failure",
            html_url="https://github.com/acme/alpha/actions/runs/99",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1),
            created_at=now - timedelta(hours=2),
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=repo.id,
            type="failure",
            title="Production smoke test needs review",
            payload={},
            status="new",
            created_at=now - timedelta(hours=4),
        )
    )
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/dashboard/ops",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["system_status"]["overall_status"] == "critical"
    assert body["system_status"]["failing_pipelines_count"] == 1
    assert body["system_status"]["broken_automations_count"] == 2
    assert body["shipped"]["fixes_count"] == 1


@pytest.mark.asyncio
async def test_ops_dashboard_drops_non_ship_failed_workflows(
    v1_client, db_session, seed_workspace
) -> None:
    """External workflow failures must not inflate Ship ops automations."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.lanes import Routine, RoutineRun
    from backend.app.db.models.pipelines import WorkflowRun

    _, raw, workspace = seed_workspace
    now = datetime.now(timezone.utc)
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=43,
        account_id=1,
        account_login="acme",
        account_type="Organization",
        installed_at=now,
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=1002,
        full_name="acme/beta",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/beta",
        activated_at=now,
    )
    db_session.add(repo)
    await db_session.flush()

    lane = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id="review",
        kind="event",
        pattern="pr-and-ci-gate",
        enabled=True,
    )
    db_session.add(lane)
    await db_session.flush()

    db_session.add(
        RoutineRun(
            routine_id=lane.id,
            workspace_id=workspace.id,
            trigger="manual",
            status="failed",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(minutes=30),
            created_at=now - timedelta(hours=1),
        )
    )
    db_session.add(
        WorkflowRun(
            workspace_id=workspace.id,
            repo_id=repo.id,
            external_id=501,
            repo_full_name=repo.full_name,
            name="Upstream CI",
            event="push",
            status="completed",
            conclusion="failure",
            html_url="https://github.com/acme/beta/actions/runs/501",
            created_at=now - timedelta(hours=2),
        )
    )
    db_session.add(
        WorkflowRun(
            workspace_id=workspace.id,
            repo_id=repo.id,
            external_id=502,
            repo_full_name=repo.full_name,
            name="Ship · Gate",
            event="workflow_dispatch",
            status="completed",
            conclusion="failure",
            html_url="https://github.com/acme/beta/actions/runs/502",
            created_at=now - timedelta(hours=2),
        )
    )
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/dashboard/ops",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["automation_health"]["failures_count"] == 2
    assert body["system_status"]["broken_automations_count"] == 1


@pytest.mark.asyncio
async def test_ops_dashboard_invalid_window_is_422(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/dashboard/ops?window=fortnight",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ops_dashboard_window_widens_failed_pipeline_cutoff(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.lanes import Routine, RoutineRun

    _, raw, workspace = seed_workspace
    now = datetime.now(timezone.utc)
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=44,
        account_id=1,
        account_login="wide",
        account_type="Organization",
        installed_at=now,
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=1003,
        full_name="wide/repo",
        default_branch="main",
        private=False,
        html_url="https://github.com/wide/repo",
        activated_at=now,
    )
    db_session.add(repo)
    await db_session.flush()
    lane = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id="l",
        kind="event",
        pattern="pr-and-ci-gate",
        enabled=True,
    )
    db_session.add(lane)
    await db_session.flush()
    stale = now - timedelta(days=3)
    db_session.add(
        RoutineRun(
            routine_id=lane.id,
            workspace_id=workspace.id,
            trigger="cron",
            status="failed",
            started_at=stale,
            finished_at=stale,
            created_at=stale,
        )
    )
    await db_session.flush()

    narrow = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/dashboard/ops?window=24h",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert narrow.status_code == 200
    assert narrow.json()["system_status"]["failing_pipelines_count"] == 0

    wide = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/dashboard/ops?window=7d",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert wide.status_code == 200
    assert wide.json()["system_status"]["failing_pipelines_count"] == 1
