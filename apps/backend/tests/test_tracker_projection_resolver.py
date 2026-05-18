"""Unit tests for :mod:`backend.app.services.tracker_projection_resolver`."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.canonical_projection import (
    CANONICAL_STATES,
    TRACKER_OVERLAY,
    default_canonical_to_native,
)
from backend.app.services.tracker_projection_resolver import (
    TrackerStateInfo,
    _resolve_deterministic,
    _resolve_fallback_no_llm,
    merge_with_default,
    resolve_projection,
    validate_mapping,
)


def _states_for_deterministic_pass() -> list[TrackerStateInfo]:
    return [
        TrackerStateInfo(id="b1", name="Backlog", type="backlog"),
        TrackerStateInfo(id="d1", name="Done", type="completed"),
        TrackerStateInfo(id="x1", name="On Hold", type="started"),
    ]


def _states_for_llm_pass() -> list[TrackerStateInfo]:
    """Workflow rows that close deterministic slots plus three started/todo rows."""
    return [
        TrackerStateInfo(id="b1", name="Backlog", type="backlog"),
        TrackerStateInfo(id="d1", name="Done", type="completed"),
        TrackerStateInfo(id="h1", name="Blocked", type="started"),
        TrackerStateInfo(id="t1", name="Todo", type="unstarted"),
        TrackerStateInfo(id="s1", name="In Progress", type="started"),
        TrackerStateInfo(id="r1", name="In Review", type="started"),
    ]


@pytest.mark.asyncio
async def test_resolve_projection_skips_llm_when_deterministic_covers_all_slots() -> None:
    full_deterministic = {
        slot: f"State-{slot}" for slot in CANONICAL_STATES
    }
    full_deterministic["awaiting_input"] = TRACKER_OVERLAY
    full_deterministic["blocked"] = TRACKER_OVERLAY

    client = AsyncMock()
    with patch(
        "backend.app.services.tracker_projection_resolver._resolve_deterministic",
        return_value=full_deterministic,
    ):
        result = await resolve_projection(
            tracker_kind="linear",
            actual_states=_states_for_deterministic_pass(),
            client=client,
        )

    assert result.llm_used is False
    assert result.retries == 0
    assert result.warnings == []
    assert len(result.mapping) == len(CANONICAL_STATES)
    client.acomplete.assert_not_called()


def test_resolve_deterministic_fills_backlog_closed_blocked_and_overlay() -> None:
    mapping = _resolve_deterministic(_states_for_deterministic_pass())

    assert mapping["backlog"] == "Backlog"
    assert mapping["closed"] == "Done"
    assert mapping["blocked"] == "On Hold"
    assert mapping["awaiting_input"] == TRACKER_OVERLAY
    assert "planning" not in mapping
    assert "executing" not in mapping
    assert "reviewing" not in mapping


@pytest.mark.asyncio
async def test_resolve_projection_heuristic_when_client_none() -> None:
    states = [
        TrackerStateInfo(id="t1", name="Todo", type="unstarted"),
        TrackerStateInfo(id="s1", name="In Progress", type="started"),
    ]
    result = await resolve_projection(
        tracker_kind="linear",
        actual_states=states,
        client=None,
    )

    assert result.llm_used is False
    assert any("LLM client unavailable" in w for w in result.warnings)
    assert len(result.mapping) == len(CANONICAL_STATES)
    for slot in CANONICAL_STATES:
        assert slot in result.mapping


@pytest.mark.asyncio
async def test_resolve_projection_uses_llm_for_valid_json_mapping() -> None:
    states = _states_for_llm_pass()
    llm_mapping = {
        "planning": "Todo",
        "executing": "In Progress",
        "reviewing": "In Review",
    }
    client = AsyncMock()
    client.acomplete.return_value = json.dumps({"mapping": llm_mapping})

    result = await resolve_projection(
        tracker_kind="linear",
        actual_states=states,
        client=client,
    )

    assert result.llm_used is True
    assert result.warnings == []
    assert not validate_mapping(result.mapping, {s.name for s in states})
    for slot, value in llm_mapping.items():
        assert result.mapping[slot] == value
    client.acomplete.assert_called()


@pytest.mark.asyncio
async def test_resolve_projection_retries_invalid_llm_then_accepts() -> None:
    states = _states_for_llm_pass()
    client = AsyncMock()
    client.acomplete.side_effect = [
        json.dumps(
            {
                "mapping": {
                    "planning": "Todo",
                    "executing": "Not A Real State",
                    "reviewing": "Also Fake",
                }
            }
        ),
        json.dumps(
            {
                "mapping": {
                    "executing": "In Progress",
                    "reviewing": "In Review",
                }
            }
        ),
    ]

    result = await resolve_projection(
        tracker_kind="linear",
        actual_states=states,
        client=client,
        max_llm_retries=2,
    )

    assert result.llm_used is True
    assert result.mapping["planning"] == "Todo"
    assert result.mapping["executing"] == "In Progress"
    assert result.mapping["reviewing"] == "In Review"
    assert client.acomplete.call_count >= 2


@pytest.mark.asyncio
async def test_resolve_projection_heuristic_after_exhausted_llm_retries() -> None:
    states = [
        TrackerStateInfo(id="t1", name="Todo", type="unstarted"),
        TrackerStateInfo(id="s1", name="In Progress", type="started"),
    ]
    client = AsyncMock()
    client.acomplete.return_value = json.dumps(
        {"mapping": {"planning": "Nope", "executing": "Nope", "reviewing": "Nope"}}
    )

    result = await resolve_projection(
        tracker_kind="linear",
        actual_states=states,
        client=client,
        max_llm_retries=1,
    )

    assert result.llm_used is True
    assert any("heuristic fallback" in w for w in result.warnings)
    assert len(result.mapping) == len(CANONICAL_STATES)
    assert result.mapping["planning"] == "Todo"
    assert result.mapping["executing"] == "In Progress"


def test_validate_mapping_errors() -> None:
    actual = {"Todo", "In Progress"}

    missing = validate_mapping({"backlog": "Todo"}, actual)
    assert any("missing entry" in err for err in missing)

    unknown = validate_mapping(
        {
            "backlog": "Todo",
            "planning": "Todo",
            "executing": "Missing",
            "reviewing": "In Progress",
            "awaiting_input": TRACKER_OVERLAY,
            "blocked": TRACKER_OVERLAY,
            "closed": "Todo",
        },
        actual,
    )
    assert any("not one of the tracker's workflow states" in err for err in unknown)

    overlay_on_executing = validate_mapping(
        {
            "backlog": "Todo",
            "planning": "Todo",
            "executing": TRACKER_OVERLAY,
            "reviewing": "In Progress",
            "awaiting_input": TRACKER_OVERLAY,
            "blocked": TRACKER_OVERLAY,
            "closed": "Todo",
        },
        actual,
    )
    assert any("overlay sentinel is only valid" in err for err in overlay_on_executing)


def test_merge_with_default_prefers_resolved_keys() -> None:
    resolved = {"planning": "Selected", "executing": "Doing"}
    merged = merge_with_default("linear", resolved)

    assert merged["planning"] == "Selected"
    assert merged["executing"] == "Doing"
    assert merged["backlog"] == default_canonical_to_native("linear")["backlog"]


def test_fallback_pins_review_state_above_in_progress_in_list_order() -> None:
    states = [
        TrackerStateInfo(id="r1", name="Code Review", type="started"),
        TrackerStateInfo(id="e1", name="In Progress", type="started"),
    ]
    fallback = _resolve_fallback_no_llm(
        ["planning", "executing", "reviewing"],
        states,
    )

    assert fallback["reviewing"] == "Code Review"
    assert fallback["executing"] == "In Progress"
