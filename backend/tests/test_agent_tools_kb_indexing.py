"""Navigator ``trigger_repo_kb_indexing`` / ``probe_repo_kb_indexing`` (ELS-62).

Companion to ``test_agent_tools_repo_kb_indexing.py`` (which covers the
older synchronous ``repo_kb_status`` / ``reindex_repo_kb``). The new
tools layer persisted :class:`KbIndexingRun` rows + async execution
over the same ``kb_indexer`` driver, so the tests mostly verify the
wrapper contract: defaults, tenancy fencing, run-state shape.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.models.agent_memory import KbIndexingRun
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.services.agent.kb_indexer import IndexReport


def _toolbox(session, *, workspace_id, user_id, active_repo_id=None):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
        active_repo_id=active_repo_id,
    )


async def _seed_install(db_session, *, workspace_id, installation_id=8_950_000):
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
    external_id: int = 990_500,
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


def _seed_run(
    db_session,
    *,
    workspace_id,
    repo_id,
    status="done",
    trigger="agent",
    stats=None,
    created_at=None,
    started_at=None,
    finished_at=None,
    error=None,
) -> KbIndexingRun:
    run = KbIndexingRun(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        repo_id=repo_id,
        status=status,
        trigger=trigger,
        stats=stats or {},
        error=error,
        created_at=created_at or datetime.now(timezone.utc),
        started_at=started_at,
        finished_at=finished_at,
    )
    db_session.add(run)
    return run


# ---------------------------------------------------------------------------
# trigger_repo_kb_indexing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_returns_run_id_immediately(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Happy path: row is created in ``pending`` and ``run_id`` is returned."""
    user, _, ws = seed_workspace
    install = await _seed_install(db_session, workspace_id=ws.id)
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, install_id=install.id,
        external_id=990_500, full_name="acme/trigger",
    )

    # Suppress the background task — we only care about the synchronous
    # response shape + DB row here.
    scheduled = {"count": 0}

    def _fake_create_task(coro):
        scheduled["count"] += 1
        coro.close()  # don't actually run

    monkeypatch.setattr("asyncio.create_task", _fake_create_task)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("trigger_repo_kb_indexing", {"repo_id": str(repo.id)})
    )
    assert out["status"] == "pending"
    assert out["trigger"] == "agent"
    assert out["repo_id"] == str(repo.id)
    run_id = uuid.UUID(out["run_id"])

    row = await db_session.get(KbIndexingRun, run_id)
    assert row is not None
    assert row.created_by_user_id == user.id
    assert row.trigger == "agent"
    assert scheduled["count"] == 1


@pytest.mark.asyncio
async def test_trigger_defaults_to_active_repo(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Omitting ``repo_id`` falls back to the chat's ``_active_repo_id``."""
    user, _, ws = seed_workspace
    install = await _seed_install(
        db_session, workspace_id=ws.id, installation_id=8_950_010,
    )
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, install_id=install.id,
        external_id=990_510, full_name="acme/active",
    )

    def _suppress(coro):
        coro.close()
    monkeypatch.setattr("asyncio.create_task", _suppress)

    box = _toolbox(
        db_session, workspace_id=ws.id, user_id=user.id,
        active_repo_id=repo.id,
    )
    out = json.loads(await box.invoke("trigger_repo_kb_indexing", {}))
    assert out["repo_id"] == str(repo.id)
    assert out["status"] == "pending"


