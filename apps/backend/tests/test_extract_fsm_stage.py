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


def test_planning_anchor_entry_resolves_to_decomposition() -> None:
    # ELS-308: start_decomposition walks the anchor into
    # ``stage:decomposition`` (the E16/ELS-123 single bundle stage).
    # The resolver MUST recognize it so the dispatcher fires the
    # decomposition routine instead of parking the anchor.
    labels = ["planning:anchor", "stage:decomposition"]
    assert resolve_fsm_stage_from_labels(labels) == "decomposition"


def test_pre_e16_wbs_label_is_dead() -> None:
    # ELS-308 root cause: ELS-123 dropped ``wbs`` from
    # DECOMPOSITION_STAGE_ORDER, so a stale ``stage:wbs`` entry label
    # resolves to None → ``dispatch.no_routine`` → silent stall. This
    # asserts the dead alias stays dead (the fix is to never write it).
    assert resolve_fsm_stage_from_labels(["planning:anchor", "stage:wbs"]) is None
