"""Inbox disposition side-effects (RFC-0010 §7).

Each disposition action's "what else happens" lives here so the route
handler stays focused on validation + state transitions. Called from
:func:`backend.app.api.v1.routes.inbox.post_disposition` AFTER the inbox
row mutates and BEFORE the response serialiser reads the row, inside
the same session transaction. Best-effort: any side-effect failure is
logged + AuditLog-recorded, never raised — the disposition itself
already succeeded by the time we run.

Direction: **inbox → legacy** (writeback). The opposite direction
(legacy → inbox mirror) lives in
:mod:`backend.app.services.inbox.dual_write`. We deliberately patch
the legacy ``clarifications`` / ``improvements`` row directly via the
ORM rather than calling :func:`mirror_clarification_resolve` /
:func:`mirror_improvement_resolve` — those would look up the same
inbox row we are already holding and noop, which is confusing.

Side-effect contract per disposition (planning §7):

============  =====================================================
action        side-effect
============  =====================================================
resolve       (none)
dismiss       if item.type='approval' → close matching escalations
              with resolution='dismissed'
approve       close matching ``RunEscalation`` rows with
              resolution='approved' (best-effort; see notes on
              schema gap below)
reject        close matching escalations with resolution='rejected'
answer        write ``answer_text`` / ``status='answered'`` /
              ``answered_by_user_id`` / ``answered_at`` back to the
              legacy ``clarifications`` row when the inbox item has
              ``source_table='clarifications'`` and ``source_id`` is
              non-NULL
accept        write ``decision='accepted'`` / ``decided_by_user_id``
              / ``decided_at`` back to the legacy ``improvements``
              row (same source-pointer guard as ``answer``)
retry         persist a durable retry signal in
              ``inbox_items.payload.retry_request`` so the next
              pipeline tick can pick it up — the dispatcher worker
              itself is out of scope for P2-09
acknowledge   (none)
============  =====================================================

**RunEscalation schema gap.** :class:`RunEscalation` does not yet
carry a ``resolved_at`` / ``resolution`` / ``resolved_by_user_id``
column (see the model — only ``id``, ``run_id``, ``inbox_item_id``,
``escalation_reason``, ``created_at`` are present). Until the
follow-up migration lands, :func:`_close_run_escalations` identifies
matching rows but cannot mutate them; it logs a WARNING with a
``TODO`` marker, still records the matches in :class:`SideEffectReport`
for observability, and writes the audit-trail entries so ops can
reconcile once the columns exist. Matching uses the only available
linkage today — ``RunEscalation.inbox_item_id == item.id``, plus a
join via ``inbox_items.run_id + play_key`` so multi-step approval
gates that emitted multiple escalations under the same run + play
all close together (planning §7 contract).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.app.db.models.agent_surface import Clarification, Improvement
from backend.app.db.models.inbox import (
    InboxItem,
    InboxItemEvent,
    RunEscalation,
)
from backend.app.db.models.tenancy import AuditLog


logger = logging.getLogger(__name__)


# Audit-log action prefix shared by every side-effect kind so ops can
# rg ``inbox.side_effect.`` to find every best-effort writeback row.
_AUDIT_ACTION_PREFIX = "inbox.side_effect."

# Inbox-item-event ``action`` strings the audit timeline grows when a
# side-effect fires. Kept short — :attr:`InboxItemEvent.action` is
# ``String(32)``.
_EVENT_ESCALATION_CLOSED = "escalation_closed"
_EVENT_LEGACY_WRITEBACK = "legacy_writeback"
_EVENT_RETRY_REQUESTED = "retry_requested"


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class SideEffectReport:
    """What the side-effect dispatcher actually did, for observability.

    Intentionally NOT serialised on the API response — the
    :class:`InboxItemDetail` shape is locked. The route logs a
    one-line summary from this report at INFO and per-failure
    WARNINGs so an alert pipeline can fire on regressions without
    leaking internal state to the client.
    """

    legacy_writebacks: list[uuid.UUID] = field(default_factory=list)
    """``source_id`` of legacy rows we patched (clarification/improvement)."""

    escalations_closed: list[uuid.UUID] = field(default_factory=list)
    """``RunEscalation.id`` rows we identified as closeable."""

    retry_requests_recorded: list[uuid.UUID] = field(default_factory=list)
    """``InboxItem.id`` of items we stamped with ``retry_request``."""

    failures: list[dict] = field(default_factory=list)
    """``[{kind, error}, ...]`` — one entry per swallowed exception."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Tz-aware ``datetime.now()`` — extracted for monkeypatch hooks in tests."""
    return datetime.now(timezone.utc)


