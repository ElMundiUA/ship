"""Unit tests for the in-process cron framework (ELS-36 prep).

Covers the lock-acquire / release behaviour around
:func:`cron_with_lock`. The scheduler-binding side
(``register_cron`` + ``start_scheduler``) is exercised lightly —
the heavy plumbing belongs to APScheduler and we trust its own
test suite there.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from backend.app.db.session import get_sessionmaker
from backend.app.services.cron import (
    CronLockId,
    cron_with_lock,
    register_cron,
    start_scheduler,
    stop_scheduler,
)


@pytest.mark.asyncio
async def test_cron_with_lock_runs_when_uncontested(db_session):
    """A single caller acquires the lock, the body runs, ``calls`` increments."""
    calls: list[str] = []

    @cron_with_lock(lock=CronLockId.KNOWLEDGE_HARVEST, name="kh-test-uncontested")
    async def job() -> None:
        calls.append("ran")

    await job()
    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_cron_with_lock_skips_when_lock_held():
    """If another connection already holds the lock, the wrapped job exits silent."""
    calls: list[str] = []

    @cron_with_lock(lock=CronLockId.KNOWLEDGE_HARVEST, name="kh-test-contested")
    async def job() -> None:
        calls.append("ran")

    sm = get_sessionmaker()
    # Hold the lock on a separate session for the duration of the call.
    async with sm() as holder:
        got = (
            await holder.execute(
                text("SELECT pg_try_advisory_lock(:k)"),
                {"k": int(CronLockId.KNOWLEDGE_HARVEST)},
            )
        ).scalar_one()
        assert got is True

        await job()
        assert calls == [], "job body must not run while another holder has the lock"

        # Release for the next test.
        await holder.execute(
            text("SELECT pg_advisory_unlock(:k)"),
            {"k": int(CronLockId.KNOWLEDGE_HARVEST)},
        )


@pytest.mark.asyncio
async def test_cron_with_lock_releases_on_failure():
    """A raise inside the body releases the lock so the next tick can re-acquire."""
    attempts: list[int] = []

    @cron_with_lock(lock=CronLockId.KNOWLEDGE_HARVEST, name="kh-test-failure")
    async def boom() -> None:
        attempts.append(1)
        raise RuntimeError("simulated job failure")

    with pytest.raises(RuntimeError):
        await boom()
    # Second invocation must be able to acquire.
    with pytest.raises(RuntimeError):
        await boom()
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_register_and_start_scheduler():
    """``register_cron`` + ``start_scheduler`` produce a running scheduler with the job bound."""
    calls: list[int] = []

    @cron_with_lock(lock=CronLockId.KNOWLEDGE_HARVEST, name="kh-test-bind")
    async def job() -> None:
        calls.append(1)

    register_cron(fn=job, cron_expr="* * * * *", job_id="kh-test-bind")
    sched = start_scheduler()
    try:
        assert sched.get_job("kh-test-bind") is not None
    finally:
        await stop_scheduler()
