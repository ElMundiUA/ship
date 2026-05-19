"""Inbox intake service (RFC-0010 P2-07).

Single entrypoint pipelines call after a run finishes. It consults
the pattern's :class:`ResolvedProfile` (from the catalog at
``profile_catalog.yaml``), decides which inbox items to emit, asks
the routing service to resolve every emit-rule's ``handle`` into a
concrete owner, and writes the resulting :class:`InboxItem` /
:class:`InboxItemEvent` rows.

Where this slots into the run lifecycle: the pipeline-finished hook
(P2-08) holds an open ``AsyncSession`` with an open transaction;
after writing the run's ``outcome`` JSONB it calls
:func:`emit_for_run` with the structured findings the play emitted
(see RFC-0010 §RunSummary). This module **never** opens or commits a
transaction — the caller owns that boundary, matching the
convention used by :mod:`backend.app.services.clarifications_sync`.

Three behaviours worth remembering:

* **Silent profile fast path** — pattern profile resolves to
  ``silent`` → every finding is recorded in
  :attr:`IntakeReport.items_skipped` with reason ``silent_profile``
  and the function returns without touching the DB.
* **Approval gate override** — any finding with
  ``requires_approval=True`` is forced through the ``approval``
  emit-rule regardless of the finding's nominal type; planning §3
  treats the approval gate as a hard override.
* **Routing failures never drop items** — if the resolver returns
  ``user_id=None`` we still insert the item (with ``owner_user_id``
  null) and surface the unresolved handle in the report + a WARN
  log. Better an unowned item an admin can fix than a dropped one
  the audit trail will never show.

The :class:`IntakeReport` returned to the caller exposes the
created item ids (in finding-input order so the audit trail is
reproducible), the skipped findings + reasons, any escalations
written, and the unresolved handles for surfacing in the run
record (e.g. "inbox: 2 items · 1 skipped · 1 unresolved handle").
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.inbox import (
    InboxItem,
    InboxItemEvent,
    RunEscalation,
)
from backend.app.services.inbox.classification import (
    category_from_type,
    priority_for_item,
)
from backend.app.services.inbox.headline import derive_headline
from backend.app.services.inbox.profiles import (
    EmitRule,
    INBOX_TYPES,
    ResolvedProfile,
    resolve_for_pattern,
)

# ``routing.py`` is being authored in parallel by a sibling subagent
# (P2-06). At module import time it may not yet exist on disk; we
# guard the import so this module loads either way and the tests can
# monkeypatch :func:`resolve_handle`. Once routing.py lands the
# shim becomes a no-op (the real symbols win) and can be removed.
try:  # pragma: no cover — exercised once routing.py lands.
    from backend.app.services.inbox.routing import (  # type: ignore[import-not-found]
        ResolvedTarget,
        RoutingContext,
        resolve_handle,
    )
except ImportError:  # pragma: no cover — keeps tests runnable in parallel.

    @dataclass(frozen=True)
    class RoutingContext:  # type: ignore[no-redef]
        """Shim placeholder until ``routing.py`` lands.

        Mirrors the contract the sibling subagent owns:
        ``workspace_id``, ``repo_id``, ``run_id``, ``source_row``.
        """

        workspace_id: uuid.UUID
        repo_id: uuid.UUID | None
        run_id: uuid.UUID | None
        source_row: dict

    @dataclass(frozen=True)
    class ResolvedTarget:  # type: ignore[no-redef]
        """Shim placeholder until ``routing.py`` lands."""

        user_id: uuid.UUID | None
        group_id: uuid.UUID | None
        intake_handle: str | None
        intake_reason: str | None

    async def resolve_handle(  # type: ignore[no-redef]
        session: AsyncSession,
        handle: str,
        ctx: "RoutingContext",
        *,
        fallback_chain: tuple[str, ...] = (),
    ) -> "ResolvedTarget":
        """Shim: returns an unresolved target. Tests monkeypatch this."""
        return ResolvedTarget(
            user_id=None,
            group_id=None,
            intake_handle=handle,
            intake_reason="unresolved:routing_module_missing",
        )


logger = logging.getLogger(__name__)


_TITLE_MAX_LEN = 250  # leaves headroom under the inbox_items.title VARCHAR(255).
_SUMMARY_MAX_LEN = 8 * 1024
_APPROVAL_TYPE = "approval"
_SILENT_PROFILE_NAME = "silent"


# RFC-0010 P3-02 — every inbox item created with a ``run_id`` gets a
# linkage row in ``run_escalations``; the bucket below is the coarse
# machine-readable label we stamp on it. Keep it stable: the
# disposition side-effects (``side_effects._close_run_escalations``)
# and any future analytics will key off these strings.
_ESCALATION_REASON_BY_TYPE: dict[str, str] = {
    "approval": "requires_approval",
    "failure": "play_failed_repeatedly",
    "clarification": "needs_clarification",
    "improvement": "improvement_proposed",
    "exception": "play_exception",
    "stuck": "stale_work",
    "blocker": "self_heal_failed",
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummaryFinding:
    """One actionable finding emitted by a play's RunSummary contract.

    Maps onto the planning §3 contract: every finding declares its
    inbox ``type`` (one of :data:`INBOX_TYPES`) plus a small payload
    the intake service will store on ``inbox_items.payload``.

    ``requires_approval`` upgrades the inbox ``type`` to ``approval``
    regardless of the source rule (planning §3 — the approval gate
    is a hard override).

    ``when_tags`` are the trigger conditions the run observed; the
    intake service compares them against the rule's catalog ``when``
    list to decide whether to emit. An empty rule ``when`` is
    treated as "always emit".
    """

    type: str
    title: str
    summary: str | None
    payload: dict
    requires_approval: bool = False
    when_tags: tuple[str, ...] = ()


@dataclass
class IntakeReport:
    """Per-run report — what got created, what got skipped, and why.

    Returned to the caller (typically the pipeline-finished hook) so
    the run record can show a one-line "inbox: 2 items, 1 skipped
    (silent profile)" breadcrumb. ``items_created`` order matches
    the order findings were passed in so the UI can render them in
    play-author order.
    """

    run_id: uuid.UUID
    profile_name: str
    items_created: list[uuid.UUID] = field(default_factory=list)
    items_skipped: list[dict] = field(default_factory=list)
    escalations_created: list[uuid.UUID] = field(default_factory=list)
    unresolved_handles: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_len: int) -> str:
    """Trim ``text`` to ``max_len`` chars without raising on ``None``."""
    if text is None:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len]


def _validate_finding_type(t: str) -> None:
    """Raise :class:`ValueError` if ``t`` is not a known inbox type.

    Kept narrow: every finding must declare a type from
    :data:`INBOX_TYPES`. This is a programmer-error gate (the play
    author or pipeline glue produced bad data); we surface it loudly
    rather than swallowing it into the skipped list.
    """
    if t not in INBOX_TYPES:
        raise ValueError(
            f"finding type {t!r} is not a known inbox type "
            f"(allowed: {', '.join(INBOX_TYPES)})"
        )


def _select_emit_rule(
    profile: ResolvedProfile,
    finding: "RunSummaryFinding",
) -> tuple[EmitRule, str]:
    """Return ``(rule, effective_type)`` honouring the approval gate.

    When ``finding.requires_approval`` is true we look up the
    ``approval`` rule regardless of ``finding.type`` so the inbox
    item's stored ``type`` becomes ``approval`` (planning §3). This
    is the only place the catalog rule and the stored item type
    diverge — keep the override explicit so callers can audit it.
    """
    if finding.requires_approval:
        return profile.rules[_APPROVAL_TYPE], _APPROVAL_TYPE
    return profile.rules[finding.type], finding.type


def _check_when_gate(
    rule: EmitRule, finding: "RunSummaryFinding"
) -> str | None:
    """Return a skip reason if ``finding`` doesn't satisfy ``rule.when``.

    Empty ``rule.when`` means "always emit" — no gate. Otherwise
    the finding must declare at least one ``when_tag`` overlapping
    the rule's allowed list. We distinguish the two failure shapes
    (``gated_no_tags`` vs ``gated``) so operators tuning a profile
    can tell whether the play forgot to tag or whether the tag set
    didn't match.
    """
    if not rule.when:
        return None
    if not finding.when_tags:
        return f"gated_no_tags:{rule.when}"
    overlap = set(finding.when_tags) & set(rule.when)
    if not overlap:
        return f"gated:{rule.when}"
    return None


def _build_inbox_item(
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    play_key: str | None,
    effective_type: str,
    finding: "RunSummaryFinding",
    resolved: "ResolvedTarget",
    source_table: str | None = None,
    source_id: uuid.UUID | None = None,
) -> InboxItem:
    """Materialise an :class:`InboxItem` from a finding + resolved target.

    Centralised so :func:`emit_for_run` and :func:`emit_legacy_record`
    produce schema-compatible rows. ``payload`` always carries an
    explicit ``requires_approval`` boolean so downstream UI can
    render the gate badge without re-reading the row's ``type``.
    """
    payload = dict(finding.payload or {})
    payload["requires_approval"] = bool(finding.requires_approval)
    title = _truncate(finding.title, _TITLE_MAX_LEN)
    summary = _truncate(finding.summary or "", _SUMMARY_MAX_LEN) or None
    explicit_headline = payload.get("headline")
    if isinstance(explicit_headline, str):
        headline_arg: str | None = explicit_headline
    else:
        headline_arg = None
    auto_resolvable = False
    stale_after = None
    if effective_type == "stuck":
        auto_resolvable = True
        stale_after = timedelta(hours=1)
    elif (
        effective_type == "failure"
        and payload.get("ticket_ref")
        and payload.get("fsm_stage")
    ):
        auto_resolvable = True
    category = category_from_type(effective_type)
    return InboxItem(
        workspace_id=workspace_id,
        repo_id=repo_id,
        type=effective_type,
        category=category,
        priority=priority_for_item(category=category, item_type=effective_type),
        source_table=source_table,
        source_id=source_id,
        play_key=play_key,
        run_id=run_id,
        title=title,
        headline=derive_headline(
            headline=headline_arg,
            summary=summary,
            title=title,
        ),
        summary=summary,
        payload=payload,
        status="new",
        owner_user_id=resolved.user_id,
        intake_handle=resolved.intake_handle,
        intake_reason=resolved.intake_reason,
        auto_resolvable=auto_resolvable,
        stale_after=stale_after,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def emit_for_run(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    play_key: str,
    pattern_meta: dict,
    findings: list[RunSummaryFinding],
    source_row: dict | None = None,
) -> IntakeReport:
    """Emit inbox items for a finished pipeline run.

    Implements planning §7's intake state machine:

    1. Resolve the pattern's profile via
       :func:`resolve_for_pattern`. If it resolves to ``silent``
       we fast-path: every finding is recorded as skipped with
       reason ``silent_profile`` and no DB rows are written.
    2. For each finding (input order preserved): pick the emit
       rule (with the approval-gate override), apply the
       ``enabled`` and ``when`` gates, ask the routing service
       for an owner, then insert :class:`InboxItem` +
       :class:`InboxItemEvent`. Routing failures are logged and
       recorded but never raise.
    3. **P3-02 (universal run linkage).** Every inbox item that
       lands with ``run_id IS NOT NULL`` gets a corresponding
       :class:`RunEscalation` row inserted via PostgreSQL
       ``ON CONFLICT DO NOTHING`` against the
       ``(run_id, inbox_item_id)`` unique constraint, so re-runs
       of this function on the same input are idempotent. The
       ``escalation_reason`` column is filled from the
       :data:`_ESCALATION_REASON_BY_TYPE` map keyed off the
       item's effective type. Each freshly-inserted linkage row
       also writes an ``escalation_linked`` audit event on the
       inbox item so the timeline records the system action.

    Never opens or commits a transaction — the caller owns the
    session boundary. We call ``session.flush()`` between item and
    event inserts so the event row can reference the item's PK.
    """
    profile = resolve_for_pattern(pattern_meta)
    report = IntakeReport(run_id=run_id, profile_name=profile.profile_name)

    # Validate every finding type up-front so a bad finding deep in
    # the list can't leave a half-written batch behind. Programmer
    # errors should fail fast and noisily.
    for finding in findings:
        _validate_finding_type(finding.type)

    if profile.profile_name == _SILENT_PROFILE_NAME:
        for index, _ in enumerate(findings):
            report.items_skipped.append(
                {"finding_index": index, "reason": "silent_profile"}
            )
        return report

    ctx_source_row = dict(source_row or {})

    for index, finding in enumerate(findings):
        rule, effective_type = _select_emit_rule(profile, finding)

        if not rule.enabled:
            report.items_skipped.append(
                {
                    "finding_index": index,
                    "reason": f"profile_disabled:{effective_type}",
                }
            )
            continue

        gate_reason = _check_when_gate(rule, finding)
        if gate_reason is not None:
            report.items_skipped.append(
                {"finding_index": index, "reason": gate_reason}
            )
            continue

        if effective_type == "exception":
            from backend.app.core.sentry import record_inbox_exception_breadcrumb

            record_inbox_exception_breadcrumb(
                source="play_intake",
                title=finding.title,
                ticket_ref=str(finding.payload.get("ticket_ref") or "") or None,
                play_key=play_key,
                run_id=str(run_id) if run_id else None,
            )
            report.items_skipped.append(
                {"finding_index": index, "reason": "exception:breadcrumb_only"}
            )
            continue

        ctx = RoutingContext(
            workspace_id=workspace_id,
            repo_id=repo_id,
            run_id=run_id,
            source_row=ctx_source_row,
        )
        # Intentionally awaited sequentially (not gathered): each call
        # may UPSERT round-robin state, and concurrent calls on the
        # same group would race. Sequential keeps the rotation honest.
        try:
            resolved = await resolve_handle(session, rule.handle, ctx)
        except Exception:  # noqa: BLE001 — routing must never sink intake.
            logger.exception(
                "routing.resolve_handle raised for handle=%s "
                "(workspace=%s, run=%s); recording as unresolved",
                rule.handle,
                workspace_id,
                run_id,
            )
            resolved = ResolvedTarget(
                user_id=None,
                group_id=None,
                intake_handle=rule.handle,
                intake_reason="unresolved:exception",
            )

        item = _build_inbox_item(
            workspace_id=workspace_id,
            repo_id=repo_id,
            run_id=run_id,
            play_key=play_key,
            effective_type=effective_type,
            finding=finding,
            resolved=resolved,
        )
        session.add(item)
        # Flush so ``item.id`` is populated before we link the event
        # row — explicit ordering beats relationship magic for the
        # audit trail.
        await session.flush()

        event = InboxItemEvent(
            item_id=item.id,
            actor_user_id=None,
            actor_kind="system",
            action="created",
            payload={
                "reason": "intake",
                "handle": rule.handle,
                "route": resolved.intake_reason,
            },
        )
        session.add(event)
        await session.flush()

        report.items_created.append(item.id)

        if resolved.user_id is None:
            report.unresolved_handles.append(rule.handle or "")
            logger.warning(
                "inbox intake: handle %r unresolved for run=%s "
                "workspace=%s (item %s created without owner; "
                "fix routing config or workspace groups)",
                rule.handle,
                run_id,
                workspace_id,
                item.id,
            )

        # P3-02 — universal run→inbox linkage. Skip when the caller
        # didn't supply a run_id (legacy paths that mirror existing
        # rows without an originating pipeline run).
        if run_id is not None:
            await _link_item_to_run(
                session,
                run_id=run_id,
                item=item,
                effective_type=effective_type,
                report=report,
            )

    return report


async def _link_item_to_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    item: InboxItem,
    effective_type: str,
    report: IntakeReport,
) -> None:
    """Insert a :class:`RunEscalation` row linking ``item`` to ``run_id``.

    The ``(run_id, inbox_item_id)`` uniqueness constraint
    (``uq_run_escalations_run_item``) plus ``ON CONFLICT DO
    NOTHING`` keeps this idempotent: re-running :func:`emit_for_run`
    against the same finding set does not duplicate linkage rows.
    Only freshly-inserted rows (``RETURNING id`` populated) are
    counted in :attr:`IntakeReport.escalations_created` and only
    those rows trigger an ``escalation_linked`` audit event — so
    the audit trail also stays free of dupes when callers retry.
    """
    reason = _ESCALATION_REASON_BY_TYPE.get(effective_type)
    if reason is None:
        # Defence in depth: every value in INBOX_TYPES has a row in
        # the map, so this branch only fires if a future type is
        # added to the catalog without updating the constant. Skip
        # silently rather than break intake — the inbox item still
        # lands, just without a linkage row.
        logger.warning(
            "inbox intake: no escalation_reason mapping for "
            "effective_type=%r (item=%s, run=%s); skipping linkage",
            effective_type,
            item.id,
            run_id,
        )
        return

    stmt = (
        pg_insert(RunEscalation)
        .values(
            run_id=run_id,
            inbox_item_id=item.id,
            escalation_reason=reason,
        )
        .on_conflict_do_nothing(
            constraint="uq_run_escalations_run_item",
        )
        .returning(RunEscalation.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is None:
        # Row already existed — re-emission case; the audit event
        # for the original linkage is already in the timeline.
        return

    report.escalations_created.append(inserted_id)
    session.add(
        InboxItemEvent(
            item_id=item.id,
            actor_user_id=None,
            actor_kind="system",
            action="escalation_linked",
            payload={"run_id": str(run_id), "reason": reason},
        )
    )
    await session.flush()


async def emit_legacy_record(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    source_table: str,
    source_id: uuid.UUID,
    finding: RunSummaryFinding,
    handle: str,
) -> uuid.UUID | None:
    """Insert an inbox item that mirrors a legacy clarification/improvement.

    Used by the P2-08 dual-write paths that already know which
    ``handle`` owns the row (e.g. ``clarifications`` writes route
    via the originating agent). Skips the profile lookup entirely
    — the caller is the source of truth for ``type`` and ``handle``.

    Returns the new ``item.id``, or ``None`` if routing returned an
    unresolved target with no ``intake_handle`` (i.e. the resolver
    couldn't even produce a record). On a partial resolution
    (``user_id=None`` but ``intake_handle`` populated) we still
    write the row to keep the audit trail intact.
    """
    _validate_finding_type(finding.type)

    ctx = RoutingContext(
        workspace_id=workspace_id,
        repo_id=repo_id,
        run_id=run_id,
        source_row={"source_table": source_table, "source_id": str(source_id)},
    )
    try:
        resolved = await resolve_handle(session, handle, ctx)
    except Exception:  # noqa: BLE001
        logger.exception(
            "routing.resolve_handle raised for legacy handle=%s "
            "(source=%s/%s); skipping legacy mirror",
            handle,
            source_table,
            source_id,
        )
        return None

    if resolved.intake_handle is None and resolved.user_id is None:
        # No fallback fired — caller will retry on the next dual-write.
        return None

    effective_type = (
        _APPROVAL_TYPE if finding.requires_approval else finding.type
    )
    item = _build_inbox_item(
        workspace_id=workspace_id,
        repo_id=repo_id,
        run_id=run_id,
        play_key=None,
        effective_type=effective_type,
        finding=finding,
        resolved=resolved,
        source_table=source_table,
        source_id=source_id,
    )
    session.add(item)
    await session.flush()

    event = InboxItemEvent(
        item_id=item.id,
        actor_user_id=None,
        actor_kind="system",
        action="created",
        payload={
            "reason": "legacy_mirror",
            "handle": handle,
            "route": resolved.intake_reason,
            "source_table": source_table,
        },
    )
    session.add(event)
    await session.flush()

    if resolved.user_id is None:
        logger.warning(
            "inbox intake (legacy): handle %r unresolved for "
            "source=%s/%s (item %s created without owner)",
            handle,
            source_table,
            source_id,
            item.id,
        )
    return item.id


# ---------------------------------------------------------------------------
# Pattern-meta helpers
# ---------------------------------------------------------------------------


def _wants_run_escalation(pattern_meta: dict) -> bool:
    """Read ``spec.inbox.escalate`` defensively from pattern meta.

    The frontmatter shape is operator-authored; we treat anything
    that isn't a strict ``True`` as opt-out so a typo doesn't
    silently start writing :class:`RunEscalation` rows.

    DEPRECATED: superseded by P3-02 universal linkage. Every inbox
    item created with a non-NULL ``run_id`` now gets a
    :class:`RunEscalation` row regardless of the pattern opt-in;
    this function is kept for one release so external imports
    don't break, but :func:`emit_for_run` no longer calls it.
    """
    spec: Any = pattern_meta.get("spec") if isinstance(pattern_meta, dict) else None
    inbox_cfg = spec.get("inbox") if isinstance(spec, dict) else None
    if not isinstance(inbox_cfg, dict):
        return False
    return inbox_cfg.get("escalate") is True


def _first_approval_item_id(
    report: IntakeReport, findings: list[RunSummaryFinding]
) -> uuid.UUID | None:
    """Pick the first created item that came from an approval finding.

    DEPRECATED: superseded by P3-02 universal linkage. Kept for one
    release so external imports don't break; :func:`emit_for_run`
    no longer calls it (every item now gets its own linkage row).

    ``IntakeReport.items_created`` is order-aligned with the input
    ``findings`` list filtered down to the ones that were actually
    written. Walking the findings + skipped list in parallel would
    over-engineer this — we just rebuild the index of created
    items: the i-th created item is the i-th finding that wasn't
    skipped, so we walk findings, count emissions, and pick the
    first one whose finding had ``requires_approval=True``.
    """
    skipped_indices = {entry["finding_index"] for entry in report.items_skipped}
    emitted_iter = iter(report.items_created)
    for index, finding in enumerate(findings):
        if index in skipped_indices:
            continue
        item_id = next(emitted_iter, None)
        if item_id is None:
            return None
        if finding.requires_approval:
            return item_id
    return None


__all__ = [
    "IntakeReport",
    "RunSummaryFinding",
    "emit_for_run",
    "emit_legacy_record",
]
