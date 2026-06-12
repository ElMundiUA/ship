"""Stall notifier (ELS-231): notify()-only egress, cooldown dedup,
and the never-touches-control-plane invariant.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.app.db.models.agent_dispatch import AgentDispatchLock
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.stall_notifier import (
    STALL_NOTIFIED_ACTION,
    notify_stalls_for_workspace,
)


def _now():
    return datetime.now(timezone.utc)


async def _expired_lock(db_session, ws_id, key="project:p1"):
    claimed = _now() - timedelta(minutes=120)
    db_session.add(
        AgentDispatchLock(
            workspace_id=ws_id,
            key=key,
            claimed_at=claimed,
            expires_at=claimed + timedelta(minutes=60),
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_stall_emits_blocker_letter_once(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _expired_lock(db_session, ws.id)

    emitted = await notify_stalls_for_workspace(db_session, workspace_id=ws.id)
    assert emitted == 1
    items = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.workspace_id == ws.id)
        )
    ).scalars().all()
    assert len(items) == 1
    assert items[0].type == "blocker"
    assert items[0].payload["kind"] == "engine_stall"
    assert items[0].payload["reason"] == "expired_not_swept"
    # dedup marker landed
    marker = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.workspace_id == ws.id,
            AuditLog.action == STALL_NOTIFIED_ACTION,
        )
    )
    assert marker == 1

    # Re-run inside the cooldown → no new letter, no new marker.
    emitted2 = await notify_stalls_for_workspace(db_session, workspace_id=ws.id)
    assert emitted2 == 0
    items2 = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.workspace_id == ws.id)
        )
    ).scalars().all()
    assert len(items2) == 1


@pytest.mark.asyncio
async def test_cooldown_expiry_renotifies(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _expired_lock(db_session, ws.id)
    assert await notify_stalls_for_workspace(db_session, workspace_id=ws.id) == 1
    # Pretend the cooldown elapsed by assessing "now" 7h in the future.
    future = _now() + timedelta(hours=7)
    emitted = await notify_stalls_for_workspace(
        db_session, workspace_id=ws.id, cooldown_minutes=360, now=future
    )
    assert emitted == 1


@pytest.mark.asyncio
async def test_notifier_never_mutates_locks(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _expired_lock(db_session, ws.id)
    def _count():
        return db_session.scalar(
            select(func.count(AgentDispatchLock.id)).where(
                AgentDispatchLock.workspace_id == ws.id
            )
        )
    before = await _count()
    await notify_stalls_for_workspace(db_session, workspace_id=ws.id)
    await db_session.flush()
    after = await _count()
    assert after == before
    lock = (
        await db_session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == ws.id
            )
        )
    ).scalars().first()
    assert lock.key == "project:p1"  # untouched


def test_source_never_calls_control_or_transition_primitives() -> None:
    """AST guard: the notifier reports — it must not import/call
    transition, acquire_lock, release_lock or sweep helpers."""
    src = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "stall_notifier.py"
    ).read_text()
    tree = ast.parse(src)
    forbidden = {
        "transition", "acquire_lock", "release_lock",
        "sweep_expired_locks", "maybe_dispatch",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.id if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute)
                else None
            )
            assert name not in forbidden, f"stall_notifier calls {name}()"
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden, (
                    f"stall_notifier imports {alias.name}"
                )
