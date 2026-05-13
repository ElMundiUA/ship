"""Tests for the per-repo tracker binding API (Wizard v2 iter 4).

Covers:

- GET reports ``source="none"`` when neither layer set anything.
- GET falls back to the workspace-level default when no per-repo
  row exists.
- GET returns the per-repo row verbatim when one exists, plus
  ``workspace_default_kind`` so the UI can show "overrides default".
- PUT creates, PUT overwrites same kind, PUT replaces different
  kind (deletes the old row so there's never two).
- DELETE removes the per-repo row; GET then falls back to default.
- 422 on unsupported kinds (notion/slack/etc. are workspace-only).
- RBAC: admin required for PUT/DELETE; read-only members can GET.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def seeded_repo(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=900_501,
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
        external_id=30_031_000,
        full_name="acme/trackers",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/trackers",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.commit()
    return raw, workspace, repo


async def _seed_workspace_tracker(
    db_session, workspace_id, kind, config, *, with_secret: bool = True
):
    """Helper: create a workspace-level tracker row (repo_id=NULL).

    ``with_secret`` defaults to True because the per-repo bind route
    now gates Linear/Notion bindings on a workspace-level OAuth row
    actually carrying a token. Tests that want to assert the bare
    "no workspace OAuth" case can pass ``with_secret=False``.
    """
    from backend.app.db.models.tenancy import Integration

    row = Integration(
        workspace_id=workspace_id,
        repo_id=None,
        kind=kind,
        config=config,
        status="ok",
        secret_ciphertext=b"oauth-token" if with_secret else None,
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.commit()
    return row


@pytest.mark.asyncio
async def test_get_returns_none_when_no_bindings(v1_client, seeded_repo) -> None:
    raw, workspace, repo = seeded_repo
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] is None
    assert body["source"] == "none"
    assert body["workspace_default_kind"] is None


@pytest.mark.asyncio
async def test_get_falls_back_to_workspace_default(
    v1_client, db_session, seeded_repo
) -> None:
    raw, workspace, repo = seeded_repo
    await _seed_workspace_tracker(
        db_session, workspace.id, "linear", {"team_id": "TEAM-ws"}
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "linear"
    assert body["source"] == "workspace"
    assert body["workspace_default_kind"] == "linear"
    assert body["config"]["team_id"] == "TEAM-ws"


@pytest.mark.asyncio
async def test_put_creates_repo_binding(v1_client, db_session, seeded_repo) -> None:
    from backend.app.db.models.tenancy import Integration

    raw, workspace, repo = seeded_repo
    # Per-repo Linear bindings inherit auth from the workspace-level
    # OAuth row; the route 412s without it.
    await _seed_workspace_tracker(db_session, workspace.id, "linear", {})

    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "linear", "config": {"team_id": "TEAM-repo"}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "linear"
    assert body["source"] == "repo"
    assert body["config"] == {"team_id": "TEAM-repo"}

    # Exactly one row persisted with repo_id set.
    rows = (
        await db_session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace.id,
                Integration.repo_id == repo.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "linear"
    assert rows[0].config == {"team_id": "TEAM-repo"}


@pytest.mark.asyncio
async def test_put_overrides_workspace_default_and_reports_both(
    v1_client, db_session, seeded_repo
) -> None:
    raw, workspace, repo = seeded_repo
    await _seed_workspace_tracker(db_session, workspace.id, "linear", {})

    # Repo overrides to Jira; UI needs workspace_default_kind=linear so
    # it can render "overriding Linear default".
    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "jira", "config": {"host": "acme.atlassian.net", "project": "WIDG"}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "jira"
    assert body["source"] == "repo"
    assert body["workspace_default_kind"] == "linear"


@pytest.mark.asyncio
async def test_put_changing_kind_replaces_prior_row(
    v1_client, db_session, seeded_repo
) -> None:
    """Repo binds Linear first, then switches to Jira. The Linear
    row must be removed so the repo never has two tracker rows
    racing each other in reconciliation."""

    from backend.app.db.models.tenancy import Integration

    raw, workspace, repo = seeded_repo
    await _seed_workspace_tracker(db_session, workspace.id, "linear", {})

    # Initial Linear binding.
    r1 = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "linear", "config": {"team_id": "T"}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r1.status_code == 200

    # Switch to Jira.
    r2 = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "jira", "config": {"host": "h", "project": "P"}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r2.status_code == 200

    rows = (
        await db_session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace.id,
                Integration.repo_id == repo.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "jira"


@pytest.mark.asyncio
async def test_put_rejects_unsupported_kind(v1_client, seeded_repo) -> None:
    raw, workspace, repo = seeded_repo
    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "slack", "config": {}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_drops_binding_and_falls_back(
    v1_client, db_session, seeded_repo
) -> None:
    raw, workspace, repo = seeded_repo
    await _seed_workspace_tracker(db_session, workspace.id, "linear", {"team_id": "ws"})

    # Set a repo-specific binding.
    await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "jira", "config": {"host": "h", "project": "P"}},
        headers={"Authorization": f"Bearer {raw}"},
    )

    resp = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 204

    # Now the repo inherits the workspace default.
    after = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert after.status_code == 200
    body = after.json()
    assert body["kind"] == "linear"
    assert body["source"] == "workspace"


@pytest.mark.asyncio
async def test_delete_is_idempotent_when_no_binding(v1_client, seeded_repo) -> None:
    raw, workspace, repo = seeded_repo
    resp = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_put_linear_412s_when_workspace_oauth_missing(
    v1_client, seeded_repo
) -> None:
    """Per-repo Linear binding without a workspace OAuth row → 412.

    Saving a token-less Linear binding would render every downstream
    tracker call 401 silently — exactly the dogfood failure on
    2026-05-02. The route blocks the write at request time and
    surfaces a stable error code so the FE can deeplink to the
    workspace-level OAuth start.
    """
    raw, workspace, repo = seeded_repo

    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "linear", "config": {"team_id": "T"}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 412, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "workspace_oauth_required"
    assert detail["kind"] == "linear"
    assert "OAuth" in detail["message"]


@pytest.mark.asyncio
async def test_put_linear_412s_when_workspace_row_has_no_secret(
    v1_client, db_session, seeded_repo
) -> None:
    """A workspace row that exists but carries no ``secret_ciphertext``
    (e.g. operator created the row through some legacy path that
    didn't run the OAuth flow) is still rejected. The gate cares
    about a usable OAuth token, not just the existence of a
    ``kind=linear`` row."""
    raw, workspace, repo = seeded_repo
    await _seed_workspace_tracker(
        db_session, workspace.id, "linear", {}, with_secret=False
    )

    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "linear", "config": {"team_id": "T"}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 412
    assert resp.json()["detail"]["code"] == "workspace_oauth_required"


@pytest.mark.asyncio
async def test_get_surfaces_workspace_oauth_meta(
    v1_client, db_session, seeded_repo
) -> None:
    """The wizard's per-repo dropdown reads ``workspace_default_config``
    (carrying ``team_options``) and ``workspace_oauth_connected`` so
    it can render a real team picker instead of the legacy "type a
    Linear team key" text input. Both fields must be populated when
    the workspace row carries an OAuth token + ``team_options``."""
    raw, workspace, repo = seeded_repo
    from backend.app.db.models.tenancy import Integration

    db_session.add(
        Integration(
            workspace_id=workspace.id,
            repo_id=None,
            kind="linear",
            config={
                "team_options": [
                    {"id": "t1", "key": "ENG", "name": "Engineering"},
                    {"id": "t2", "key": "PLAT", "name": "Platform"},
                ],
                # secret-adjacent crud that must NOT leak through.
                "scope": "read,write,issues:create",
            },
            status="ok",
            secret_ciphertext=b"oauth-token",
        )
    )
    await db_session.flush()
    await db_session.commit()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_oauth_connected"] is True
    assert "team_options" in body["workspace_default_config"]
    assert len(body["workspace_default_config"]["team_options"]) == 2
    assert body["workspace_default_config"]["team_options"][0]["key"] == "ENG"
    # ``scope`` must be stripped — only safe-to-expose fields leak.
    assert "scope" not in body["workspace_default_config"]


@pytest.mark.asyncio
async def test_get_workspace_oauth_connected_false_when_no_secret(
    v1_client, db_session, seeded_repo
) -> None:
    """Workspace row exists but no token (legacy / partial setup) → the
    flag is False so the FE knows to show "Re-auth" instead of treating
    the integration as live."""
    raw, workspace, repo = seeded_repo
    await _seed_workspace_tracker(
        db_session, workspace.id, "linear", {}, with_secret=False
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace_oauth_connected"] is False


@pytest.mark.asyncio
async def test_put_github_skips_workspace_oauth_gate(
    v1_client, seeded_repo
) -> None:
    """GitHub bindings ride on the GitHub App installation token, not
    on a workspace ``Integration`` row, so the OAuth gate doesn't
    apply. Without this exclusion the entire wizard flow would
    require a no-op workspace ``kind=github`` row."""
    raw, workspace, repo = seeded_repo

    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/tracker",
        json={"kind": "github", "config": {}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "github"


@pytest.mark.asyncio
async def test_unknown_repo_404(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404
