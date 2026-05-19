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
)


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
    run_id = await _insert_dispatch_audit(
        db_session, workspace_id=ws, ticket_ref="PAC-32"
    )
    # Backdate dispatch to match the lock's claimed_at (sweeper resolves
    # owner via project_id fallback within ±5 min of claimed_at).
    await db_session.execute(
        text(
            "UPDATE audit_log SET created_at = NOW() - INTERVAL '70 minutes' "
            "WHERE id = :id"
        ),
        {"id": run_id},
    )
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
    assert payload["owning_run_id"] == str(run_id)  # audit_log bigint id
    assert sweep_rows[0].target_id == "PAC-32"


@pytest.mark.asyncio
async def test_stale_lock_with_active_chain_is_left_alone(
    db_session,
) -> None:
    """A long-running chain (recent ``agent_run.dispatch`` on the
    same ticket) means the lock is still load-bearing — don't release."""
    ws = await _make_workspace(db_session)
    run_id = await _insert_dispatch_audit(
        db_session,
        workspace_id=ws,
        ticket_ref="PAC-99",
        project_id="proj-active",
    )
    await db_session.execute(
        text(
            "UPDATE audit_log SET created_at = NOW() - INTERVAL '70 minutes' "
            "WHERE id = :id"
        ),
        {"id": run_id},
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
    run_id = await _insert_dispatch_audit(
        db_session, workspace_id=ws, ticket_ref="PAC-1"
    )
    await db_session.execute(
        text(
            "UPDATE audit_log SET created_at = NOW() - INTERVAL '90 minutes' "
            "WHERE id = :id"
        ),
        {"id": run_id},
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
