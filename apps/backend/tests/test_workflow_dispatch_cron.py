"""Backend-driven trigger-workflow dispatcher.

Background: the customer trigger workflow used to ride on a GHA
``schedule: */30 * * * *`` block, but GitHub throttles scheduled
events under load — production observed 4-8 hour gaps between
ticks on busy days. This module's cron fires ``workflow_dispatch``
on every activated repo at a deterministic cadence so the picker
actually runs when it's supposed to.

These tests pin the load-bearing behaviour of ``dispatch_due_repos``:

* Activated repos get dispatched; not-activated ones are ignored.
* A 404 from GitHub on one repo doesn't poison the whole tick —
  the dispatcher keeps going through the fleet.
* Every dispatch lands an ``audit_log`` row so an operator can
  reconstruct "did Ship fire the workflow for repo X at time Y"
  without rummaging through GitHub's audit log.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone

from backend.app.core.config import get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog
from backend.app.integrations.github.workflows import WorkflowDispatchError
from backend.app.services.workflow_dispatch_cron import dispatch_due_repos
from sqlalchemy import select


@pytest_asyncio.fixture
async def seed_two_repos(db_session, seed_workspace):
    """One activated repo + one inactive repo on the same install.

    The inactive row exercises the ``activated_at IS NULL`` filter
    — Ship doesn't dispatch to repos the operator hasn't completed
    the install handshake on, even if the GitHub App is technically
    granted access.
    """
    _, _, workspace = seed_workspace
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

    active = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_777_001,
        full_name="acme/visitor-web",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/visitor-web",
        activated_at=datetime.now(timezone.utc),
    )
    inactive = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_777_002,
        full_name="acme/visitor-mob",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/visitor-mob",
        activated_at=None,
    )
    db_session.add_all([active, inactive])
    await db_session.flush()
    return workspace, install, active, inactive


@pytest.mark.asyncio
async def test_dispatch_due_repos_fires_only_on_activated(
    db_session, seed_two_repos, monkeypatch
) -> None:
    """The dispatcher must skip the inactive repo on the same install.

    Otherwise an operator who half-finished a connect flow would
    burn cron ticks on a repo whose workflow isn't even installed
    yet, and the resulting 404s would noise up the audit log
    every hour.
    """
    workspace, install, active, inactive = seed_two_repos

    calls: list[tuple[str, dict[str, str]]] = []

    async def _fake_dispatch(
        repo, _install, workflow_file, *, inputs, settings, ref=None, client=None
    ):
        calls.append((repo.full_name, dict(inputs)))

    monkeypatch.setattr(
        "backend.app.services.workflow_dispatch_cron.dispatch_workflow",
        _fake_dispatch,
    )

    result = await dispatch_due_repos(db_session, settings=get_settings())

    assert result.dispatched == 1
    assert result.failed == 0
    assert calls == [("acme/visitor-web", {})], (
        "only the activated repo should receive a dispatch; inputs "
        "are empty so the workflow falls into the normal cron path "
        "exactly as a schedule-event would have"
    )


@pytest.mark.asyncio
async def test_dispatch_due_repos_keeps_going_after_404(
    db_session, seed_two_repos, monkeypatch
) -> None:
    """One 404 must not starve the rest of the fleet.

    Reproduction case: customer hasn't merged the wizard-seed PR
    on ``visitor-mob`` yet, so the workflow file 404s. We want
    ``visitor-web`` (also on the cron) to still get its dispatch
    in the same tick. Without this guarantee, a single
    half-installed repo would silently hold the entire workspace.
    """
    workspace, install, active, inactive = seed_two_repos

    # Activate the second repo too so both should be dispatched —
    # then make the first one 404.
    inactive.activated_at = datetime.now(timezone.utc)
    await db_session.flush()

    calls: list[str] = []

    async def _fake_dispatch(
        repo, _install, workflow_file, *, inputs, settings, ref=None, client=None
    ):
        calls.append(repo.full_name)
        if repo.full_name == "acme/visitor-web":
            raise WorkflowDispatchError(404, "workflow_file not found")

    monkeypatch.setattr(
        "backend.app.services.workflow_dispatch_cron.dispatch_workflow",
        _fake_dispatch,
    )

    result = await dispatch_due_repos(db_session, settings=get_settings())

    assert result.failed == 1
    assert result.dispatched == 1
    assert set(calls) == {"acme/visitor-web", "acme/visitor-mob"}
    assert any(
        f["repo"] == "acme/visitor-web" and f["status"] == "404"
        for f in result.failures
    )


@pytest.mark.asyncio
async def test_dispatch_due_repos_audits_each_dispatch(
    db_session, seed_two_repos, monkeypatch
) -> None:
    """Each dispatch lands an ``audit_log`` row.

    Failures land their own row too (with the upstream status +
    error excerpt), so an operator chasing "why did repo X stop
    ticking" can reconstruct the cron's view from the dashboard
    without re-running the job or scraping GitHub's audit log.
    """
    workspace, install, active, inactive = seed_two_repos
    inactive.activated_at = datetime.now(timezone.utc)
    await db_session.flush()

    async def _fake_dispatch(
        repo, _install, workflow_file, *, inputs, settings, ref=None, client=None
    ):
        if repo.full_name == "acme/visitor-mob":
            raise WorkflowDispatchError(502, "Bad Gateway")

    monkeypatch.setattr(
        "backend.app.services.workflow_dispatch_cron.dispatch_workflow",
        _fake_dispatch,
    )

    await dispatch_due_repos(db_session, settings=get_settings())
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.workspace_id == workspace.id)
        )
    ).scalars().all()
    actions = {r.action for r in rows}
    assert "workflow_dispatch.tick" in actions, (
        "successful dispatches must produce a ``workflow_dispatch.tick`` "
        "audit row keyed on the repo"
    )
    assert "workflow_dispatch.failed" in actions, (
        "failures must produce a ``workflow_dispatch.failed`` row "
        "carrying the upstream status + error message"
    )
    failed_row = next(r for r in rows if r.action == "workflow_dispatch.failed")
    assert failed_row.payload["upstream_status"] == 502
    assert failed_row.payload["repo_full_name"] == "acme/visitor-mob"