@pytest.mark.asyncio
async def test_trigger_returns_repo_id_required_when_no_default(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(await box.invoke("trigger_repo_kb_indexing", {}))
    assert out == {"error": "repo_id_required"}


@pytest.mark.asyncio
async def test_trigger_rejects_foreign_workspace_repo(
    db_session, seed_workspace, monkeypatch
) -> None:
    from backend.app.db.models.tenancy import Workspace

    user, _, ws_a = seed_workspace
    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-b-{uuid.uuid4().hex[:6]}", name="B"
    )
    db_session.add(ws_b)
    await db_session.flush()
    install_b = await _seed_install(
        db_session, workspace_id=ws_b.id, installation_id=8_950_020,
    )
    foreign = await _seed_repo(
        db_session, workspace_id=ws_b.id, install_id=install_b.id,
        external_id=990_520, full_name="acme/foreign",
    )

    fired = {"count": 0}

    def _explode(coro):
        fired["count"] += 1
        coro.close()
    monkeypatch.setattr("asyncio.create_task", _explode)

    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "trigger_repo_kb_indexing", {"repo_id": str(foreign.id)}
        )
    )
    assert out == {"error": "repo_not_found_in_workspace"}
    # The row never made it onto the table.
    from sqlalchemy import select
    rows = (await db_session.execute(
        select(KbIndexingRun).where(KbIndexingRun.repo_id == foreign.id)
    )).scalars().all()
    assert rows == []
    assert fired["count"] == 0


# ---------------------------------------------------------------------------
# probe_repo_kb_indexing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_latest_returns_null_for_never_indexed_repo(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_600,
        full_name="acme/empty",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("probe_repo_kb_indexing", {"repo_id": str(repo.id)})
    )
    assert out["latest"] is None
    assert out["kb_chunk_count"] == 0
    assert out["kb_last_indexed_at"] is None


@pytest.mark.asyncio
async def test_probe_latest_picks_most_recent_run(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_601,
        full_name="acme/latest",
    )
    base = datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc)
    _seed_run(
        db_session, workspace_id=ws.id, repo_id=repo.id,
        status="done", trigger="push", created_at=base,
        stats={"files_indexed": 1, "chunks_written": 2},
    )
    newer = _seed_run(
        db_session, workspace_id=ws.id, repo_id=repo.id,
        status="running", trigger="agent",
        created_at=base + timedelta(minutes=10),
        started_at=base + timedelta(minutes=10),
    )
    await db_session.flush()

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("probe_repo_kb_indexing", {"repo_id": str(repo.id)})
    )
    assert out["latest"]["run_id"] == str(newer.id)
    assert out["latest"]["status"] == "running"
    assert out["latest"]["trigger"] == "agent"


@pytest.mark.asyncio
async def test_probe_explicit_run_id_returns_run(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_602,
        full_name="acme/explicit",
    )
    run = _seed_run(
        db_session, workspace_id=ws.id, repo_id=repo.id,
        status="error", trigger="agent",
        error="OPENAI_API_KEY missing",
    )
    await db_session.flush()

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "probe_repo_kb_indexing",
            {"repo_id": str(repo.id), "run_id": str(run.id)},
        )
    )
    assert out["run"]["run_id"] == str(run.id)
    assert out["run"]["status"] == "error"
    assert out["run"]["error"] == "OPENAI_API_KEY missing"


@pytest.mark.asyncio
async def test_probe_run_from_other_repo_returns_run_not_found(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo_a = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_603,
        full_name="acme/probe-a",
    )
    repo_b = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_604,
        full_name="acme/probe-b",
    )
    run = _seed_run(
        db_session, workspace_id=ws.id, repo_id=repo_b.id,
        status="done", trigger="agent",
    )
    await db_session.flush()

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "probe_repo_kb_indexing",
            {"repo_id": str(repo_a.id), "run_id": str(run.id)},
        )
    )
    assert out == {"error": "run_not_found"}


@pytest.mark.asyncio
async def test_probe_run_id_invalid_returns_run_not_found(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, external_id=990_605,
        full_name="acme/invalid",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "probe_repo_kb_indexing",
            {"repo_id": str(repo.id), "run_id": "not-a-uuid"},
        )
    )
    assert out == {"error": "run_not_found"}


@pytest.mark.asyncio
async def test_probe_rejects_foreign_workspace_repo(
    db_session, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import Workspace

    user, _, ws_a = seed_workspace
    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-b-{uuid.uuid4().hex[:6]}", name="B"
    )
    db_session.add(ws_b)
    await db_session.flush()
    foreign = await _seed_repo(
        db_session, workspace_id=ws_b.id, external_id=990_606,
        full_name="acme/foreign",
    )

    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "probe_repo_kb_indexing", {"repo_id": str(foreign.id)}
        )
    )
    assert out == {"error": "repo_not_found_in_workspace"}


