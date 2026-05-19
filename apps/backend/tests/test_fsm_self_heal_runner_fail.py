"""Tests for fsm_self_heal runner-fail loop detection (C2, 2026-05-19).

Covers:

- ``_looks_like_runner_fail_loop`` — returns True when ≥3 dispatches
  in the window with zero matching finishes (the GH Actions runner
  is crashing silently); False when finishes match dispatches, when
  dispatch count is below threshold, or when finishes exist for the
  ticket via ``payload.ticket_ref`` (the canonical legacy path).
- ``_file_runner_fail_blocker`` — files exactly one ``blocker``
  inbox row with ``intake_handle=runner-fail:<ticket>``; idempotent
  on re-call (no duplicate while the original is still ``new``).

These run against the postgres test DB so the SQLAlchemy queries
behave identically to production. Single-shot, no GH or workflow
fixtures needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.fsm_self_heal import (
    RUNNER_FAIL_THRESHOLD,
    _file_runner_fail_blocker,
    _looks_like_runner_fail_loop,
)


def _dispatch_row(workspace_id, ticket_ref: str, *, ago: timedelta) -> AuditLog:
    """``agent_run.dispatch`` audit row, ``ago`` before now."""
    return AuditLog(
        workspace_id=workspace_id,
        action="agent_run.dispatch",
        target_kind="ticket",
        target_id=ticket_ref,
        created_at=datetime.now(timezone.utc) - ago,
        payload={
            "fsm_stage": "code_review",
            "trigger_kind": "fsm_self_heal",
        },
    )


def _finish_row(
    workspace_id,
    ticket_ref: str,
    *,
    ago: timedelta,
    outcome: str = "ready_next_step",
    via_payload: bool = False,
) -> AuditLog:
    """``agent_run.finish`` audit row. ``via_payload=True`` mimics
    legacy rows that stored ``ticket_ref`` inside payload instead of
    ``target_id``; the detector must count both shapes."""
    return AuditLog(
        workspace_id=workspace_id,
        action="agent_run.finish",
        target_kind="agent_run",
        target_id=None if via_payload else ticket_ref,
        created_at=datetime.now(timezone.utc) - ago,
        payload={
            "fsm_stage": "code_review",
            "outcome": outcome,
            **({"ticket_ref": ticket_ref} if via_payload else {}),
        },
    )


@pytest.mark.asyncio
async def test_three_dispatches_zero_finishes_is_a_runner_fail_loop(
    db_session, seed_workspace
) -> None:
    """Canonical happy-path detection. PAC-32 on Visitor 2026-05-19 —
    fsm_self_heal re-fired the same ticket 6 times in 5h, zero of
    them produced an ``agent_run.finish`` row, project_lock kept
    re-acquiring on each tick."""
    _, _, ws = seed_workspace
    for i in range(3):
        db_session.add(
            _dispatch_row(ws.id, "PAC-32", ago=timedelta(hours=1 + i))
        )
    await db_session.flush()

    assert (
        await _looks_like_runner_fail_loop(db_session, ws.id, "PAC-32")
        is True
    )


@pytest.mark.asyncio
async def test_dispatch_count_below_threshold_is_not_a_loop(
    db_session, seed_workspace
) -> None:
    """Two stale dispatches isn't enough. ``RUNNER_FAIL_THRESHOLD=3``
    keeps the false-positive rate low — a single restart shouldn't
    pause a ticket."""
    _, _, ws = seed_workspace
    assert RUNNER_FAIL_THRESHOLD == 3  # contract — bump intentionally
    for i in range(RUNNER_FAIL_THRESHOLD - 1):
        db_session.add(
            _dispatch_row(ws.id, "PAC-50", ago=timedelta(hours=1 + i))
        )
    await db_session.flush()

    assert (
        await _looks_like_runner_fail_loop(db_session, ws.id, "PAC-50")
        is False
    )


@pytest.mark.asyncio
async def test_finishes_match_dispatches_is_not_a_loop(
    db_session, seed_workspace
) -> None:
    """3 dispatches + 3 finishes (any outcome) means the runner did
    its job — the chain may be blocked at a real defect, but that's
    handled by the refire-cap path, not the runner-fail path."""
    _, _, ws = seed_workspace
    for i in range(3):
        db_session.add(
            _dispatch_row(ws.id, "PAC-51", ago=timedelta(hours=1 + i))
        )
        db_session.add(
            _finish_row(
                ws.id, "PAC-51",
                ago=timedelta(hours=1 + i, minutes=-1),  # 1 min after dispatch
                outcome="blocked",
            )
        )
    await db_session.flush()

    assert (
        await _looks_like_runner_fail_loop(db_session, ws.id, "PAC-51")
        is False
    )


@pytest.mark.asyncio
async def test_finishes_via_payload_ticket_ref_also_count(
    db_session, seed_workspace
) -> None:
    """Legacy ``agent_run.finish`` rows stored ticket_ref in
    ``payload.ticket_ref`` rather than ``target_id``. The detector
    must count both shapes or it would mis-flag legacy chains as
    runner-fail loops."""
    _, _, ws = seed_workspace
    for i in range(3):
        db_session.add(
            _dispatch_row(ws.id, "PAC-52", ago=timedelta(hours=1 + i))
        )
        db_session.add(
            _finish_row(
                ws.id, "PAC-52",
                ago=timedelta(hours=1 + i, minutes=-1),
                outcome="ready_next_step",
                via_payload=True,
            )
        )
    await db_session.flush()

    assert (
        await _looks_like_runner_fail_loop(db_session, ws.id, "PAC-52")
        is False
    )


@pytest.mark.asyncio
async def test_file_runner_fail_blocker_creates_one_row(
    db_session, seed_workspace
) -> None:
    """First call files the letter; the row carries the right
    ``intake_handle`` for dedup and ``action_items`` so the operator
    has one-click controls."""
    _, _, ws = seed_workspace
    await _file_runner_fail_blocker(
        db_session, ws.id, "PAC-32", "code_review"
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_reason == "runner_fail_loop",
            )
        )
    ).scalar_one()

    assert row.intake_handle == "runner-fail:PAC-32"
    assert row.type == "blocker"
    assert row.status == "new"
    assert "PAC-32" in (row.title or "")
    assert (row.payload or {}).get("resolution_mode") == "single_choice"
    ai = (row.payload or {}).get("action_items") or []
    assert len(ai) == 3
    kinds = {a.get("id") for a in ai}
    assert kinds == {"investigated_resume", "pause_project", "already_handled"}


@pytest.mark.asyncio
async def test_file_runner_fail_blocker_is_idempotent(
    db_session, seed_workspace
) -> None:
    """Calling twice (next cron tick before the operator resolves)
    must not spam — dedup via ``intake_handle`` keyed on ticket."""
    _, _, ws = seed_workspace
    await _file_runner_fail_blocker(
        db_session, ws.id, "PAC-32", "code_review"
    )
    await db_session.flush()
    await _file_runner_fail_blocker(
        db_session, ws.id, "PAC-32", "code_review"
    )
    await db_session.flush()

    count = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_handle == "runner-fail:PAC-32",
            )
        )
    ).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_file_runner_fail_blocker_separate_tickets_get_separate_letters(
    db_session, seed_workspace
) -> None:
    """Dedup is per-ticket; PAC-32 and PAC-13 each get their own
    letter when both runners crash in the same window."""
    _, _, ws = seed_workspace
    await _file_runner_fail_blocker(
        db_session, ws.id, "PAC-32", "code_review"
    )
    await _file_runner_fail_blocker(
        db_session, ws.id, "PAC-13", "dev_implementation"
    )
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_reason == "runner_fail_loop",
            )
        )
    ).scalars().all()
    assert {r.intake_handle for r in rows} == {
        "runner-fail:PAC-32",
        "runner-fail:PAC-13",
    }
