"""Scheduled ticket-creating routines (ELS-232/233/234, trigger c).

Pins: durable audit-ledger idempotency per (kind, period_key),
fail-closed per-workspace opt-in, period-key granularity per cadence,
and the template guardrails (self-contained briefs that never bypass
the FSM).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.app.db.models.tenancy import AuditLog
from backend.app.services.scheduled_routines import (
    SPECS,
    TICKET_CREATED_ACTION,
    already_created,
    create_routine_ticket,
    period_key_for,
)


class _RecordingTracker:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create_ticket(self, *, title, body, labels=None, **kwargs):
        self.created.append({"title": title, "body": body, "labels": labels})
        ref = SimpleNamespace(id=f"FAKE-{len(self.created)}")
        return SimpleNamespace(ref=ref, display_id=f"ELS-{900 + len(self.created)}")


@pytest.fixture
def fake_tracker(monkeypatch):
    tracker = _RecordingTracker()

    async def fake_resolve(**kwargs):
        return SimpleNamespace(kind="linear", gateway=tracker)

    monkeypatch.setattr(
        "backend.app.services.tracker_resolver.resolve_for_workspace",
        fake_resolve,
    )
    return tracker


def test_period_key_granularity_matches_cadence() -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
    assert period_key_for(SPECS["daily"], now) == "2026-06-11"
    assert period_key_for(SPECS["retro"], now) == "2026-W24"
    assert period_key_for(SPECS["techdebt"], now) == "2026-W24"
    # cadence sanity: day-period specs run on a daily cron, week-period
    # specs on a weekly cron (single-day-of-week field).
    assert SPECS["daily"].period == "day"
    for kind in ("retro", "techdebt"):
        dow = SPECS[kind].cron_expr.split()[4]
        assert dow != "*", f"{kind} cron should pin a day-of-week"


@pytest.mark.asyncio
async def test_optout_workspace_skipped_fail_closed(
    db_session, seed_workspace, fake_tracker
) -> None:
    _, _, ws = seed_workspace  # no scheduled_routines settings at all
    ref = await create_routine_ticket(
        db_session, workspace_id=ws.id, spec=SPECS["techdebt"]
    )
    assert ref is None
    assert fake_tracker.created == []


@pytest.mark.asyncio
async def test_two_ticks_one_period_create_one_ticket(
    db_session, seed_workspace, fake_tracker
) -> None:
    _, _, ws = seed_workspace
    ws.settings = {"scheduled_routines": {"techdebt": True}}
    await db_session.flush()

    now = datetime(2026, 6, 11, 7, 0, tzinfo=timezone.utc)
    ref1 = await create_routine_ticket(
        db_session, workspace_id=ws.id, spec=SPECS["techdebt"], now=now
    )
    assert ref1 is not None
    ref2 = await create_routine_ticket(
        db_session, workspace_id=ws.id, spec=SPECS["techdebt"], now=now
    )
    assert ref2 is None  # second tick same period → skip
    assert len(fake_tracker.created) == 1
    # ledger row durable in audit_log
    rows = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.workspace_id == ws.id,
            AuditLog.action == TICKET_CREATED_ACTION,
        )
    )
    assert rows == 1
    assert await already_created(
        db_session, workspace_id=ws.id, kind="techdebt",
        period_key=period_key_for(SPECS["techdebt"], now),
    )


@pytest.mark.asyncio
async def test_new_period_creates_fresh_ticket(
    db_session, seed_workspace, fake_tracker
) -> None:
    _, _, ws = seed_workspace
    ws.settings = {"scheduled_routines": {"daily": True}}
    await db_session.flush()
    d1 = datetime(2026, 6, 11, 6, 30, tzinfo=timezone.utc)
    d2 = datetime(2026, 6, 12, 6, 30, tzinfo=timezone.utc)
    assert await create_routine_ticket(
        db_session, workspace_id=ws.id, spec=SPECS["daily"], now=d1
    )
    assert await create_routine_ticket(
        db_session, workspace_id=ws.id, spec=SPECS["daily"], now=d2
    )
    assert len(fake_tracker.created) == 2


@pytest.mark.asyncio
async def test_created_ticket_carries_stage_label_and_brief(
    db_session, seed_workspace, fake_tracker
) -> None:
    _, _, ws = seed_workspace
    ws.settings = {"scheduled_routines": {"retro": True}}
    await db_session.flush()
    await create_routine_ticket(
        db_session, workspace_id=ws.id, spec=SPECS["retro"]
    )
    t = fake_tracker.created[0]
    assert t["labels"] == ["stage:planning"]
    assert "Weekly retro" in t["title"]
    # self-contained brief, with the FSM + no-MCP guardrails baked in
    assert "never Linear via MCP" in t["body"]
    assert "bypass" in t["body"] or "do not" in t["body"].lower()


def test_templates_never_instruct_fsm_bypass() -> None:
    for spec in SPECS.values():
        body = spec.body_template.lower()
        assert "do not" in body or "never" in body
        assert "mcp" in body  # the no-side-channel guardrail
        for forbidden in ("merge it yourself", "skip the review", "force-merge"):
            assert forbidden not in body
