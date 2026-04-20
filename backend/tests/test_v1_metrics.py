"""SHIP-book metrics overview (D11) — aggregator correctness.

The happy path: seed a mix of pipelines, runs, clarifications,
improvements, chats, PRs, and workflow runs. Hit
``/metrics/overview`` and verify every panel's counts + ratios.

Plus the edge cases that bit during development:

- Empty workspace → zeros everywhere, ``None`` for ratios.
- Window filter excludes old rows but keeps all-time panels (the
  ``pipelines`` card is not windowed — that's deliberate).
- Invalid window label rejected with 422.
- RBAC — a non-member is 403.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_repo_and_install(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=917_001,
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
        external_id=97_017_017,
        full_name="acme/metrics-repo",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/metrics-repo",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return raw, workspace, install, repo


async def _seed_runs(
    db_session, workspace_id, repo_id
) -> None:
    """Seed two pipelines + a mix of runs (succeeded / failed / running)."""
    from backend.app.db.models.pipelines import Pipeline, PipelineRun

    pr = Pipeline(
        workspace_id=workspace_id,
        repo_id=repo_id,
        kind="pr_review",
        name="PR review",
        workflow_id="pr-and-ci-gate",
        enabled=True,
        config={},
    )
    td = Pipeline(
        workspace_id=workspace_id,
        repo_id=repo_id,
        kind="tech_debt",
        name="Tech debt",
        workflow_id="parallel-audit-lanes",
        enabled=False,
        config={},
    )
    db_session.add_all([pr, td])
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            PipelineRun(
                pipeline_id=pr.id,
                workspace_id=workspace_id,
                trigger="manual",
                status="succeeded",
                started_at=now - timedelta(minutes=4),
                finished_at=now - timedelta(minutes=2),
            ),
            PipelineRun(
                pipeline_id=pr.id,
                workspace_id=workspace_id,
                trigger="webhook",
                status="succeeded",
                started_at=now - timedelta(minutes=8),
                finished_at=now - timedelta(minutes=5),
            ),
            PipelineRun(
                pipeline_id=pr.id,
                workspace_id=workspace_id,
                trigger="manual",
                status="failed",
                started_at=now - timedelta(minutes=12),
                finished_at=now - timedelta(minutes=11),
            ),
            PipelineRun(
                pipeline_id=td.id,
                workspace_id=workspace_id,
                trigger="onboarding",
                status="succeeded",
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(minutes=55),
            ),
            PipelineRun(
                pipeline_id=td.id,
                workspace_id=workspace_id,
                trigger="manual",
                status="running",
                started_at=now - timedelta(seconds=30),
            ),
        ]
    )
    await db_session.flush()


async def _seed_agent_surface(db_session, workspace_id, repo_id) -> None:
    from backend.app.db.models.agent_surface import (
        ChatMessage,
        ChatThread,
        Clarification,
        Improvement,
    )

    now = datetime.now(timezone.utc)

    db_session.add_all(
        [
            Clarification(
                workspace_id=workspace_id,
                repo_id=repo_id,
                question="Q1",
                status="answered",
                answer="A1",
                answered_at=now - timedelta(hours=2),
            ),
            Clarification(
                workspace_id=workspace_id,
                repo_id=repo_id,
                question="Q2",
                status="answered",
                answer="A2",
                answered_at=now - timedelta(hours=6),
            ),
            Clarification(
                workspace_id=workspace_id,
                repo_id=repo_id,
                question="Q3",
                status="open",
            ),
            Clarification(
                workspace_id=workspace_id,
                repo_id=repo_id,
                question="Q4",
                status="skipped",
            ),
        ]
    )

    db_session.add_all(
        [
            Improvement(
                workspace_id=workspace_id,
                repo_id=repo_id,
                kind="refactor",
                title="one",
                body="...",
                decision="accepted",
            ),
            Improvement(
                workspace_id=workspace_id,
                repo_id=repo_id,
                kind="doc",
                title="two",
                body="...",
                decision="accepted",
            ),
            Improvement(
                workspace_id=workspace_id,
                repo_id=repo_id,
                kind="test",
                title="three",
                body="...",
                decision="declined",
                decision_reason="nope",
            ),
            Improvement(
                workspace_id=workspace_id,
                repo_id=repo_id,
                kind="arch",
                title="four",
                body="...",
                decision="pending",
            ),
        ]
    )

    t_active = ChatThread(
        workspace_id=workspace_id,
        repo_id=repo_id,
        title="still talking",
        status="active",
    )
    t_resolved = ChatThread(
        workspace_id=workspace_id,
        repo_id=repo_id,
        title="resolved",
        status="resolved",
        resolved_ticket_ref="TICK-1",
    )
    t_archived = ChatThread(
        workspace_id=workspace_id,
        repo_id=repo_id,
        title="archived",
        status="archived",
    )
    db_session.add_all([t_active, t_resolved, t_archived])
    await db_session.flush()
    db_session.add_all(
        [
            ChatMessage(thread_id=t_active.id, role="user", body="hi"),
            ChatMessage(thread_id=t_active.id, role="assistant", body="hi back"),
            ChatMessage(thread_id=t_resolved.id, role="user", body="x"),
            ChatMessage(thread_id=t_resolved.id, role="assistant", body="y"),
            ChatMessage(thread_id=t_resolved.id, role="user", body="z"),
        ]
    )
    await db_session.flush()


async def _seed_prs_and_workflows(db_session, workspace_id, repo_id) -> None:
    from backend.app.db.models.pipelines import PullRequest, WorkflowRun

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            PullRequest(
                workspace_id=workspace_id,
                repo_id=repo_id,
                external_id=1001,
                number=1,
                repo_full_name="acme/metrics-repo",
                title="one",
                state="closed",
                merged=True,
                draft=False,
                html_url="https://example.com/1",
                opened_at=now - timedelta(hours=6),
                merged_at=now - timedelta(hours=2),
            ),
            PullRequest(
                workspace_id=workspace_id,
                repo_id=repo_id,
                external_id=1002,
                number=2,
                repo_full_name="acme/metrics-repo",
                title="two",
                state="closed",
                merged=True,
                draft=False,
                html_url="https://example.com/2",
                opened_at=now - timedelta(hours=24),
                merged_at=now - timedelta(hours=20),
            ),
            PullRequest(
                workspace_id=workspace_id,
                repo_id=repo_id,
                external_id=1003,
                number=3,
                repo_full_name="acme/metrics-repo",
                title="three",
                state="open",
                merged=False,
                draft=False,
                html_url="https://example.com/3",
                opened_at=now - timedelta(hours=1),
            ),
        ]
    )
    db_session.add_all(
        [
            WorkflowRun(
                workspace_id=workspace_id,
                repo_id=repo_id,
                external_id=2001,
                repo_full_name="acme/metrics-repo",
                name="ci",
                status="completed",
                conclusion="success",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2, minutes=50),
            ),
            WorkflowRun(
                workspace_id=workspace_id,
                repo_id=repo_id,
                external_id=2002,
                repo_full_name="acme/metrics-repo",
                name="ci",
                status="completed",
                conclusion="failure",
                started_at=now - timedelta(hours=5),
                finished_at=now - timedelta(hours=4, minutes=55),
            ),
            WorkflowRun(
                workspace_id=workspace_id,
                repo_id=repo_id,
                external_id=2003,
                repo_full_name="acme/metrics-repo",
                name="ci",
                status="completed",
                conclusion="success",
                started_at=now - timedelta(hours=8),
                finished_at=now - timedelta(hours=7, minutes=50),
            ),
        ]
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_overview_populated_panels(
    v1_client, db_session, seed_repo_and_install
) -> None:
    raw, workspace, _install, repo = seed_repo_and_install
    await _seed_runs(db_session, workspace.id, repo.id)
    await _seed_agent_surface(db_session, workspace.id, repo.id)
    await _seed_prs_and_workflows(db_session, workspace.id, repo.id)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/metrics/overview",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Pipelines — not windowed.
    assert body["pipelines"]["total"] == 2
    assert body["pipelines"]["enabled"] == 1
    assert body["pipelines"]["disabled"] == 1
    assert {b["kind"] for b in body["pipelines"]["by_kind"]} == {
        "pr_review",
        "tech_debt",
    }

    # Runs — 3 succeeded, 1 failed, 1 running.
    runs = body["runs"]
    assert runs["total"] == 5
    assert runs["succeeded"] == 3
    assert runs["failed"] == 1
    assert runs["running"] == 1
    assert runs["success_rate"] == pytest.approx(0.75, rel=1e-3)
    assert runs["avg_duration_seconds"] > 0
    trig = {b["key"]: b["value"] for b in runs["by_trigger"]}
    assert trig == {"manual": 3, "webhook": 1, "onboarding": 1}

    # Clarifications — 2 answered / 1 open / 1 skipped.
    cl = body["clarifications"]
    assert cl["total"] == 4
    assert cl["answered"] == 2
    assert cl["open"] == 1
    assert cl["skipped"] == 1
    # answered vs answered+skipped = 2/3.
    assert cl["answer_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert cl["median_resolution_hours"] is not None

    # Improvements — 2 accepted / 1 declined / 1 pending.
    imp = body["improvements"]
    assert imp["accepted"] == 2
    assert imp["declined"] == 1
    assert imp["pending"] == 1
    # accept_rate = accepted / decided = 2/3.
    assert imp["accept_rate"] == pytest.approx(2 / 3, rel=1e-3)

    # Chat — 1 active / 1 resolved / 1 archived. Ticket rate =
    # resolved / (resolved + archived) = 1/2.
    ch = body["chat"]
    assert ch["threads_total"] == 3
    assert ch["threads_resolved"] == 1
    assert ch["threads_archived"] == 1
    assert ch["messages_total"] == 5
    assert ch["ticket_rate"] == pytest.approx(0.5, rel=1e-3)

    # DORA — 2 merged of 3 opened; 1 failed of 3 workflow runs.
    dora = body["dora"]
    assert dora["prs_opened"] == 3
    assert dora["prs_merged"] == 2
    assert dora["workflow_runs_total"] == 3
    assert dora["workflow_runs_failed"] == 1
    assert dora["change_failure_rate"] == pytest.approx(1 / 3, rel=1e-3)
    assert dora["avg_lead_time_hours"] is not None
    assert dora["mttr_hours"] is None  # explicitly not computed


@pytest.mark.asyncio
async def test_overview_empty_workspace_returns_zeros(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/metrics/overview",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pipelines"]["total"] == 0
    assert body["runs"]["total"] == 0
    assert body["runs"]["success_rate"] is None
    assert body["clarifications"]["answer_rate"] is None
    assert body["improvements"]["accept_rate"] is None
    assert body["chat"]["ticket_rate"] is None
    assert body["dora"]["change_failure_rate"] is None


@pytest.mark.asyncio
async def test_window_filter_excludes_old_rows(
    v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.db.models.agent_surface import Improvement

    raw, workspace, _install, repo = seed_repo_and_install
    old = datetime.now(timezone.utc) - timedelta(days=45)
    recent = datetime.now(timezone.utc) - timedelta(days=2)

    for created_at, decision in [(old, "accepted"), (recent, "declined")]:
        row = Improvement(
            workspace_id=workspace.id,
            repo_id=repo.id,
            kind="k",
            title="t",
            body="b",
            decision=decision,
            decision_reason="x",
        )
        db_session.add(row)
        await db_session.flush()
        row.created_at = created_at
    await db_session.flush()

    # 7d window — only the recent row.
    short = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/metrics/overview?window=7d",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert short.status_code == 200
    assert short.json()["improvements"]["total"] == 1
    assert short.json()["improvements"]["declined"] == 1

    # 90d window — both rows.
    long = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/metrics/overview?window=90d",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert long.status_code == 200
    assert long.json()["improvements"]["total"] == 2


@pytest.mark.asyncio
async def test_invalid_window_rejected(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/metrics/overview?window=365d",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_non_member_forbidden(
    v1_client, db_session, seed_workspace
) -> None:
    """A user who isn't in the workspace can't read metrics."""
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import ApiToken, User

    _, _raw, workspace = seed_workspace
    outsider = User(email="outsider@example.com", display_name="Outsider")
    db_session.add(outsider)
    await db_session.flush()
    raw_pat = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=outsider.id,
            name="pat",
            hashed_secret=_hash_token(raw_pat),
            prefix=PAT_PREFIX,
            scopes=["workspace:read"],
        )
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/metrics/overview",
        headers={"Authorization": f"Bearer {raw_pat}"},
    )
    assert resp.status_code in {403, 404}
