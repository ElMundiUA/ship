"""Server-side defense for ``outcome=blocked`` without ``stage_next``
on review-style stages (ELS-FSM polish 2026-05-19 / B2 → auto-cascade).

Pre-fix on Ship-on-Ship + Visitor: 77% of ``code_review`` blocks
shipped with ``stage_next=null``; the picker had nowhere to forward
the ticket, re-fired the same stage every tick, and refire-cap
fired after 3 in 24h → 24h ticket-idle window. The reviewer prompt
update (commit a045e38) is the load-bearing fix.

The server's behaviour evolved from "file a letter immediately" (B2)
to **auto-cascade first, escalate only if it doesn't help** — keeps
the inbox quiet (operator only hears about tickets the server
couldn't self-heal):

- First two ``blocked+no_next`` finishes on a review stage in a 4h
  window → silent auto-cascade: rewrite ``stage_next`` to
  ``dev_implementation`` so the cascade matrix re-fires dev, tag the
  finish ``actions=['cascade:blocked_no_next_auto']`` and stamp
  ``auto_cascade_from_no_next=true`` on the audit row. NO inbox row.
- Third ``blocked+no_next`` (≥2 prior autos) → the auto-cascade isn't
  converging; file ONE blocker letter
  (``intake_reason='blocked_cascade_exhausted'``,
  ``actions=['inbox:blocker:cascade_exhausted']``) with 3 choice
  action_items so the operator can investigate.
- ``dev_implementation`` blocked without stage_next is the legacy
  transient-refire path (refire-cap handles budget); the defense must
  NOT touch it — keeps ``test_blocked_finish_does_not_create_inbox_row``
  green.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from backend.app.api.v1.routes import agent_runs as agent_runs_routes
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.tracker_resolver import ResolvedTracker


async def _seed_prior_autos(
    db_session, workspace_id, *, ticket_ref: str = "ELS-200", count: int = 2
) -> None:
    """Insert ``count`` prior auto-cascade finish audit rows so the
    next ``blocked+no_next`` on this ticket trips path (C) — the
    exhausted-cascade escalation."""
    now = datetime.now(timezone.utc)
    for i in range(count):
        db_session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="agent_run.finish",
                target_id=ticket_ref,
                payload={
                    "ticket_ref": ticket_ref,
                    "auto_cascade_from_no_next": True,
                },
                created_at=now - timedelta(minutes=30 + i),
            )
        )
    await db_session.flush()


def _finish_payload(**overrides):
    base = {
        "run_id": f"run-{uuid.uuid4().hex[:8]}",
        "outcome": "blocked",
        "fsm_stage": "code_review",
        "stage_next": None,
        "ticket_ref": "ELS-200",
        "comment": "Reviewer found blockers. [Ship SDLC:role-reviewer]",
        "process": "development",
    }
    base.update(overrides)
    return base


class _FakeGateway:
    def __init__(self) -> None:
        self.transition = AsyncMock()
        self.add_signal_label = AsyncMock()
        self.label_calls: list[tuple] = []

    async def comment(self, _ref, *, body: str) -> None:
        return None


@pytest.fixture
def fake_tracker(monkeypatch):
    gateway = _FakeGateway()

    # Patch add_signal_label so we can record the call site.
    async def _tracked_add(ref, *, key: str) -> None:
        gateway.label_calls.append((ref.id, key))

    gateway.add_signal_label.side_effect = _tracked_add

    resolved = ResolvedTracker(
        kind="linear",
        gateway=gateway,
        scope_hint=None,
        source="legacy",
    )

    async def _resolve(*_a, **_k):
        return resolved

    monkeypatch.setattr(
        agent_runs_routes,
        "resolve_for_workspace",
        _resolve,
    )
    return gateway


@pytest.mark.asyncio
async def test_code_review_blocked_no_next_auto_cascades_first(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """Canonical case — reviewer blocks without saying who fixes it.
    The first time, the server silently auto-cascades to
    ``dev_implementation`` (no inbox spam, no clarification label) so
    the dev agent gets another shot before the operator is bothered."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    assert "cascade:blocked_no_next_auto" in actions
    # Path (B) is silent — no escalation letter, no clarification label.
    assert "inbox:blocker:cascade_exhausted" not in actions
    assert "tracker:label:needs_clarification" not in actions
    assert fake_tracker.label_calls == []

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.type == "blocker",
        )
    )
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_third_blocked_no_next_files_cascade_exhausted_letter(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """After two prior auto-cascades in the window, the third
    ``blocked+no_next`` escalates: the server stamps
    ``needs_clarification`` and files ONE blocker letter so the
    operator can investigate the non-converging ticket."""
    _, raw, ws = seed_workspace
    await _seed_prior_autos(db_session, ws.id, ticket_ref="ELS-200", count=2)

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    assert "inbox:blocker:cascade_exhausted" in actions
    assert "tracker:label:needs_clarification" in actions
    assert "cascade:blocked_no_next_auto" not in actions

    row = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_reason == "blocked_cascade_exhausted",
            )
        )
    ).scalar_one()
    assert row.type == "blocker"
    assert row.status == "new"
    ai = (row.payload or {}).get("action_items") or []
    assert len(ai) == 3
    assert {a.get("id") for a in ai} == {
        "applied_manually", "override_force_merge", "mark_handled"
    }

    assert fake_tracker.label_calls == [("ELS-200", "needs_clarification")]


