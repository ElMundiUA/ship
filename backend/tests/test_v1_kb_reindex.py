"""HTTP surface for on-demand KB indexing (ELS-62).

Covers the three endpoints under ``/v1/workspaces/{ws}/repos/{repo_id}/kb``:

- ``POST /reindex`` — enqueue a run row and a background task; returns
  the ``run_id`` immediately so the agent can probe asynchronously.
- ``GET /runs/{run_id}`` — single run read-out with workspace
  ``kb_chunk_count`` / ``kb_last_indexed_at`` folded in.
- ``GET /runs?limit=N`` — recent runs for one repo, newest first.

The indexer's GitHub round-trip is stubbed out via ``monkeypatch`` of
the ``reindex_repo_kb`` callable so the tests stay offline. The
indexer's own behaviour is covered by ``test_agent_kb_indexer.py`` +
``test_services_repo_intel.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.app.db.models.agent_memory import KbIndexingRun
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.agent.kb_indexer import IndexReport


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_install(db_session, *, workspace_id, installation_id=8_900_001):
    install = GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=installation_id,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    return install


async def _seed_repo(
    db_session,
    *,
    workspace_id,
    install_id=None,
    external_id: int = 990_001,
    full_name: str = "acme/repo",
) -> WorkspaceRepo:
    repo = WorkspaceRepo(
        workspace_id=workspace_id,
        installation_id=install_id,
        provider="github",
        external_id=external_id,
        full_name=full_name,
        default_branch="main",
        private=False,
        html_url=f"https://github.com/{full_name}",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


def _stub_report(repo_id, **overrides) -> IndexReport:
    fields = {
        "repo_id": str(repo_id),
        "files_discovered": 3,
        "files_indexed": 2,
        "files_skipped_unchanged": 1,
        "files_skipped_too_big": 0,
        "files_skipped_binary": 0,
        "chunks_deleted": 4,
        "chunks_written": 7,
    }
    fields.update(overrides)
    return IndexReport(**fields)


# ---------------------------------------------------------------------------
# POST /reindex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_reindex_returns_run_id_synchronously(
    db_session, v1_client, seed_workspace, monkeypatch
) -> None:
    """Trigger endpoint returns within ~200ms and writes a pending run row."""
    user, raw, ws = seed_workspace
    install = await _seed_install(db_session, workspace_id=ws.id)
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, install_id=install.id,
        external_id=990_001, full_name="acme/trigger-happy",
    )
    await db_session.commit()

    # Stub the indexer; we don't care about its result here — we only
    # verify the HTTP response shape + the row state. The background
    # task fires but we don't await it.
    async def _fake_reindex(session, repo_arg, install_arg, *, settings=None, gateway=None):
        return _stub_report(repo_arg.id)

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb",
        _fake_reindex,
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{ws.id}/repos/{repo.id}/kb/reindex",
        headers=_bearer(raw),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["trigger"] == "agent"
    assert body["repo_id"] == str(repo.id)
    run_id = uuid.UUID(body["run_id"])

    row = await db_session.get(KbIndexingRun, run_id)
    assert row is not None
    assert row.workspace_id == ws.id
    assert row.repo_id == repo.id
    assert row.created_by_user_id == user.id
    assert row.trigger == "agent"


@pytest.mark.asyncio
async def test_post_reindex_returns_404_for_foreign_workspace_repo(
    db_session, v1_client, seed_workspace
) -> None:
    """A repo id that belongs to another workspace must not leak."""
    from backend.app.db.models.tenancy import Workspace

    user, raw, ws_a = seed_workspace
    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-b-{uuid.uuid4().hex[:6]}", name="B"
    )
    db_session.add(ws_b)
    await db_session.flush()
    foreign = await _seed_repo(
        db_session, workspace_id=ws_b.id, external_id=990_010,
        full_name="acme/foreign",
    )
    await db_session.commit()

    resp = await v1_client.post(
        f"/v1/workspaces/{ws_a.id}/repos/{foreign.id}/kb/reindex",
        headers=_bearer(raw),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "repo_not_found_in_workspace"

    # No row should have been written.
    from sqlalchemy import select
    rows = (await db_session.execute(
        select(KbIndexingRun).where(KbIndexingRun.repo_id == foreign.id)
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_post_reindex_writes_audit_log_entry(
    db_session, v1_client, seed_workspace, monkeypatch
) -> None:
    user, raw, ws = seed_workspace
    install = await _seed_install(db_session, workspace_id=ws.id)
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, install_id=install.id,
        external_id=990_002, full_name="acme/audited",
    )
    await db_session.commit()

    async def _fake_reindex(session, repo_arg, install_arg, *, settings=None, gateway=None):
        return _stub_report(repo_arg.id)

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb",
        _fake_reindex,
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{ws.id}/repos/{repo.id}/kb/reindex",
        headers=_bearer(raw),
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    from sqlalchemy import select
    entry = (await db_session.execute(
        select(AuditLog).where(
            AuditLog.workspace_id == ws.id,
            AuditLog.action == "kb_indexing.trigger",
        )
    )).scalars().first()
    assert entry is not None
    assert entry.target_kind == "workspace_repo"
    assert entry.target_id == str(repo.id)
    assert entry.actor_user_id == user.id
    assert entry.payload["run_id"] == run_id
    assert entry.payload["trigger"] == "agent"


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_returns_terminal_state_and_aggregates(
    db_session, v1_client, seed_workspace
) -> None:
    user, raw, ws = seed_workspace
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_020,
        full_name="acme/done",
    )
    run = KbIndexingRun(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        repo_id=repo.id,
        status="done",
        trigger="agent",
        stats={
            "files_discovered": 5,
            "files_indexed": 3,
            "files_skipped_unchanged": 2,
            "files_skipped_too_big": 0,
            "files_skipped_binary": 0,
            "chunks_deleted": 1,
            "chunks_written": 11,
        },
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/repos/{repo.id}/kb/runs/{run.id}",
        headers=_bearer(raw),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["stats"]["files_indexed"] == 3
    assert body["stats"]["chunks_written"] == 11
    assert body["kb_chunk_count"] == 0  # no chunks seeded
    assert body["kb_last_indexed_at"] is None


@pytest.mark.asyncio
async def test_get_run_rejects_run_from_other_repo(
    db_session, v1_client, seed_workspace
) -> None:
    """``run_id`` belonging to another repo (same workspace) must 404 with
    ``run_not_found`` rather than leak the row's state."""
    user, raw, ws = seed_workspace
    repo_a = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_030,
        full_name="acme/a",
    )
    repo_b = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_031,
        full_name="acme/b",
    )
    run = KbIndexingRun(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        repo_id=repo_b.id,
        status="done",
        trigger="agent",
        stats={},
    )
    db_session.add(run)
    await db_session.commit()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/repos/{repo_a.id}/kb/runs/{run.id}",
        headers=_bearer(raw),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"


