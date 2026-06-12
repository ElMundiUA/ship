"""ELS-149 — unit tests for the ``project:*`` lock sweeper.

Cases covered:

1. **Stale lock + dead chain** → released + audit row written.
2. **Stale lock + active chain (recent dispatch)** → left alone.
3. **Non-stale lock (< 60 min)** → left alone, regardless of chain.
4. **Non-project lock (``ticket:*``)** → never touched.
5. **Lock with no owning dispatch row** (``run_id`` is None or row
   deleted) → left alone — sweeper has no ticket_ref to verify.

The sweeper queries by NOW(); we manipulate ``claimed_at`` directly
to age locks past the threshold.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from backend.app.db.models.agent_dispatch import AgentDispatchLock
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.dispatch_lock_sweep import (
    sweep_dangling_project_locks,
    sweep_expired_locks_tick,
)


@pytest.fixture
def _patch_lock_sweep_sessionmaker(db_session, monkeypatch):
    """Wire tick helpers to the test's transactional session."""
    from contextlib import asynccontextmanager

    from backend.app.services import dispatch_lock_sweep

    @asynccontextmanager
    async def _bound_session_factory():
        yield db_session

    class _SM:
        def __call__(self):
            return _bound_session_factory()

    monkeypatch.setattr(dispatch_lock_sweep, "get_sessionmaker", lambda: _SM())


async def _make_workspace(db_session) -> uuid.UUID:
    from backend.app.db.models.tenancy import Org, Workspace

    org = Org(slug=f"t-{uuid.uuid4().hex[:8]}", name="Test", plan="free")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id, slug=f"t-{uuid.uuid4().hex[:8]}", name="Test ws"
    )
    db_session.add(ws)
    await db_session.flush()
    return ws.id


async def _insert_dispatch_audit(
    db_session, *, workspace_id, ticket_ref, project_id="proj-xyz"
) -> uuid.UUID:
    row = AuditLog(
        workspace_id=workspace_id,
        action="agent_run.dispatch",
        target_kind="ticket",
        target_id=ticket_ref,
        payload={
            "ticket_ref": ticket_ref,
            "project_id": project_id,
            "fsm_stage": "dev_implementation",
        },
    )
    db_session.add(row)
    await db_session.flush()
    return row.id


async def _insert_lock(
    db_session,
    *,
    workspace_id,
    key,
    run_id=None,
    age_minutes=70,
) -> uuid.UUID:
    """Insert a lock with `claimed_at` aged into the past."""
    claimed_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    expires_at = claimed_at + timedelta(hours=24)
    lock = AgentDispatchLock(
        workspace_id=workspace_id,
        key=key,
        claimed_at=claimed_at,
        expires_at=expires_at,
        run_id=run_id,
    )
    db_session.add(lock)
    await db_session.flush()
    return lock.id


@pytest.mark.asyncio
async def test_stale_lock_with_dead_chain_is_released(db_session) -> None:
    """The canonical leak case: project_lock 60+ min old, no recent
    agent activity for the owning ticket → sweep releases + audits."""
    ws = await _make_workspace(db_session)
    dispatch_id = await _insert_dispatch_audit(
        db_session, workspace_id=ws, ticket_ref="PAC-32"
    )
    # Align dispatch timestamp with lock claim (fallback lookup uses ±5m).
    await db_session.execute(
        text(
            "UPDATE audit_log SET created_at = NOW() - INTERVAL '70 minutes' "
            "WHERE id = :id"
        ),
        {"id": dispatch_id},
    )
    # run_id is NULL in prod (audit_log.id is bigint; lock.run_id is uuid).
    await _insert_lock(
        db_session,
        workspace_id=ws,
        key="project:proj-xyz",
        run_id=None,
        age_minutes=70,
    )

    released = await sweep_dangling_project_locks(db_session)
    assert released == 1

    remaining_locks = (
        await db_session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == ws
            )
        )
    ).scalars().all()
    assert remaining_locks == []

    sweep_rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == "dispatch.project_lock_swept",
            )
        )
    ).scalars().all()
    assert len(sweep_rows) == 1
    payload = sweep_rows[0].payload
    assert payload["lock_key"] == "project:proj-xyz"
    assert payload["reason"] == "no_recent_activity"
    assert payload["owning_run_id"] == str(dispatch_id)
    assert sweep_rows[0].target_id == "PAC-32"


