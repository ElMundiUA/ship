"""End-to-end tests for ``/v1/workspaces/{ws}/lanes*`` (RFC-0007 Phase 7A).

Mocks :class:`GitHubCodeHost` directly in ``services.lanes_sync`` so
the suite never hits api.github.com.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest


class _FakeBlob:
    def __init__(self, content: str, sha: str = "deadbeef"):
        self.content = content
        self.encoding = "utf-8"
        self.sha = sha
        self.path = ".ship/config.yml"
        self.ref = "main"
        self.size = len(content)


class _FakeGateway:
    def __init__(self, content: str | None):
        self._content = content

    async def get_blob(self, ref: Any, *, path: str, ref_sha: str | None):
        if self._content is None:
            raise FileNotFoundError(path)
        return _FakeBlob(self._content)


@pytest.fixture
def github_app_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", "wh_test_secret")
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_repo(db_session, workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=4242,
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
        full_name="acme/widgets",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/widgets",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return install, repo


_YAML = """
version: 2
lanes:
  pr_review:
    event: pull_request
    pattern: pr-and-ci-gate
  daily:
    schedule: "0 9 * * *"
    pattern: scheduled-sdlc-lane
"""


@pytest.mark.asyncio
async def test_sync_lanes_creates_rows_and_audits(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.db.models.lanes import Lane
    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services import lanes_sync as svc

    _, raw, workspace = seed_workspace
    _install, repo = await _seed_repo(db_session, workspace)

    def _ctor(*args, **kwargs):
        return _FakeGateway(_YAML)

    monkeypatch.setattr(svc, "GitHubCodeHost", _ctor)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/lanes/sync",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"] == 2
    assert body["updated"] == 0
    assert body["removed"] == 0
    assert body["errors"] == []
    assert body["sync_source"].endswith(":.ship/config.yml")

    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(Lane).where(Lane.repo_id == repo.id)
        )
    ).scalars().all()
    assert {row.lane_id for row in rows} == {"pr_review", "daily"}

    audits = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "lanes.sync")
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload["added"] == 2


@pytest.mark.asyncio
async def test_list_lanes_returns_rows(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services import lanes_sync as svc

    _, raw, workspace = seed_workspace
    _install, repo = await _seed_repo(db_session, workspace)

    def _ctor(*args, **kwargs):
        return _FakeGateway(_YAML)

    monkeypatch.setattr(svc, "GitHubCodeHost", _ctor)

    await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/lanes/sync",
        headers={"Authorization": f"Bearer {raw}"},
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["lanes"]) == 2
    lane_ids = {lane["lane_id"] for lane in body["lanes"]}
    assert lane_ids == {"pr_review", "daily"}
    first = body["lanes"][0]
    assert first["repo_full_name"] == "acme/widgets"
    assert "config" in first


@pytest.mark.asyncio
async def test_list_lanes_respects_repo_filter(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services import lanes_sync as svc

    _, raw, workspace = seed_workspace
    _install, repo = await _seed_repo(db_session, workspace)

    def _ctor(*args, **kwargs):
        return _FakeGateway(_YAML)

    monkeypatch.setattr(svc, "GitHubCodeHost", _ctor)

    await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/lanes/sync",
        headers={"Authorization": f"Bearer {raw}"},
    )

    # Filter by a different repo id -> empty list.
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
        params={"repo_id": str(uuid.uuid4())},
    )
    assert response.status_code == 200
    assert response.json()["lanes"] == []

    # Filter by the real repo id -> 2 rows.
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes",
        headers={"Authorization": f"Bearer {raw}"},
        params={"repo_id": str(repo.id)},
    )
    assert response.status_code == 200
    assert len(response.json()["lanes"]) == 2


@pytest.mark.asyncio
async def test_sync_missing_config_returns_409(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services import lanes_sync as svc

    _, raw, workspace = seed_workspace
    _install, repo = await _seed_repo(db_session, workspace)

    def _ctor(*args, **kwargs):
        return _FakeGateway(None)

    monkeypatch.setattr(svc, "GitHubCodeHost", _ctor)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/lanes/sync",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 409
    assert "config.yml" in response.json()["detail"]


@pytest.mark.asyncio
async def test_lane_detail_returns_recent_runs_empty(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.db.models.lanes import Lane
    from backend.app.services import lanes_sync as svc
    from sqlalchemy import select

    _, raw, workspace = seed_workspace
    _install, repo = await _seed_repo(db_session, workspace)

    def _ctor(*args, **kwargs):
        return _FakeGateway(_YAML)

    monkeypatch.setattr(svc, "GitHubCodeHost", _ctor)

    await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/lanes/sync",
        headers={"Authorization": f"Bearer {raw}"},
    )

    lane_row = (
        await db_session.execute(
            select(Lane).where(Lane.lane_id == "pr_review")
        )
    ).scalars().first()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/lanes/{lane_row.id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lane_id"] == "pr_review"
    assert body["kind"] == "event"
    assert body["pattern"] == "pr-and-ci-gate"
    assert body["recent_runs"] == []


@pytest.mark.asyncio
async def test_sync_unknown_repo_returns_404(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
) -> None:
    _, raw, workspace = seed_workspace

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}/lanes/sync",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 404
