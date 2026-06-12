"""Engine health / stall residue surface (ELS-230).

Pins: read-only derivation from agent_dispatch_locks + audit_log,
the two stall reasons, and zero-mutation behavior.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.db.models.agent_dispatch import AgentDispatchLock
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.engine_health import assess_engine_health


def _now():
    return datetime.now(timezone.utc)


async def _add_lock(db_session, ws_id, key, *, claimed_min_ago, ttl_min):
    claimed = _now() - timedelta(minutes=claimed_min_ago)
    db_session.add(
        AgentDispatchLock(
            workspace_id=ws_id,
            key=key,
            claimed_at=claimed,
            expires_at=claimed + timedelta(minutes=ttl_min),
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_healthy_empty_workspace(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    liveness = await assess_engine_health(db_session, workspace_id=ws.id)
    assert liveness.healthy
    assert liveness.active_locks == 0
    assert liveness.stalled == []
    assert liveness.last_dispatch_at is None


@pytest.mark.asyncio
async def test_expired_unswept_lock_reported(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    # claimed 120 min ago with a 60-min TTL → expired 60 min ago, never swept
    await _add_lock(db_session, ws.id, "project:proj-1", claimed_min_ago=120, ttl_min=60)
    liveness = await assess_engine_health(db_session, workspace_id=ws.id)
    assert not liveness.healthy
    assert liveness.expired_unswept_locks == 1
    assert liveness.stalled[0].reason == "expired_not_swept"
    assert liveness.stalled[0].lock_key == "project:proj-1"


@pytest.mark.asyncio
async def test_lock_held_no_progress(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    # valid (not expired) lock held 50 min, no dispatch audit rows at all
    await _add_lock(db_session, ws.id, "ticket:ELS-42", claimed_min_ago=50, ttl_min=120)
    liveness = await assess_engine_health(
        db_session, workspace_id=ws.id, lock_stall_minutes=45
    )
    assert not liveness.healthy
    assert liveness.active_locks == 1
    assert liveness.stalled[0].reason == "lock_held_no_progress"


@pytest.mark.asyncio
async def test_recent_progress_suppresses_stall(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _add_lock(db_session, ws.id, "ticket:ELS-42", claimed_min_ago=50, ttl_min=120)
    db_session.add(
        AuditLog(
            workspace_id=ws.id,
            actor_user_id=None,
            actor_token_id=None,
            action="agent_run.dispatch",
            target_kind="ticket",
            target_id="ELS-42",
            payload={},
        )
    )
    await db_session.flush()
    liveness = await assess_engine_health(
        db_session, workspace_id=ws.id, lock_stall_minutes=45
    )
    assert liveness.healthy
    assert liveness.last_dispatch_at is not None


@pytest.mark.asyncio
async def test_assessment_mutates_zero_rows(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _add_lock(db_session, ws.id, "project:p", claimed_min_ago=999, ttl_min=10)
    before_locks = await db_session.scalar(select(func.count(AgentDispatchLock.id)))
    before_audit = await db_session.scalar(select(func.count(AuditLog.id)))
    await assess_engine_health(db_session, workspace_id=ws.id)
    await db_session.flush()
    assert await db_session.scalar(select(func.count(AgentDispatchLock.id))) == before_locks
    assert await db_session.scalar(select(func.count(AuditLog.id))) == before_audit


@pytest.mark.asyncio
async def test_route_read_only_and_authorized(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/engine-health",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["healthy"] is True
    assert body["stalled"] == []
    # unauthorized workspace → 403/404, not data
    other = await v1_client.get(
        f"/v1/workspaces/{uuid.uuid4()}/engine-health",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert other.status_code in (403, 404)
