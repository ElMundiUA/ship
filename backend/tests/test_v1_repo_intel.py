"""Tests for the repo-intel read + manual harvest endpoints (P5-09).

Covers:

- ``GET /v1/workspaces/{ws}/repos/{repo}/intel/current`` — returns the
  live :class:`backend.app.db.models.repo_intel.RepoIntel` snapshot or
  404 when no harvest has landed yet.
- ``POST /v1/workspaces/{ws}/repos/{repo}/intel/harvest`` — re-runs
  the harvest via the same dispatch path the wizard uses, returning
  the inline ``intel_id`` (no redis pool in the test app).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seeded_repo(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=900_801,
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
        external_id=30_032_950,
        full_name="acme/intel-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/intel-target",
        description=None,
        activated_at=datetime.now(timezone.utc),
        preset="default",
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.commit()
    return raw, workspace, install, repo


@pytest.mark.asyncio
async def test_get_current_intel_returns_live_row(
    v1_client, db_session, seeded_repo
) -> None:
    from backend.app.db.models.repo_intel import RepoIntel

    raw, workspace, _install, repo = seeded_repo

    intel = RepoIntel(
        workspace_id=workspace.id,
        repo_id=repo.id,
        version=1,
        is_current=True,
        languages={"typescript": 0.62, "python": 0.31},
        frameworks=["next.js", "fastapi"],
        package_managers=["npm", "uv"],
        entry_points=[{"path": "console/src/app/page.tsx", "kind": "page"}],
        structure={"top_level_dirs": ["console", "backend"], "file_count": 1234},
        commit_style={"convention": "conventional"},
        visual_tokens={"primary_color": "#0ea5e9"},
        harvested_at=datetime.now(timezone.utc),
        harvested_by="wizard",
        harvest_duration_ms=4321,
        harvest_error=None,
    )
    db_session.add(intel)
    await db_session.flush()
    await db_session.commit()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/intel/current",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intel_id"] == str(intel.id)
    assert body["version"] == 1
    assert body["is_current"] is True
    assert body["languages"] == {"typescript": 0.62, "python": 0.31}
    assert body["frameworks"] == ["next.js", "fastapi"]
    assert body["package_managers"] == ["npm", "uv"]
    assert body["structure"]["file_count"] == 1234
    assert body["harvested_by"] == "wizard"
    assert body["harvest_duration_ms"] == 4321
    assert body["harvest_error"] is None


@pytest.mark.asyncio
async def test_get_current_intel_404_when_no_harvest(
    v1_client, seeded_repo
) -> None:
    raw, workspace, _install, repo = seeded_repo
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/intel/current",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_current_intel_404_unknown_repo(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}/intel/current",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_intel_harvest_dispatches_inline(
    monkeypatch, v1_client, seeded_repo
) -> None:
    """No redis pool → the dispatch path runs the harvester inline and
    surfaces the freshly-inserted intel id on the response.
    """
    from backend.app.services import repo_intel as repo_intel_module
    from backend.app.services.repo_intel import HarvestReport

    raw, workspace, _install, repo = seeded_repo

    fake_intel_id = uuid.uuid4()

    async def _fake_harvest(**_kwargs):
        return HarvestReport(
            intel_id=fake_intel_id,
            version=1,
            duration_ms=12,
            files_examined=0,
            languages_detected=0,
            knowledge_articles_written=0,
        )

    monkeypatch.setattr(repo_intel_module, "harvest_repo_intel", _fake_harvest)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/intel/harvest",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enqueued"] is False
    assert body["job_id"] is None
    assert body["intel_id"] == str(fake_intel_id)


@pytest.mark.asyncio
async def test_post_intel_harvest_404_unknown_repo(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}/intel/harvest",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404
