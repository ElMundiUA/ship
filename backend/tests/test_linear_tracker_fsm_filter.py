"""Linear adapter ``_fsm_filter`` shape (ELS-90 Linear).

Two invariants the picker depends on:

1. **Empty-label tickets pass the own-stage exclusion.** Linear's
   ``IssueLabelCollectionFilter`` treats bare ``{"id": {"nin": ...}}``
   as ``some`` semantics — vacuously false on tickets with no labels.
   We must use the explicit ``none.id.eq`` form so a freshly-created
   ticket without ANY ``stage:*`` label can flow through ``task_intake``.

2. **Asymmetric intake.** ``task_intake`` is the default entry — it
   picks Todo tickets without ``stage:task_intake`` (own-label
   absent), regardless of whether they have other labels. No
   previous-stage label is required because intake has no previous
   stage.

Reproduction case for the bug being fixed: a Linear-native ticket
filed directly by an operator (not via Navigator's ``create_ticket``)
lands in Todo with no Ship labels. With the bare-``nin`` form, the
filter ``{"labels": {"id": {"nin": [intake_label]}}}`` does NOT
match because the ticket has zero labels — Linear's GraphQL
interprets it as "some label exists with id not in [intake_label]",
and "some" of an empty collection is false. The fix uses
``{"labels": {"every": {"id": {"neq": intake_label}}}}``: every
label's id differs from intake_label, which is vacuously true on
the empty label collection. (Linear removed the ``none`` operator
from ``IssueLabelCollectionFilter`` after this fix shipped, so the
implementation switched to the ``every / neq`` shape; the semantic
intent — "ticket lacks this label" — is unchanged.)
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.integrations.linear.tracker_adapter import LinearTracker


def _make_tracker(stage_labels: dict[str, str] | None = None) -> LinearTracker:
    """Build a LinearTracker with provisioner data populated but no
    real network. We don't call any GraphQL methods in these tests;
    we only inspect ``_fsm_filter`` output."""
    return LinearTracker(
        access_token="lin_oauth_x",
        team_id="00000000-0000-0000-0000-team",
        team_key="ELS",
        state_id_by_name={
            "Todo": "todo_state_id",
            "In Progress": "ip_state_id",
            "Review": "rev_state_id",
            "Done": "done_state_id",
        },
        fsm_to_linear_state={
            "task_intake": "Todo",
            "bug_triage": "Todo",
            "ba_requirements": "Todo",
            "tech_arch_plan": "Todo",
            "dev_implementation": "In Progress",
            "qa_manual": "In Progress",
            "pr_review": "Review",
        },
        label_id_by_stage=stage_labels
        or {
            "task_intake": "lbl_intake",
            "bug_triage": "lbl_bug_triage",
            "ba_requirements": "lbl_ba",
            "tech_arch_plan": "lbl_tech_arch",
            "dev_implementation": "lbl_dev",
            "qa_manual": "lbl_qa_manual",
            "pr_review": "lbl_pr_review",
        },
        signal_label_ids={"needs_clarification": "lbl_clar"},
    )


def _find_label_clauses(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in parts if "labels" in p]


def test_intake_filter_excludes_own_label_via_every_neq() -> None:
    """The own-label exclusion must encode "ticket lacks this label"
    in a way that matches the empty label collection too — that's
    the common shape for a freshly-filed Linear-native ticket. We
    use ``every / neq`` because Linear removed the ``none`` operator
    from ``IssueLabelCollectionFilter``."""
    tracker = _make_tracker()
    parts = tracker._fsm_filter("task_intake")  # noqa: SLF001 - shape test

    label_clauses = _find_label_clauses(parts)
    # At minimum: own-stage exclusion + needs:clarification exclusion.
    assert len(label_clauses) == 2

    own = label_clauses[0]
    assert "every" in own["labels"], (
        f"intake's own-label exclusion must use ``every`` operator (got {own!r})"
    )
    assert own["labels"]["every"] == {"id": {"neq": "lbl_intake"}}


def test_intake_filter_has_no_previous_stage_clause() -> None:
    """``task_intake`` is the entry — no previous stage label
    required. The filter must NOT have a ``some`` clause demanding
    some prior label, otherwise Linear-native tickets are stuck."""
    tracker = _make_tracker()
    parts = tracker._fsm_filter("task_intake")
    for p in parts:
        if "labels" in p:
            # ``some`` would mean "previous stage label is present" —
            # that's the bug for entry stages.
            assert "some" not in p["labels"], (
                f"intake filter must not require a ``some`` label "
                f"(got {p!r})"
            )


def test_intake_filter_state_clause_is_todo() -> None:
    tracker = _make_tracker()
    parts = tracker._fsm_filter("task_intake")
    state_parts = [p for p in parts if "state" in p]
    assert len(state_parts) == 1
    assert state_parts[0] == {"state": {"id": {"eq": "todo_state_id"}}}


def test_tech_arch_plan_filter_requires_previous_stage_label() -> None:
    """Subsequent stages still depend on the previous stage's label
    being set (intake adds ``stage:task_intake`` on transition).
    ``ba_requirements`` was folded into ``task_intake``, so the
    first non-entry SDLC stage is now ``tech_arch_plan`` and its
    predecessor is ``task_intake``."""
    tracker = _make_tracker()
    parts = tracker._fsm_filter("tech_arch_plan")
    label_clauses = _find_label_clauses(parts)
    has_some = any("some" in p["labels"] for p in label_clauses)
    assert has_some, (
        "tech_arch_plan must require the previous (task_intake) stage "
        "label via the ``some`` operator"
    )
    # The own-label exclusion must use ``every / neq``.
    has_every = any("every" in p["labels"] for p in label_clauses)
    assert has_every


def test_needs_clarification_exclusion_uses_every_neq_too() -> None:
    """Same empty-collection issue applies to the ``needs:clarification``
    exclusion. A ticket with no labels at all must not be silently
    excluded by it."""
    tracker = _make_tracker()
    parts = tracker._fsm_filter("tech_arch_plan")
    clar_clauses = [
        p
        for p in parts
        if "labels" in p
        and "every" in p["labels"]
        and p["labels"]["every"].get("id", {}).get("neq") == "lbl_clar"
    ]
    assert len(clar_clauses) == 1, (
        "``needs:clarification`` exclusion should be a single ``every.id.neq`` clause"
    )


def test_self_heal_falls_back_to_coarse_open_filter() -> None:
    """``self_heal`` runs out-of-band; ``_fsm_filter`` returns no
    label clauses for it (the caller routes it through ``state="open"``).

    Today this means ``_fsm_filter("self_heal")`` returns an empty
    parts list because there's no entry in ``fsm_to_linear_state`` for
    self_heal — confirm we don't accidentally start filtering by it.
    """
    tracker = _make_tracker()
    parts = tracker._fsm_filter("self_heal")
    # No ``state`` clause and no own-label since FSM_TO_LINEAR_STATE
    # doesn't include self_heal in the test fixture's mapping.
    assert all("state" not in p for p in parts)


def test_missing_own_label_returns_impossible_sentinel() -> None:
    """When the workspace's provisioner didn't mint a label for a
    stage that IS bound to a Linear workflow state, the picker must
    fail-safe to "match nothing" instead of "match every ticket".

    Reproduction of the production loop: bug_triage's stage label is
    not in ``label_id_by_stage``, but bug_triage IS mapped to ``Todo``
    in ``fsm_to_linear_state``. The previous filter shape silently
    dropped the own-label exclusion → picker matched every Todo
    ticket → routine ran every cron tick, burning agent tokens for
    no progress. The new shape adds an impossible-id ``some.id.eq``
    sentinel so the picker returns zero rows until provisioning is
    repaired.
    """
    # Tracker fixture WITHOUT bug_triage in label_id_by_stage but WITH
    # the state mapping (mirrors a workspace where the provisioner
    # ran before bug_triage was added to SHIP_FSM_STAGES).
    tracker = _make_tracker(
        stage_labels={
            # Mirror a pre-fix workspace where bug_triage / qa_automation
            # / code_review were never minted as labels.
            "task_intake": "lbl_intake",
            "ba_requirements": "lbl_ba",
            "tech_arch_plan": "lbl_tech_arch",
            "dev_implementation": "lbl_dev",
            "qa_manual": "lbl_qa_manual",
            "pr_review": "lbl_pr_review",
        }
    )
    parts = tracker._fsm_filter("bug_triage")

    # The state clause still lands.
    state_clauses = [p for p in parts if "state" in p]
    assert len(state_clauses) == 1
    assert (
        state_clauses[0]["state"]["id"]["eq"] == "todo_state_id"
    )

    # The impossible-id sentinel matches nothing — no real label
    # carries this id.
    sentinel_clauses = [
        p
        for p in parts
        if "labels" in p
        and "some" in p["labels"]
        and p["labels"]["some"].get("id", {}).get("eq")
        == "00000000-0000-0000-0000-000000000000"
    ]
    assert len(sentinel_clauses) == 1, (
        "missing-own-label stage must inject an impossible-id sentinel "
        "so the picker matches zero tickets"
    )

    # And crucially, no plain ``every.id.neq`` own-label exclusion —
    # that's what would silently let every ticket through if the
    # provisioner gap was tolerated.
    every_neq_clauses = [
        p
        for p in parts
        if "labels" in p
        and "every" in p["labels"]
        and "neq" in p["labels"]["every"].get("id", {})
    ]
    # ``needs:clarification`` exclusion is one ``every / neq`` clause;
    # the bug_triage own-label is the second that would exist in the
    # healthy case. Here we want exactly one (the clarification one),
    # not two.
    assert len(every_neq_clauses) == 1, (
        "missing-own-label stage must not emit an own-label exclusion "
        "clause; only the needs:clarification one is expected"
    )