# ---------------------------------------------------------------------------
# kb_indexer wrapper — execute_kb_indexing_run lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_drives_pending_to_done(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Wrapper takes a ``pending`` row through ``running → done`` and
    persists the :class:`IndexReport` into ``stats``."""
    from backend.app.services.agent.kb_indexer import (
        create_kb_indexing_run,
        execute_kb_indexing_run,
    )

    user, _, ws = seed_workspace
    install = await _seed_install(
        db_session, workspace_id=ws.id, installation_id=8_950_100,
    )
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, install_id=install.id,
        external_id=990_700, full_name="acme/execute",
    )

    async def _fake_reindex(session, repo_arg, install_arg, *, settings=None, gateway=None):
        return IndexReport(
            repo_id=str(repo_arg.id),
            files_discovered=5,
            files_indexed=3,
            files_skipped_unchanged=2,
            files_skipped_too_big=0,
            files_skipped_binary=0,
            chunks_deleted=1,
            chunks_written=11,
        )

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb",
        _fake_reindex,
    )

    run = await create_kb_indexing_run(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        trigger="agent",
        created_by_user_id=user.id,
    )
    await execute_kb_indexing_run(db_session, run_id=run.id)
    await db_session.refresh(run)
    assert run.status == "done"
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.stats["files_indexed"] == 3
    assert run.stats["chunks_written"] == 11
    assert run.error is None


@pytest.mark.asyncio
async def test_execute_captures_indexer_runtime_error(
    db_session, seed_workspace, monkeypatch
) -> None:
    """If ``reindex_repo_kb`` raises (e.g. OPENAI_API_KEY missing) the
    run row transitions to ``error`` with the message captured. The
    wrapper never re-raises into the caller."""
    from backend.app.services.agent.kb_indexer import (
        create_kb_indexing_run,
        execute_kb_indexing_run,
    )

    user, _, ws = seed_workspace
    install = await _seed_install(
        db_session, workspace_id=ws.id, installation_id=8_950_110,
    )
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, install_id=install.id,
        external_id=990_710, full_name="acme/no-key",
    )

    async def _no_key(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb",
        _no_key,
    )

    run = await create_kb_indexing_run(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        trigger="agent",
        created_by_user_id=user.id,
    )
    await execute_kb_indexing_run(db_session, run_id=run.id)
    await db_session.refresh(run)
    assert run.status == "error"
    assert "OPENAI_API_KEY" in (run.error or "")
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_execute_marks_error_when_install_missing(
    db_session, seed_workspace
) -> None:
    """Repo without an installation transitions straight to error so the
    advisory lock never spins. Probe still surfaces the message."""
    from backend.app.services.agent.kb_indexer import (
        create_kb_indexing_run,
        execute_kb_indexing_run,
    )

    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, workspace_id=ws.id, install_id=None,
        external_id=990_720, full_name="acme/no-install",
    )
    run = await create_kb_indexing_run(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        trigger="agent",
        created_by_user_id=user.id,
    )
    await execute_kb_indexing_run(db_session, run_id=run.id)
    await db_session.refresh(run)
    assert run.status == "error"
    assert run.error == "github_install_missing"


@pytest.mark.asyncio
async def test_advisory_lock_key_is_stable_per_repo() -> None:
    """Same repo id → same key; different repo id → almost certainly
    different key (single-collision tolerance only). The push and
    agent paths derive the same key from the same UUID."""
    from backend.app.services.agent.kb_indexer import _advisory_lock_key_for_repo

    repo_id = uuid.uuid4()
    assert _advisory_lock_key_for_repo(repo_id) == _advisory_lock_key_for_repo(repo_id)
    # ``str`` and ``UUID`` accept the same lookup path.
    assert _advisory_lock_key_for_repo(str(repo_id)) == _advisory_lock_key_for_repo(repo_id)
    # Type sanity: must fit Postgres' signed-bigint advisory-lock arg.
    key = _advisory_lock_key_for_repo(repo_id)
    assert -(1 << 63) <= key < (1 << 63)