def _record_failure(
    report: SideEffectReport,
    *,
    kind: str,
    item: InboxItem,
    exc: Exception,
) -> None:
    """Append a failure entry + log WARNING with full context.

    Side-effect failures are best-effort: the disposition itself
    already succeeded by the time this fires, so we never raise.
    The report carries enough detail for an alert handler to file
    a follow-up.
    """
    logger.warning(
        "inbox side-effect %s failed for item=%s (workspace=%s, "
        "type=%s, run=%s, play_key=%s): %s",
        kind,
        item.id,
        item.workspace_id,
        item.type,
        item.run_id,
        item.play_key,
        exc,
    )
    report.failures.append({"kind": kind, "error": str(exc)})


def _add_audit(
    session: AsyncSession,
    *,
    item: InboxItem,
    actor_user_id: uuid.UUID,
    suffix: str,
    payload: dict,
) -> None:
    """Append an :class:`AuditLog` row for the side-effect.

    ``suffix`` is appended to :data:`_AUDIT_ACTION_PREFIX` to form
    e.g. ``inbox.side_effect.escalation_close``. ``actor_token_id``
    is intentionally ``None`` here — the disposition route already
    stamped its own ``inbox.disposition.<action>`` audit row with
    the originating token; side-effect rows are downstream of that
    and don't need to re-attribute.
    """
    session.add(
        AuditLog(
            workspace_id=item.workspace_id,
            actor_user_id=actor_user_id,
            actor_token_id=None,
            action=f"{_AUDIT_ACTION_PREFIX}{suffix}",
            target_kind="inbox_item",
            target_id=str(item.id),
            payload=payload,
        )
    )


def _add_event(
    session: AsyncSession,
    *,
    item: InboxItem,
    actor_user_id: uuid.UUID,
    action: str,
    payload: dict,
) -> None:
    """Append an :class:`InboxItemEvent` row to the item's timeline."""
    session.add(
        InboxItemEvent(
            item_id=item.id,
            actor_user_id=actor_user_id,
            actor_kind="user",
            action=action,
            payload=payload,
        )
    )


# ---------------------------------------------------------------------------
# RunEscalation closing
# ---------------------------------------------------------------------------


# Columns the future migration is expected to add to ``run_escalations``
# so the close becomes a real mutation. Until they exist we treat the
# "close" as a marker-only operation (audit + event) and log a TODO.
_ESCALATION_RESOLUTION_COLUMNS: tuple[str, ...] = (
    "resolved_at",
    "resolution",
    "resolved_by_user_id",
)


def _escalation_columns_present() -> bool:
    """Return ``True`` once the schema grows the resolution columns.

    Detected via :func:`hasattr` on the model rather than an
    Alembic version check so a partially-applied migration in a
    dev sandbox can't silently mismatch the Python side.
    """
    return all(
        hasattr(RunEscalation, col) for col in _ESCALATION_RESOLUTION_COLUMNS
    )


async def _find_matching_escalations(
    session: AsyncSession, item: InboxItem
) -> list[RunEscalation]:
    """Return every escalation row planning §7 says we should close.

    The contract is "match by ``(workspace_id, run_id, play_key)``"
    but the model carries neither ``workspace_id`` nor ``play_key``.
    We approximate via the available linkages:

    * Direct: ``inbox_item_id == item.id`` (the FK on the model).
    * Sibling: when ``item.run_id`` AND ``item.play_key`` are both
      set, JOIN ``inbox_items`` on ``RunEscalation.inbox_item_id``
      and include rows that share the run + play key. This catches
      multi-step approval gates that emitted multiple escalations
      under the same run for the same play (planning §7 — "close
      ALL of them").

    Results are de-duplicated by ``RunEscalation.id``.
    """
    matches: dict[uuid.UUID, RunEscalation] = {}

    direct_stmt = select(RunEscalation).where(
        RunEscalation.inbox_item_id == item.id
    )
    for esc in (await session.execute(direct_stmt)).scalars():
        matches[esc.id] = esc

    if item.run_id is not None and item.play_key is not None:
        sibling_stmt = (
            select(RunEscalation)
            .join(InboxItem, InboxItem.id == RunEscalation.inbox_item_id)
            .where(
                RunEscalation.run_id == item.run_id,
                InboxItem.workspace_id == item.workspace_id,
                InboxItem.play_key == item.play_key,
            )
        )
        for esc in (await session.execute(sibling_stmt)).scalars():
            matches[esc.id] = esc

    return list(matches.values())


