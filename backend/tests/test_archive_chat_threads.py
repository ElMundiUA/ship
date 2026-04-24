"""Wave C — idle chat-thread sweeper.

Exercises the service-layer primitive
(:func:`archive_idle_chat_threads_once`) plus the worker cron
entrypoint (:func:`archive_idle_chat_threads`) against the live
Postgres test fixture so the SQL ``WHERE`` clause is the same one
production runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_surface import ChatThread
from backend.app.services.agent.chat_threads import (
    THRESHOLD_DAYS_DEFAULT,
    archive_idle_chat_threads_once,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_thread(
    db_session,
    workspace_id: uuid.UUID,
    *,
    status: str = "active",
    last_activity_offset: timedelta | None = None,
    archived_at: datetime | None = None,
    title: str = "thread",
) -> ChatThread:
    """Insert a thread with explicit lifecycle fields for the test.

    ``last_activity_offset`` is added to "now" — pass ``-timedelta(days=10)``
    for an idle thread, ``-timedelta(hours=1)`` for a fresh one, or ``None``
    to leave ``last_user_activity_at`` NULL (the zombie case).
    """
    now = datetime.now(timezone.utc)
    last_activity_at = (
        now + last_activity_offset if last_activity_offset is not None else None
    )
    row = ChatThread(
        workspace_id=workspace_id,
        title=title,
        status=status,
        last_user_activity_at=last_activity_at,
        archived_at=archived_at,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _refresh(db_session, row: ChatThread) -> ChatThread:
    refreshed = (
        await db_session.execute(
            select(ChatThread).where(ChatThread.id == row.id)
        )
    ).scalar_one()
    await db_session.refresh(refreshed)
    return refreshed


# ---------------------------------------------------------------------------
# Service-layer cases (cover all the eligibility branches)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_active_thread_is_archived(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    idle = await _make_thread(
        db_session,
        workspace.id,
        status="active",
        last_activity_offset=-timedelta(days=THRESHOLD_DAYS_DEFAULT + 2),
        title="idle",
    )

    result = await archive_idle_chat_threads_once(db_session)

    assert result.archived >= 1
    refreshed = await _refresh(db_session, idle)
    assert refreshed.status == "archived"
    assert refreshed.archived_at is not None


@pytest.mark.asyncio
async def test_recent_active_thread_is_left_alone(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    fresh = await _make_thread(
        db_session,
        workspace.id,
        status="active",
        last_activity_offset=-timedelta(hours=1),
        title="fresh",
    )

    await archive_idle_chat_threads_once(db_session)

    refreshed = await _refresh(db_session, fresh)
    assert refreshed.status == "active"
    assert refreshed.archived_at is None


@pytest.mark.asyncio
async def test_already_archived_thread_is_not_rebumped(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    original_archived_at = datetime.now(timezone.utc) - timedelta(days=30)
    pre_archived = await _make_thread(
        db_session,
        workspace.id,
        status="archived",
        last_activity_offset=-timedelta(days=THRESHOLD_DAYS_DEFAULT + 5),
        archived_at=original_archived_at,
        title="already archived",
    )

    await archive_idle_chat_threads_once(db_session)

    refreshed = await _refresh(db_session, pre_archived)
    assert refreshed.status == "archived"
    # Compare with a tolerance — DB round-trip may rewrite to UTC offset.
    assert refreshed.archived_at is not None
    assert abs(
        (refreshed.archived_at - original_archived_at).total_seconds()
    ) < 1


@pytest.mark.asyncio
async def test_resolved_thread_is_left_alone(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    resolved = await _make_thread(
        db_session,
        workspace.id,
        status="resolved",
        last_activity_offset=-timedelta(days=THRESHOLD_DAYS_DEFAULT + 5),
        title="resolved",
    )

    await archive_idle_chat_threads_once(db_session)

    refreshed = await _refresh(db_session, resolved)
    assert refreshed.status == "resolved"
    assert refreshed.archived_at is None


@pytest.mark.asyncio
async def test_active_thread_with_null_activity_is_left_alone(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    zombie = await _make_thread(
        db_session,
        workspace.id,
        status="active",
        last_activity_offset=None,
        title="zombie",
    )

    await archive_idle_chat_threads_once(db_session)

    refreshed = await _refresh(db_session, zombie)
    assert refreshed.status == "active"
    assert refreshed.archived_at is None


@pytest.mark.asyncio
async def test_sweep_is_idempotent(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    idle = await _make_thread(
        db_session,
        workspace.id,
        status="active",
        last_activity_offset=-timedelta(days=THRESHOLD_DAYS_DEFAULT + 1),
        title="idle",
    )

    first = await archive_idle_chat_threads_once(db_session)
    assert first.archived >= 1

    refreshed_after_first = await _refresh(db_session, idle)
    assert refreshed_after_first.status == "archived"
    pinned_archived_at = refreshed_after_first.archived_at
    assert pinned_archived_at is not None

    second = await archive_idle_chat_threads_once(db_session)
    # Second run may see other test-suite leftovers (the shared DB is
    # transactional per test, so within *this* test we expect zero).
    assert second.archived == 0

    refreshed_after_second = await _refresh(db_session, idle)
    assert refreshed_after_second.archived_at == pinned_archived_at


# ---------------------------------------------------------------------------
# Worker entrypoint smoke test — proves the cron wiring sees rows the same
# way the service does.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_entrypoint_archives_and_logs(
    monkeypatch: pytest.MonkeyPatch, db_session, seed_workspace
) -> None:
    from backend.app.workers import archive_chat_threads as worker

    _, _, workspace = seed_workspace
    idle = await _make_thread(
        db_session,
        workspace.id,
        status="active",
        last_activity_offset=-timedelta(days=THRESHOLD_DAYS_DEFAULT + 3),
        title="idle for worker",
    )

    monkeypatch.setattr(
        worker, "get_sessionmaker", lambda: _MakerWrapper(db_session)
    )

    summary = await worker.archive_idle_chat_threads({"job_try": 1})

    assert summary["archived"] >= 1
    assert summary["scanned"] >= 1
    refreshed = await _refresh(db_session, idle)
    assert refreshed.status == "archived"
    assert refreshed.archived_at is not None


# ---------------------------------------------------------------------------
# Helpers — re-use the same shape as test_worker_secret_probe.py so the
# worker's ``async with sessionmaker() as s`` shape composes with our
# transactional fixture.
# ---------------------------------------------------------------------------


class _MakerWrapper:
    def __init__(self, session) -> None:
        self._session = session

    def __call__(self) -> "_NoCloseSession":
        return _NoCloseSession(self._session)


class _NoCloseSession:
    def __init__(self, inner) -> None:
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *_):
        # Fixture owns lifecycle; .commit() inside the worker becomes a
        # SAVEPOINT-bounded no-op so test isolation holds.
        return None