# ---------------------------------------------------------------------------
# GET /runs (list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_returns_newest_first(
    db_session, v1_client, seed_workspace
) -> None:
    user, raw, ws = seed_workspace
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_040,
        full_name="acme/list",
    )
    from datetime import timedelta
    base = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    runs = []
    for i in range(3):
        r = KbIndexingRun(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            repo_id=repo.id,
            status="done",
            trigger="push" if i == 1 else "agent",
            stats={},
            created_at=base + timedelta(seconds=i),
        )
        db_session.add(r)
        runs.append(r)
    await db_session.commit()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/repos/{repo.id}/kb/runs",
        headers=_bearer(raw),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [r["run_id"] for r in body["runs"]]
    assert ids == [str(runs[2].id), str(runs[1].id), str(runs[0].id)]
    # Trigger discriminator round-trips intact.
    assert body["runs"][1]["trigger"] == "push"


@pytest.mark.asyncio
async def test_push_webhook_writes_run_with_trigger_push(
    db_session, seed_workspace, monkeypatch
) -> None:
    """``_apply_push_event_for_kb`` lands a ``kb_indexing_runs`` row with
    ``trigger='push'`` so the new probe / list endpoints surface
    webhook-driven reindexes alongside agent-driven ones."""
    from sqlalchemy import select

    from backend.app.api.v1.routes.github_app import _apply_push_event_for_kb
    from backend.app.core.config import get_settings

    user, raw, ws = seed_workspace
    install = await _seed_install(
        db_session, workspace_id=ws.id, installation_id=8_900_500,
    )
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, install_id=install.id,
        external_id=990_500, full_name="acme/push-target",
    )
    await db_session.flush()

    async def _fake_reindex(session, repo_arg, install_arg, *, settings=None, gateway=None):
        return _stub_report(repo_arg.id, files_indexed=4, chunks_written=9)

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb",
        _fake_reindex,
    )

    payload = {
        "ref": "refs/heads/main",
        "installation": {"id": install.installation_id},
        "repository": {"id": repo.external_id},
        "commits": [
            {
                "added": [".ship/knowledge/runbook.md"],
                "modified": [],
                "removed": [],
            }
        ],
    }
    await _apply_push_event_for_kb(
        db_session, payload, settings=get_settings()
    )
    await db_session.flush()

    rows = (await db_session.execute(
        select(KbIndexingRun).where(KbIndexingRun.repo_id == repo.id)
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.trigger == "push"
    assert row.status == "done"
    assert row.stats["files_indexed"] == 4
    assert row.created_by_user_id is None  # webhooks aren't attributed


@pytest.mark.asyncio
async def test_list_runs_clamps_limit(
    db_session, v1_client, seed_workspace
) -> None:
    user, raw, ws = seed_workspace
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_041,
        full_name="acme/clamp",
    )
    await db_session.commit()

    # limit=0 (below floor) and limit=999 (above ceiling) both fail
    # validation rather than silently coercing — keeps callers honest.
    r0 = await v1_client.get(
        f"/v1/workspaces/{ws.id}/repos/{repo.id}/kb/runs?limit=0",
        headers=_bearer(raw),
    )
    assert r0.status_code == 422
    r999 = await v1_client.get(
        f"/v1/workspaces/{ws.id}/repos/{repo.id}/kb/runs?limit=999",
        headers=_bearer(raw),
    )
    assert r999.status_code == 422
