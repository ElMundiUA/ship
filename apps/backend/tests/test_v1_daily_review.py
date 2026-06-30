"""Daily review read-API tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_daily_review_empty_workspace_returns_explicit_empty_sections(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/daily-review",
        headers=_auth(raw),
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["movement"] == []
    assert body["stuck"] == []
    assert body["pull_requests"] == []
    assert body["recommendations"] == []
    assert "No verified movement in the last 24 hours." in body["markdown"]
    assert "None found from Ship control-plane data." in body["markdown"]


@pytest.mark.asyncio
async def test_daily_review_summarizes_movement_and_stuck_work(
    v1_client, seed_workspace, db_session
) -> None:
    from backend.app.db.models.agent_dispatch import AgentDispatchLock
    from backend.app.db.models.tenancy import AuditLog

    _, raw, ws = seed_workspace
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            AuditLog(
                workspace_id=ws.id,
                action="agent_run.dispatch",
                target_kind="process",
                target_id="ELS-100",
                payload={"ticket_ref": "ELS-100", "fsm_stage": "dev_implementation"},
                created_at=now - timedelta(hours=1),
            ),
            AuditLog(
                workspace_id=ws.id,
                action="agent_run.finish",
                target_kind="process",
                target_id="ELS-101",
                payload={
                    "ticket_ref": "ELS-101",
                    "outcome": "needs_clarification",
                    "fsm_stage": "dev_implementation",
                },
                created_at=now - timedelta(minutes=30),
            ),
            AuditLog(
                workspace_id=ws.id,
                action="agent_run.finish",
                target_kind="process",
                target_id="ELS-OLD",
                payload={"ticket_ref": "ELS-OLD", "outcome": "ready_next_step"},
                created_at=now - timedelta(days=2),
            ),
            AgentDispatchLock(
                workspace_id=ws.id,
                key="ticket:ELS-102",
                claimed_at=now - timedelta(hours=2),
                expires_at=now - timedelta(hours=1),
            ),
        ]
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/daily-review",
        headers=_auth(raw),
    )

    assert res.status_code == 200, res.text
    body = res.json()
    moved_refs = {item["ticket_ref"] for item in body["movement"]}
    assert moved_refs == {"ELS-100", "ELS-101"}
    stuck = {(item["ticket_ref"], item["reason"]) for item in body["stuck"]}
    assert ("ELS-101", "waiting on clarification") in stuck
    assert ("ELS-102", "expired_not_swept") in stuck
    assert all(item["ticket_ref"] != "ELS-OLD" for item in body["movement"])


@pytest.mark.asyncio
async def test_daily_review_flags_pr_attention_ci_and_duplicates(
    v1_client, seed_workspace, db_session
) -> None:
    from backend.app.db.models.pipelines import PullRequest, WorkflowRun

    _, raw, ws = seed_workspace
    now = datetime.now(timezone.utc)
    repo = "ship/test"
    db_session.add_all(
        [
            PullRequest(
                workspace_id=ws.id,
                external_id=1001,
                number=1,
                repo_full_name=repo,
                title="feat(ELS-200): add review",
                state="open",
                merged=False,
                draft=False,
                html_url="https://github.com/ship/test/pull/1",
                updated_at_external=now - timedelta(minutes=10),
            ),
            PullRequest(
                workspace_id=ws.id,
                external_id=1002,
                number=2,
                repo_full_name=repo,
                title="fix(ELS-200): follow-up",
                state="open",
                merged=False,
                draft=False,
                html_url="https://github.com/ship/test/pull/2",
                updated_at_external=now - timedelta(minutes=5),
            ),
            WorkflowRun(
                workspace_id=ws.id,
                external_id=5001,
                repo_full_name=repo,
                name="test",
                status="completed",
                conclusion="failure",
                head_branch="fix/ELS-200-auto",
                html_url="https://github.com/ship/test/actions/runs/5001",
                updated_at=now - timedelta(minutes=1),
            ),
        ]
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/daily-review",
        headers=_auth(raw),
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["duplicate_pr_ticket_refs"] == ["ELS-200"]
    assert len(body["recommendations"]) <= 3
    pr_items = body["pull_requests"]
    assert len(pr_items) == 2
    assert all(item["awaiting_review"] for item in pr_items)
    assert all(item["red_ci"] for item in pr_items)
    assert "Duplicate open PR risk for: ELS-200." in body["markdown"]


@pytest.mark.asyncio
async def test_daily_review_marks_pr_ci_unverified_when_workflow_cache_missing(
    v1_client, seed_workspace, db_session
) -> None:
    from backend.app.db.models.pipelines import PullRequest

    _, raw, ws = seed_workspace
    now = datetime.now(timezone.utc)
    db_session.add(
        PullRequest(
            workspace_id=ws.id,
            external_id=1003,
            number=3,
            repo_full_name="ship/test",
            title="feat(ELS-201): add report",
            state="open",
            merged=False,
            draft=False,
            html_url="https://github.com/ship/test/pull/3",
            updated_at_external=now - timedelta(minutes=10),
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/daily-review",
        headers=_auth(raw),
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pull_requests"] == [
        {
            "ticket_ref": "ELS-201",
            "title": "feat(ELS-201): add report",
            "url": "https://github.com/ship/test/pull/3",
            "repo_full_name": "ship/test",
            "awaiting_review": True,
            "ci_status_verified": False,
            "red_ci": False,
            "ci_conclusion": None,
            "ci_url": None,
            "updated_at": body["pull_requests"][0]["updated_at"],
        }
    ]
    assert body["unverified_sections"] == [
        "CI status could not be verified from Ship workflow-run cache for 1 open PR."
    ]
    assert "CI unverified" in body["markdown"]
    assert "No cached open PRs needing review or red-CI attention." not in body["markdown"]
