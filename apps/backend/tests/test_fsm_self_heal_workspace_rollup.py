"""C2 workspace-rollup — collapse per-ticket runner-fail spam into one
workspace-level letter when the whole workspace is down (2026-05-21).

A single root cause (GitHub Actions billing block, org Actions disabled,
revoked secret) kills every run at preflight, so the per-ticket detector
filed a separate ``runner_fail_loop`` letter per stuck ticket — caught on
askslayer/Visitor where one billing block spammed PAC-33/34/35/36.

``_looks_like_workspace_runner_fail`` fires when ≥THRESHOLD distinct
ticket dispatches landed in the window with **zero** finishes (scheduled
routines excluded); ``_file_workspace_runner_fail_blocker`` files one
deduped letter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.fsm_self_heal import (
    WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD,
    _file_workspace_runner_fail_blocker,
    _looks_like_workspace_runner_fail,
)


def _dispatch(workspace_id, target: str, *, mins: int = 30) -> AuditLog:
    return AuditLog(
        workspace_id=workspace_id,
        action="agent_run.dispatch",
        target_kind="ticket",
        target_id=target,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=mins),
        payload={"ticket_ref": target},
    )


def _finish(workspace_id, target: str, *, mins: int = 20) -> AuditLog:
    return AuditLog(
        workspace_id=workspace_id,
        action="agent_run.finish",
        target_kind="agent_run",
        target_id=target,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=mins),
        payload={"ticket_ref": target, "outcome": "ready_next_step"},
    )


@pytest.mark.asyncio
async def test_detects_workspace_wide_runner_fail(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}"))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is True


@pytest.mark.asyncio
async def test_any_finish_means_workspace_is_alive(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}"))
    # one ticket DID finish → not a preflight-level kill
    db_session.add(_finish(ws.id, "PAC-30"))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_scheduled_routines_dont_count(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    # self-heal / digest ticks dispatch but aren't tickets — a quiet
    # workspace must not trip the rollup.
    for tgt in ("self-heal", "daily-digest", "weekly-audit"):
        db_session.add(_dispatch(ws.id, tgt))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_below_threshold_does_not_fire(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD - 1):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}"))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_old_dispatches_outside_window_ignored(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}", mins=60 * 9))  # 9h ago
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_files_one_letter_idempotent(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _file_workspace_runner_fail_blocker(db_session, ws.id, 4)
    await db_session.flush()
    await _file_workspace_runner_fail_blocker(db_session, ws.id, 4)
    await db_session.flush()

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.intake_reason == "runner_fail_workspace",
        )
    )
    assert int(count or 0) == 1

    row = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_handle == "runner-fail-workspace",
            )
        )
    ).scalar_one()
    assert row.type == "blocker"
    ai = (row.payload or {}).get("action_items") or []
    assert {a.get("id") for a in ai} == {
        "fixed_resume", "pause_workspace", "already_handled"
    }
