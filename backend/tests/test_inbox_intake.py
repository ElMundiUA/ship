"""Unit tests for :mod:`backend.app.services.inbox.intake` (P2-07).

Cover the silent-profile fast path, the per-finding emit pipeline
(profile gate, ``when`` gate, routing, dual insert), the
approval-gate override, the unresolved-handle behaviour, the
transaction-boundary discipline (we never commit), and the
escalation row that gets emitted when a pattern opts in.

The routing service (P2-06) ships in parallel; the
:func:`fake_resolve` fixture monkeypatches the resolver in the
intake module so these tests don't depend on it being on disk.
"""

from __future__ import annotations

import logging
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
from backend.app.db.models.pipelines import Pipeline, PipelineRun
from backend.app.services.inbox import intake as intake_mod
from backend.app.services.inbox.intake import (
    IntakeReport,
    RunSummaryFinding,
    emit_for_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pattern_meta(profile: str | None, *, escalate: bool = False) -> dict:
    """Build a minimal pattern frontmatter dict for the resolver."""
    inbox: dict[str, Any] = {}
    if profile is not None:
        inbox["profile"] = profile
    if escalate:
        inbox["escalate"] = True
    return {"id": "test-pattern", "spec": {"inbox": inbox}}


@pytest_asyncio.fixture
async def seeded_run(db_session: AsyncSession, seed_workspace):
    """Insert a Pipeline + PipelineRun so inbox FKs resolve.

    Returns ``(workspace, run, user_id)``. We persist a real run
    because ``inbox_items.run_id`` carries a FK; SET NULL would
    silently drop the link if we faked the id. ``user_id`` is the
    workspace owner — tests that want a real owner_user_id assign
    it via the fake resolver to keep the FK happy.
    """
    user, _raw, workspace = seed_workspace
    pipeline = Pipeline(
        workspace_id=workspace.id,
        repo_id=None,
        lane_id=f"intake_test_{uuid.uuid4().hex[:8]}",
        name="intake-test pipeline",
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
    return workspace, run, user.id


class _FakeResolver:
    """Configurable callable that mimics ``routing.resolve_handle``.

    Defaults: every call resolves to a fresh user uuid and a
    deterministic ``intake_reason`` so tests can assert on it. Tests
    override either ``return_target`` (returned for all handles) or
    ``side_effect`` (called per-invocation; may return a target or
    raise).
    """

    def __init__(self) -> None:
        self.return_target: Any = None
        self.side_effect: Any = None
        self.calls: list[dict] = []

    async def __call__(
        self,
        session: AsyncSession,
        handle: str,
        ctx: Any,
        *,
        fallback_chain: tuple[str, ...] = (),
    ) -> Any:
        self.calls.append(
            {
                "handle": handle,
                "ctx": ctx,
                "fallback_chain": fallback_chain,
            }
        )
        if self.side_effect is not None:
            result = self.side_effect(handle)
            if isinstance(result, BaseException):
                raise result
            return result
        if self.return_target is not None:
            return self.return_target
        # Default: resolved with NO owner. Tests that want a real
        # user should set ``return_target`` explicitly with an id
        # that exists in the ``users`` table — a random uuid here
        # would trip the owner_user_id FK.
        return intake_mod.ResolvedTarget(
            user_id=None,
            group_id=None,
            intake_handle=handle,
            intake_reason=f"fake:unresolved:{handle}",
        )


@pytest.fixture
def fake_resolve(monkeypatch):
    """Replace the routing resolver with a deterministic fake.

    The intake module imports ``resolve_handle`` at module load
    time, so the patch must target the module-level binding, not
    the original ``routing`` module — otherwise the already-bound
    reference inside ``intake`` keeps using the real one.
    """
    fake = _FakeResolver()
    monkeypatch.setattr(intake_mod, "resolve_handle", fake)
    return fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_profile_skips_every_finding(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    findings = [
        RunSummaryFinding(
            type="improvement",
            title=f"finding {i}",
            summary=None,
            payload={"i": i},
        )
        for i in range(3)
    ]
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="op-retry-sweep",
        pattern_meta=_pattern_meta("silent"),
        findings=findings,
    )

    assert isinstance(report, IntakeReport)
    assert report.profile_name == "silent"
    assert report.items_created == []
    assert len(report.items_skipped) == 3
    assert all(s["reason"] == "silent_profile" for s in report.items_skipped)
    assert [s["finding_index"] for s in report.items_skipped] == [0, 1, 2]
    assert fake_resolve.calls == []
    items = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.run_id == run.id)
        )
    ).scalars().all()
    assert items == []


