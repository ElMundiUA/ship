"""Picker planning-anchor exempt.

Planning anchors live in Drafts projects (``priority.state='planning'``)
by construction — the decomposition FSM (wbs / architecture / qa_plan /
planning_done) is what graduates Drafts → Parked. The picker therefore
bypasses the project-priority gate for any row carrying the
``planning:anchor`` label; without the exempt the decomposition routines
would deadlock on their own gate.

The matcher is intentionally exact + case-sensitive — the Linear adapter
mints the literal label string, and any near-match is operator drift
the picker should treat as a regular ticket.
"""

from __future__ import annotations

from backend.app.api.v1.routes.agent_runs import _is_planning_anchor


def test_anchor_label_present_returns_true() -> None:
    assert _is_planning_anchor(["planning:anchor"]) is True


def test_anchor_label_alongside_stage_labels_returns_true() -> None:
    """Stage labels travel with the anchor through FSM transitions."""
    assert (
        _is_planning_anchor(["stage:wbs", "planning:anchor", "p1"]) is True
    )


def test_no_anchor_label_returns_false() -> None:
    assert _is_planning_anchor(["stage:wbs", "p1", "frontend"]) is False


def test_empty_labels_returns_false() -> None:
    assert _is_planning_anchor([]) is False


def test_case_variant_does_not_match() -> None:
    """Adapter mints the literal — operator-renamed labels are not anchors."""
    assert _is_planning_anchor(["Planning:Anchor"]) is False
    assert _is_planning_anchor(["PLANNING:ANCHOR"]) is False


def test_near_miss_strings_do_not_match() -> None:
    """Suffix / prefix variants are unrelated free-form labels."""
    assert _is_planning_anchor(["planning:anchor-v2"]) is False
    assert _is_planning_anchor(["x-planning:anchor"]) is False


def test_non_string_entries_skip_silently() -> None:
    """Garbage in the labels array doesn't crash the matcher."""
    assert (
        _is_planning_anchor(  # type: ignore[list-item]
            [None, 42, "", "planning:anchor"]
        )
        is True
    )
    assert (
        _is_planning_anchor(  # type: ignore[list-item]
            [None, 42, ""]
        )
        is False
    )
