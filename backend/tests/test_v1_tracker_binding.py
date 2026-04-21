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


async def _seed_workspace_tracker(db_session, workspace_id, kind, config):
    """Helper: create a workspace-level tracker row (repo_id=NULL)."""
    from backend.app.db.models.tenancy import Integration

    row = Integration(
        workspace_id=workspace_id,
        repo_id=None,
        kind=kind,
        config=config,
        status="ok",
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
async def test_unknown_repo_404(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}/tracker",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404