@pytest.mark.asyncio
async def test_finding_emits_inbox_item_with_routed_owner(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    fake_resolve.return_target = intake_mod.ResolvedTarget(
        user_id=owner_user_id,
        group_id=None,
        intake_handle="workspace_owner",
        intake_reason="round_robin:workspace-owners",
    )
    finding = RunSummaryFinding(
        type="improvement",
        title="recurring finding",
        summary="The same thing keeps showing up.",
        payload={"k": "v"},
        when_tags=("recurring_finding_detected",),
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-tech-debt",
        pattern_meta=_pattern_meta("scan_default"),
        findings=[finding],
    )

    assert len(report.items_created) == 1
    item = await db_session.get(InboxItem, report.items_created[0])
    assert item is not None
    assert item.type == "improvement"
    assert item.status == "new"
    assert item.owner_user_id == owner_user_id
    assert item.intake_handle == "workspace_owner"
    assert item.intake_reason == "round_robin:workspace-owners"
    assert item.workspace_id == workspace.id
    assert item.run_id == run.id
    assert item.play_key == "scan-tech-debt"
    assert item.payload["k"] == "v"
    assert item.payload["requires_approval"] is False

    events = (
        await db_session.execute(
            select(InboxItemEvent)
            .where(InboxItemEvent.item_id == item.id)
            .order_by(InboxItemEvent.created_at)
        )
    ).scalars().all()
    # P3-02 — every item with a run_id also gets a system-issued
    # ``escalation_linked`` event (in addition to ``created``) so the
    # audit trail captures the linkage.
    actions = [event.action for event in events]
    assert actions == ["created", "escalation_linked"]
    assert all(event.actor_kind == "system" for event in events)


@pytest.mark.asyncio
async def test_requires_approval_promotes_to_approval_type(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type="improvement",
        title="must approve before merge",
        summary=None,
        payload={},
        requires_approval=True,
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
    item = await db_session.get(InboxItem, report.items_created[0])
    assert item is not None
    assert item.type == "approval"
    assert item.payload["requires_approval"] is True

    # The approval-gate override must have routed via the
    # ``approval`` rule's handle (``code_owner`` per the catalog),
    # NOT via ``improvement``'s handle.
    assert len(fake_resolve.calls) == 1
    assert fake_resolve.calls[0]["handle"] == "code_owner"


@pytest.mark.asyncio
async def test_disabled_rule_in_profile_skips_finding(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type="clarification",
        title="anything",
        summary=None,
        payload={},
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-tech-debt",
        pattern_meta=_pattern_meta("silent"),
        findings=[finding],
    )

    assert report.items_created == []
    assert len(report.items_skipped) == 1
    # Silent profile fires the silent-profile fast path before the
    # per-rule disabled gate; both reasons describe the same
    # operator-visible truth ("nothing emitted"). Either one is
    # fine — pin the silent path because it's deterministic.
    assert report.items_skipped[0]["reason"] == "silent_profile"
    assert fake_resolve.calls == []


@pytest.mark.asyncio
async def test_disabled_rule_in_non_silent_profile_skips_finding(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type="clarification",
        title="anything",
        summary=None,
        payload={},
    )
    # ``scan_default`` disables ``clarification`` but is not silent.
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-tech-debt",
        pattern_meta=_pattern_meta("scan_default"),
        findings=[finding],
    )
    assert report.items_created == []
    assert report.items_skipped == [
        {"finding_index": 0, "reason": "profile_disabled:clarification"}
    ]
    assert fake_resolve.calls == []


@pytest.mark.asyncio
async def test_gated_rule_emits_only_when_tag_matches(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    # ``scan_default``.improvement is gated on
    # ``recurring_finding_detected`` / ``automation_candidate_detected``.
    no_tag_finding = RunSummaryFinding(
        type="improvement",
        title="bare finding",
        summary=None,
        payload={},
    )
    matching_finding = RunSummaryFinding(
        type="improvement",
        title="tagged finding",
        summary=None,
        payload={},
        when_tags=("recurring_finding_detected",),
    )
    bad_tag_finding = RunSummaryFinding(
        type="improvement",
        title="off-topic tag",
        summary=None,
        payload={},
        when_tags=("nope_unrelated",),
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-tech-debt",
        pattern_meta=_pattern_meta("scan_default"),
        findings=[no_tag_finding, matching_finding, bad_tag_finding],
    )
    assert len(report.items_created) == 1
    reasons = sorted(s["reason"] for s in report.items_skipped)
    # Exact representation of ``rule.when`` may vary but must show
    # the gate keyword and which rule blocked.
    assert any(r.startswith("gated_no_tags:") for r in reasons)
    assert any(r.startswith("gated:") for r in reasons)


@pytest.mark.asyncio
async def test_unresolved_handle_still_creates_item_with_null_owner(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    fake_resolve.return_target = intake_mod.ResolvedTarget(
        user_id=None,
        group_id=None,
        intake_handle="workspace_owner",
        intake_reason="unresolved",
    )
    finding = RunSummaryFinding(
        type="improvement",
        title="orphan",
        summary=None,
        payload={},
        when_tags=("recurring_finding_detected",),
    )

    # Attach a handler directly to the intake logger — pytest's
    # ``caplog`` is fiddly under ``pytest-asyncio`` auto mode and
    # doesn't reliably catch records emitted from async tasks here.
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=logging.WARNING)
    intake_mod.logger.addHandler(handler)
    prior_level = intake_mod.logger.level
    prior_disabled = intake_mod.logger.disabled
    intake_mod.logger.setLevel(logging.WARNING)
    # Some autouse fixtures + pytest-asyncio leave the module logger
    # in a ``disabled=True`` state between tests; force it back on
    # so our handler actually receives the WARN.
    intake_mod.logger.disabled = False
    try:
        report = await emit_for_run(
            db_session,
            workspace_id=workspace.id,
            repo_id=None,
            run_id=run.id,
            play_key="scan-tech-debt",
            pattern_meta=_pattern_meta("scan_default"),
            findings=[finding],
        )
    finally:
        intake_mod.logger.removeHandler(handler)
        intake_mod.logger.setLevel(prior_level)
        intake_mod.logger.disabled = prior_disabled

    assert len(report.items_created) == 1
    item = await db_session.get(InboxItem, report.items_created[0])
    assert item is not None
    assert item.owner_user_id is None
    assert item.intake_reason == "unresolved"
    assert report.unresolved_handles == ["workspace_owner"]
    assert any(
        record.levelno == logging.WARNING
        and "unresolved" in record.getMessage()
        for record in captured
    )


@pytest.mark.asyncio
async def test_intake_does_not_commit_transaction(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type="improvement",
        title="check no commit",
        summary=None,
        payload={},
        when_tags=("recurring_finding_detected",),
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-tech-debt",
        pattern_meta=_pattern_meta("scan_default"),
        findings=[finding],
    )
    assert len(report.items_created) == 1
    # Roll back the outer SAVEPOINT — if intake had committed, the
    # row would survive. The next query in the same fixture's
    # transaction proves it didn't.
    await db_session.rollback()
    items = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.id == report.items_created[0])
        )
    ).scalars().all()
    assert items == []


