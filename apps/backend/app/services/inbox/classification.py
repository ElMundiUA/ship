"""Inbox category, priority, and lane derivation (ELS-147).

Pure helpers used at intake, list projection, and in tests. Lane rules
mirror the ticket architecture: server-side classification, client-side
lane chip filtering over the returned ``lane`` field.

Inbox Decision UI (ELS-158, 2026-05-18) extends the row payload with
a small structured contract — see :class:`ActionItem` /
:func:`derive_resolution_mode`. Agent finish handlers populate
``payload.action_items`` so the Console renders one-click pills /
checkboxes instead of a free-text textarea every time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from backend.app.db.models.inbox import InboxItem
    from backend.app.db.models.lanes import RoutineRun


InboxCategory = Literal["decision_needed", "failure", "attention"]
InboxLane = Literal["now", "today", "whenever"]

ACTIONABLE_CATEGORIES: frozenset[str] = frozenset(
    {"decision_needed", "failure", "attention"}
)

# ---------------------------------------------------------------------------
# Inbox Decision UI (ELS-158) — structured action_items payload contract.
#
# Lives under ``inbox_items.payload`` as ``action_items: list[ActionItem]``
# plus ``resolution_mode: ResolutionMode``. No new columns; rows without
# these fields fall back to ``freeform_only`` (legacy textarea path).
# ---------------------------------------------------------------------------

ActionItemKind = Literal["choice", "checkbox", "ack", "binary"]
ResolutionMode = Literal[
    "single_choice",   # clarification — pick 1 of N or freeform
    "multi_select",    # retro / improvements — check 0..N + Apply
    "ack_only",        # digest / read-only — Acknowledge button
    "per_item_binary",  # report digest — one primary/secondary pair per item
    "freeform_only",   # legacy fallback — textarea only
]


class ActionItem(BaseModel):
    """One renderable control inside an inbox row.

    Agent's finish handler emits these in ``payload.action_items``. The
    Console renders them as pills (``kind=choice``), checkboxes
    (``kind=checkbox``), or an Acknowledge button (``kind=ack``); the
    /decide endpoint routes side-effects per kind.

    ``target_project_id`` is the Linear project a resulting child
    ticket should land in. The auto-merger leaves it ``None`` (use the
    inbox row's source ticket's project); retro / learning-capture set
    it explicitly per item because each action_item may belong in a
    different project.
    """

    id: str = Field(..., min_length=1, max_length=64)
    kind: ActionItemKind
    label: str = Field(..., min_length=1, max_length=160)
    hint: str | None = Field(default=None, max_length=400)
    secondary_label: str | None = Field(default=None, max_length=160)
    default: bool = False
    target_project_id: str | None = Field(default=None, max_length=64)

    @field_validator("id")
    @classmethod
    def _id_no_whitespace(cls, v: str) -> str:
        if any(ch.isspace() for ch in v):
            raise ValueError("ActionItem.id cannot contain whitespace")
        return v


def parse_action_items(payload: dict[str, Any] | None) -> list[ActionItem]:
    """Best-effort: read ``payload.action_items`` and return parsed
    rows. Skip invalid entries silently — UI / endpoint never crashes
    on a malformed agent emission, just falls through to freeform."""
    if not payload:
        return []
    raw = payload.get("action_items")
    if not isinstance(raw, list):
        return []
    out: list[ActionItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(ActionItem.model_validate(entry))
        except Exception:  # noqa: BLE001 — best-effort; bad row ignored
            continue
    return out


def derive_resolution_mode(
    payload: dict[str, Any] | None,
    item_type: str | None = None,
) -> ResolutionMode:
    """Compute the resolution mode for a row.

    Order of preference:

    1. Explicit ``payload.resolution_mode`` set by the agent — trust it
       if it's a known literal.
    2. Infer from ``action_items`` shape:
       - any ``kind=checkbox`` → ``multi_select``
       - all ``kind=choice`` → ``single_choice``
       - all ``kind=ack`` → ``ack_only``
    3. Empty / unknown → ``freeform_only`` (legacy textarea fallback,
       what the Console rendered pre-ELS-158).

    Pure function; safe to call on every row projection.
    """
    if payload:
        explicit = payload.get("resolution_mode")
        if explicit in (
            "single_choice",
            "multi_select",
            "ack_only",
            "per_item_binary",
            "freeform_only",
        ):
            return explicit  # type: ignore[return-value]

    items = parse_action_items(payload)
    if items:
        kinds = {it.kind for it in items}
        if "binary" in kinds:
            return "per_item_binary"
        if "checkbox" in kinds:
            return "multi_select"
        if kinds == {"choice"}:
            return "single_choice"
        if kinds == {"ack"}:
            return "ack_only"
        # mixed — default to multi_select so the UI shows all of them
        return "multi_select"

    # Daily digest / learning-capture should ideally emit ack_only,
    # but pre-Phase-2 they don't carry any action_items, so
    # ``freeform_only`` is the safer default. P2-3 will change this.
    _ = item_type  # reserved for future per-type defaults
    return "freeform_only"

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