@pytest.mark.asyncio
async def test_stale_lock_with_active_chain_is_left_alone(
    db_session,
) -> None:
    """A long-running chain (recent ``agent_run.dispatch`` on the
    same ticket) means the lock is still load-bearing — don't release."""
    ws = await _make_workspace(db_session)
    dispatch_id = await _insert_dispatch_audit(
        db_session,
        workspace_id=ws,
        ticket_ref="PAC-99",
        project_id="proj-active",
    )
    # The owning dispatch is old, but a more recent dispatch on the
    # same ticket (cascade) keeps the chain alive.
    await db_session.execute(
        text(
            "UPDATE audit_log SET created_at = NOW() - INTERVAL '70 minutes' "
            "WHERE id = :id"
        ),
        {"id": dispatch_id},
    )
    db_session.add(
        AuditLog(
            workspace_id=ws,
            action="agent_run.dispatch",
            target_kind="ticket",
            target_id="PAC-99",
            payload={
                "ticket_ref": "PAC-99",
                "project_id": "proj-active",
                "fsm_stage": "code_review",
            },
        )
    )
    await db_session.flush()

    await _insert_lock(
        db_session,
        workspace_id=ws,
        key="project:proj-active",
        run_id=None,
        age_minutes=70,
    )

    released = await sweep_dangling_project_locks(db_session)
    assert released == 0

    remaining_locks = (
        await db_session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == ws
            )
        )
    ).scalars().all()
    assert len(remaining_locks) == 1


@pytest.mark.asyncio
async def test_non_stale_lock_is_skipped(db_session) -> None:
    """A young lock (claimed_at < 60 min ago) is below the floor and
    must be ignored even if the chain looks idle."""
    ws = await _make_workspace(db_session)
    dispatch_id = await _insert_dispatch_audit(
        db_session, workspace_id=ws, ticket_ref="PAC-1"
    )
    await db_session.execute(
        text(
            "UPDATE audit_log SET created_at = NOW() - INTERVAL '90 minutes' "
            "WHERE id = :id"
        ),
        {"id": dispatch_id},
    )
    await _insert_lock(
        db_session,
        workspace_id=ws,
        key="project:proj-young",
        run_id=None,
        age_minutes=15,  # well under the 60-min floor
    )

    released = await sweep_dangling_project_locks(db_session)
    assert released == 0


@pytest.mark.asyncio
async def test_ticket_lock_is_not_swept(db_session) -> None:
    """The sweeper only touches keys starting with ``project:``. A
    ``ticket:*`` lock is left alone (it has its own TTL)."""
    ws = await _make_workspace(db_session)
    await _insert_lock(
        db_session,
        workspace_id=ws,
        key="ticket:PAC-50",
        run_id=None,
        age_minutes=120,
    )

    released = await sweep_dangling_project_locks(db_session)
    assert released == 0

    remaining = (
        await db_session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == ws,
                AgentDispatchLock.key == "ticket:PAC-50",
            )
        )
    ).scalars().all()
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_lock_without_owning_dispatch_is_left_alone(
    db_session,
) -> None:
    """A project lock with no resolvable owning dispatch row (NULL
    run_id, or pointing at a deleted audit row) has no ticket_ref the
    sweeper can use to verify chain health. Leave it for the 24h TTL."""
    ws = await _make_workspace(db_session)
    await _insert_lock(
        db_session,
        workspace_id=ws,
        key="project:proj-orphan",
        run_id=None,  # no owning dispatch
        age_minutes=120,
    )

    released = await sweep_dangling_project_locks(db_session)
    assert released == 0


