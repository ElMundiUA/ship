"""Tests for inbox disposition side-effects (RFC-0010 P2-09).

Covers the side-effect dispatcher
(:mod:`backend.app.services.inbox.side_effects`) plus end-to-end
HTTP wiring through ``POST /v1/workspaces/{ws}/inbox/{id}/disposition``
so a regression in either layer trips a test.

The :class:`RunEscalation` table does not yet carry resolution
columns (see the side_effects module docstring + the RFC-0010
schema-gap note); the close path is exercised as a marker-only
operation here. When the follow-up migration lands the assertions
on ``esc.resolved_at`` etc. should be flipped on.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_surface import Clarification, Improvement
from backend.app.db.models.inbox import (
    InboxItem,
    InboxItemEvent,
    RunEscalation,
)
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.inbox import side_effects as side_effects_mod
from backend.app.services.inbox.side_effects import (
    SideEffectReport,
    apply_side_effects,
)


# ---------------------------------------------------------------------------
# Helpers (lightweight; full HTTP scaffolding lives in test_v1_inbox.py)
# ---------------------------------------------------------------------------


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


async def _mint_user(db_session, *, email: str | None = None):
    from backend.app.db.models.tenancy import User

    user = User(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Side-Effect Tester",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_run(db_session, workspace):
    """Insert a Pipeline + :class:`PipelineRun` for escalation linkage.

    ``RunEscalation.run_id`` carries a real FK; faking the id would
    let the insert silently SET NULL and break the matching tests.
    """
    from backend.app.db.models.pipelines import Pipeline, PipelineRun

    pipeline = Pipeline(
        workspace_id=workspace.id,
        repo_id=None,
        lane_id=f"side_effects_{uuid.uuid4().hex[:8]}",
        name="side-effects test pipeline",
        workflow_id="pr-and-ci-gate",
    )
    db_session.add(pipeline)
    await db_session.flush()
    run = PipelineRun(
        pipeline_id=pipeline.id,
        workspace_id=workspace.id,
        trigger="manual",
        status="succeeded",
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _make_item(
    db_session,
    workspace,
    *,
    type: str = "approval",
    status: str = "new",
    owner_user_id: uuid.UUID | None = None,
    play_key: str | None = None,
    run_id: uuid.UUID | None = None,
    source_table: str | None = None,
    source_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> InboxItem:
    item = InboxItem(
        workspace_id=workspace.id,
        type=type,
        status=status,
        owner_user_id=owner_user_id,
        play_key=play_key,
        run_id=run_id,
        source_table=source_table,
        source_id=source_id,
        title=f"item-{uuid.uuid4().hex[:6]}",
        summary="side-effect test item",
        payload=payload or {},
        intake_handle="test_handle",
        intake_reason="test:fixture",
    )
    db_session.add(item)
    await db_session.flush()
    return item


async def _make_escalation(
    db_session,
    *,
    run_id: uuid.UUID,
    inbox_item_id: uuid.UUID,
    reason: str = "requires_approval",
) -> RunEscalation:
    esc = RunEscalation(
        run_id=run_id,
        inbox_item_id=inbox_item_id,
        escalation_reason=reason,
    )
    db_session.add(esc)
    await db_session.flush()
    return esc


async def _make_clarification(
    db_session, workspace, *, question: str = "is it OK?"
) -> Clarification:
    row = Clarification(
        workspace_id=workspace.id,
        question=question,
        context={},
        source="manual",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _make_improvement(
    db_session, workspace, *, title: str = "Add a test"
) -> Improvement:
    row = Improvement(
        workspace_id=workspace.id,
        kind="test",
        title=title,
        body="Body",
        context={},
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _events_for(
    db_session, item_id: uuid.UUID
) -> list[InboxItemEvent]:
    rows = (
        await db_session.execute(
            select(InboxItemEvent)
            .where(InboxItemEvent.item_id == item_id)
            .order_by(InboxItemEvent.created_at.asc(), InboxItemEvent.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def _audits_with_action(
    db_session, action: str
) -> list[AuditLog]:
    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == action)
        )
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# 1. resolve → no side effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_action_records_no_side_effects(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    item = await _make_item(
        db_session, ws, type="clarification", owner_user_id=user.id
    )
    report = await apply_side_effects(
        db_session,
        item=item,
        action="resolve",
        payload={},
        actor_user_id=user.id,
    )
    assert isinstance(report, SideEffectReport)
    assert report.legacy_writebacks == []
    assert report.escalations_closed == []
    assert report.retry_requests_recorded == []
    assert report.failures == []


# ---------------------------------------------------------------------------
# 2. approve closes matching RunEscalation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_closes_matching_run_escalation(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    run = await _make_run(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="approval",
        owner_user_id=user.id,
        play_key="security/review",
        run_id=run.id,
    )
    esc = await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item.id
    )

    report = await apply_side_effects(
        db_session,
        item=item,
        action="approve",
        payload={},
        actor_user_id=user.id,
    )
    assert esc.id in report.escalations_closed
    assert report.failures == []

    # Audit + event rows recorded so ops can reconcile (and so the
    # close survives the schema-pending gap on RunEscalation).
    audits = await _audits_with_action(
        db_session, "inbox.side_effect.escalation_close"
    )
    assert any(a.target_id == str(item.id) for a in audits)
    actions = [e.action for e in await _events_for(db_session, item.id)]
    assert "escalation_closed" in actions


# ---------------------------------------------------------------------------
# 3. approve closes multiple escalations for same (run, play_key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_closes_multiple_escalations_for_same_run(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    run = await _make_run(db_session, ws)
    item_a = await _make_item(
        db_session,
        ws,
        type="approval",
        owner_user_id=user.id,
        play_key="multi/gate",
        run_id=run.id,
    )
    item_b = await _make_item(
        db_session,
        ws,
        type="approval",
        owner_user_id=user.id,
        play_key="multi/gate",
        run_id=run.id,
    )
    esc_a = await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item_a.id
    )
    esc_b = await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item_b.id
    )

    report = await apply_side_effects(
        db_session,
        item=item_a,
        action="approve",
        payload={},
        actor_user_id=user.id,
    )
    assert {esc_a.id, esc_b.id}.issubset(set(report.escalations_closed))
    assert report.failures == []


# ---------------------------------------------------------------------------
# 4. approve does not touch escalations marked already-resolved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_does_not_touch_already_resolved_escalation(
    db_session, seed_workspace
):
    """Skip rows pre-marked as closed.

    ``RunEscalation`` does not yet carry a ``resolved_at`` column;
    until the migration lands the helper treats every match as
    closeable. We assert the matching set still includes both rows
    (the marker-only close is idempotent) so the test documents the
    intended future behaviour without breaking on the current schema.
    Once ``resolved_at`` exists the assertion should flip to require
    the resolved row to be EXCLUDED.
    """
    user, _, ws = seed_workspace
    run = await _make_run(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="approval",
        owner_user_id=user.id,
        play_key="solo/gate",
        run_id=run.id,
    )
    item_b = await _make_item(
        db_session,
        ws,
        type="approval",
        owner_user_id=user.id,
        play_key="solo/gate",
        run_id=run.id,
    )
    esc_unresolved = await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item.id
    )
    esc_resolved = await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item_b.id
    )
    if hasattr(esc_resolved, "resolved_at"):
        esc_resolved.resolved_at = datetime.now(timezone.utc)
        esc_resolved.resolution = "approved"
        esc_resolved.resolved_by_user_id = user.id
        await db_session.flush()

    report = await apply_side_effects(
        db_session,
        item=item,
        action="approve",
        payload={},
        actor_user_id=user.id,
    )

    assert esc_unresolved.id in report.escalations_closed
    if hasattr(esc_resolved, "resolved_at"):
        assert esc_resolved.id not in report.escalations_closed
    else:
        # Schema-pending: matching identifies both rows; the close
        # is marker-only so neither is mutated. Keep the test as a
        # regression marker for the future migration.
        assert esc_resolved.id in report.escalations_closed


# ---------------------------------------------------------------------------
# 5. reject → resolution='rejected'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_closes_escalation_with_rejected_resolution(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    run = await _make_run(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="approval",
        owner_user_id=user.id,
        play_key="risk/gate",
        run_id=run.id,
    )
    await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item.id
    )

    report = await apply_side_effects(
        db_session,
        item=item,
        action="reject",
        payload={},
        actor_user_id=user.id,
    )

    assert len(report.escalations_closed) == 1
    events = await _events_for(db_session, item.id)
    closed_event = next(e for e in events if e.action == "escalation_closed")
    assert closed_event.payload["resolution"] == "rejected"


# ---------------------------------------------------------------------------
# 6. dismiss on approval type closes escalation as 'dismissed'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_on_approval_type_closes_escalation_with_dismissed_resolution(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    run = await _make_run(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="approval",
        owner_user_id=user.id,
        play_key="ignore/gate",
        run_id=run.id,
    )
    await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item.id
    )

    report = await apply_side_effects(
        db_session,
        item=item,
        action="dismiss",
        payload={},
        actor_user_id=user.id,
    )
    assert len(report.escalations_closed) == 1
    events = await _events_for(db_session, item.id)
    closed_event = next(e for e in events if e.action == "escalation_closed")
    assert closed_event.payload["resolution"] == "dismissed"


# ---------------------------------------------------------------------------
# 7. dismiss on non-approval does NOT touch escalations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_on_non_approval_type_does_not_touch_escalations(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    run = await _make_run(db_session, ws)
    # Even if an escalation exists for the item's run, a non-approval
    # dismiss must not touch it (planning §7 — only the approval
    # gate's dismiss closes the linkage).
    item = await _make_item(
        db_session,
        ws,
        type="failure",
        owner_user_id=user.id,
        play_key="random/play",
        run_id=run.id,
    )
    await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item.id
    )

    report = await apply_side_effects(
        db_session,
        item=item,
        action="dismiss",
        payload={},
        actor_user_id=user.id,
    )
    assert report.escalations_closed == []
    assert report.failures == []


# ---------------------------------------------------------------------------
# 8. answer writes back to legacy clarification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answer_writes_back_to_legacy_clarification(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    clar = await _make_clarification(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="clarification",
        owner_user_id=user.id,
        source_table="clarifications",
        source_id=clar.id,
    )

    report = await apply_side_effects(
        db_session,
        item=item,
        action="answer",
        payload={"answer": "yes ship it"},
        actor_user_id=user.id,
    )
    assert clar.id in report.legacy_writebacks
    assert report.failures == []

    refreshed = (
        await db_session.execute(
            select(Clarification).where(Clarification.id == clar.id)
        )
    ).scalar_one()
    assert refreshed.answer == "yes ship it"
    assert refreshed.status == "answered"
    assert refreshed.answered_by_user_id == user.id
    assert refreshed.answered_at is not None


# ---------------------------------------------------------------------------
# 9. answer skips writeback when no legacy source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answer_skips_writeback_when_no_legacy_source(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    item = await _make_item(
        db_session,
        ws,
        type="clarification",
        owner_user_id=user.id,
        source_table=None,
        source_id=None,
    )
    report = await apply_side_effects(
        db_session,
        item=item,
        action="answer",
        payload={"answer": "no source row"},
        actor_user_id=user.id,
    )
    assert report.legacy_writebacks == []
    assert report.failures == []
    # No legacy_writeback event written either.
    actions = [e.action for e in await _events_for(db_session, item.id)]
    assert "legacy_writeback" not in actions


# ---------------------------------------------------------------------------
# 10. accept writes back to legacy improvement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_writes_back_to_legacy_improvement(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    impr = await _make_improvement(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="improvement",
        owner_user_id=user.id,
        source_table="improvements",
        source_id=impr.id,
    )

    report = await apply_side_effects(
        db_session,
        item=item,
        action="accept",
        payload={},
        actor_user_id=user.id,
    )
    assert impr.id in report.legacy_writebacks
    assert report.failures == []

    refreshed = (
        await db_session.execute(
            select(Improvement).where(Improvement.id == impr.id)
        )
    ).scalar_one()
    assert refreshed.decision == "accepted"
    assert refreshed.decided_by_user_id == user.id
    assert refreshed.decided_at is not None


# ---------------------------------------------------------------------------
# 11. retry records signal in payload + emits event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_records_retry_request_in_payload_and_event(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    run = await _make_run(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="failure",
        owner_user_id=user.id,
        play_key="ci/build",
        run_id=run.id,
        payload={"existing": "key"},
    )

    report = await apply_side_effects(
        db_session,
        item=item,
        action="retry",
        payload={},
        actor_user_id=user.id,
    )
    assert item.id in report.retry_requests_recorded
    assert report.failures == []

    refreshed = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.id == item.id)
        )
    ).scalar_one()
    signal = refreshed.payload.get("retry_request")
    assert signal is not None
    assert signal["run_id"] == str(run.id)
    assert signal["play_key"] == "ci/build"
    assert signal["requested_by_user_id"] == str(user.id)
    assert "requested_at" in signal
    # The pre-existing payload key survives the merge.
    assert refreshed.payload["existing"] == "key"

    actions = [e.action for e in await _events_for(db_session, item.id)]
    assert "retry_requested" in actions


# ---------------------------------------------------------------------------
# 12. acknowledge → no side effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledge_records_no_side_effects(
    db_session, seed_workspace
):
    user, _, ws = seed_workspace
    item = await _make_item(
        db_session, ws, type="exception", owner_user_id=user.id
    )
    report = await apply_side_effects(
        db_session,
        item=item,
        action="acknowledge",
        payload={},
        actor_user_id=user.id,
    )
    assert report.legacy_writebacks == []
    assert report.escalations_closed == []
    assert report.retry_requests_recorded == []
    assert report.failures == []


# ---------------------------------------------------------------------------
# 13. side-effect failure does NOT break the disposition (HTTP path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_side_effect_failure_does_not_break_disposition(
    v1_client, seed_workspace, db_session, monkeypatch
):
    """A raise inside a side-effect helper is swallowed.

    The disposition itself succeeds (200 + status='resolved'), the
    failure surfaces in the side-effect report (logged + audit
    trail), and an ``inbox.disposition.<action>`` audit row is
    written normally.
    """
    user, raw, ws = seed_workspace
    run = await _make_run(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="approval",
        owner_user_id=user.id,
        play_key="boom/gate",
        run_id=run.id,
    )
    await _make_escalation(
        db_session, run_id=run.id, inbox_item_id=item.id
    )

    async def _explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        side_effects_mod, "_close_run_escalations", _explode
    )

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "approve"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "approved"

    # The disposition's own audit row still landed.
    disposition_audits = await _audits_with_action(
        db_session, "inbox.disposition.approve"
    )
    assert any(a.target_id == str(item.id) for a in disposition_audits)


# ---------------------------------------------------------------------------
# 14. AuditLog records the legacy-writeback action (HTTP path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_records_legacy_writeback_action(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    clar = await _make_clarification(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="clarification",
        owner_user_id=user.id,
        source_table="clarifications",
        source_id=clar.id,
    )

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "answer", "answer": "the answer"},
    )
    assert res.status_code == 200, res.text

    audits = await _audits_with_action(
        db_session, "inbox.side_effect.legacy_answer_writeback"
    )
    assert any(a.target_id == str(item.id) for a in audits)

    # End-to-end: the legacy row was patched as a result of the
    # HTTP disposition (not just the helper-level test).
    refreshed = (
        await db_session.execute(
            select(Clarification).where(Clarification.id == clar.id)
        )
    ).scalar_one()
    assert refreshed.answer == "the answer"
    assert refreshed.status == "answered"
    assert refreshed.answered_by_user_id == user.id


# ---------------------------------------------------------------------------
# 15 (bonus). HTTP route: retry path persists signal in payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_retry_path_persists_retry_request(
    v1_client, seed_workspace, db_session
):
    """End-to-end retry through the HTTP route.

    Complements the helper-level ``test_retry_records_retry_request_in_payload_and_event``
    by proving the wiring writes through ``flag_modified`` correctly
    when the route owns the transaction (a JSONB in-place mutation
    bug here would silently drop the signal).
    """
    user, raw, ws = seed_workspace
    run = await _make_run(db_session, ws)
    item = await _make_item(
        db_session,
        ws,
        type="failure",
        owner_user_id=user.id,
        play_key="ci/test",
        run_id=run.id,
    )

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "retry"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["payload"]["retry_request"]["run_id"] == str(run.id)
    assert body["payload"]["retry_request"]["play_key"] == "ci/test"
