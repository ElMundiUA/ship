"""Refire-cap counter slice fix (ELS-FSM polish 2026-05-19 / B3).

``_recent_finish_count_for_stage`` filtered the audit pull by
``fsm_stage`` for ALL rows, so a ``validation`` finish with
``outcome=ready_next_step`` (which cascaded into ``code_review``)
was invisible to the code_review cap counter. The cap stayed armed
across the cross-stage success and fired prematurely.

Caught on Ship-on-Ship/ELS-7 2026-05-18: auto_merge bounced 3× with
proper ``stage_next=dev_implementation`` cascade. Between bounces,
validation+code_review both completed ready_next_step on the next
iteration. The cap counter for auto_merge never saw those
successes (they were validation/code_review stage rows), so the
third bounce tripped cap=3.

The contract now:

- ``clarification_resolved`` → reset (operator answered)
- any-stage ``ready_next_step`` → reset (chain made progress)
- same-stage non-success finish → increment
- cross-stage non-success → skip (different cap bucket)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.api.v1.routes.agent_runs import (
    _REFIRE_CAP_WINDOW,
    _recent_finish_count_for_stage,
)
from backend.app.db.models.tenancy import AuditLog


def _finish(
    workspace_id,
    ticket_ref: str,
    *,
    ago: timedelta,
    stage: str,
    outcome: str,
) -> AuditLog:
    return AuditLog(
        workspace_id=workspace_id,
        action="agent_run.finish",
        target_kind="agent_run",
        target_id=ticket_ref,
        created_at=datetime.now(timezone.utc) - ago,
        payload={
            "fsm_stage": stage,
            "outcome": outcome,
            "ticket_ref": ticket_ref,
        },
    )


@pytest.mark.asyncio
async def test_three_same_stage_blocks_count_to_three(
    db_session, seed_workspace
) -> None:
    """Plain happy path — three consecutive ``blocked`` finishes
    on the same stage hit cap=3."""
    _, _, ws = seed_workspace
    for i in range(3):
        db_session.add(
            _finish(
                ws.id, "ELS-300",
                ago=timedelta(hours=1 + i),
                stage="code_review",
                outcome="blocked",
            )
        )
    await db_session.flush()

    count = await _recent_finish_count_for_stage(
        db_session,
        workspace_id=ws.id,
        fsm_stage="code_review",
        ticket_ref="ELS-300",
        window=_REFIRE_CAP_WINDOW,
    )
    assert count == 3


@pytest.mark.asyncio
async def test_cross_stage_success_resets_cap(
    db_session, seed_workspace
) -> None:
    """validation→code_review ``ready_next_step`` (cross-stage) must
    reset the code_review cap counter. Pre-B3, the per-stage filter
    hid the cross-stage success and the cap stayed armed."""
    _, _, ws = seed_workspace
    # Older: 2 code_review blocks
    db_session.add(
        _finish(ws.id, "ELS-301", ago=timedelta(hours=10), stage="code_review", outcome="blocked")
    )
    db_session.add(
        _finish(ws.id, "ELS-301", ago=timedelta(hours=9), stage="code_review", outcome="blocked")
    )
    # Newer: validation ready_next_step → cascaded into code_review.
    # This is the "chain made progress" signal that must reset the
    # code_review counter.
    db_session.add(
        _finish(ws.id, "ELS-301", ago=timedelta(hours=5), stage="validation", outcome="ready_next_step")
    )
    # Newer still: a single code_review block (post-reset)
    db_session.add(
        _finish(ws.id, "ELS-301", ago=timedelta(hours=1), stage="code_review", outcome="blocked")
    )
    await db_session.flush()

    count = await _recent_finish_count_for_stage(
        db_session,
        workspace_id=ws.id,
        fsm_stage="code_review",
        ticket_ref="ELS-301",
        window=_REFIRE_CAP_WINDOW,
    )
    assert count == 1  # only the post-reset block counts


@pytest.mark.asyncio
async def test_cross_stage_blocks_dont_increment_other_caps(
    db_session, seed_workspace
) -> None:
    """A ``validation`` blocked finish must NOT add to the
    ``code_review`` cap bucket. Each stage has its own counter."""
    _, _, ws = seed_workspace
    # 2 validation blocks (would trip validation cap)
    for i in range(2):
        db_session.add(
            _finish(
                ws.id, "ELS-302",
                ago=timedelta(hours=2 + i),
                stage="validation",
                outcome="blocked",
            )
        )
    # 1 code_review block
    db_session.add(
        _finish(
            ws.id, "ELS-302",
            ago=timedelta(hours=1),
            stage="code_review",
            outcome="blocked",
        )
    )
    await db_session.flush()

    cr_count = await _recent_finish_count_for_stage(
        db_session,
        workspace_id=ws.id,
        fsm_stage="code_review",
        ticket_ref="ELS-302",
        window=_REFIRE_CAP_WINDOW,
    )
    val_count = await _recent_finish_count_for_stage(
        db_session,
        workspace_id=ws.id,
        fsm_stage="validation",
        ticket_ref="ELS-302",
        window=_REFIRE_CAP_WINDOW,
    )
    # cross-stage blocks don't leak into the other bucket
    assert cr_count == 1
    assert val_count == 2


@pytest.mark.asyncio
async def test_same_stage_success_resets_too(
    db_session, seed_workspace
) -> None:
    """A same-stage ``ready_next_step`` resets — covered before B3
    too, kept here as a regression guard."""
    _, _, ws = seed_workspace
    db_session.add(
        _finish(ws.id, "ELS-303", ago=timedelta(hours=4), stage="code_review", outcome="blocked")
    )
    db_session.add(
        _finish(ws.id, "ELS-303", ago=timedelta(hours=3), stage="code_review", outcome="ready_next_step")
    )
    db_session.add(
        _finish(ws.id, "ELS-303", ago=timedelta(hours=2), stage="code_review", outcome="blocked")
    )
    await db_session.flush()

    count = await _recent_finish_count_for_stage(
        db_session,
        workspace_id=ws.id,
        fsm_stage="code_review",
        ticket_ref="ELS-303",
        window=_REFIRE_CAP_WINDOW,
    )
    assert count == 1


@pytest.mark.asyncio
async def test_window_cutoff_excludes_old_rows(
    db_session, seed_workspace
) -> None:
    """Rows past ``_REFIRE_CAP_WINDOW`` (24h) drop out — older blocks
    don't keep the cap armed indefinitely."""
    _, _, ws = seed_workspace
    # Block 30h ago — outside window
    db_session.add(
        _finish(ws.id, "ELS-304", ago=timedelta(hours=30), stage="code_review", outcome="blocked")
    )
    # Block 1h ago — inside
    db_session.add(
        _finish(ws.id, "ELS-304", ago=timedelta(hours=1), stage="code_review", outcome="blocked")
    )
    await db_session.flush()

    count = await _recent_finish_count_for_stage(
        db_session,
        workspace_id=ws.id,
        fsm_stage="code_review",
        ticket_ref="ELS-304",
        window=_REFIRE_CAP_WINDOW,
    )
    assert count == 1