async def _insert_scheduled_lock(
    db_session,
    *,
    workspace_id,
    key,
    expires_offset_minutes: int,
) -> uuid.UUID:
    """Insert a ``*:scheduled`` lock with a specific ``expires_at``."""
    now = datetime.now(timezone.utc)
    claimed_at = now - timedelta(hours=1)
    expires_at = now + timedelta(minutes=expires_offset_minutes)
    lock = AgentDispatchLock(
        workspace_id=workspace_id,
        key=key,
        claimed_at=claimed_at,
        expires_at=expires_at,
    )
    db_session.add(lock)
    await db_session.flush()
    return lock.id


@pytest.mark.asyncio
async def test_expired_scheduled_lock_swept_on_tick(
    db_session, _patch_lock_sweep_sessionmaker
) -> None:
    """ELS-264 AC#3 — expired ``self-heal:scheduled`` row is deleted."""
    ws = await _make_workspace(db_session)
    await _insert_scheduled_lock(
        db_session,
        workspace_id=ws,
        key="self-heal:scheduled",
        expires_offset_minutes=-60,
    )

    await sweep_expired_locks_tick()

    remaining = (
        await db_session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == ws,
                AgentDispatchLock.key == "self-heal:scheduled",
            )
        )
    ).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_non_expired_scheduled_lock_preserved(
    db_session, _patch_lock_sweep_sessionmaker
) -> None:
    """Future ``expires_at`` on a bundle lock must survive the TTL tick."""
    ws = await _make_workspace(db_session)
    await _insert_scheduled_lock(
        db_session,
        workspace_id=ws,
        key="daily-digest:scheduled",
        expires_offset_minutes=30,
    )

    await sweep_expired_locks_tick()

    remaining = (
        await db_session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == ws,
                AgentDispatchLock.key == "daily-digest:scheduled",
            )
        )
    ).scalars().all()
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_expired_lock_tick_deletes_all_namespaces(
    db_session, _patch_lock_sweep_sessionmaker
) -> None:
    """Global TTL sweep removes expired ``ticket:*`` and ``project:*`` rows."""
    ws = await _make_workspace(db_session)
    for key in ("ticket:ELS-99", "project:proj-1", "weekly-audit:scheduled"):
        await _insert_scheduled_lock(
            db_session,
            workspace_id=ws,
            key=key,
            expires_offset_minutes=-10,
        )

    await sweep_expired_locks_tick()

    remaining = (
        await db_session.execute(
            select(AgentDispatchLock).where(AgentDispatchLock.workspace_id == ws)
        )
    ).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_agent_dispatch_lock_sweep_tick_runs_ttl_cleanup(
    db_session, monkeypatch, _patch_lock_sweep_sessionmaker
) -> None:
    """Cron wiring — combined tick invokes TTL cleanup before project sweep."""
    ws = await _make_workspace(db_session)
    await _insert_scheduled_lock(
        db_session,
        workspace_id=ws,
        key="self-heal:scheduled",
        expires_offset_minutes=-60,
    )
    project_sweep_called = {"value": False}

    async def _stub_project_sweep() -> None:
        project_sweep_called["value"] = True

    monkeypatch.setattr(
        "backend.app.services.dispatch_lock_sweep.sweep_dangling_project_locks_tick",
        _stub_project_sweep,
    )

    from backend.app.services.cron_jobs import _agent_dispatch_lock_sweep_tick

    await _agent_dispatch_lock_sweep_tick()

    assert project_sweep_called["value"] is True
    remaining = (
        await db_session.execute(
            select(AgentDispatchLock).where(AgentDispatchLock.workspace_id == ws)
        )
    ).scalars().all()
    assert remaining == []
