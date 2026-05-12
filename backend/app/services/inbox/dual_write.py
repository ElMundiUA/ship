"""Legacy → Inbox dual-write helpers (RFC-0010 P2-08).

Phase 2 of the Inbox redesign keeps ``clarifications`` /
``improvements`` as the source of truth for the legacy UI but mirrors
every NEW row (and every terminal-state PATCH) into ``inbox_items``
so the unified Inbox surface stays in sync from day one of the
cutover. The 0032 backfill migration captured pre-cutover history;
this module wires the GOING-FORWARD writes via the legacy create /
update routes plus the tracker projection cron.

**Never blocks the legacy path.** Every helper wraps its DB work in
a broad ``try/except`` that logs at WARNING and swallows the failure
— the legacy row still commits, and the unwritten mirror is
reconcilable later via a sweeper (TODO: tracking ticket placeholder
``RFC-0010-P2-12`` for the sweeper). Callers should never branch on
the return value other than to log it.

**Marker convention for distinguishing dual-write events from
real-intake events.** :func:`backend.app.services.inbox.intake.emit_legacy_record`
already stamps the create event payload with
``reason='legacy_mirror'``; we keep that marker for create rows.
The resolution mirror writes a separate event with
``reason='legacy_writeback'`` so the audit timeline can answer "was
this row resolved via the legacy PATCH or via the new Inbox UI?".

**Mirror lookup may legitimately miss.** Pre-cutover legacy rows
that were never backfilled, or rows whose dual-write was swallowed
on create, will not have a mirror row when their resolve PATCH
fires. That's expected — :func:`mirror_clarification_resolve` /
:func:`mirror_improvement_resolve` log INFO and return ``False``
(best-effort: the legacy row is the source of truth).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_surface import Clarification, Improvement
from backend.app.db.models.inbox import InboxItem, InboxItemEvent
from backend.app.services.inbox.intake import (
    RunSummaryFinding,
    emit_legacy_record,
)


logger = logging.getLogger(__name__)


# Marker the audit-trail consumer can grep for to distinguish a
# resolution mirror from a real intake event.
_RESOLVE_EVENT_REASON = "legacy_writeback"

# Snooze window for ``improvement.deferred`` legacy decisions —
# mirrors the implicit "look again next week" semantics encoded in
# the 0032 backfill migration's deferred branch.
_DEFERRED_SNOOZE_DAYS = 7


# ---------------------------------------------------------------------------
# Mirror lookup
# ---------------------------------------------------------------------------


async def _find_mirror_item(
    session: AsyncSession,
    *,
    source_table: str,
    source_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> InboxItem | None:
    """Return the existing inbox mirror for a legacy row, if any.

    Workspace-scoped on purpose (paranoid but cheap): legacy
    primary-key collisions across workspaces are extraordinarily
    unlikely with UUIDv4, but a stray match would hand a tenant's
    PATCH the wrong inbox row. The triple key keeps that safe.
    """
    stmt = (
        select(InboxItem)
        .where(
            InboxItem.source_table == source_table,
            InboxItem.source_id == source_id,
            InboxItem.workspace_id == workspace_id,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Create-side mirrors
# ---------------------------------------------------------------------------


def _build_clarification_finding(
    clarification: Clarification,
) -> RunSummaryFinding:
    """Build the :class:`RunSummaryFinding` for a clarification mirror.

    Mirrors the column → payload mapping the 0032 backfill migration
    uses so a legacy row created before P2-08 looks identical to one
    mirrored after the cutover. Title is the question (clipped by
    the intake's ``_truncate``); summary is the full question text;
    payload carries the ticket / tracker breadcrumbs.
    """
    question = clarification.question or "(empty question)"
    payload = {
        "ticket_ref": clarification.ticket_ref,
        "context": clarification.context or {},
        "source": clarification.source,
        "tracker_provider": clarification.tracker_provider,
        "tracker_issue_key": clarification.tracker_issue_key,
        "tracker_issue_url": clarification.tracker_issue_url,
    }
    return RunSummaryFinding(
        type="clarification",
        title=question,
        summary=question,
        payload=payload,
    )


def _build_improvement_finding(improvement: Improvement) -> RunSummaryFinding:
    """Build the :class:`RunSummaryFinding` for an improvement mirror.

    Same backfill-mapping intent as :func:`_build_clarification_finding`:
    the inbox row's title is the improvement title, summary is the
    body, payload carries the kind / impact / effort / context bag.
    Keeps the inbox UI a faithful read-through of the legacy row.
    """
    payload = {
        "kind": improvement.kind,
        "impact": improvement.impact,
        "effort": improvement.effort,
        "decision_reason": improvement.decision_reason,
        "next_action_url": improvement.next_action_url,
        "context": improvement.context or {},
    }
    return RunSummaryFinding(
        type="improvement",
        title=improvement.title or "(untitled)",
        summary=improvement.body or improvement.title or "",
        payload=payload,
    )


async def mirror_clarification_create(
    session: AsyncSession,
    *,
    clarification: Clarification,
    actor_handle: str = "requested_by",
    actor_user_id: uuid.UUID | None = None,
    actor_kind: str = "user",
) -> uuid.UUID | None:
    """Insert a mirror inbox_item for a NEW clarification row.

    Never raises: a logged WARNING is the only signal a caller gets
    on failure (return value of ``None``). The legacy row continues
    to commit regardless. ``actor_handle`` is the routing handle the
    intake service uses to pick an owner; ``actor_user_id`` and
    ``actor_kind`` are accepted for API symmetry with the resolve
    helpers but the create event is always written by intake itself
    (with ``actor_kind='system'``) so we don't double-stamp it.

    Idempotent on retry: a SELECT pre-check returns the existing
    mirror's id rather than inserting a second row. Returns the
    mirror item id on success / pre-existing match, ``None`` on
    swallowed failure or when intake declined to materialise a row.
    """
    del actor_user_id, actor_kind  # See docstring; symmetric API.
    try:
        existing = await _find_mirror_item(
            session,
            source_table="clarifications",
            source_id=clarification.id,
            workspace_id=clarification.workspace_id,
        )
        if existing is not None:
            return existing.id

        finding = _build_clarification_finding(clarification)
        item_id = await emit_legacy_record(
            session,
            workspace_id=clarification.workspace_id,
            repo_id=clarification.repo_id,
            run_id=clarification.pipeline_run_id,
            source_table="clarifications",
            source_id=clarification.id,
            finding=finding,
            handle=actor_handle,
        )
        # Flush so the caller sees the insert in the same
        # transaction (matches the convention emit_legacy_record uses
        # internally; explicit here for the no-op pre-check path).
        await session.flush()
        return item_id
    except Exception as exc:  # noqa: BLE001 — best-effort mirror.
        logger.warning(
            "inbox dual-write: clarification mirror create failed "
            "(workspace=%s source_id=%s): %s",
            clarification.workspace_id,
            clarification.id,
            exc,
        )
        return None


async def mirror_improvement_create(
    session: AsyncSession,
    *,
    improvement: Improvement,
    actor_handle: str = "repo_maintainer",
) -> uuid.UUID | None:
    """Insert a mirror inbox_item for a NEW improvement row.

    Same never-raises / idempotent contract as
    :func:`mirror_clarification_create`. ``actor_handle`` defaults
    to ``repo_maintainer`` so improvements route to the repo's
    maintainer/admin/owner via the routing service's built-in
    fallback chain (planning §6) — there is no legacy "requester"
    column on improvements to seed a more specific handle from.
    """
    try:
        existing = await _find_mirror_item(
            session,
            source_table="improvements",
            source_id=improvement.id,
            workspace_id=improvement.workspace_id,
        )
        if existing is not None:
            return existing.id

        finding = _build_improvement_finding(improvement)
        item_id = await emit_legacy_record(
            session,
            workspace_id=improvement.workspace_id,
            repo_id=improvement.repo_id,
            run_id=improvement.pipeline_run_id,
            source_table="improvements",
            source_id=improvement.id,
            finding=finding,
            handle=actor_handle,
        )
        await session.flush()
        return item_id
    except Exception as exc:  # noqa: BLE001 — best-effort mirror.
        logger.warning(
            "inbox dual-write: improvement mirror create failed "
            "(workspace=%s source_id=%s): %s",
            improvement.workspace_id,
            improvement.id,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Resolve-side mirrors
# ---------------------------------------------------------------------------


def _map_clarification_status(
    clarification: Clarification,
) -> tuple[str | None, str | None]:
    """Map ``clarifications.status`` to ``(inbox.status, inbox.resolution)``.

    Mirrors the 0032 backfill mapping for terminal states; ``open``
    returns ``(None, None)`` so the caller knows there is nothing to
    write back (re-open is intentionally not propagated; planning
    §"Resolution mapping" — the inbox UI doesn't support re-opening
    a resolved row in v1).
    """
    src = clarification.status
    if src == "answered":
        return "resolved", "answered"
    if src == "skipped":
        return "dismissed", "dismissed"
    if src == "stale":
        return "dismissed", "dismissed"
    return None, None


def _map_improvement_decision(
    improvement: Improvement,
) -> tuple[str | None, str | None, datetime | None]:
    """Map ``improvements.decision`` to inbox ``(status, resolution, snoozed_until)``.

    ``deferred`` produces ``('snoozed', None, decided_at + 7d)`` —
    the snooze window mirrors the 0032 backfill semantics so the
    legacy "look again next week" intent stays intact across both
    create paths and the legacy PATCH path. Non-terminal decisions
    (``pending``) return all-``None`` so the caller skips writeback.
    """
    src = improvement.decision
    if src == "accepted":
        return "resolved", "accepted", None
    if src == "declined":
        return "dismissed", "dismissed", None
    if src == "deferred":
        snoozed_until: datetime | None = None
        if improvement.decided_at is not None:
            snoozed_until = improvement.decided_at + timedelta(
                days=_DEFERRED_SNOOZE_DAYS
            )
        return "snoozed", None, snoozed_until
    return None, None, None


def _resolve_state_already_matches(
    item: InboxItem,
    *,
    status: str,
    resolution: str | None,
    snoozed_until: datetime | None = None,
) -> bool:
    """Return True when the mirror already encodes the desired terminal state.

    Used as the idempotency gate so a re-PATCH to the same legacy
    state doesn't append a duplicate ``resolved`` event. We compare
    the small set of fields we'd otherwise mutate; nothing else.
    """
    if item.status != status:
        return False
    if item.resolution != resolution:
        return False
    if snoozed_until is not None and item.snoozed_until != snoozed_until:
        return False
    return True


async def mirror_clarification_resolve(
    session: AsyncSession,
    *,
    clarification: Clarification,
    actor_user_id: uuid.UUID | None,
    actor_kind: str = "user",
) -> bool:
    """Update the mirror inbox_item to match a resolved/skipped legacy row.

    Returns ``True`` when a mirror was found and is now in the
    target state (idempotent — second call on the same row is a
    no-op). Returns ``False`` and logs INFO when no mirror exists
    (expected for pre-cutover rows that were never backfilled).
    Never raises: any DB failure is logged at WARNING and swallowed
    so the legacy PATCH still commits.
    """
    try:
        target_status, target_resolution = _map_clarification_status(
            clarification
        )
        if target_status is None:
            # Re-open / unsupported transition — nothing to write.
            logger.info(
                "inbox dual-write: clarification resolve skipped "
                "(workspace=%s source_id=%s status=%s — no mirror update)",
                clarification.workspace_id,
                clarification.id,
                clarification.status,
            )
            return False

        item = await _find_mirror_item(
            session,
            source_table="clarifications",
            source_id=clarification.id,
            workspace_id=clarification.workspace_id,
        )
        if item is None:
            logger.info(
                "inbox dual-write: no mirror for clarification "
                "(workspace=%s source_id=%s) — pre-cutover row",
                clarification.workspace_id,
                clarification.id,
            )
            return False

        if _resolve_state_already_matches(
            item, status=target_status, resolution=target_resolution
        ):
            return True

        now = datetime.now(timezone.utc)
        item.status = target_status
        item.resolution = target_resolution
        item.resolved_at = clarification.answered_at or now
        item.resolved_by_user_id = actor_user_id

        event = InboxItemEvent(
            item_id=item.id,
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
            action="resolved",
            payload={
                "reason": _RESOLVE_EVENT_REASON,
                "source_table": "clarifications",
                "to_status": target_status,
                "to_resolution": target_resolution,
                "legacy_status": clarification.status,
            },
        )
        session.add(event)
        await session.flush()
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort mirror.
        logger.warning(
            "inbox dual-write: clarification mirror resolve failed "
            "(workspace=%s source_id=%s): %s",
            clarification.workspace_id,
            clarification.id,
            exc,
        )
        return False


async def mirror_improvement_resolve(
    session: AsyncSession,
    *,
    improvement: Improvement,
    actor_user_id: uuid.UUID | None,
    actor_kind: str = "user",
) -> bool:
    """Update the mirror inbox_item to match an accepted/declined/deferred row.

    Same never-raises / idempotent contract as
    :func:`mirror_clarification_resolve`. ``deferred`` is a special
    case: it transitions the mirror to ``snoozed`` (not resolved)
    with a 7-day snooze window from ``improvement.decided_at`` —
    callers must invoke this AFTER the legacy PATCH stamps
    ``decided_at`` so the snooze window is computed off the current
    decision time, not a stale one.
    """
    try:
        target_status, target_resolution, snoozed_until = (
            _map_improvement_decision(improvement)
        )
        if target_status is None:
            logger.info(
                "inbox dual-write: improvement resolve skipped "
                "(workspace=%s source_id=%s decision=%s — no mirror update)",
                improvement.workspace_id,
                improvement.id,
                improvement.decision,
            )
            return False

        item = await _find_mirror_item(
            session,
            source_table="improvements",
            source_id=improvement.id,
            workspace_id=improvement.workspace_id,
        )
        if item is None:
            logger.info(
                "inbox dual-write: no mirror for improvement "
                "(workspace=%s source_id=%s) — pre-cutover row",
                improvement.workspace_id,
                improvement.id,
            )
            return False

        if _resolve_state_already_matches(
            item,
            status=target_status,
            resolution=target_resolution,
            snoozed_until=snoozed_until,
        ):
            return True

        now = datetime.now(timezone.utc)
        item.status = target_status
        item.resolution = target_resolution
        if target_status in ("resolved", "dismissed"):
            item.resolved_at = improvement.decided_at or now
            item.resolved_by_user_id = actor_user_id
            item.snoozed_until = None
        elif target_status == "snoozed":
            item.snoozed_until = snoozed_until
            item.resolved_at = None
            item.resolved_by_user_id = None

        event = InboxItemEvent(
            item_id=item.id,
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
            action="resolved" if target_status != "snoozed" else "snoozed",
            payload={
                "reason": _RESOLVE_EVENT_REASON,
                "source_table": "improvements",
                "to_status": target_status,
                "to_resolution": target_resolution,
                "legacy_decision": improvement.decision,
            },
        )
        session.add(event)
        await session.flush()
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort mirror.
        logger.warning(
            "inbox dual-write: improvement mirror resolve failed "
            "(workspace=%s source_id=%s): %s",
            improvement.workspace_id,
            improvement.id,
            exc,
        )
        return False


__all__ = [
    "mirror_clarification_create",
    "mirror_clarification_resolve",
    "mirror_improvement_create",
    "mirror_improvement_resolve",
]
