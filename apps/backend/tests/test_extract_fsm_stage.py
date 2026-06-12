"""ELS-278 — highest-order stage breadcrumb resolution."""

from __future__ import annotations

from backend.app.services.linear_provisioner import resolve_fsm_stage_from_labels


def test_multi_breadcrumb_returns_highest_sdlc_stage() -> None:
    labels = ["stage:planning", "stage:validation", "bug"]
    assert resolve_fsm_stage_from_labels(labels) == "validation"


def test_devops_and_sdlc_breadcrumbs_return_highest_tail_stage() -> None:
    labels = [
        "stage:planning",
        "stage:devops_implementation",
        "stage:validation",
    ]
    assert resolve_fsm_stage_from_labels(labels) == "validation"


def test_terminal_state_returns_none_despite_breadcrumbs() -> None:
    labels = ["stage:planning", "stage:validation"]
    assert resolve_fsm_stage_from_labels(labels, state="Done") is None


def test_todo_without_labels_returns_task_intake() -> None:
    assert resolve_fsm_stage_from_labels([], state="Todo") == "task_intake"


def test_decomposition_chain_respected() -> None:
    labels = ["stage:decomposition", "stage:planning"]
    assert resolve_fsm_stage_from_labels(labels) == "planning"