@pytest.mark.asyncio
async def test_validation_blocked_no_next_also_auto_cascades(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """Validation is also a review-style gate — same defense applies:
    first block auto-cascades, no inbox row."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(fsm_stage="validation"),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    assert "cascade:blocked_no_next_auto" in actions
    assert "inbox:blocker:cascade_exhausted" not in actions

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.type == "blocker",
        )
    )
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_dev_implementation_blocked_no_next_does_not_file_letter(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """``dev_implementation`` blocked+no_next is the legacy transient
    refire path. The new defense must NOT inbox-spam every dev retry —
    keeps existing test_blocked_finish_does_not_create_inbox_row
    semantics intact."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(
            fsm_stage="dev_implementation",
            comment=(
                "Push refused. PR: https://github.com/o/r/pull/1 "
                "[Ship SDLC:role-developer]"
            ),
        ),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    assert "inbox:blocker:blocked_no_next" not in actions
    assert "tracker:label:needs_clarification" not in actions

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.intake_reason == "blocked_without_cascade",
        )
    )
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_blocked_with_stage_next_does_not_file_letter(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """Reviewer that correctly cascades to dev_implementation — the
    cascade matrix handles re-dispatch and no inbox row should
    appear. The defense is specifically for the ``no_next`` case."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(stage_next="dev_implementation"),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    assert "inbox:blocker:blocked_no_next" not in actions

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.intake_reason == "blocked_without_cascade",
        )
    )
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_label_add_failure_is_swallowed_and_letter_still_lands(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """Tracker hiccup on ``add_signal_label`` must not turn into a
    422 on the agent's finish call — the inbox row is the load-bearing
    signal, the label is best-effort. Label is only attempted on the
    exhausted-cascade path, so seed two prior autos first."""
    async def _boom(_ref, *, key: str):
        raise RuntimeError("simulated tracker outage")
    fake_tracker.add_signal_label.side_effect = _boom

    _, raw, ws = seed_workspace
    await _seed_prior_autos(db_session, ws.id, ticket_ref="ELS-200", count=2)
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    # label_add failed → no tracker:label:needs_clarification action,
    # but inbox letter still landed
    assert "tracker:label:needs_clarification" not in actions
    assert "inbox:blocker:cascade_exhausted" in actions

    row_count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.intake_reason == "blocked_cascade_exhausted",
        )
    )
    assert int(row_count or 0) == 1
