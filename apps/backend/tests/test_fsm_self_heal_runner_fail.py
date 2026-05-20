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
    DEV_NOT_CONVERGING_DEV_CYCLES,
    DEV_NOT_CONVERGING_REVIEW_BLOCKS,
    RUNNER_FAIL_THRESHOLD,
    _file_dev_not_converging_blocker,
    _file_runner_fail_blocker,
    _looks_like_dev_not_converging,
    _looks_like_runner_fail_loop,
)


def _stage_finish_row(
    workspace_id,
    ticket_ref: str,
    *,
    ago: timedelta,
    stage: str,
    outcome: str,
) -> AuditLog:
    """``agent_run.finish`` audit row at a specific stage/outcome.
    Used by dev-not-converging tests; orthogonal to the runner-fail
    detector's ``_finish_row`` (which defaults to ``code_review`` /
    ``ready_next_step``)."""
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


def _picker_null_release(
    workspace_id, ticket_ref: str, *, ago: timedelta, via: str,
) -> AuditLog:
    """``dispatch.project_lock_released`` row emitted by
    ``_release_project_lock_for_ticket`` when the picker rejected
    the ticket. ``via`` shape: ``picker_<reason>``."""
    return AuditLog(
        workspace_id=workspace_id,
        action="dispatch.project_lock_released",
        target_kind="ticket",
        target_id=ticket_ref,
        created_at=datetime.now(timezone.utc) - ago,
        payload={"via": via, "project_id": "test-project-id"},
    )


@pytest.mark.parametrize(
    "via",
    [
        "picker_refire_capped",
        "picker_overlay_frozen",
        "picker_priority_skipped",
    ],
)
@pytest.mark.asyncio
async def test_picker_null_release_excludes_ticket_from_runner_fail(
    db_session, seed_workspace, via,
) -> None:
    """False-positive shapes caught on Ship-on-Ship 2026-05-19 right
    after C2 first deploys:

    - ``picker_refire_capped`` — ELS-117/119/146 (cap exhausted,
      operator already has refire-cap letter with action_items)
    - ``picker_overlay_frozen`` — ELS-155 (Linear project in
      ``planning`` state / Drafts, picker freezes by design)
    - ``picker_priority_skipped`` — same operator-driven gate

    All three look identical to C2's counter (N dispatches, 0
    finishes) but the runner ISN'T crashing — the picker is
    legitimately bailing. The corresponding paths already file
    their own operator-facing artefacts; C2 duplicating is noise.
    Bail when ANY ``picker_*`` lock release exists in the 4h C2
    window."""
    _, _, ws = seed_workspace
    for i in range(3):
        db_session.add(
            _dispatch_row(ws.id, "ELS-555", ago=timedelta(hours=1 + i))
        )
        db_session.add(
            _picker_null_release(
                ws.id, "ELS-555",
                ago=timedelta(hours=1 + i, minutes=-1),  # lock released after dispatch
                via=via,
            )
        )
    await db_session.flush()

    assert (
        await _looks_like_runner_fail_loop(db_session, ws.id, "ELS-555")
        is False
    )


@pytest.mark.asyncio
async def test_picker_null_outside_window_does_not_exclude(
    db_session, seed_workspace,
) -> None:
    """Picker bails that fell out of the 4h window must NOT suppress
    real runner-fail detection — a ticket that hit cap last week
    and now genuinely has a crashing runner should still surface."""
    _, _, ws = seed_workspace
    # 3 fresh dispatches w/o finish in the 4h window — runner-fail shape
    for i in range(3):
        db_session.add(
            _dispatch_row(ws.id, "ELS-556", ago=timedelta(hours=1 + i))
        )
    # Picker bail from 5h ago — outside the window
    db_session.add(
        _picker_null_release(
            ws.id, "ELS-556",
            ago=timedelta(hours=5),
            via="picker_refire_capped",
        )
    )
    await db_session.flush()

    assert (
        await _looks_like_runner_fail_loop(db_session, ws.id, "ELS-556")
        is True
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


# ---------------------------------------------------------------------------
# dev_not_converging detection (2026-05-19 — Ship-on-Ship ELS-117/ELS-7/
# ELS-111 post-mortem). Distinct from runner-fail: the runner IS alive
# and calling /finish, the chain IS cycling reviewer→dev→reviewer, but
# the developer agent isn't pushing a fix that lands the reviewer's
# blocker. Refire-cap catches the symptom but ships a misleading
# "breadcrumb label not added" diagnosis; the dedicated letter points
# the operator at the actual PR with concrete next-step action_items.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_code_review_blocks_plus_two_dev_cycles_is_dev_not_converging(
    db_session, seed_workspace
) -> None:
    """Canonical ELS-117 shape: reviewer rejects 3×, dev cycles 2×
    between, no successful code_review finish — dev keeps iterating
    without actually addressing the reviewer's blocker."""
    _, _, ws = seed_workspace
    # 3 code_review blocks
    for i in range(3):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-117",
                ago=timedelta(hours=1 + i),
                stage="code_review",
                outcome="blocked",
            )
        )
    # 2 dev_implementation successes interleaved
    for i in range(2):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-117",
                ago=timedelta(hours=1 + i, minutes=15),
                stage="dev_implementation",
                outcome="ready_next_step",
            )
        )
    await db_session.flush()

    assert (
        await _looks_like_dev_not_converging(db_session, ws.id, "ELS-117")
        is True
    )


