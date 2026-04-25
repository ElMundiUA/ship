"""End-to-end tests for ``GET /v1/workspaces/{ws}/dashboard`` (pilot Day 3)."""

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
        "enabled_pipelines": 0,
        "open_pull_requests": 0,
        "runs_last_24h": 0,
    }
    assert body["pipelines"] == []
    assert body["pull_requests"] == []
    assert body["workflow_runs"] == []
    assert body["pipeline_runs"] == []


@pytest.mark.asyncio
async def test_dashboard_aggregates_counts_and_recent_strips(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.pipelines import (
        PipelineRun,
        PullRequest,
        WorkflowRun,
    )
    from backend.app.services.lane_recipes import seed_default_pipelines

    user, raw, workspace = seed_workspace
    seeded = await seed_default_pipelines(db_session, workspace.id)
    pr_review = next(p for p in seeded if p.lane_id == "pr_review")

    # Activated repo + GitHub install (to drive `active_repos` count
    # and to keep the dashboard's joins happy).
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
        PipelineRun(
            pipeline_id=pr_review.id,
            workspace_id=workspace.id,
            trigger="manual",
            status="succeeded",
            started_at=now,
            finished_at=now,
            summary="ok",
        )
    )
    # An older run, beyond the 24h window — should not be counted.
    old = now - timedelta(days=2)
    db_session.add(
        PipelineRun(
            pipeline_id=pr_review.id,
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
    # 5 default pipelines minus 1 disabled (self_heal) = 4 enabled.
    assert body["counts"]["enabled_pipelines"] == 4
    assert body["counts"]["open_pull_requests"] == 1
    assert body["counts"]["runs_last_24h"] == 1
    assert len(body["pipelines"]) == 5
    assert {p["repo_full_name"] for p in body["pull_requests"]} == {"acme/alpha"}
    assert {r["repo_full_name"] for r in body["workflow_runs"]} == {"acme/alpha"}
    assert len(body["pipeline_runs"]) == 2


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
    from backend.app.db.models.pipelines import (
        PipelineRun,
        PullRequest,
        WorkflowRun,
    )
    from backend.app.services.lane_recipes import seed_default_pipelines

    user, raw, workspace = seed_workspace
    seeded = await seed_default_pipelines(db_session, workspace.id)
    pr_review = next(p for p in seeded if p.lane_id == "pr_review")

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
        PipelineRun(
            pipeline_id=pr_review.id,
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
            name="CI",
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
    assert body["system_status"]["stuck_prs_count"] == 1
    assert body["system_status"]["broken_automations_count"] == 2
    assert [b["impact"] for b in body["blockers"]][:3] == ["high", "high", "high"]
    assert any(b["type"] == "pr" for b in body["blockers"])
    assert len(body["blockers"]) <= 7
    assert body["shipped"]["fixes_count"] == 1
    assert body["shipped"]["items"][0]["type"] == "fix"
    assert len(body["suggested_actions"]) <= 5
