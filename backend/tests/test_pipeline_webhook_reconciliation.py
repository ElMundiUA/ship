"""Webhook → :class:`PipelineRun` reconciliation (Day-4 Phase-1).

The dispatched workflow reports its terminal state via the in-runner
callback (``POST /v1/pipelines/runs/{id}/result``). When that callback
can't reach us (egress firewall, runner crash mid-step, customer
cancels the run from the GitHub UI), we fall back on the
``workflow_run`` webhook GitHub already sends. These tests pin that
fallback so the dashboard can never be stuck on "running" forever.

Covers three slices:

- enrichment-only when the callback already terminated the run,
- terminal status fill-in when no callback ever arrived,
- silent no-op when no in-flight run matches (so customer-triggered
  workflows don't accidentally bind to our rows).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


WEBHOOK_SECRET = "wh_test_secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


@pytest.fixture
def github_app_env(monkeypatch):
    """Pin the webhook secret + slug so the route can verify deliveries."""
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def seed_install_repo_pipeline_run(db_session, seed_workspace):
    """Set up an install + repo + pipeline + queued PipelineRun.

    Mirrors the post-dispatch state: the dispatcher would have flushed
    a queued/running row before handing control to GitHub. We fix the
    payload's ``gh_workflow_run_id`` to a *different* number than the
    one the webhook will carry so we exercise the fallback "freshest
    in-flight" path; a separate test pins the fast-match-by-id path.
    """
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.pipelines import Pipeline, PipelineRun

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=900_001,
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
        external_id=42_000_001,
        full_name="acme/widgets",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/widgets",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()

    pipeline = Pipeline(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id="pr_review",
        name="PR review",
        workflow_id="pr_review",
        enabled=True,
        config={},
    )
    db_session.add(pipeline)
    await db_session.flush()

    run = PipelineRun(
        pipeline_id=pipeline.id,
        workspace_id=workspace.id,
        status="running",
        trigger="manual",
        started_at=datetime.now(timezone.utc),
        payload={},
    )
    db_session.add(run)
    await db_session.flush()
    return raw, workspace, install, repo, pipeline, run


def _workflow_run_payload(
    *,
    install_id: int,
    repo_external_id: int,
    repo_full_name: str,
    gh_run_id: int,
    status: str,
    conclusion: str | None,
    name: str = "Ship · PR review gate",
) -> bytes:
    payload = {
        "action": "completed" if status == "completed" else "requested",
        "workflow_run": {
            "id": gh_run_id,
            "name": name,
            "event": "workflow_dispatch",
            "status": status,
            "conclusion": conclusion,
            "head_branch": "main",
            "head_sha": "deadbeef",
            "html_url": f"https://github.com/{repo_full_name}/actions/runs/{gh_run_id}",
            "run_started_at": "2026-04-20T00:00:00Z",
            "updated_at": "2026-04-20T00:05:00Z",
            "actor": {"login": "ship-app[bot]"},
        },
        "repository": {
            "id": repo_external_id,
            "full_name": repo_full_name,
        },
        "installation": {"id": install_id},
    }
    return json.dumps(payload).encode("utf-8")


@pytest.mark.asyncio
async def test_workflow_run_webhook_fills_terminal_status_when_callback_missed(
    v1_client, db_session, github_app_env, seed_install_repo_pipeline_run
) -> None:
    """No prior callback → webhook wins the race and terminates the run."""
    from backend.app.db.models.pipelines import PipelineRun

    _, _, install, repo, _, run = seed_install_repo_pipeline_run
    run_id = run.id  # snapshot before ``expire_all`` invalidates the proxy
    install_id = install.installation_id
    repo_external_id = repo.external_id
    repo_full_name = repo.full_name
    body = _workflow_run_payload(
        install_id=install_id,
        repo_external_id=repo_external_id,
        repo_full_name=repo_full_name,
        gh_run_id=778899,
        status="completed",
        conclusion="success",
    )

    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    refreshed = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )
    ).scalar_one()
    assert refreshed.status == "succeeded"
    assert refreshed.finished_at is not None
    metrics = (refreshed.payload or {}).get("metrics") or {}
    assert metrics.get("gh_workflow_run_id") == 778899
    assert metrics.get("gh_html_url", "").endswith("/runs/778899")


@pytest.mark.asyncio
async def test_workflow_run_webhook_does_not_overwrite_terminal_run(
    v1_client, db_session, github_app_env, seed_install_repo_pipeline_run
) -> None:
    """Callback already won (status=succeeded) → webhook only enriches metrics."""
    from backend.app.db.models.pipelines import PipelineRun

    _, _, install, repo, _, run = seed_install_repo_pipeline_run
    # Simulate the callback having landed first — the workflow's
    # ``Report back to Ship`` step posts the gh_workflow_run_id alongside
    # the terminal status, so the fast-match-by-id path picks this run up
    # for enrichment.
    run.status = "succeeded"
    run.summary = "Gate green from callback"
    run.finished_at = datetime.now(timezone.utc)
    run.payload = {"metrics": {"gh_workflow_run_id": 999_111}}
    await db_session.flush()
    run_id = run.id
    install_id = install.installation_id
    repo_external_id = repo.external_id
    repo_full_name = repo.full_name

    body = _workflow_run_payload(
        install_id=install_id,
        repo_external_id=repo_external_id,
        repo_full_name=repo_full_name,
        gh_run_id=999_111,
        # Webhook reports a *different* terminal verdict; we must not
        # let it stomp the callback's authoritative answer.
        status="completed",
        conclusion="failure",
    )
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    refreshed = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )
    ).scalar_one()
    assert refreshed.status == "succeeded"
    assert refreshed.summary == "Gate green from callback"
    metrics = (refreshed.payload or {}).get("metrics") or {}
    # Enrichment still happens so deep-link works.
    assert metrics.get("gh_workflow_run_id") == 999_111
    assert metrics.get("gh_html_url", "").endswith("/runs/999111")


@pytest.mark.asyncio
async def test_workflow_run_webhook_for_unrelated_workflow_is_no_op(
    v1_client, db_session, github_app_env, seed_install_repo_pipeline_run
) -> None:
    """Customer-authored workflows never bind to our PipelineRun rows."""
    from backend.app.db.models.pipelines import PipelineRun

    _, _, install, repo, _, run = seed_install_repo_pipeline_run
    run_id = run.id
    install_id = install.installation_id
    repo_external_id = repo.external_id
    repo_full_name = repo.full_name
    body = _workflow_run_payload(
        install_id=install_id,
        repo_external_id=repo_external_id,
        repo_full_name=repo_full_name,
        gh_run_id=12321,
        status="completed",
        conclusion="success",
        # Crucial: missing the ``Ship · `` prefix used by our starter
        # workflow file — reconciliation should bail before touching
        # the in-flight pipeline run.
        name="Customer CI · build",
    )
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    refreshed = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )
    ).scalar_one()
    # Run untouched.
    assert refreshed.status == "running"
    assert refreshed.finished_at is None
    assert (refreshed.payload or {}).get("metrics") in (None, {})
