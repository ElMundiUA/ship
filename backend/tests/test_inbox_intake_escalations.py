"""Unit tests for the P3-02 universal run→inbox linkage.

Cover the new ``run_escalations`` writes performed by
:func:`backend.app.services.inbox.intake.emit_for_run`:

- Every inbox item created with ``run_id != None`` gets a
  linkage row (no longer gated on ``spec.inbox.escalate``).
- Calls with ``run_id=None`` create no linkage rows.
- Each inbox type maps to the documented coarse
  ``escalation_reason`` bucket (RFC-0010 P3-02).
- Re-emission against the same ``(run_id, inbox_item_id)``
  doesn't duplicate linkage rows or audit events
  (``ON CONFLICT DO NOTHING`` against the unique constraint).
- Each freshly-inserted linkage row writes an
  ``escalation_linked`` ``inbox_item_event``.

Reuses the same routing-shim pattern as ``test_inbox_intake.py`` so
these tests don't depend on ``routing.py`` being on disk.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.inbox import (
    InboxItem,
    InboxItemEvent,
    RunEscalation,
)
from backend.app.db.models.lanes import Routine, RoutineRun
from backend.app.services.inbox import intake as intake_mod
from backend.app.services.inbox.intake import (
    RunSummaryFinding,
    emit_for_run,
)


# ---------------------------------------------------------------------------
# Fixtures (mirror the ones in test_inbox_intake.py)
# ---------------------------------------------------------------------------


def _pattern_meta(profile: str | None) -> dict:
    inbox: dict[str, Any] = {}
    if profile is not None:
        inbox["profile"] = profile
    return {"id": "test-pattern", "spec": {"inbox": inbox}}


@pytest_asyncio.fixture
async def seeded_run(db_session: AsyncSession, seed_workspace):
    """Insert a Routine + RoutineRun so inbox FKs resolve."""
    from backend.app.db.models.integrations import WorkspaceRepo

    user, _raw, workspace = seed_workspace
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        provider="github",
        external_id=hash(uuid.uuid4()) & 0x7FFFFFFF,
        full_name=f"test/escalation-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(repo)
    await db_session.flush()
    routine = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id=f"escalation_test_{uuid.uuid4().hex[:8]}",
        kind="event",
        pattern="pr-and-ci-gate",
    )
    db_session.add(routine)
    await db_session.flush()
    run = RoutineRun(
        routine_id=routine.id,
        workspace_id=workspace.id,
        trigger="manual",
        status="succeeded",
    )
    db_session.add(run)
    await db_session.flush()
    return workspace, run, user.id


class _FakeResolver:
    """Configurable callable that mimics ``routing.resolve_handle``."""

    def __init__(self, owner_user_id: uuid.UUID | None = None) -> None:
        self.owner_user_id = owner_user_id
        self.calls: list[dict] = []

    async def __call__(
        self,
        session: AsyncSession,
        handle: str,
        ctx: Any,
        *,
        fallback_chain: tuple[str, ...] = (),
    ) -> Any:
        self.calls.append({"handle": handle, "ctx": ctx})
        return intake_mod.ResolvedTarget(
            user_id=self.owner_user_id,
            group_id=None,
            intake_handle=handle,
            intake_reason=f"fake:{handle}",
        )


@pytest.fixture
def fake_resolve(monkeypatch, seeded_run):
    _, _, owner_user_id = seeded_run
    fake = _FakeResolver(owner_user_id=owner_user_id)
    monkeypatch.setattr(intake_mod, "resolve_handle", fake)
    return fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intake_with_run_id_creates_escalation_per_inbox_item(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    """Three findings → three RunEscalation rows, each with the right reason."""
    workspace, run, _owner_user_id = seeded_run
    findings = [
        # ``flow_release`` enables clarification / approval / failure /
        # improvement / exception — pick three different types so we
        # can prove the per-item linkage isn't a special case.
        RunSummaryFinding(
            type="clarification",
            title="missing release metadata",
            summary=None,
            payload={},
            when_tags=("missing_release_metadata",),
        ),
        RunSummaryFinding(
            type="failure",
            title="release signal source down",
            summary=None,
            payload={},
            when_tags=("play_failed_repeatedly",),
        ),
        RunSummaryFinding(
            type="exception",
            title="ship with known risk",
            summary=None,
            payload={},
            when_tags=("allow_release_with_known_risk",),
        ),
    ]
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="flow-release-notes",
        pattern_meta=_pattern_meta("flow_release"),
        findings=findings,
    )

    assert len(report.items_created) == 3
    assert len(report.escalations_created) == 3

    escalations = (
        await db_session.execute(
            select(RunEscalation).where(RunEscalation.run_id == run.id)
        )
    ).scalars().all()
    assert len(escalations) == 3
    by_item = {e.inbox_item_id: e.escalation_reason for e in escalations}
    # Item order matches finding order.
    assert by_item[report.items_created[0]] == "needs_clarification"
    assert by_item[report.items_created[1]] == "play_failed_repeatedly"
    assert by_item[report.items_created[2]] == "play_exception"


@pytest.mark.asyncio
async def test_intake_without_run_id_skips_escalations(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    """``run_id=None`` short-circuits the linkage write."""
    workspace, _run, _owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type="failure",
        title="ad-hoc failure",
        summary=None,
        payload={},
        when_tags=("play_failed_repeatedly",),
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=None,
        play_key="flow-release-notes",
        pattern_meta=_pattern_meta("flow_release"),
        findings=[finding],
    )

    assert len(report.items_created) == 1
    assert report.escalations_created == []

    escalations = (
        await db_session.execute(
            select(RunEscalation).where(
                RunEscalation.inbox_item_id == report.items_created[0]
            )
        )
    ).scalars().all()
    assert escalations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "finding_type", "when_tag", "requires_approval", "expected_reason"),
    [
        # `requires_approval=True` is the approval-gate override —
        # the inbox item lands as type=approval regardless of the
        # nominal ``finding.type``. The escalation reason must
        # follow the *effective* type, not the source one.
        (
            "scan_with_autofix",
            "improvement",
            "autofix_proposed",
            True,
            "requires_approval",
        ),
        (
            "flow_release",
            "failure",
            "play_failed_repeatedly",
            False,
            "play_failed_repeatedly",
        ),
        (
            "flow_release",
            "clarification",
            "target_release_unclear",
            False,
            "needs_clarification",
        ),
        (
            "flow_release",
            "improvement",
            "repeated_release_gap_detected",
            False,
            "improvement_proposed",
        ),
        (
            "flow_release",
            "exception",
            "allow_release_with_known_risk",
            False,
            "play_exception",
        ),
    ],
    ids=[
        "approval_via_requires_approval_override",
        "failure",
        "clarification",
        "improvement",
        "exception",
    ],
)
async def test_intake_escalation_reason_mapping(
    db_session: AsyncSession,
    seeded_run,
    fake_resolve,
    profile: str,
    finding_type: str,
    when_tag: str,
    requires_approval: bool,
    expected_reason: str,
):
    """Each inbox type / approval-gate combo maps to the right reason."""
    workspace, run, _owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type=finding_type,
        title=f"{finding_type} finding",
        summary=None,
        payload={},
        requires_approval=requires_approval,
        when_tags=(when_tag,),
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="test-play",
        pattern_meta=_pattern_meta(profile),
        findings=[finding],
    )

    assert len(report.items_created) == 1
    assert len(report.escalations_created) == 1
    escalation = await db_session.get(
        RunEscalation, report.escalations_created[0]
    )
    assert escalation is not None
    assert escalation.escalation_reason == expected_reason
    assert escalation.inbox_item_id == report.items_created[0]


@pytest.mark.asyncio
async def test_intake_escalation_idempotent_on_re_emit(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    """Re-emit with the same item id only inserts one linkage row.

    Inbox-item idempotency is a separate concern (current
    ``emit_for_run`` re-creates the item on each call — see the
    pre-existing-bug note in the report). This test focuses on
    the *linkage* idempotency the new ``ON CONFLICT DO NOTHING``
    guarantees: we manually re-call the linkage helper twice on
    the same item, then prove the second call neither duplicated
    the row nor wrote a second ``escalation_linked`` event.
    """
    workspace, run, _owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type="failure",
        title="repeated failure",
        summary=None,
        payload={},
        when_tags=("play_failed_repeatedly",),
    )
    first = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="flow-release-notes",
        pattern_meta=_pattern_meta("flow_release"),
        findings=[finding],
    )
    assert len(first.items_created) == 1
    assert len(first.escalations_created) == 1
    item_id = first.items_created[0]

    # Replay the linkage on the same (run_id, item_id) pair — this
    # is the path the unique constraint + ON CONFLICT DO NOTHING
    # guards. We re-fetch the item so the helper has a real ORM
    # instance to key off.
    item = await db_session.get(InboxItem, item_id)
    assert item is not None
    second_report = intake_mod.IntakeReport(run_id=run.id, profile_name="flow_release")
    await intake_mod._link_item_to_run(
        db_session,
        run_id=run.id,
        item=item,
        effective_type="failure",
        report=second_report,
    )
    # Second call observed the unique-constraint conflict and
    # swallowed the insert — no new linkage id and no audit event.
    assert second_report.escalations_created == []

    # DB still has exactly one linkage row.
    linkage_rows = (
        await db_session.execute(
            select(RunEscalation).where(
                RunEscalation.run_id == run.id,
                RunEscalation.inbox_item_id == item_id,
            )
        )
    ).scalars().all()
    assert len(linkage_rows) == 1

    # And exactly one escalation_linked event.
    linked_events = (
        await db_session.execute(
            select(InboxItemEvent).where(
                InboxItemEvent.item_id == item_id,
                InboxItemEvent.action == "escalation_linked",
            )
        )
    ).scalars().all()
    assert len(linked_events) == 1


@pytest.mark.asyncio
async def test_intake_emits_escalation_linked_event(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    """Each fresh linkage row writes a system ``escalation_linked`` event."""
    workspace, run, _owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type="approval",
        title="needs human approval",
        summary=None,
        payload={},
        requires_approval=False,
        when_tags=("autofix_proposed",),
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-security-deps",
        pattern_meta=_pattern_meta("scan_with_autofix"),
        findings=[finding],
    )

    assert len(report.items_created) == 1
    assert len(report.escalations_created) == 1
    item_id = report.items_created[0]

    events = (
        await db_session.execute(
            select(InboxItemEvent)
            .where(InboxItemEvent.item_id == item_id)
            .order_by(InboxItemEvent.created_at)
        )
    ).scalars().all()
    actions = [e.action for e in events]
    assert actions == ["created", "escalation_linked"]

    linked = events[1]
    assert linked.actor_kind == "system"
    assert linked.actor_user_id is None
    assert linked.payload["run_id"] == str(run.id)
    assert linked.payload["reason"] == "requires_approval"
