"""Disconnect-repo endpoint (B6).

Pins the cascade contract: deleting a ``WorkspaceRepo`` also deletes
every routine bound to it plus their runs (via FK cascade), records
an audit log, and never 500s when run against a clean workspace.
GitHub side is deliberately untouched — the operator owns the
workflow YAMLs in their repo after Ship's install PR merges.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def seed_disconnect_workspace(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.lanes import Routine, RoutineRun

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=666_001,
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
        external_id=42_666_001,
        full_name="acme/to-disconnect",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/to-disconnect",
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()

    routine = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id="pr_review",
        kind="event",
        pattern="pr-and-ci-gate",
        enabled=True,
        config_blob={},
    )
    db_session.add(routine)
    await db_session.flush()

    # Two runs so we can assert the cascade wipes them too.
    for _ in range(2):
        db_session.add(
            RoutineRun(
                routine_id=routine.id,
                workspace_id=workspace.id,
                trigger="manual",
                status="succeeded",
                started_at=datetime.now(timezone.utc),
                payload={},
            )
        )
    await db_session.flush()
    return raw, workspace, install, repo, routine


@pytest.mark.asyncio
async def test_disconnect_repo_deletes_routines_and_runs(
    v1_client, db_session, seed_disconnect_workspace
) -> None:
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.lanes import Routine, RoutineRun
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, _install, repo, routine = seed_disconnect_workspace
    repo_id = repo.id
    routine_id = routine.id
    workspace_id = workspace.id

    response = await v1_client.delete(
        f"/v1/workspaces/{workspace_id}/repos/{repo_id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_routines"] == 1
    assert body["deleted_runs"] == 2
    assert body["full_name"] == "acme/to-disconnect"

    db_session.expire_all()
    assert (
        await db_session.execute(
            select(WorkspaceRepo).where(WorkspaceRepo.id == repo_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(Routine).where(Routine.id == routine_id)
        )
    ).scalar_one_or_none() is None
    remaining_runs = (
        await db_session.execute(
            select(RoutineRun).where(RoutineRun.routine_id == routine_id)
        )
    ).scalars().all()
    assert remaining_runs == []

    audit = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.workspace_id == workspace_id)
            .where(AuditLog.action == "repo.disconnect")
        )
    ).scalars().all()
    assert len(audit) == 1
    payload = audit[0].payload
    assert payload["deleted_routines"] == 1
    assert payload["deleted_runs"] == 2


@pytest.mark.asyncio
async def test_disconnect_unknown_repo_returns_404(
    v1_client, seed_workspace
) -> None:
    import uuid

    _, raw, workspace = seed_workspace
    response = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 404
