"""Server-side defense for ``outcome=blocked`` without ``stage_next``
on review-style stages (ELS-FSM polish 2026-05-19 / B2).

Review stages with ``blocked+no_next`` auto-cascade to
``dev_implementation`` twice in 4h (silent retry). Only after two
prior auto-cascades does the server file an inbox escalation letter.
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


async def _seed_prior_auto_cascades(
    db_session, *, workspace_id, ticket_ref: str, count: int = 2
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    for i in range(count):
        db_session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_token_id=None,
                action="agent_run.finish",
                target_kind="agent_run",
                target_id=f"prior-auto-{i}",
                payload={
                    "ticket_ref": ticket_ref,
                    "auto_cascade_from_no_next": "true",
                },
                created_at=cutoff + timedelta(minutes=i),
            )
        )
    await db_session.flush()


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
    """First blocked+no_next on a review stage silently cascades to dev."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    assert "cascade:blocked_no_next_auto" in actions
    assert "inbox:blocker:cascade_exhausted" not in actions
    assert fake_tracker.label_calls == []

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.intake_reason == "blocked_cascade_exhausted",
        )
    )
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_code_review_blocked_no_next_escalates_after_two_autos(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """After two auto-cascades in 4h, the third blocked+no_next escalates."""
    _, raw, ws = seed_workspace
    await _seed_prior_auto_cascades(
        db_session, workspace_id=ws.id, ticket_ref="ELS-200", count=2
    )

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
        "applied_manually",
        "override_force_merge",
        "mark_handled",
    }

    assert fake_tracker.label_calls == [("ELS-200", "needs_clarification")]


@pytest.mark.asyncio
async def test_validation_blocked_no_next_also_auto_cascades(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """Validation is also a review-style gate — same auto-cascade path."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(fsm_stage="validation"),
    )
    assert res.status_code == 200, res.text
    assert "cascade:blocked_no_next_auto" in (res.json().get("actions") or [])


@pytest.mark.asyncio
async def test_dev_implementation_blocked_no_next_does_not_file_letter(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """``dev_implementation`` blocked+no_next stays inbox-quiet."""
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
    assert "cascade:blocked_no_next_auto" not in actions
    assert "inbox:blocker:cascade_exhausted" not in actions
    assert "tracker:label:needs_clarification" not in actions

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
        )
    )
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_blocked_with_stage_next_does_not_file_letter(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """Reviewer that sets stage_next — cascade matrix handles it."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(stage_next="dev_implementation"),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    assert "cascade:blocked_no_next_auto" not in actions
    assert "inbox:blocker:cascade_exhausted" not in actions

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
        )
    )
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_label_add_failure_is_swallowed_and_letter_still_lands(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    """Tracker hiccup on label add must not 422 — inbox row still lands."""
    async def _boom(_ref, *, key: str):
        raise RuntimeError("simulated tracker outage")

    fake_tracker.add_signal_label.side_effect = _boom

    _, raw, ws = seed_workspace
    await _seed_prior_auto_cascades(
        db_session, workspace_id=ws.id, ticket_ref="ELS-200", count=2
    )

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions") or []
    assert "tracker:label:needs_clarification" not in actions
    assert "inbox:blocker:cascade_exhausted" in actions

    row_count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.intake_reason == "blocked_cascade_exhausted",
        )
    )
    assert int(row_count or 0) == 1
