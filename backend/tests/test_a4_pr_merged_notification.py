"""A4 "Return-to-Ship after PR merge" — dashboard banner on merge.

The promise: a user who clicks Merge on github.com and comes back to
Ship lands on a friendly "your PR merged" callout instead of an
unchanged dashboard. This suite locks the webhook → notification wire
end-to-end, including the dedupe + "don't double-announce the install
PR" branches that make the UX bearable in practice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select


WEBHOOK_SECRET = "wh_a4_secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


@pytest.fixture
def github_app_env(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_install_and_repo(db_session, workspace_id, *, installation_id=4242):
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
        external_id=9_001,
        full_name="acme/alpha",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/alpha",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return install, repo


def _merge_payload(
    *,
    install_id: int,
    repo_external_id: int,
    repo_full_name: str,
    pr_id: int = 7_777_001,
    pr_number: int = 42,
    title: str = "Refactor the thing",
    head_ref: str = "feature/refactor",
) -> bytes:
    payload = {
        "action": "closed",
        "installation": {"id": install_id},
        "repository": {"id": repo_external_id, "full_name": repo_full_name},
        "pull_request": {
            "id": pr_id,
            "number": pr_number,
            "title": title,
            "state": "closed",
            "merged": True,
            "draft": False,
            "user": {"login": "octo"},
            "html_url": f"https://github.com/{repo_full_name}/pull/{pr_number}",
            "created_at": "2026-04-20T00:00:00Z",
            "updated_at": "2026-04-20T00:05:00Z",
            "closed_at": "2026-04-20T00:05:00Z",
            "merged_at": "2026-04-20T00:05:00Z",
            "head": {"ref": head_ref, "sha": "deadbeef"},
            "base": {"ref": "main"},
        },
    }
    return json.dumps(payload).encode("utf-8")


@pytest.mark.asyncio
async def test_customer_pr_merge_creates_dashboard_banner(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    """A non-install PR merge mints a ``pr_merged`` WorkspaceNotification."""
    from backend.app.db.models.notifications import WorkspaceNotification

    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)
    workspace_id = workspace.id
    install_number = install.installation_id
    repo_external = repo.external_id
    repo_full = repo.full_name

    body = _merge_payload(
        install_id=install_number,
        repo_external_id=repo_external,
        repo_full_name=repo_full,
    )
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200, response.text

    rows = (
        await db_session.execute(
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "pr_merged"
    assert row.dedupe_key == "pr_merged:7777001"
    assert row.href == "https://github.com/acme/alpha/pull/42"
    assert "Refactor the thing" in row.title
    assert row.payload["pr_number"] == 42
    assert row.payload["repo_full_name"] == "acme/alpha"
    assert row.dismissed_at is None


@pytest.mark.asyncio
async def test_replayed_merge_webhook_does_not_duplicate_banner(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    """A replayed webhook for the same PR stays silent (dedupe key hit)."""
    from backend.app.db.models.notifications import WorkspaceNotification

    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)
    workspace_id = workspace.id

    body = _merge_payload(
        install_id=install.installation_id,
        repo_external_id=repo.external_id,
        repo_full_name=repo.full_name,
        pr_id=7_777_002,
    )
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _sign(body),
    }
    first = await v1_client.post(
        "/v1/webhooks/github", content=body, headers=headers
    )
    assert first.status_code == 200
    # Same payload → GitHub replay.
    second = await v1_client.post(
        "/v1/webhooks/github", content=body, headers=headers
    )
    assert second.status_code == 200

    rows = (
        await db_session.execute(
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1, "dedupe_key must keep replays from stacking"


@pytest.mark.asyncio
async def test_install_pr_merge_does_not_mint_pr_merged_banner(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    """``ship/install-*`` merges use the existing ``back_from_pr`` surface.

    Minting a second "PR merged" banner on top of the install-PR
    auto-dispatch UX would double-notify during onboarding — right
    when the user is most easily overwhelmed. Keep install PRs silent
    on the A4 rail.
    """
    from backend.app.db.models.notifications import WorkspaceNotification

    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)
    workspace_id = workspace.id

    body = _merge_payload(
        install_id=install.installation_id,
        repo_external_id=repo.external_id,
        repo_full_name=repo.full_name,
        pr_id=7_777_003,
        head_ref="ship/install-tech_debt-1760000000",
    )
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
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_non_merge_pr_update_does_not_mint_banner(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    """An open → ready_for_review (still merged=false) leaves the rail empty."""
    from backend.app.db.models.notifications import WorkspaceNotification

    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)
    workspace_id = workspace.id

    payload = {
        "action": "ready_for_review",
        "installation": {"id": install.installation_id},
        "repository": {"id": repo.external_id, "full_name": repo.full_name},
        "pull_request": {
            "id": 7_777_004,
            "number": 77,
            "title": "Draft becomes ready",
            "state": "open",
            "merged": False,
            "html_url": "https://github.com/acme/alpha/pull/77",
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
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_notifications_api_list_and_dismiss(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    """List endpoint returns open banners; dismiss removes them."""
    _, raw_token, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)
    workspace_id = workspace.id

    # Fire two merges → two open banners.
    for pr_id in (7_777_010, 7_777_011):
        body = _merge_payload(
            install_id=install.installation_id,
            repo_external_id=repo.external_id,
            repo_full_name=repo.full_name,
            pr_id=pr_id,
        )
        resp = await v1_client.post(
            "/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        assert resp.status_code == 200

    headers = {"Authorization": f"Bearer {raw_token}"}
    listed = await v1_client.get(
        f"/v1/workspaces/{workspace_id}/notifications", headers=headers
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 2
    kinds = {i["kind"] for i in items}
    assert kinds == {"pr_merged"}
    # Newest-first ordering.
    assert items[0]["created_at"] >= items[1]["created_at"]

    first_id = items[0]["id"]
    dismiss = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/notifications/{first_id}/dismiss",
        headers=headers,
    )
    assert dismiss.status_code == 204
    # Idempotent — dismissing again is still 204.
    dismiss2 = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/notifications/{first_id}/dismiss",
        headers=headers,
    )
    assert dismiss2.status_code == 204

    after = await v1_client.get(
        f"/v1/workspaces/{workspace_id}/notifications", headers=headers
    )
    assert len(after.json()["items"]) == 1

    # dismiss-all clears the rail.
    dismiss_all = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/notifications/dismiss-all",
        headers=headers,
    )
    assert dismiss_all.status_code == 204

    empty = await v1_client.get(
        f"/v1/workspaces/{workspace_id}/notifications", headers=headers
    )
    assert empty.json()["items"] == []

    with_history = await v1_client.get(
        f"/v1/workspaces/{workspace_id}/notifications?include_dismissed=true",
        headers=headers,
    )
    assert len(with_history.json()["items"]) == 2


@pytest.mark.asyncio
async def test_dashboard_surfaces_notifications(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    """The dashboard payload carries the newest open banners inline."""
    _, raw_token, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)
    workspace_id = workspace.id

    body = _merge_payload(
        install_id=install.installation_id,
        repo_external_id=repo.external_id,
        repo_full_name=repo.full_name,
        pr_id=7_777_020,
        title="Fix the thing",
    )
    resp = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 200

    headers = {"Authorization": f"Bearer {raw_token}"}
    dash = await v1_client.get(
        f"/v1/workspaces/{workspace_id}/dashboard", headers=headers
    )
    assert dash.status_code == 200, dash.text
    payload = dash.json()
    assert "notifications" in payload
    assert any(
        n["kind"] == "pr_merged" and "Fix the thing" in n["title"]
        for n in payload["notifications"]
    )