async def _close_run_escalations(
    session: AsyncSession,
    item: InboxItem,
    resolution: str,
    actor_user_id: uuid.UUID,
    *,
    report: SideEffectReport,
) -> list[uuid.UUID]:
    """Close every :class:`RunEscalation` row matching ``item``.

    "Close" today is best-effort: if the model has the resolution
    columns we set them; otherwise we log a TODO + warning and
    treat the operation as marker-only (the audit + event rows
    still go in so ops can correlate later). Matching rows that
    were already resolved are skipped.

    Returns the list of escalation ids that were *identified* as
    closeable (not necessarily mutated — see schema-gap note in
    the module docstring).
    """
    try:
        matches = await _find_matching_escalations(session, item)
    except Exception as exc:  # noqa: BLE001 — best-effort.
        _record_failure(
            report, kind="escalation_close_lookup", item=item, exc=exc
        )
        return []

    if not matches:
        return []

    closed_ids: list[uuid.UUID] = []
    columns_present = _escalation_columns_present()
    if not columns_present:
        # TODO(rfc-0010): once ``run_escalations`` grows
        # ``resolved_at`` / ``resolution`` / ``resolved_by_user_id``
        # the close becomes a real UPDATE. Until then we emit the
        # audit trail so ops can reconcile and we don't lose the
        # signal.
        logger.warning(
            "inbox side-effect: RunEscalation has no resolution "
            "columns (resolved_at/resolution/resolved_by_user_id); "
            "treating close as marker-only for item=%s "
            "(matched %d escalation row(s))",
            item.id,
            len(matches),
        )

    for esc in matches:
        try:
            if columns_present:
                # Skip rows already closed — re-applying the same
                # resolution would clobber the original closer's
                # attribution.
                if getattr(esc, "resolved_at", None) is not None:
                    continue
                setattr(esc, "resolved_at", _now())
                setattr(esc, "resolution", resolution)
                setattr(esc, "resolved_by_user_id", actor_user_id)
            event_payload = {
                "escalation_id": str(esc.id),
                "run_id": str(esc.run_id),
                "resolution": resolution,
                "schema_pending": not columns_present,
            }
            _add_event(
                session,
                item=item,
                actor_user_id=actor_user_id,
                action=_EVENT_ESCALATION_CLOSED,
                payload=event_payload,
            )
            _add_audit(
                session,
                item=item,
                actor_user_id=actor_user_id,
                suffix="escalation_close",
                payload=event_payload,
            )
            closed_ids.append(esc.id)
        except Exception as exc:  # noqa: BLE001 — best-effort, per row.
            _record_failure(
                report,
                kind="escalation_close",
                item=item,
                exc=exc,
            )

    return closed_ids


# ---------------------------------------------------------------------------
# Legacy writebacks
# ---------------------------------------------------------------------------


