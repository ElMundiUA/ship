"""Unit tests for the MemoryCodeHost + MemoryCi adapters (E19 step 4).

Pins the laptop-offline gateway implementations against the
behaviour the orchestrator + KB indexer rely on:

- ``list_repos`` / ``list_repo_summaries`` return the workspace's
  seeded repos
- ``list_files`` / ``get_blob`` round-trip text files at a ref
- ``get_pull_request`` returns a GH-shaped dict with head/base/state
- ``MemoryCi.list_runs`` walks ``queued → in_progress → completed``
  on the ``transition_at`` schedule
- Cross-workspace isolation: repos in workspace A are invisible to
  workspace B on the same Postgres
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.models.memory_adapters import MemoryCiRun
from backend.app.integrations.gateway.code_host import (
    PullRequestRef,
    RepoRef,
)
from backend.app.integrations.local.ci import MemoryCi
from backend.app.integrations.local.code_host import MemoryCodeHost


# ---------------------------------------------------------------------------
# CodeHost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repos_and_files_round_trip(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    gw = MemoryCodeHost(session=db_session, workspace_id=workspace.id)
    repo = await gw.ensure_repo(owner="acme", name="widget", default_branch="main")
    await gw.upsert_file(repo, path="README.md", content="# widget\n")
    await gw.upsert_file(repo, path="src/main.py", content="print('hi')\n")
    await db_session.commit()

    refs = await gw.list_repos()
    assert refs == [RepoRef(kind="github", owner="acme", repo="widget")]

    summaries = await gw.list_repo_summaries()
    assert summaries[0].full_name == "acme/widget"
    assert summaries[0].default_branch == "main"

    paths = await gw.list_files(refs[0])
    assert paths == ["README.md", "src/main.py"]

    blob = await gw.get_blob(refs[0], path="src/main.py")
    assert blob.content == "print('hi')\n"
    assert blob.encoding == "utf-8"
    assert len(blob.sha) == 64  # sha256 hex


@pytest.mark.asyncio
async def test_get_blob_raises_on_missing(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    gw = MemoryCodeHost(session=db_session, workspace_id=workspace.id)
    repo = await gw.ensure_repo(owner="acme", name="widget")
    await db_session.commit()
    with pytest.raises(FileNotFoundError):
        await gw.get_blob(
            RepoRef(kind="github", owner="acme", repo="widget"),
            path="does/not/exist.txt",
        )


@pytest.mark.asyncio
async def test_pull_request_round_trip(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    gw = MemoryCodeHost(session=db_session, workspace_id=workspace.id)
    repo = await gw.ensure_repo(owner="acme", name="widget")
    await db_session.commit()

    pr_row = await gw.open_pull_request(
        repo,
        title="feat: thing",
        body="body",
        head="feature/thing",
    )
    await db_session.commit()

    pr = await gw.get_pull_request(
        PullRequestRef(
            repo=RepoRef(kind="github", owner="acme", repo="widget"),
            number=pr_row.number,
        )
    )
    assert pr["title"] == "feat: thing"
    assert pr["state"] == "open"
    assert pr["merged"] is False
    assert pr["head"]["ref"] == "feature/thing"
    assert pr["base"]["ref"] == "main"
    assert len(pr["head"]["sha"]) == 40

    await gw.mark_pr_merged(repo, number=pr_row.number)
    await db_session.commit()
    merged = await gw.get_pull_request(
        PullRequestRef(
            repo=RepoRef(kind="github", owner="acme", repo="widget"),
            number=pr_row.number,
        )
    )
    assert merged["state"] == "closed"
    assert merged["merged"] is True
    assert merged["merged_at"] is not None


@pytest.mark.asyncio
async def test_repos_isolated_per_workspace(db_session, seed_user):
    from backend.app.db.models.tenancy import Workspace, WorkspaceMember

    user, org = seed_user
    ws_a = Workspace(org_id=org.id, slug=f"a-{uuid.uuid4().hex[:6]}", name="A")
    ws_b = Workspace(org_id=org.id, slug=f"b-{uuid.uuid4().hex[:6]}", name="B")
    db_session.add_all([ws_a, ws_b])
    await db_session.flush()
    db_session.add_all(
        [
            WorkspaceMember(
                workspace_id=ws_a.id, user_id=user.id, role="owner",
                answer_specialist_slugs=["*"],
            ),
            WorkspaceMember(
                workspace_id=ws_b.id, user_id=user.id, role="owner",
                answer_specialist_slugs=["*"],
            ),
        ]
    )
    await db_session.flush()

    gw_a = MemoryCodeHost(session=db_session, workspace_id=ws_a.id)
    gw_b = MemoryCodeHost(session=db_session, workspace_id=ws_b.id)
    await gw_a.ensure_repo(owner="acme", name="a-repo")
    await gw_b.ensure_repo(owner="acme", name="b-repo")
    await db_session.commit()

    assert {r.full_name for r in await gw_a.list_repos()} == {"acme/a-repo"}
    assert {r.full_name for r in await gw_b.list_repos()} == {"acme/b-repo"}


# ---------------------------------------------------------------------------
# MemoryCi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ci_run_walks_states(db_session, seed_workspace):
    """A run with elapsed transition_at advances queued → in_progress
    → completed when ``list_runs`` is called."""
    from sqlalchemy import select

    _, _, workspace = seed_workspace
    gw = MemoryCodeHost(session=db_session, workspace_id=workspace.id)
    ci = MemoryCi(session=db_session, workspace_id=workspace.id)
    repo = await gw.ensure_repo(owner="acme", name="widget")
    await db_session.commit()

    run = await ci.dispatch(
        repo,
        workflow_name="ci.yml",
        branch="main",
        outcome="success",
    )
    await db_session.commit()

    # Initial read — still queued (transition_at is 5s in future).
    rows = await ci.list_runs(
        RepoRef(kind="github", owner="acme", repo="widget")
    )
    assert rows[0]["status"] == "queued"
    assert rows[0]["conclusion"] is None

    # Backdate to make both phases ripe + re-read until completed.
    db_run = (
        await db_session.execute(
            select(MemoryCiRun).where(MemoryCiRun.id == run.id)
        )
    ).scalar_one()
    db_run.transition_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await db_session.commit()
    # First tick — queued → in_progress
    rows = await ci.list_runs(
        RepoRef(kind="github", owner="acme", repo="widget")
    )
    assert rows[0]["status"] == "in_progress"
    # Make in_progress phase ripe too.
    db_run = (
        await db_session.execute(
            select(MemoryCiRun).where(MemoryCiRun.id == run.id)
        )
    ).scalar_one()
    db_run.transition_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await db_session.commit()
    rows = await ci.list_runs(
        RepoRef(kind="github", owner="acme", repo="widget")
    )
    assert rows[0]["status"] == "completed"
    assert rows[0]["conclusion"] == "success"

    logs = await ci.get_logs(
        RepoRef(kind="github", owner="acme", repo="widget"),
        run_id=run.id,
    )
    assert "[memory-ci]" in logs
    # outcome sentinel got stripped
    assert "__pending_outcome:" not in logs


@pytest.mark.asyncio
async def test_ci_run_failure_outcome(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    gw = MemoryCodeHost(session=db_session, workspace_id=workspace.id)
    ci = MemoryCi(session=db_session, workspace_id=workspace.id)
    repo = await gw.ensure_repo(owner="acme", name="widget")
    run = await ci.dispatch(
        repo,
        workflow_name="ci.yml",
        outcome="failure",
        logs="boom\n",
    )
    # Backdate both phases at once so a single list_runs walks
    # all the way to completed.
    from sqlalchemy import select

    db_run = (
        await db_session.execute(
            select(MemoryCiRun).where(MemoryCiRun.id == run.id)
        )
    ).scalar_one()
    db_run.transition_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    await db_session.commit()
    rows = await ci.list_runs(
        RepoRef(kind="github", owner="acme", repo="widget")
    )
    # First tick: queued → in_progress
    assert rows[0]["status"] == "in_progress"
    db_run = (
        await db_session.execute(
            select(MemoryCiRun).where(MemoryCiRun.id == run.id)
        )
    ).scalar_one()
    db_run.transition_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    await db_session.commit()
    rows = await ci.list_runs(
        RepoRef(kind="github", owner="acme", repo="widget")
    )
    assert rows[0]["status"] == "completed"
    assert rows[0]["conclusion"] == "failure"


@pytest.mark.asyncio
async def test_ci_rerun_resets_state(db_session, seed_workspace):
    from sqlalchemy import select

    _, _, workspace = seed_workspace
    gw = MemoryCodeHost(session=db_session, workspace_id=workspace.id)
    ci = MemoryCi(session=db_session, workspace_id=workspace.id)
    repo = await gw.ensure_repo(owner="acme", name="widget")
    run = await ci.dispatch(repo, workflow_name="ci.yml", outcome="success")

    db_run = (
        await db_session.execute(
            select(MemoryCiRun).where(MemoryCiRun.id == run.id)
        )
    ).scalar_one()
    db_run.transition_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    await db_session.commit()
    await ci.list_runs(RepoRef(kind="github", owner="acme", repo="widget"))
    db_run = (
        await db_session.execute(
            select(MemoryCiRun).where(MemoryCiRun.id == run.id)
        )
    ).scalar_one()
    db_run.transition_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    await db_session.commit()
    await ci.list_runs(RepoRef(kind="github", owner="acme", repo="widget"))
    # Run is now completed.
    rows = await ci.list_runs(
        RepoRef(kind="github", owner="acme", repo="widget")
    )
    assert rows[0]["status"] == "completed"

    await ci.rerun(
        RepoRef(kind="github", owner="acme", repo="widget"),
        run_id=run.id,
    )
    await db_session.commit()
    rows = await ci.list_runs(
        RepoRef(kind="github", owner="acme", repo="widget")
    )
    assert rows[0]["status"] == "queued"
    assert rows[0]["conclusion"] is None
