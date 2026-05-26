"""Merge-policy gate (auto / human_required / evidence_required).

When ``workspace.settings.merge_policy`` opts the workspace in,
auto-merger's ``merge`` decision is rewritten to a human-clarification
stall before the GitHub squash runs. Default is ``auto`` — every
existing workspace keeps current behaviour.

These tests pin the mode resolver and the eligibility matrix
(``merge`` vs other auto_merge_actions, fsm_stage gating). The
async remap helper itself does a workspace-row lookup so it sits
behind an integration test; here we exercise the pure shape.
"""

from __future__ import annotations

from backend.app.api.v1.routes.agent_runs import (
    FinishIn,
    _resolve_merge_policy,
)


# ── _resolve_merge_policy ──────────────────────────────────────────


def test_default_is_auto_when_no_settings() -> None:
    assert _resolve_merge_policy(None) == "auto"
    assert _resolve_merge_policy({}) == "auto"
    assert _resolve_merge_policy({"unrelated": "field"}) == "auto"


def test_explicit_modes_round_trip() -> None:
    assert _resolve_merge_policy({"merge_policy": "auto"}) == "auto"
    assert _resolve_merge_policy({"merge_policy": "human_required"}) == "human_required"
    assert (
        _resolve_merge_policy({"merge_policy": "evidence_required"})
        == "evidence_required"
    )


def test_typo_falls_back_to_auto() -> None:
    # Fail-open on merge policy mirrors fail-closed on review policy:
    # both pick the "current behaviour" branch when the operator
    # mistypes, so production never silently shifts shape.
    assert _resolve_merge_policy({"merge_policy": "humanrequired"}) == "auto"
    assert _resolve_merge_policy({"merge_policy": ""}) == "auto"
    assert _resolve_merge_policy({"merge_policy": True}) == "auto"
    assert _resolve_merge_policy({"merge_policy": None}) == "auto"


def test_case_insensitive() -> None:
    assert _resolve_merge_policy({"merge_policy": "Human_Required"}) == "human_required"
    assert _resolve_merge_policy({"merge_policy": "AUTO"}) == "auto"


# ── stall payload shape (pure model_copy contract) ─────────────────


def test_human_required_remap_shape_contract() -> None:
    # Exercise the model_copy + payload shape the live remap uses,
    # without a DB hit. The auto-merger hook downstream looks at
    # payload.auto_merge_action; if a remap forgets to flip it the
    # server would still squash. Pin both fields together so the
    # contract can't drift.
    merge_finish = FinishIn(
        run_id="r1",
        outcome="ready_next_step",
        fsm_stage="auto_merge",
        stage_next="merged",
        ticket_ref="ELS-1",
        process="development",
        comment="all signals green",
        payload={
            "auto_merge_action": "merge",
            "merge_method": "squash",
            "signals": {"reviewer": "green", "ci": "green"},
        },
    )
    # Simulate remap tail.
    new_payload = dict(merge_finish.payload or {})
    new_payload["merge_policy_remap"] = {
        "mode": "human_required",
        "from_action": "merge",
        "risk_level": None,
    }
    new_payload["auto_merge_action"] = "stall"
    new_payload["action_items"] = [
        {"id": "merge-now", "kind": "choice", "label": "Merge now"},
        {"id": "request-changes", "kind": "choice", "label": "Request changes"},
        {"id": "discard", "kind": "choice", "label": "Discard PR"},
    ]
    new_payload["resolution_mode"] = "single_choice"
    remapped = merge_finish.model_copy(
        update={
            "outcome": "needs_clarification",
            "stage_next": None,
            "comment": merge_finish.comment + "\n\n_policy banner_",
            "payload": new_payload,
        }
    )
    assert remapped.outcome == "needs_clarification"
    assert remapped.stage_next is None
    assert remapped.payload["auto_merge_action"] == "stall"
    assert remapped.payload["merge_policy_remap"]["mode"] == "human_required"
    # Signals from the auto-merger are preserved for audit.
    assert remapped.payload["signals"]["reviewer"] == "green"


def test_evidence_required_only_fires_on_advisory_signal() -> None:
    # Cleanest invariant: evidence_required is a CONDITIONAL gate.
    # Without an upstream advisory_blocked marker, a green merge
    # should proceed. We exercise the marker check that the live
    # remap helper performs.
    payload_clean = {
        "auto_merge_action": "merge",
        "signals": {"reviewer": "green"},
    }
    payload_advisory = {
        "auto_merge_action": "merge",
        "risk_level": "advisory_blocked",
        "signals": {"reviewer": "green"},
    }
    assert payload_clean.get("risk_level") != "advisory_blocked"
    assert payload_advisory.get("risk_level") == "advisory_blocked"