@pytest.mark.asyncio
async def test_auto_merge_block_pattern_also_dev_not_converging(
    db_session, seed_workspace
) -> None:
    """ELS-7 shape — auto_merger keeps bouncing back to dev because
    CI is red. Dev cycles, auto_merge re-blocks. Different stage but
    same architectural pattern."""
    _, _, ws = seed_workspace
    for i in range(DEV_NOT_CONVERGING_REVIEW_BLOCKS):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-7",
                ago=timedelta(hours=1 + i),
                stage="auto_merge",
                outcome="blocked",
            )
        )
    for i in range(DEV_NOT_CONVERGING_DEV_CYCLES):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-7",
                ago=timedelta(hours=1 + i, minutes=15),
                stage="dev_implementation",
                outcome="ready_next_step",
            )
        )
    await db_session.flush()

    assert (
        await _looks_like_dev_not_converging(db_session, ws.id, "ELS-7")
        is True
    )


@pytest.mark.asyncio
async def test_blocks_without_dev_cycles_is_not_dev_not_converging(
    db_session, seed_workspace
) -> None:
    """3 code_review blocks but the developer never re-ran in between
    — that's a different failure mode (runner-fail OR cascade routing
    bug). Not dev-not-converging."""
    _, _, ws = seed_workspace
    for i in range(3):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-800",
                ago=timedelta(hours=1 + i),
                stage="code_review",
                outcome="blocked",
            )
        )
    await db_session.flush()

    assert (
        await _looks_like_dev_not_converging(db_session, ws.id, "ELS-800")
        is False
    )


@pytest.mark.asyncio
async def test_dev_cycles_without_reviewer_blocks_is_not_dev_not_converging(
    db_session, seed_workspace
) -> None:
    """Dev re-ran 5× but reviewer hasn't blocked — chain is just slow,
    not stuck. Detection only fires when BOTH sides of the cycle are
    repeating."""
    _, _, ws = seed_workspace
    for i in range(5):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-801",
                ago=timedelta(hours=0, minutes=15 * (i + 1)),
                stage="dev_implementation",
                outcome="ready_next_step",
            )
        )
    await db_session.flush()

    assert (
        await _looks_like_dev_not_converging(db_session, ws.id, "ELS-801")
        is False
    )


@pytest.mark.asyncio
async def test_below_block_threshold_is_not_dev_not_converging(
    db_session, seed_workspace
) -> None:
    """``DEV_NOT_CONVERGING_REVIEW_BLOCKS=3`` keeps the false-positive
    rate low — 2 reviewer blocks plus heavy dev iteration shouldn't
    pause the ticket."""
    _, _, ws = seed_workspace
    for i in range(DEV_NOT_CONVERGING_REVIEW_BLOCKS - 1):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-802",
                ago=timedelta(hours=1 + i),
                stage="code_review",
                outcome="blocked",
            )
        )
    for i in range(5):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-802",
                ago=timedelta(hours=1 + i, minutes=15),
                stage="dev_implementation",
                outcome="ready_next_step",
            )
        )
    await db_session.flush()

    assert (
        await _looks_like_dev_not_converging(db_session, ws.id, "ELS-802")
        is False
    )


@pytest.mark.asyncio
async def test_picker_null_release_excludes_from_dev_not_converging(
    db_session, seed_workspace
) -> None:
    """A ticket that's in refire-cap purgatory / overlay-frozen /
    priority-skipped already has a refire-cap blocker letter — the
    dev-not-converging detector must NOT double-file. Mirror of the
    runner-fail exclusion semantics."""
    _, _, ws = seed_workspace
    for i in range(DEV_NOT_CONVERGING_REVIEW_BLOCKS):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-803",
                ago=timedelta(hours=1 + i),
                stage="code_review",
                outcome="blocked",
            )
        )
    for i in range(DEV_NOT_CONVERGING_DEV_CYCLES):
        db_session.add(
            _stage_finish_row(
                ws.id, "ELS-803",
                ago=timedelta(hours=1 + i, minutes=15),
                stage="dev_implementation",
                outcome="ready_next_step",
            )
        )
    db_session.add(
        _picker_null_release(
            ws.id, "ELS-803",
            ago=timedelta(minutes=30),
            via="picker_refire_capped",
        )
    )
    await db_session.flush()

    assert (
        await _looks_like_dev_not_converging(db_session, ws.id, "ELS-803")
        is False
    )


@pytest.mark.asyncio
async def test_file_dev_not_converging_blocker_creates_one_row(
    db_session, seed_workspace
) -> None:
    """First call files the letter with the 4-choice action_items
    set, each wired to a real server-side executor: redispatch_dev /
    force_merge / cancel_ticket / snooze_24h. Dedup via
    ``intake_handle=dev-stuck:<ticket>``."""
    _, _, ws = seed_workspace
    await _file_dev_not_converging_blocker(
        db_session, ws.id, "ELS-117", "code_review"
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_reason == "dev_not_converging",
            )
        )
    ).scalar_one()

    assert row.intake_handle == "dev-stuck:ELS-117"
    assert row.type == "blocker"
    assert row.status == "new"
    assert "ELS-117" in (row.title or "")
    assert (row.payload or {}).get("resolution_mode") == "single_choice"
    ai = (row.payload or {}).get("action_items") or []
    assert {a.get("id") for a in ai} == {
        "redispatch_dev_with_hint",
        "force_merge",
        "cancel_ticket",
        "snooze_24h",
    }


@pytest.mark.asyncio
async def test_file_dev_not_converging_blocker_is_idempotent(
    db_session, seed_workspace
) -> None:
    """Subsequent ticks while the operator's letter is still open
    must not spam — dedup keyed on ticket."""
    _, _, ws = seed_workspace
    await _file_dev_not_converging_blocker(
        db_session, ws.id, "ELS-117", "code_review"
    )
    await db_session.flush()
    await _file_dev_not_converging_blocker(
        db_session, ws.id, "ELS-117", "code_review"
    )
    await db_session.flush()

    count = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_handle == "dev-stuck:ELS-117",
            )
        )
    ).scalars().all()
    assert len(count) == 1