@pytest.mark.asyncio
async def test_event_row_has_handle_and_route_in_payload(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    fake_resolve.return_target = intake_mod.ResolvedTarget(
        user_id=owner_user_id,
        group_id=None,
        intake_handle="workspace_owner",
        intake_reason="round_robin:workspace-owners",
    )
    finding = RunSummaryFinding(
        type="improvement",
        title="event payload",
        summary=None,
        payload={},
        when_tags=("recurring_finding_detected",),
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-tech-debt",
        pattern_meta=_pattern_meta("scan_default"),
        findings=[finding],
    )
    assert len(report.items_created) == 1
    # P3-02: timeline now carries both ``created`` and the universal
    # ``escalation_linked`` event. Filter to the ``created`` row so
    # the assertion is specific to the routing payload it carries.
    created_event = (
        await db_session.execute(
            select(InboxItemEvent).where(
                InboxItemEvent.item_id == report.items_created[0],
                InboxItemEvent.action == "created",
            )
        )
    ).scalar_one()
    assert created_event.payload["handle"] == "workspace_owner"
    assert created_event.payload["route"] == "round_robin:workspace-owners"


@pytest.mark.asyncio
async def test_finding_with_invalid_type_raises_value_error(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    bad = RunSummaryFinding(
        type="nonsense",
        title="bad type",
        summary=None,
        payload={},
    )
    with pytest.raises(ValueError, match="nonsense"):
        await emit_for_run(
            db_session,
            workspace_id=workspace.id,
            repo_id=None,
            run_id=run.id,
            play_key="scan-tech-debt",
            pattern_meta=_pattern_meta("scan_default"),
            findings=[bad],
        )

    written = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.run_id == run.id)
        )
    ).scalars().all()
    assert written == []


