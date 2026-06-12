"""Regression: the GitHub Actions deploy-planner path must persist the
Deployment row and return 202 — not 500 on response serialization.

Repro of the bug found while testing the no-manual-key (Actions) planner
path on staging:

``trigger_deploy`` creates the Deployment row, stores the planner callback
token, flushes (an UPDATE that fires ``updated_at``'s server-side
``onupdate=now()`` and EXPIRES that attribute), dispatches the workflow,
then returns ``_to_out_async`` — whose synchronous serializer reads
``dep.updated_at`` and triggers a lazy refresh from sync code →
``greenlet_spawn has not been called`` → HTTP 500 → the request rolls
back → the already-dispatched workflow's ``/plan-result`` callback then
404s ("Deployment not found") and the Actions run fails.

The manual-key and dispatch-failure paths don't hit this because they set
``updated_at`` to a Python value before serialization; the success branch
was the only one that didn't. The fix sets ``updated_at`` explicitly
before the flush (and commits the row before dispatch so the async
callback can always find it).

This drives the real route over the ASGI app with a real Postgres session,
so the greenlet path is genuinely exercised (it cannot be reproduced with a
fake session — the expiry only happens against a live async engine).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def seed_repo_for_deploy(db_session, seed_workspace):
    """Workspace + GitHub installation + one activated repo with a saved
    Gemini planner preference (mirrors a repo wired for the Actions path)."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, _raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=778_001,
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
        external_id=42_778_001,
        full_name="acme/frontend-only",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/frontend-only",
        activated_at=datetime.now(timezone.utc),
        deploy_planner_provider="gemini",
        deploy_planner_model="gemini-3.5-flash",
    )
    db_session.add(repo)
    await db_session.flush()
    return workspace, install, repo


@pytest.mark.asyncio
async def test_actions_planner_deploy_persists_row_and_returns_202(
    v1_client, db_session, seed_workspace, seed_repo_for_deploy, monkeypatch
) -> None:
    from backend.app.api.v1.routes import deploy as deploy_route
    from backend.app.db.models.deploy import (
        Deployment,
        DeploymentStatus as DS,
    )

    _user, raw_token, workspace = seed_workspace
    _ws, _install, repo = seed_repo_for_deploy

    # DigitalOcean connected for the workspace.
    async def _do_token(*_a, **_kw):
        return "do-token-xyz"

    monkeypatch.setattr(deploy_route, "get_do_token", _do_token)

    # The repo has the deploy-plan workflow installed + registered, so the
    # route takes the Actions branch (no manual LLM key in the request).
    async def _list_workflows(*_a, **_kw):
        return {deploy_route._DEPLOY_PLAN_WORKFLOW}

    monkeypatch.setattr(deploy_route, "list_repo_workflows", _list_workflows)

    # Capture the dispatch instead of hitting GitHub.
    dispatched: dict = {}

    async def _dispatch(repo_, install_, workflow, *, inputs, settings):
        dispatched["workflow"] = workflow
        dispatched["inputs"] = inputs

    monkeypatch.setattr(deploy_route, "dispatch_workflow", _dispatch)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/deploy",
        json={},
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    # Pre-fix this is 500 with a ``greenlet_spawn`` body; post-fix it's 202.
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == DS.PLANNING

    # The Actions workflow was dispatched with the repo's planner provider.
    assert dispatched["workflow"] == deploy_route._DEPLOY_PLAN_WORKFLOW
    assert dispatched["inputs"]["planner_provider"] == "gemini"
    assert dispatched["inputs"]["ship_deployment_id"] == body["id"]

    # The row is persisted so the async ``/plan-result`` callback can find
    # it: status PLANNING, updated_at populated, callback token recorded.
    dep_id = uuid.UUID(body["id"])
    row = (
        await db_session.execute(
            select(Deployment).where(Deployment.id == dep_id)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.status == DS.PLANNING
    assert row.updated_at is not None
    assert (row.provider_ref or {}).get("planner_token_hash")
