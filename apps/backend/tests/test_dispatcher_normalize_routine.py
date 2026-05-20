"""normalize_routine_id — stage label → canonical routine id.

The manual dispatch endpoint names the runner branch
``ship-<routine_id>-<ticket>``. A caller passing a stage label
(``dev_implementation``) instead of the routine (``developer``) forked a
divergent branch + duplicate PR for a ticket already in flight. The
endpoint normalises via this helper so both spellings land on the
canonical branch.
"""

from __future__ import annotations

import pytest

from backend.app.services.dispatcher import normalize_routine_id


@pytest.mark.parametrize(
    "stage,routine",
    [
        ("dev_implementation", "developer"),
        ("code_review", "reviewer"),
        ("auto_merge", "auto-merger"),
        ("pr_review", "reviewer"),
        ("qa_manual", "validation"),
        ("qa_automation", "validation"),
        ("task_intake", "planning"),
        ("wbs", "decomposition"),
    ],
)
def test_stage_label_maps_to_routine(stage: str, routine: str) -> None:
    assert normalize_routine_id(stage) == routine


@pytest.mark.parametrize(
    "routine",
    ["developer", "reviewer", "auto-merger", "planning", "validation", "decomposition"],
)
def test_routine_id_passes_through(routine: str) -> None:
    # Already-canonical routine ids are not keys in the stage map, so
    # they pass through unchanged — the call is idempotent.
    assert normalize_routine_id(routine) == routine


def test_unknown_value_passes_through() -> None:
    assert normalize_routine_id("totally-custom-routine") == "totally-custom-routine"


def test_stages_where_label_equals_routine_are_stable() -> None:
    # validation / planning / decomposition are both a stage label AND
    # the routine id; normalising must be a no-op, not a double-map.
    for v in ("validation", "planning", "decomposition"):
        assert normalize_routine_id(normalize_routine_id(v)) == v
