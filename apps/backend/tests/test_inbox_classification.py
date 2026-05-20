"""Unit tests for inbox category / lane derivation (ELS-147)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.lanes import RoutineRun
from backend.app.services.inbox.classification import (
    ACTIONABLE_CATEGORIES,
    category_from_type,
    derive_lane,
    priority_for_item,
)


def _item(
    *,
    type: str = "clarification",
    category: str | None = None,
    status: str = "new",
    created_at: datetime | None = None,
    intake_reason: str | None = None,
    run_id: uuid.UUID | None = None,
) -> InboxItem:
    cat = category if category is not None else category_from_type(type)
    return InboxItem(
        workspace_id=uuid.uuid4(),
        type=type,
        category=cat,
        priority=priority_for_item(category=cat, item_type=type),
        status=status,
        title="t",
        payload={},
        intake_reason=intake_reason,
        run_id=run_id,
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_category_from_type_maps_reports_to_attention() -> None:
    assert category_from_type("report") == "attention"
    assert category_from_type("failure") == "failure"
    assert category_from_type("clarification") == "decision_needed"


def test_refire_capped_failure_is_lane_now() -> None:
    item = _item(type="blocker", category="failure", intake_reason="refire_capped")
    assert derive_lane(item, run=None) == "now"


def test_decision_needed_with_running_run_is_lane_now() -> None:
    item = _item(type="clarification", category="decision_needed", run_id=uuid.uuid4())
    run = RoutineRun(
        routine_id=uuid.uuid4(),
        workspace_id=item.workspace_id,
        trigger="manual",
        status="running",
        outcome={},
        payload={},
    )
    assert derive_lane(item, run=run) == "now"


def test_decision_needed_without_run_is_lane_today() -> None:
    item = _item(type="clarification", category="decision_needed")
    assert derive_lane(item, run=None) == "today"


def test_stale_item_is_lane_whenever() -> None:
    old = datetime.now(timezone.utc) - timedelta(days=8)
    item = _item(type="failure", category="failure", created_at=old)
    assert derive_lane(item, run=None) == "whenever"


def test_actionable_categories_constant() -> None:
    assert ACTIONABLE_CATEGORIES == frozenset({"decision_needed", "failure"})
