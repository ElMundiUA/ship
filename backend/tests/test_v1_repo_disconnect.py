"""Disconnect-repo endpoint (B6).

Pins the cascade contract: deleting a ``WorkspaceRepo`` also deletes
every pipeline bound to it plus their runs, records an audit log,
and never 500s when run against a clean workspace. GitHub side is
deliberately untouched — the operator owns the workflow YAMLs in
their repo after Ship's install PR merges.
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
    from backend.app.db.models.pipelines import Pipeline, PipelineRun

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

    pipeline = Pipeline(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id="pr_review",
        name="PR review",
        workflow_id="pr-and-ci-gate",
        enabled=True,
        config={},
    )
    db_session.add(pipeline)
    await db_session.flush()

    # Two runs so we can assert the cascade wipes them too.
    for _ in range(2):
        db_session.add(
            PipelineRun(
                pipeline_id=pipeline.id,
                workspace_id=workspace.id,
                trigger="manual",
                status="succeeded",
                started_at=datetime.now(timezone.utc),
                payload={},
            )
        )
    await db_session.flush()
    return raw, workspace, install, repo, pipeline


@pytest.mark.asyncio
async def test_disconnect_repo_deletes_pipelines_and_runs(
    v1_client, db_session, seed_disconnect_workspace
) -> None:
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.pipelines import Pipeline, PipelineRun
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, _install, repo, pipeline = seed_disconnect_workspace
    repo_id = repo.id
    pipeline_id = pipeline.id
    workspace_id = workspace.id  # snapshot before expire_all

    response = await v1_client.delete(
        f"/v1/workspaces/{workspace_id}/repos/{repo_id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_pipelines"] == 1
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
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
    ).scalar_one_or_none() is None
    remaining_runs = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.pipeline_id == pipeline_id)
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
    assert payload["deleted_pipelines"] == 1
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


@pytest.mark.asyncio
async def test_disconnect_repo_preserves_workspace_level_pipelines(
    v1_client, db_session, seed_disconnect_workspace
) -> None:
    """A workspace-level (``repo_id=None``) pipeline survives disconnect."""
    from backend.app.db.models.pipelines import Pipeline

    raw, workspace, _install, repo, _pipeline = seed_disconnect_workspace
    # Seed a legacy unbound pipeline — its repo_id was never set.
    unbound = Pipeline(
        workspace_id=workspace.id,
        repo_id=None,
        lane_id="self_heal",
        name="Pipeline self-heal",
        workflow_id="pipeline-self-heal",
        enabled=False,
        config={},
    )
    db_session.add(unbound)
    await db_session.flush()
    unbound_id = unbound.id

    response = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200

    db_session.expire_all()
    still_there = (
        await db_session.execute(
            select(Pipeline).where(Pipeline.id == unbound_id)
        )
    ).scalar_one_or_none()
    assert still_there is not None
