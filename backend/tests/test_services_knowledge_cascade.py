"""Unit tests for the post-sync cascade fast-path.

Exercises the lock-aware coordination — these tests deliberately
don't go through the real ``route_pending_notes`` /
``synthesise_workspace`` bodies. Both already have their own
coverage; here we want to lock down:

- a happy-path cascade calls both stages once, in order;
- if the global ``KNOWLEDGE_ROUTE`` advisory lock is held by some
  other connection, the route stage is skipped silently and the
  synth stage still runs;
- a stage that raises is logged + recorded in the report, but
  does not propagate (callers fire-and-forget).

Sessions are stubbed via a fake sessionmaker so the tests are pure
unit tests — no Postgres needed and no asyncpg event-loop teardown
flake.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from backend.app.services import knowledge_cascade
from backend.app.services.cron import CronLockId


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeSession:
    """Records SQL it sees and answers ``pg_try_advisory_lock`` per recipe.

    ``lock_grants`` maps ``CronLockId`` int values to the boolean the
    fake should return when ``pg_try_advisory_lock`` is called for
    that id. Anything else (commits, rollbacks, unlocks) is a no-op.
    """

    def __init__(self, lock_grants: dict[int, bool]):
        self.lock_grants = lock_grants
        self.executed: list[str] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append(sql)
        if "pg_try_advisory_lock" in sql:
            return _FakeResult(self.lock_grants.get(int(params["k"]), True))
        return _FakeResult(None)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _install_fake_sessionmaker(
    monkeypatch, *, lock_grants: dict[int, bool] | None = None
) -> list[_FakeSession]:
    """Patch ``get_sessionmaker`` so cascade stages use ``_FakeSession``.

    Returns the list the fake sessionmaker appends each yielded
    session to, so tests can inspect commit/rollback flags after the
    fact.
    """
    sessions: list[_FakeSession] = []
    grants = lock_grants or {}

    @asynccontextmanager
    async def _ctx():
        s = _FakeSession(grants)
        sessions.append(s)
        try:
            yield s
        finally:
            pass

    def _sessionmaker():
        return _ctx()

    monkeypatch.setattr(
        knowledge_cascade, "get_sessionmaker", lambda: _sessionmaker
    )
    monkeypatch.setattr(
        knowledge_cascade, "pick_default_client", lambda settings=None: None
    )
    return sessions


@pytest.mark.asyncio
async def test_cascade_runs_route_and_synth_when_locks_free(monkeypatch):
    """Happy path: both stages execute, neither short-circuits on lock."""
    ws_id = uuid.uuid4()
    called: list[str] = []

    async def fake_route(session, *, workspace_id, llm_client):
        called.append(f"route:{workspace_id}")

    async def fake_synth(session, *, workspace_id, llm_client):
        called.append(f"synth:{workspace_id}")

    monkeypatch.setattr(knowledge_cascade, "route_pending_notes", fake_route)
    monkeypatch.setattr(knowledge_cascade, "synthesise_workspace", fake_synth)
    sessions = _install_fake_sessionmaker(monkeypatch)

    report = await knowledge_cascade.cascade_workspace_pipeline(ws_id)

    assert called == [f"route:{ws_id}", f"synth:{ws_id}"]
    assert report.route_ran is True
    assert report.synth_ran is True
    assert report.errors == []
    assert all(s.committed for s in sessions)


@pytest.mark.asyncio
async def test_cascade_skips_route_when_route_lock_held(monkeypatch):
    """If the global route lock is held (regular cron mid-sweep), the
    cascade route stage is a no-op while the synth stage still runs.
    """
    ws_id = uuid.uuid4()
    called: list[str] = []

    async def fake_route(session, *, workspace_id, llm_client):
        called.append("route")

    async def fake_synth(session, *, workspace_id, llm_client):
        called.append("synth")

    monkeypatch.setattr(knowledge_cascade, "route_pending_notes", fake_route)
    monkeypatch.setattr(knowledge_cascade, "synthesise_workspace", fake_synth)
    _install_fake_sessionmaker(
        monkeypatch,
        lock_grants={int(CronLockId.KNOWLEDGE_ROUTE): False},
    )

    report = await knowledge_cascade.cascade_workspace_pipeline(ws_id)

    assert called == ["synth"]
    assert report.route_ran is False
    assert report.route_skipped_lock_held is True
    assert report.synth_ran is True


@pytest.mark.asyncio
async def test_cascade_records_stage_failure_without_raising(monkeypatch):
    """A stage that raises must be logged + reported, but not propagate
    so background-task callers don't get spurious task crashes."""
    ws_id = uuid.uuid4()

    async def boom_route(session, *, workspace_id, llm_client):
        raise RuntimeError("simulated route failure")

    async def fake_synth(session, *, workspace_id, llm_client):
        return None

    monkeypatch.setattr(knowledge_cascade, "route_pending_notes", boom_route)
    monkeypatch.setattr(knowledge_cascade, "synthesise_workspace", fake_synth)
    sessions = _install_fake_sessionmaker(monkeypatch)

    report = await knowledge_cascade.cascade_workspace_pipeline(ws_id)

    assert report.route_ran is False
    assert report.synth_ran is True
    assert any("route" in e for e in report.errors)
    # Route session rolled back; synth session committed.
    assert sessions[0].rolled_back is True
    assert sessions[0].committed is False
    assert sessions[1].committed is True