async def _writeback_clarification_answer(
    session: AsyncSession,
    item: InboxItem,
    payload: dict,
    actor_user_id: uuid.UUID,
    *,
    report: SideEffectReport,
) -> uuid.UUID | None:
    """Patch the legacy ``clarifications`` row for an ``answer`` disposition.

    Mirrors :func:`backend.app.api.v1.routes.clarifications.update_clarification`
    column-for-column (``answer_text``, ``status='answered'``,
    ``answered_by_user_id``, ``answered_at``) so the legacy
    ``/clarifications`` UI sees the answer the moment the inbox UI
    submits it. We deliberately do NOT call
    :func:`mirror_clarification_resolve` here — that runs the OTHER
    direction (legacy → inbox) and would no-op against the same
    row we're holding.

    Returns the legacy row id we patched, or ``None`` when the
    item has no legacy linkage / the row no longer exists / the
    writeback was swallowed by an exception.
    """
    if item.source_table != "clarifications" or item.source_id is None:
        return None

    answer_text = payload.get("answer")
    if not answer_text:
        # The route validator already 422s missing answers, so this
        # branch is defence-in-depth — never expected at runtime.
        return None

    try:
        row = (
            await session.execute(
                select(Clarification).where(Clarification.id == item.source_id)
            )
        ).scalar_one_or_none()
        if row is None:
            logger.warning(
                "inbox side-effect: clarification %s referenced by inbox "
                "item %s no longer exists (weak FK by design)",
                item.source_id,
                item.id,
            )
            return None

        now = _now()
        row.answer = str(answer_text)
        row.status = "answered"
        row.answered_by_user_id = actor_user_id
        row.answered_at = now

        event_payload = {
            "legacy_table": "clarifications",
            "legacy_row_id": str(row.id),
            "fields": ["answer", "status", "answered_by_user_id", "answered_at"],
        }
        _add_event(
            session,
            item=item,
            actor_user_id=actor_user_id,
            action=_EVENT_LEGACY_WRITEBACK,
            payload=event_payload,
        )
        _add_audit(
            session,
            item=item,
            actor_user_id=actor_user_id,
            suffix="legacy_answer_writeback",
            payload=event_payload,
        )
        return row.id
    except Exception as exc:  # noqa: BLE001 — best-effort.
        _record_failure(
            report, kind="legacy_answer_writeback", item=item, exc=exc
        )
        return None


async def _writeback_improvement_accept(
    session: AsyncSession,
    item: InboxItem,
    actor_user_id: uuid.UUID,
    *,
    report: SideEffectReport,
) -> uuid.UUID | None:
    """Patch the legacy ``improvements`` row for an ``accept`` disposition.

    Mirrors :func:`backend.app.api.v1.routes.improvements.update_improvement`'s
    accept branch (``decision='accepted'`` / ``decided_by_user_id``
    / ``decided_at``). Same direction-of-mirror caveat as
    :func:`_writeback_clarification_answer` — we patch the row
    directly rather than going through ``dual_write``.
    """
    if item.source_table != "improvements" or item.source_id is None:
        return None

    try:
        row = (
            await session.execute(
                select(Improvement).where(Improvement.id == item.source_id)
            )
        ).scalar_one_or_none()
        if row is None:
            logger.warning(
                "inbox side-effect: improvement %s referenced by inbox "
                "item %s no longer exists (weak FK by design)",
                item.source_id,
                item.id,
            )
            return None

        now = _now()
        row.decision = "accepted"
        row.decided_by_user_id = actor_user_id
        row.decided_at = now

        event_payload = {
            "legacy_table": "improvements",
            "legacy_row_id": str(row.id),
            "fields": ["decision", "decided_by_user_id", "decided_at"],
        }
        _add_event(
            session,
            item=item,
            actor_user_id=actor_user_id,
            action=_EVENT_LEGACY_WRITEBACK,
            payload=event_payload,
        )
        _add_audit(
            session,
            item=item,
            actor_user_id=actor_user_id,
            suffix="legacy_accept_writeback",
            payload=event_payload,
        )
        return row.id
    except Exception as exc:  # noqa: BLE001 — best-effort.
        _record_failure(
            report, kind="legacy_accept_writeback", item=item, exc=exc
        )
        return None


# ---------------------------------------------------------------------------
# Retry signal
# ---------------------------------------------------------------------------


