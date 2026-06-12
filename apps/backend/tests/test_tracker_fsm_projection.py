"""Egress-only projection maps (ELS-228).

Pins (1) the unified source of truth: the provisioner's
FSM_TO_LINEAR_STATE and project_state_sync's _TRANSITION_PLAN derive
from tracker_fsm; (2) per-state equivalence with the pre-unification
literals (guard against drift between display strings and live state
names); (3) the egress-only invariant: neither dispatcher.py nor
tracker_poller.py (the control/ingest paths) consumes the projection
maps.
"""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def test_provisioner_map_is_the_shared_table() -> None:
    from backend.app.services.linear_provisioner import FSM_TO_LINEAR_STATE
    from backend.app.services.tracker_fsm import FSM_TO_NATIVE_STATE

    assert FSM_TO_LINEAR_STATE is FSM_TO_NATIVE_STATE["linear"]


def test_project_state_plan_derives_from_shared_table() -> None:
    from backend.app.services.agent.project_state_sync import _TRANSITION_PLAN
    from backend.app.services.tracker_fsm import PROJECT_STATE_TICKET_MOVES

    assert _TRANSITION_PLAN == dict(PROJECT_STATE_TICKET_MOVES)


def test_linear_targets_equal_pre_unification_values() -> None:
    """Per-state equivalence with the literals that lived in
    linear_provisioner before ELS-228 — the unification must not move
    any native target."""
    from backend.app.services.tracker_fsm import FSM_TO_NATIVE_STATE

    expected = {
        "planning": "Todo",
        "dev_implementation": "In Progress",
        "devops_implementation": "In Progress",
        "validation": "In Progress",
        "code_review": "Review",
        "auto_merge": "Review",
        "merged": "Done",
        "self_heal": "Todo",
        "decomposition": "In Progress",
        "planning_done": "Done",
        "task_intake": "Todo",
        "ba_requirements": "Todo",
        "tech_arch_plan": "Todo",
        "qa_arch_plan": "Todo",
        "qa_manual": "In Progress",
        "qa_automation": "In Progress",
        "pr_review": "Review",
        "wbs": "In Progress",
        "architecture": "In Progress",
        "test_architecture": "In Progress",
        "tasks": "In Progress",
    }
    assert FSM_TO_NATIVE_STATE["linear"] == expected


def test_project_moves_equal_pre_unification_values() -> None:
    from backend.app.services.tracker_fsm import PROJECT_STATE_TICKET_MOVES

    assert PROJECT_STATE_TICKET_MOVES == {
        "active": ("Backlog", "Todo"),
        "planning": ("Todo", "Backlog"),
        "parked": ("Todo", "Backlog"),
    }


def test_control_and_ingest_paths_never_consume_projection_maps() -> None:
    """Egress-only invariant: the maps are write maps for the human
    read-model. The dispatch control path and the poller ingest path
    must never import them (inverting projection into control)."""
    forbidden = (
        "FSM_TO_NATIVE_STATE",
        "PROJECT_STATE_TICKET_MOVES",
        "TRACKER_MAPPING_HINTS",
        "FSM_TO_LINEAR_STATE",
    )
    for rel in ("services/dispatcher.py", "services/tracker_poller.py"):
        src = (APP / rel).read_text()
        for name in forbidden:
            assert name not in src, (
                f"{rel} references {name} — projection maps are "
                "egress-only and must not feed control/ingest decisions "
                "(thesis 2, ELS-228)."
            )
