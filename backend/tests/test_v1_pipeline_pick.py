"""Tests for ``POST /v1/.../repos/{id}/pipeline-pick``.

Pipeline-pick is the trigger workflow's fallback when no cron routine
is due — it picks one downstream specialist per tick, rotating across
ticks so a single empty queue doesn't starve the others.

Pinned invariants:

- Fresh repo (no audit history): the first specialist in
  ``_PIPELINE_SPECIALIST_ORDER`` wins.
- Each pick writes one ``repo.pipeline_pick_dispatched`` audit row
  carrying the chosen specialist.
- Rotation: the pick prefers specialists whose last_picked_at is
  oldest; ties break downstream-first.
- 404 on unknown repo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def pipeline_pick_repo(db_session, seed_workspace):
    """Minimal workspace + repo + GH install enough for pipeline-pick.

    Pipeline-pick has no GitHub-side dependencies beyond auth and
    repo lookup, so we don't need the full ``seeded_wizard_repo``
    machinery here.
    """
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=900_700,
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
        external_id=30_032_700,
        full_name="acme/pipeline-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/pipeline-target",
        description=None,
        activated_at=datetime.now(timezone.utc),
        preset="default",
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.commit()
    return raw, workspace, repo


@pytest.mark.asyncio
async def test_pipeline_pick_returns_first_specialist_on_fresh_repo(
    v1_client, pipeline_pick_repo
) -> None:
    raw, workspace, repo = pipeline_pick_repo

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/pipeline-pick",
        json={"event": "schedule"},
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "pipeline_pick"
    # Downstream-first canonical order — reviewer wins on a fresh repo
    # because every specialist has the same epoch ``last_picked_at`` and
    # ties break by downstream index.
    assert body["specialist"] == "reviewer"
    # Candidates list mirrors the canonical order on a fresh repo.
    slugs = [c["specialist"] for c in body["candidates"]]
    assert slugs[:2] == ["reviewer", "qa-automation"]
    assert "intake" in slugs


@pytest.mark.asyncio
async def test_pipeline_pick_writes_audit_log(
    db_session, v1_client, pipeline_pick_repo
) -> None:
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, repo = pipeline_pick_repo

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/pipeline-pick",
        json={"event": "schedule"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200

    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "repo.pipeline_pick_dispatched",
                AuditLog.target_id == str(repo.id),
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload["specialist"] == "reviewer"
    assert rows[0].payload["event"] == "schedule"


@pytest.mark.asyncio
async def test_pipeline_pick_rotates_oldest_first(
    db_session, v1_client, pipeline_pick_repo
) -> None:
    """Specialists with a recent ``last_picked_at`` step aside for ones
    that haven't been tried in a while.

    We pre-write audit log entries for ``reviewer`` and ``qa-automation``
    with a recent timestamp; the next pick should be ``qa-engineer``
    (next downstream that's never been picked).
    """
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, repo = pipeline_pick_repo
    now = datetime.now(timezone.utc)

    for slug, ago_minutes in [("reviewer", 60), ("qa-automation", 30)]:
        db_session.add(
            AuditLog(
                workspace_id=workspace.id,
                actor_user_id=None,
                actor_token_id=None,
                action="repo.pipeline_pick_dispatched",
                target_kind="workspace_repo",
                target_id=str(repo.id),
                payload={"specialist": slug, "event": "schedule"},
                created_at=now - timedelta(minutes=ago_minutes),
            )
        )
    await db_session.commit()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/pipeline-pick",
        json={"event": "schedule"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    # qa-engineer is the most-downstream slug that's never been picked
    # (its last_picked_at is still epoch).
    assert resp.json()["specialist"] == "qa-engineer"


@pytest.mark.asyncio
async def test_pipeline_pick_404_on_unknown_repo(
    v1_client, pipeline_pick_repo
) -> None:
    raw, workspace, _repo = pipeline_pick_repo

    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{bogus}/pipeline-pick",
        json={"event": "schedule"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404, resp.text