async def _record_retry_request(
    session: AsyncSession,
    item: InboxItem,
    actor_user_id: uuid.UUID,
    *,
    report: SideEffectReport,
) -> bool:
    """Persist a durable ``retry_request`` signal on ``inbox_items.payload``.

    The actual retry dispatcher worker is explicitly out of scope
    for P2-09 (planning defers it). We just write the durable
    signal so it's never lost and emit a ``retry_requested``
    timeline event so the audit trail records the intent.

    SQLAlchemy doesn't track in-place mutations of JSONB columns,
    so we replace ``item.payload`` wholesale and call
    :func:`flag_modified` for belt-and-braces.
    """
    try:
        now = _now()
        signal = {
            "requested_at": now.isoformat(),
            "requested_by_user_id": str(actor_user_id),
            "run_id": str(item.run_id) if item.run_id else None,
            "play_key": item.play_key,
        }
        item.payload = {**(item.payload or {}), "retry_request": signal}
        flag_modified(item, "payload")

        event_payload = {
            "retry_request": signal,
        }
        _add_event(
            session,
            item=item,
            actor_user_id=actor_user_id,
            action=_EVENT_RETRY_REQUESTED,
            payload=event_payload,
        )
        _add_audit(
            session,
            item=item,
            actor_user_id=actor_user_id,
            suffix="retry_request_recorded",
            payload=event_payload,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort.
        _record_failure(
            report, kind="retry_request_recorded", item=item, exc=exc
        )
        return False


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


async def apply_side_effects(
    session: AsyncSession,
    *,
    item: InboxItem,
    action: str,
    payload: dict,
    actor_user_id: uuid.UUID,
) -> SideEffectReport:
    """Run every side-effect implied by ``action`` against ``item``.

    Called from the disposition route AFTER the inbox row has
    transitioned to its terminal state and the canonical
    ``resolved`` / ``dismissed`` event + ``inbox.disposition.<action>``
    audit log have been queued. Type / status guards are assumed
    pre-validated by ``_validate_disposition`` — by the time we
    run, the (action, type, status) tuple is guaranteed legal.

    Best-effort: every helper catches at the boundary, logs a
    WARNING, appends a ``{kind, error}`` entry to
    :attr:`SideEffectReport.failures`, and returns. The route
    inspects the report for observability; it never raises.
    """
    report = SideEffectReport()

    # Each branch is wrapped in a top-level try/except as belt-and-
    # braces. The helpers themselves already swallow exceptions at
    # the boundary, but a future refactor (or a monkeypatched test
    # double) could let one slip through; this guarantees the
    # disposition itself is never sunk by a side-effect bug.
    try:
        if action == "approve":
            report.escalations_closed.extend(
                await _close_run_escalations(
                    session, item, "approved", actor_user_id, report=report
                )
            )
        elif action == "reject":
            report.escalations_closed.extend(
                await _close_run_escalations(
                    session, item, "rejected", actor_user_id, report=report
                )
            )
        elif action == "dismiss":
            # Approvals close their escalation linkage on dismiss
            # too — an approval gate that's dismissed is functionally
            # a rejection of the gate. Other types have no escalation
            # rows to close (intake only writes them for approvals
            # today).
            if item.type == "approval":
                report.escalations_closed.extend(
                    await _close_run_escalations(
                        session,
                        item,
                        "dismissed",
                        actor_user_id,
                        report=report,
                    )
                )
        elif action == "answer":
            wb_id = await _writeback_clarification_answer(
                session, item, payload, actor_user_id, report=report
            )
            if wb_id is not None:
                report.legacy_writebacks.append(wb_id)
        elif action == "accept":
            wb_id = await _writeback_improvement_accept(
                session, item, actor_user_id, report=report
            )
            if wb_id is not None:
                report.legacy_writebacks.append(wb_id)
        elif action == "retry":
            if await _record_retry_request(
                session, item, actor_user_id, report=report
            ):
                report.retry_requests_recorded.append(item.id)
        # ``resolve`` and ``acknowledge`` have no side-effects beyond
        # the inbox row mutation the route already performed.
    except Exception as exc:  # noqa: BLE001 — last-line defence.
        _record_failure(report, kind=f"dispatch:{action}", item=item, exc=exc)
        # Best-effort: also write an audit row so an alert can fire
        # on the unexpected failure even if the helper-level audit
        # never got queued.
        try:
            _add_audit(
                session,
                item=item,
                actor_user_id=actor_user_id,
                suffix="dispatch_failed",
                payload={"action": action, "error": str(exc)},
            )
        except Exception:  # noqa: BLE001 — never raise from the dispatcher.
            logger.warning(
                "inbox side-effect: failed to record dispatch_failed "
                "audit row for item=%s action=%s",
                item.id,
                action,
            )

    return report


__all__ = [
    "SideEffectReport",
    "apply_side_effects",
]
