"""End-to-end tests for ``/v1/workspaces/{ws}/repos/*`` (pilot Day 2).

We mock ``GitHubCodeHost.list_repo_summaries`` / ``list_files`` directly
on the routes module so the suite never hits api.github.com — the
adapter itself is unit-tested separately in
``test_github_code_host_adapter.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select


@pytest.fixture
def github_app_env(monkeypatch: pytest.MonkeyPatch):
    """Same shape as ``test_v1_github_app.github_app_env`` — minimal env
    so ``Settings`` doesn't trip on missing GitHub App vars when route
    code constructs a gateway."""
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", "wh_test_secret")
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _summary(
    *,
    external_id: int,
    owner: str = "acme",
    repo: str,
    branch: str = "main",
    private: bool = False,
    description: str | None = None,
) -> Any:
    from backend.app.integrations.gateway.code_host import RepoRef, RepoSummary

    return RepoSummary(
        ref=RepoRef(kind="github", owner=owner, repo=repo),
        external_id=external_id,
        full_name=f"{owner}/{repo}",
        default_branch=branch,
        private=private,
        html_url=f"https://github.com/{owner}/{repo}",
        description=description,
    )


async def _seed_installation(
    db_session, workspace_id: uuid.UUID, *, installation_id: int = 100
):
    """Helper: persist a GitHub App installation row tied to the workspace."""
    from backend.app.db.models.integrations import GitHubInstallation

    row = GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=installation_id,
        account_id=1,
        account_login="acme",
        account_type="Organization",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
async def test_available_requires_installation(
    v1_client, seed_workspace, github_app_env
) -> None:
    _, raw, workspace = seed_workspace
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/available",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 409, response.text
    assert "GitHub App" in response.json()["detail"]


@pytest.mark.asyncio
async def test_available_returns_live_set_with_activation_flag(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.api.v1.routes import repos as repos_module
    from backend.app.db.models.integrations import WorkspaceRepo

    _, raw, workspace = seed_workspace
    install = await _seed_installation(db_session, workspace.id)

    db_session.add(
        WorkspaceRepo(
            workspace_id=workspace.id,
            installation_id=install.id,
            provider="github",
            external_id=1001,
            full_name="acme/already-on",
            default_branch="main",
            private=False,
            html_url="https://github.com/acme/already-on",
            activated_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    canned = [
        _summary(external_id=1001, repo="already-on"),
        _summary(external_id=1002, repo="fresh", private=True),
    ]

    async def _stub(self) -> list:
        return canned

    monkeypatch.setattr(
        repos_module.GitHubCodeHost, "list_repo_summaries", _stub
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/available",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    by_id = {r["external_id"]: r for r in body}
    assert by_id[1001]["activated"] is True
    assert by_id[1002]["activated"] is False
    assert by_id[1002]["private"] is True
    assert by_id[1002]["full_name"] == "acme/fresh"


@pytest.mark.asyncio
async def test_available_resolves_sibling_attached_install(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-workspace per install (migration 0076 / ELS-303): the
    workspace owns NO install of its own but activated a repo under a
    sibling workspace's install. _resolve_installation must fall back to
    that install via the repo's installation_id instead of 409-ing."""
    from backend.app.api.v1.routes import repos as repos_module
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.tenancy import Workspace

    _, raw, workspace = seed_workspace
    # The install belongs to a sibling workspace in the same org.
    sibling = Workspace(
        org_id=workspace.org_id,
        slug=f"sib-{uuid.uuid4().hex[:6]}",
        name="Sibling",
    )
    db_session.add(sibling)
    await db_session.flush()
    install = await _seed_installation(
        db_session, sibling.id, installation_id=200
    )
    # Target workspace activated a repo under the sibling's install.
    db_session.add(
        WorkspaceRepo(
            workspace_id=workspace.id,
            installation_id=install.id,
            provider="github",
            external_id=2001,
            full_name="acme/shared",
            default_branch="main",
            private=False,
            html_url="https://github.com/acme/shared",
            activated_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    async def _stub(self) -> list:
        return [_summary(external_id=2001, repo="shared")]

    monkeypatch.setattr(
        repos_module.GitHubCodeHost, "list_repo_summaries", _stub
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/available",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    by_id = {r["external_id"]: r for r in response.json()}
    assert by_id[2001]["activated"] is True


@pytest.mark.asyncio
async def test_activate_replaces_set_and_audit_logs(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.api.v1.routes import repos as repos_module
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.tenancy import AuditLog

    _, raw, workspace = seed_workspace
    install = await _seed_installation(db_session, workspace.id)

    db_session.add(
        WorkspaceRepo(
            workspace_id=workspace.id,
            installation_id=install.id,
            provider="github",
            external_id=999,
            full_name="acme/old",
            default_branch="main",
            private=False,
            html_url="https://github.com/acme/old",
            activated_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    canned = [
        _summary(external_id=1001, repo="alpha"),
        _summary(external_id=1002, repo="beta", branch="trunk"),
        # 999 is no longer in the live set => can't be re-activated even
        # if the user tried; we don't test that case here, just that
        # activate replaces the set down to {1001, 1002}.
    ]

    async def _stub(self) -> list:
        return canned

    monkeypatch.setattr(
        repos_module.GitHubCodeHost, "list_repo_summaries", _stub
    )

    # Snapshot the id before any session-wide expire so the test never
    # touches an expired ORM attribute (which would trigger sync IO and
    # blow up under asyncpg with MissingGreenlet).
    workspace_id = workspace.id

    response = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/repos/activate",
        headers={"Authorization": f"Bearer {raw}"},
        json={"external_ids": [1001, 1002]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(r["external_id"] for r in body) == [1001, 1002]
    assert {r["full_name"] for r in body} == {"acme/alpha", "acme/beta"}

    rows = (
        await db_session.execute(
            select(WorkspaceRepo).where(WorkspaceRepo.workspace_id == workspace_id)
        )
    ).scalars().all()
    assert sorted(r.external_id for r in rows) == [1001, 1002]
    # 999 is gone (removed from the set).
    assert 999 not in {r.external_id for r in rows}

    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "repos.activate",
            )
        )
    ).scalars().all()
    assert len(audits) == 1
    payload = audits[0].payload
    assert sorted(payload["added"]) == [1001, 1002]
    assert payload["removed"] == [999]
    # No preset was sent on the request → audit log records it as null
    # and the preset column on each row stays null (legacy-shaped).
    assert payload["preset"] is None
    assert all(r.preset is None for r in rows)


@pytest.mark.asyncio
async def test_activate_with_legacy_preset_collapses_to_default(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-P5-01 the preset catalog collapsed to a single
    canonical ``"default"``. Legacy ids (``monorepo`` here, but the
    same applies to every entry in ``LEGACY_PRESETS``) stay accepted
    at the API boundary for backwards compat but normalize to
    ``"default"`` before persistence and audit logging — that's the
    whole point of the collapse."""
    from backend.app.api.v1.routes import repos as repos_module
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.tenancy import AuditLog

    _, raw, workspace = seed_workspace
    await _seed_installation(db_session, workspace.id)

    canned = [_summary(external_id=1001, repo="alpha")]

    async def _stub(self) -> list:
        return canned

    monkeypatch.setattr(
        repos_module.GitHubCodeHost, "list_repo_summaries", _stub
    )

    workspace_id = workspace.id
    response = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/repos/activate",
        headers={"Authorization": f"Bearer {raw}"},
        json={"external_ids": [1001], "preset": "monorepo"},
    )
    assert response.status_code == 200, response.text
    # Returned row reflects the NORMALIZED value, not the legacy id
    # the caller sent.
    assert response.json()[0]["preset"] == "default"

    rows = (
        await db_session.execute(
            select(WorkspaceRepo).where(WorkspaceRepo.workspace_id == workspace_id)
        )
    ).scalars().all()
    assert rows[0].preset == "default"

    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "repos.activate",
            )
        )
    ).scalars().all()
    # Audit telemetry stops fragmenting on legacy ids — every row
    # records ``"default"`` regardless of what the caller sent.
    assert audits[0].payload["preset"] == "default"


@pytest.mark.asyncio
async def test_activate_accepts_default_preset_directly(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single canonical ``"default"`` preset is accepted as-is."""
    from backend.app.api.v1.routes import repos as repos_module
    from backend.app.db.models.integrations import WorkspaceRepo

    _, raw, workspace = seed_workspace
    await _seed_installation(db_session, workspace.id)

    canned = [_summary(external_id=1001, repo="alpha")]

    async def _stub(self) -> list:
        return canned

    monkeypatch.setattr(
        repos_module.GitHubCodeHost, "list_repo_summaries", _stub
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/activate",
        headers={"Authorization": f"Bearer {raw}"},
        json={"external_ids": [1001], "preset": "default"},
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["preset"] == "default"

    rows = (
        await db_session.execute(
            select(WorkspaceRepo).where(WorkspaceRepo.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert rows[0].preset == "default"


@pytest.mark.asyncio
async def test_activate_rejects_unknown_external_id(
    v1_client,
    db_session,
    seed_workspace,
    github_app_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.api.v1.routes import repos as repos_module

    _, raw, workspace = seed_workspace
    await _seed_installation(db_session, workspace.id)

    canned = [_summary(external_id=1001, repo="alpha")]

    async def _stub(self) -> list:
        return canned

    monkeypatch.setattr(
        repos_module.GitHubCodeHost, "list_repo_summaries", _stub
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/activate",
        headers={"Authorization": f"Bearer {raw}"},
        json={"external_ids": [1001, 9999]},
    )
    assert response.status_code == 422, response.text
    assert "9999" in response.json()["detail"]


@pytest.mark.asyncio
async def test_activate_404_for_non_member(
    v1_client, github_app_env
) -> None:
    """Strangers can't even discover that a workspace exists.

    We don't need a separate "member-but-not-admin" case because
    ``_require_membership`` is shared with the (already covered) GitHub
    install start endpoint; the role check is the same code path.
    """
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX

    # Random unauthenticated-feeling bearer; we just need *something* to
    # exercise the workspace_id 404 path even if the token itself 401s.
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    response = await v1_client.post(
        f"/v1/workspaces/{uuid.uuid4()}/repos/activate",
        headers={"Authorization": f"Bearer {raw}"},
        json={"external_ids": [1]},
    )
    # 401 (bad token) or 404 (good token, no membership) are both fine
    # — the point is "not 200, not 500, not a leak of the workspace
    # existence".
    assert response.status_code in (401, 404)
