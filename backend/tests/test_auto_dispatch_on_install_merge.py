"""Auto-dispatch knowledge lanes after an install PR merges (A2).

The WOW promise: merge the install PR → open the dashboard → actually
see data instead of a grid of empty cards. This test locks the wire:
a ``pull_request.closed && merged`` delivery on a ``ship/install-*``
branch causes ``auto_dispatch_knowledge_pipelines`` to fire a
``workflow_dispatch`` for every enabled knowledge-kind pipeline bound
to that repo whose workflow is live in the customer repo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


WEBHOOK_SECRET = "wh_auto_dispatch_secret"


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


@pytest_asyncio.fixture
async def seed_tech_debt_pipeline(db_session, seed_workspace):
    """Seed an install + repo + enabled tech_debt pipeline bound to it."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.pipelines import Pipeline

    _, _raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=777_001,
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
        external_id=42_777_001,
        full_name="acme/knowledge-widget",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/knowledge-widget",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()

    pipeline = Pipeline(
        workspace_id=workspace.id,
        repo_id=repo.id,
        kind="tech_debt",
        name="Tech-debt scan",
        workflow_id="parallel-audit-lanes",
        enabled=True,
        config={},
    )
    db_session.add(pipeline)
    await db_session.flush()
    return workspace, install, repo, pipeline


def _merged_pr_payload(
    *,
    install_id: int,
    repo_external_id: int,
    repo_full_name: str,
    head_ref: str,
) -> bytes:
    payload = {
        "action": "closed",
        "pull_request": {
            "id": 555_000_1,
            "number": 42,
            "title": "Ship: install tech_debt workflow",
            "state": "closed",
            "merged": True,
            "draft": False,
            "html_url": f"https://github.com/{repo_full_name}/pull/42",
            "user": {"login": "ship-elmundi[bot]"},
            "created_at": "2026-04-20T00:00:00Z",
            "updated_at": "2026-04-20T00:05:00Z",
            "closed_at": "2026-04-20T00:05:00Z",
            "merged_at": "2026-04-20T00:05:00Z",
            "head": {"ref": head_ref, "sha": "deadbeef"},
            "base": {"ref": "main"},
        },
        "repository": {"id": repo_external_id, "full_name": repo_full_name},
        "installation": {"id": install_id},
    }
    return json.dumps(payload).encode("utf-8")


@pytest.mark.asyncio
async def test_install_pr_merge_auto_dispatches_knowledge_lane(
    v1_client, db_session, github_app_env, seed_tech_debt_pipeline, monkeypatch
) -> None:
    """ship/install-* PR merge → tech_debt pipeline gets a queued/running run."""
    workspace, install, repo, pipeline = seed_tech_debt_pipeline
    pipeline_id = pipeline.id  # snapshot before any expire_all

    dispatched: list[tuple[str, dict[str, str]]] = []

    async def _fake_list(*args, **kwargs):
        # Workflow file is live on main (install PR just merged).
        return {"parallel-audit-lanes.yml"}

    async def _fake_dispatch(
        repo, install, workflow_file, *, inputs, settings, ref=None, client=None
    ):
        dispatched.append((workflow_file, dict(inputs)))

    monkeypatch.setattr(
        "backend.app.api.v1.routes.pipelines.list_repo_workflows", _fake_list
    )
    monkeypatch.setattr(
        "backend.app.api.v1.routes.pipelines.dispatch_workflow", _fake_dispatch
    )

    body = _merged_pr_payload(
        install_id=install.installation_id,
        repo_external_id=repo.external_id,
        repo_full_name=repo.full_name,
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
    assert response.status_code == 200, response.text

    # One dispatch happened, addressed at the catalog workflow file.
    assert len(dispatched) == 1
    workflow_file, inputs = dispatched[0]
    assert workflow_file == "parallel-audit-lanes.yml"
    assert set(inputs) >= {"ship_run_id", "ship_callback_url", "ship_run_token"}

    # And a PipelineRun row was persisted with the auto trigger.
    from backend.app.db.models.pipelines import PipelineRun

    db_session.expire_all()
    runs = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.pipeline_id == pipeline_id)
        )
    ).scalars().all()
    assert len(runs) == 1
    assert runs[0].trigger == "auto_post_install"
    assert runs[0].status == "running"


@pytest.mark.asyncio
async def test_non_ship_branch_merge_does_not_auto_dispatch(
    v1_client, db_session, github_app_env, seed_tech_debt_pipeline, monkeypatch
) -> None:
    """Customer-merged PRs on non-``ship/install-*`` branches leave us alone."""
    workspace, install, repo, pipeline = seed_tech_debt_pipeline
    pipeline_id = pipeline.id

    called = {"list": False, "dispatch": False}

    async def _fake_list(*args, **kwargs):
        called["list"] = True
        return set()

    async def _fake_dispatch(*args, **kwargs):
        called["dispatch"] = True

    monkeypatch.setattr(
        "backend.app.api.v1.routes.pipelines.list_repo_workflows", _fake_list
    )
    monkeypatch.setattr(
        "backend.app.api.v1.routes.pipelines.dispatch_workflow", _fake_dispatch
    )

    body = _merged_pr_payload(
        install_id=install.installation_id,
        repo_external_id=repo.external_id,
        repo_full_name=repo.full_name,
        head_ref="feature/customer-branch",
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

    assert called["list"] is False
    assert called["dispatch"] is False

    from backend.app.db.models.pipelines import PipelineRun

    db_session.expire_all()
    runs = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.pipeline_id == pipeline_id)
        )
    ).scalars().all()
    assert runs == []
