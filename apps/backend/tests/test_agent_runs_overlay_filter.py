"""Picker overlay-label filter (ELS-84).

The picker drops tickets carrying overlay labels that mean the
operator owes a reply: ``needs:clarification`` and the ``blocked``
family (``blocked``, ``blocked-on-acme``, ``blocked:foo``). Match is
case-insensitive; stage labels (``stage:wbs`` / ``stage:wbs-foo``)
must NOT match — they're how the FSM filters tickets *into* the
picker, not freeze overlays.
"""

from __future__ import annotations

from backend.app.api.v1.routes.agent_runs import _matched_overlay_labels


def test_exact_match_needs_clarification() -> None:
    assert _matched_overlay_labels(["needs:clarification"]) == [
        "needs:clarification"
    ]


def test_exact_match_blocked() -> None:
    assert _matched_overlay_labels(["blocked"]) == ["blocked"]


def test_dash_suffix_matches_blocked() -> None:
    """Operator-friendly form: ``blocked-on-acme`` is a freeze too."""
    assert _matched_overlay_labels(["blocked-on-acme"]) == ["blocked-on-acme"]


def test_colon_suffix_matches_blocked() -> None:
    """Namespace form: ``blocked:foo`` is a freeze."""
    assert _matched_overlay_labels(["blocked:foo"]) == ["blocked:foo"]


def test_case_insensitive_match() -> None:
    """Linear permits arbitrary casing on label names."""
    assert _matched_overlay_labels(["BLOCKED", "Needs:Clarification"]) == [
        "BLOCKED",
        "Needs:Clarification",
    ]


def test_stage_label_does_not_match() -> None:
    """``stage:wbs`` is the FSM filter, not a freeze. Picker keeps it."""
    assert _matched_overlay_labels(["stage:wbs", "stage:wbs-anchor"]) == []


def test_unrelated_label_does_not_match() -> None:
    """Random project labels stay invisible to the freeze filter."""
    assert _matched_overlay_labels(["bug", "p1", "frontend"]) == []


def test_mixed_labels_returns_only_freeze_matches() -> None:
    """Non-freeze labels travel with the row; only freeze matches return."""
    matched = _matched_overlay_labels(
        ["stage:wbs", "needs:clarification", "p1", "blocked"]
    )
    assert matched == ["needs:clarification", "blocked"]


def test_empty_or_invalid_inputs_skip_silently() -> None:
    """Garbage input doesn't blow the matcher — empty / non-strings drop."""
    matched = _matched_overlay_labels(["", "  ", None, 42, "blocked"])  # type: ignore[list-item]
    assert matched == ["blocked"]
