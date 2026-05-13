"""Day-3 webhook handlers — ``pull_request`` and ``workflow_run`` cache."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select


WEBHOOK_SECRET = "wh_test_secret"


@pytest.fixture
def github_app_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


async def _seed_install_and_repo(
    db_session, workspace_id, *, installation_id=42, repo_external_id=1001
):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    install = GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=installation_id,
        account_id=1,
        account_login="acme",
        account_type="Organization",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace_id,
        installation_id=install.id,
        provider="github",
        external_id=repo_external_id,
        full_name="acme/alpha",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/alpha",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return install, repo


@pytest.mark.asyncio
async def test_pull_request_event_caches_row(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    from backend.app.db.models.pipelines import PullRequest

    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)

    payload = {
        "action": "opened",
        "installation": {"id": install.installation_id},
        "repository": {"id": repo.external_id, "full_name": repo.full_name},
        "pull_request": {
            "id": 9001,
            "number": 7,
            "title": "Add caching",
            "state": "open",
            "merged": False,
            "draft": False,
            "user": {"login": "octo"},
            "html_url": "https://github.com/acme/alpha/pull/7",
            "created_at": "2026-04-19T10:00:00Z",
            "updated_at": "2026-04-19T11:00:00Z",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200, response.text

    row = (
        await db_session.execute(
            select(PullRequest).where(PullRequest.external_id == 9001)
        )
    ).scalar_one()
    assert row.workspace_id == workspace.id
    assert row.repo_id == repo.id
    assert row.title == "Add caching"
    assert row.state == "open"
    assert row.author == "octo"


@pytest.mark.asyncio
async def test_pull_request_event_marks_merged_state(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    from backend.app.db.models.pipelines import PullRequest

    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)

    # First open it; then send a "closed + merged" update so we can
    # verify the upsert path on the same row.
    open_payload = {
        "installation": {"id": install.installation_id},
        "repository": {"id": repo.external_id, "full_name": repo.full_name},
        "pull_request": {
            "id": 9002,
            "number": 8,
            "title": "Refactor",
            "state": "open",
            "html_url": "https://github.com/acme/alpha/pull/8",
        },
    }
    body = json.dumps({"action": "opened", **open_payload}).encode("utf-8")
    await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )

    merged_payload = {
        "installation": {"id": install.installation_id},
        "repository": {"id": repo.external_id, "full_name": repo.full_name},
        "pull_request": {
            "id": 9002,
            "number": 8,
            "title": "Refactor",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-04-19T12:00:00Z",
            "html_url": "https://github.com/acme/alpha/pull/8",
        },
    }
    body2 = json.dumps({"action": "closed", **merged_payload}).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body2,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body2),
        },
    )
    assert response.status_code == 200

    row = (
        await db_session.execute(
            select(PullRequest).where(PullRequest.external_id == 9002)
        )
    ).scalar_one()
    assert row.state == "merged"
    assert row.merged is True


@pytest.mark.asyncio
async def test_pull_request_event_for_inactive_repo_is_silently_dropped(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    from backend.app.db.models.pipelines import PullRequest

    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)

    payload = {
        "action": "opened",
        "installation": {"id": install.installation_id},
        # A *different* repo id the user hasn't activated.
        "repository": {"id": 9999, "full_name": "acme/other"},
        "pull_request": {
            "id": 12345,
            "number": 1,
            "title": "Stray",
            "state": "open",
            "html_url": "https://github.com/acme/other/pull/1",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200

    rows = (
        await db_session.execute(
            select(PullRequest).where(PullRequest.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_workflow_run_event_caches_row(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    from backend.app.db.models.pipelines import WorkflowRun

    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)

    payload = {
        "action": "completed",
        "installation": {"id": install.installation_id},
        "repository": {"id": repo.external_id, "full_name": repo.full_name},
        "workflow_run": {
            "id": 555,
            "name": "CI",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": "abc1234",
            "actor": {"login": "octo"},
            "html_url": "https://github.com/acme/alpha/actions/runs/555",
            "run_started_at": "2026-04-19T10:00:00Z",
            "updated_at": "2026-04-19T10:05:00Z",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200, response.text

    row = (
        await db_session.execute(
            select(WorkflowRun).where(WorkflowRun.external_id == 555)
        )
    ).scalar_one()
    assert row.status == "completed"
    assert row.conclusion == "success"
    assert row.actor == "octo"
    assert row.repo_id == repo.id
    assert row.finished_at is not None
