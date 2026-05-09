"""Picker ``needs:intake`` priority-gate escape hatch.

Reviewer-shaped routines (qa-reviewer, security-reviewer, retro,
learning-capture) auto-file coverage tickets that need to enter the
SDLC chain via ``task_intake`` regardless of the parent project's
priority bucket. The picker treats the literal ``needs:intake`` label
as an exemption from the project-priority gate — same shape as the
planning-anchor exempt — so newly-created reviewer tickets don't sit
in Drafts/Parked projects emitting ``priority_skipped`` audit noise
every cron tick.

Match is intentionally exact + case-sensitive: the routines mint the
literal namespaced label, and arbitrary near-misses
(``needs-intake``, ``intake``, ``Needs:Intake``) shouldn't accidentally
bypass the gate.
"""

from __future__ import annotations

from backend.app.api.v1.routes.agent_runs import _has_intake_label


def test_intake_label_present_returns_true() -> None:
    assert _has_intake_label(["needs:intake"]) is True


def test_intake_label_alongside_other_labels() -> None:
    """Other labels travel with the ticket; presence anywhere counts."""
    assert (
        _has_intake_label(
            ["source:qa-reviewer", "audit:auto", "needs:intake", "qa-debt"]
        )
        is True
    )


def test_no_intake_label_returns_false() -> None:
    assert _has_intake_label(["source:qa-reviewer", "qa-debt"]) is False


def test_empty_labels_returns_false() -> None:
    assert _has_intake_label([]) is False


def test_case_variant_does_not_match() -> None:
    """Reviewer routines mint the literal — operator-renamed labels
    are not auto-intake escape hatches."""
    assert _has_intake_label(["Needs:Intake"]) is False
    assert _has_intake_label(["NEEDS:INTAKE"]) is False


def test_near_miss_strings_do_not_match() -> None:
    """Suffix / prefix variants are unrelated free-form labels."""
    assert _has_intake_label(["needs:intake-priority"]) is False
    assert _has_intake_label(["needs-intake"]) is False
    assert _has_intake_label(["intake"]) is False


def test_non_string_entries_skip_silently() -> None:
    """Garbage in the labels array doesn't crash the matcher."""
    assert (
        _has_intake_label(  # type: ignore[list-item]
            [None, 42, "", "needs:intake"]
        )
        is True
    )
    assert (
        _has_intake_label(  # type: ignore[list-item]
            [None, 42, ""]
        )
        is False
    )
