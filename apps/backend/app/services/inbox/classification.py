"""Inbox category, priority, and lane derivation (ELS-147).

Pure helpers used at intake, list projection, and in tests. Lane rules
mirror the ticket architecture: server-side classification, client-side
lane chip filtering over the returned ``lane`` field.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from backend.app.db.models.inbox import InboxItem
    from backend.app.db.models.lanes import RoutineRun


InboxCategory = Literal["decision_needed", "failure", "attention"]
InboxLane = Literal["now", "today", "whenever"]

ACTIONABLE_CATEGORIES: frozenset[str] = frozenset({"decision_needed", "failure"})

# Align with Console ``stale-badge`` ERR band (7d).
LANE_WHENEVER_AGE_DAYS = 7


def category_from_type(item_type: str) -> InboxCategory:
    """Map legacy ``inbox_items.type`` → triage category."""
    if item_type == "report":
        return "attention"
    if item_type in ("failure", "blocker", "exception"):
        return "failure"
    if item_type in ("clarification", "improvement", "approval", "stuck"):
        return "decision_needed"
    return "decision_needed"


def priority_for_item(*, category: str, item_type: str) -> int:
    if category == "failure" or item_type in ("blocker", "failure"):
        return 10
    if item_type in ("clarification", "approval"):
        return 8
    if item_type == "improvement":
        return 5
    if item_type == "exception":
        return 6
    return 0


def _refire_capped(item: InboxItem) -> bool:
    if item.intake_reason == "refire_capped":
        return True
    payload = item.payload if isinstance(item.payload, dict) else {}
    return bool(payload.get("refire_capped"))


def _run_is_parked(run: RoutineRun | None) -> bool:
    if run is None:
        return False
    outcome = run.outcome if isinstance(run.outcome, dict) else {}
    if outcome.get("parked") is True:
        return True
    payload = run.payload if isinstance(run.payload, dict) else {}
    if payload.get("parked") is True:
        return True
    if run.status == "running" and outcome.get("waiting_on") == "human":
        return True
    return False


def _run_blocks_live(run: RoutineRun | None) -> bool:
    """True when a linked routine run is still active and waiting on a human."""
    if run is None:
        return False
    if _run_is_parked(run):
        return True
    return run.status == "running"


def derive_lane(
    item: InboxItem,
    *,
    run: RoutineRun | None = None,
    now: datetime | None = None,
) -> InboxLane:
    """Compute urgency lane for an open inbox row."""
    clock = now or datetime.now(timezone.utc)
    category = getattr(item, "category", None) or category_from_type(item.type)

    if category == "attention":
        return "whenever"

    created = item.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (clock - created).total_seconds() / 86400.0)

    if item.status == "snoozed" and item.snoozed_until:
        snooze_until = item.snoozed_until
        if snooze_until.tzinfo is None:
            snooze_until = snooze_until.replace(tzinfo=timezone.utc)
        if snooze_until <= clock:
            # Snooze expired — surface in whenever unless younger rules apply.
            if age_days >= LANE_WHENEVER_AGE_DAYS:
                return "whenever"
        else:
            return "whenever"

    if age_days >= LANE_WHENEVER_AGE_DAYS:
        return "whenever"

    if category == "failure" and _refire_capped(item):
        return "now"

    if category == "decision_needed" and _run_blocks_live(run):
        return "now"

    if category in ACTIONABLE_CATEGORIES:
        return "today"

    return "whenever"