@pytest.mark.asyncio
async def test_emit_for_run_preserves_finding_order_in_items_created(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    workspace, run, owner_user_id = seeded_run
    findings = [
        RunSummaryFinding(
            type="improvement",
            title=f"finding {i}",
            summary=None,
            payload={"i": i},
            when_tags=("recurring_finding_detected",),
        )
        for i in range(3)
    ]
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-tech-debt",
        pattern_meta=_pattern_meta("scan_default"),
        findings=findings,
    )
    assert len(report.items_created) == 3
    # Re-read the items and confirm their order in items_created
    # matches the play-author order via the ``i`` payload key.
    items = []
    for item_id in report.items_created:
        items.append(await db_session.get(InboxItem, item_id))
    assert [item.payload["i"] for item in items] == [0, 1, 2]
    assert [item.title for item in items] == [
        "finding 0",
        "finding 1",
        "finding 2",
    ]


@pytest.mark.asyncio
async def test_run_escalation_emitted_for_approval_finding(
    db_session: AsyncSession, seeded_run, fake_resolve
):
    """Approval finding still produces a ``requires_approval`` linkage row.

    Under P3-02 the linkage is universal (no longer gated on
    ``spec.inbox.escalate``). This test pins the historical
    approval case so any regression to the old "only when
    pattern opts in" behaviour fails loudly.
    """
    workspace, run, owner_user_id = seeded_run
    finding = RunSummaryFinding(
        type="improvement",
        title="needs human approval",
        summary=None,
        payload={},
        requires_approval=True,
        when_tags=("autofix_proposed",),
    )
    report = await emit_for_run(
        db_session,
        workspace_id=workspace.id,
        repo_id=None,
        run_id=run.id,
        play_key="scan-security-deps",
        # No ``escalate=True`` opt-in — proves P3-02 made the gate
        # unconditional. The pattern profile is what matters; the
        # legacy frontmatter flag is now ignored by intake.
        pattern_meta=_pattern_meta("scan_with_autofix"),
        findings=[finding],
    )

    assert len(report.escalations_created) == 1
    escalation = await db_session.get(
        RunEscalation, report.escalations_created[0]
    )
    assert escalation is not None
    assert escalation.run_id == run.id
    assert escalation.escalation_reason == "requires_approval"
    assert escalation.inbox_item_id == report.items_created[0]
