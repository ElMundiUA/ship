"""Phase 1 FSM rearchitecture — ``outcome=blocked`` freezes via label.

The agent reports ``outcome=blocked``. The server adds the ``blocked``
signal label to the Linear ticket and files one inbox blocker letter.
The label is already in ``OVERLAY_FREEZE_LABEL_PREFIXES``; the picker
drops the ticket from every subsequent candidate scan until the
operator clears the label in Linear. No auto-cascade, no
``dev_implementation`` rewrite, no refire-cap detector, no synthetic
finish — the operator unblocks once and the cascade resumes via the
Linear webhook.

These tests pin the new contract:

1. ``outcome=blocked`` on any reviewer stage adds the ``blocked``
   signal label.
2. ``outcome=blocked`` files exactly one inbox row with
   ``intake_reason=agent_blocked``.
3. Deleted auto-cascade is gone: ``stage_next`` is NOT rewritten when
   the agent left it null; no
   ``cascade:blocked_no_next_auto`` action; no
   ``blocked_cascade_exhausted`` letter.
4. Legacy workspaces whose Linear team was provisioned before
   ``blocked`` joined ``SIGNAL_LABELS`` fall back to
   ``needs_clarification`` so the freeze still takes effect (both
   labels live in ``OVERLAY_FREEZE_LABEL_PREFIXES``).
"""

from __future__ import annotations

from backend.app.services.linear_provisioner import (
    OVERLAY_FREEZE_LABEL_PREFIXES,
    SIGNAL_LABELS,
)


def test_blocked_is_a_signal_label() -> None:
    """The new Phase-1 label must be in SIGNAL_LABELS so
    add_signal_label(key='blocked') resolves to a provisioned label
    on freshly provisioned Linear teams."""
    assert "blocked" in SIGNAL_LABELS
    assert SIGNAL_LABELS["blocked"] == "blocked"


def test_blocked_label_is_in_overlay_freeze() -> None:
    """The freeze contract is: blocked label → picker drops the
    ticket. The label name SIGNAL_LABELS["blocked"] must match an
    overlay-freeze prefix so the existing _matched_overlay_labels
    walk in the picker covers it without a code change."""
    label_name = SIGNAL_LABELS["blocked"]
    matched = any(
        label_name == p
        or label_name.startswith(p + "-")
        or label_name.startswith(p + ":")
        for p in OVERLAY_FREEZE_LABEL_PREFIXES
    )
    assert matched, (
        f"blocked label {label_name!r} must overlap with "
        f"OVERLAY_FREEZE_LABEL_PREFIXES={set(OVERLAY_FREEZE_LABEL_PREFIXES)!r} "
        "so the picker freezes the ticket without a separate rule."
    )


def test_needs_clarification_label_is_in_overlay_freeze() -> None:
    """``needs:clarification`` is the legacy-fallback for the same
    freeze, and the natural label for outcome=needs_clarification.
    Pin its membership so a future label rename doesn't silently
    bypass the freeze on either outcome."""
    label_name = SIGNAL_LABELS["needs_clarification"]
    matched = any(
        label_name == p
        or label_name.startswith(p + "-")
        or label_name.startswith(p + ":")
        for p in OVERLAY_FREEZE_LABEL_PREFIXES
    )
    assert matched


def test_refire_cap_machinery_is_gone() -> None:
    """Phase 1 deleted the refire_cap throttle that masked
    permanently-stuck tickets as transient retries. Pin that the
    private symbols are NOT re-exported by the route module so a
    future refactor doesn't accidentally resurrect them under a new
    name. The freeze label is the new throttle."""
    from backend.app.api.v1.routes import agent_runs

    for name in (
        "_REFIRE_CAP_LIMIT",
        "_REFIRE_CAP_WINDOW",
        "_recent_finish_count_for_stage",
        "_write_synthetic_picker_noop_finish",
    ):
        assert not hasattr(agent_runs, name), (
            f"{name} should be deleted; if you brought it back, "
            "delete the blocked-label freeze (this commit's design) "
            "or rename so the conflict is intentional."
        )


def test_dev_not_converging_detector_is_gone() -> None:
    """fsm_self_heal lost the dev_not_converging detector in the
    same Phase 1 commit. The freeze label catches the same pattern
    one cycle earlier."""
    from backend.app.services import fsm_self_heal

    for name in (
        "DEV_NOT_CONVERGING_REVIEW_BLOCKS",
        "DEV_NOT_CONVERGING_DEV_CYCLES",
        "DEV_NOT_CONVERGING_REVIEW_STAGES",
        "_looks_like_dev_not_converging",
        "_file_dev_not_converging_blocker",
        "_fetch_reviewer_last_comment",
    ):
        assert not hasattr(fsm_self_heal, name)


def test_blocked_handler_source_has_no_auto_cascade() -> None:
    """The old blocked handler had ~150 lines of auto-cascade-to-
    dev_implementation + escalation logic. Pin that those phrases are
    gone from the route source — if they come back, this PR's design
    intent has been silently reverted."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "routes" / "agent_runs.py"
    body = src.read_text(encoding="utf-8")
    assert "cascade:blocked_no_next_auto" not in body
    assert "auto_cascade_from_no_next" not in body
    assert "blocked_cascade_exhausted" not in body
    assert "_BLOCKED_NO_NEXT_REVIEW_STAGES" not in body


def test_blocked_handler_adds_signal_label_in_source() -> None:
    """Pin the new behaviour: the blocked handler must call
    add_signal_label with key='blocked'. Source-text assertion is
    intentionally coarse — Phase 1 lacks the integration-test
    plumbing for end-to-end coverage of the blocked → label flow,
    but a code refactor that drops the call will fail this check."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "routes" / "agent_runs.py"
    body = src.read_text(encoding="utf-8")
    # The handler calls add_signal with key="blocked"
    assert 'await add_signal(ref, key="blocked")' in body
    # Plus the fallback to needs_clarification for legacy workspaces
    # whose Linear team was provisioned before SIGNAL_LABELS gained
    # the "blocked" entry — both are in OVERLAY_FREEZE_LABEL_PREFIXES.
    assert 'await add_signal(ref, key="needs_clarification")' in body
    # And the dedicated inbox letter
    assert 'intake_reason="agent_blocked"' in body
