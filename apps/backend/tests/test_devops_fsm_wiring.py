"""BS0.1 — devops_implementation wired into the FSM (integrity-safe).

Planning routes ``infra`` tickets to ``devops_implementation`` instead of
``dev_implementation``; the infra path rejoins the shared tail. A bounce
back to implementation is server-redirected to devops via the
``stage:devops_implementation`` breadcrumb so an infra ticket never lands
on the feature developer.
"""

from __future__ import annotations


def test_devops_stage_routes_to_devops_routine() -> None:
    from backend.app.services.dispatcher import (
        _STAGE_TO_ROUTINE,
        normalize_routine_id,
    )

    assert _STAGE_TO_ROUTINE["devops_implementation"] == "devops"
    assert normalize_routine_id("devops_implementation") == "devops"
    # Unchanged for the existing stages.
    assert normalize_routine_id("dev_implementation") == "developer"


def test_redirect_infra_bounce() -> None:
    from backend.app.services.dispatcher import redirect_infra_bounce

    infra_labels = ["stage:planning", "stage:devops_implementation", "stage:validation"]
    feature_labels = ["stage:planning", "stage:dev_implementation"]

    # Infra ticket bounced to implementation → devops.
    assert (
        redirect_infra_bounce("dev_implementation", infra_labels)
        == "devops_implementation"
    )
    # Feature ticket → stays on the developer.
    assert (
        redirect_infra_bounce("dev_implementation", feature_labels)
        == "dev_implementation"
    )
    # Non-implementation bounces are untouched even for infra tickets.
    assert redirect_infra_bounce("validation", infra_labels) == "validation"
    assert redirect_infra_bounce("merged", infra_labels) == "merged"
    # None / empty labels pass through.
    assert redirect_infra_bounce(None, infra_labels) is None
    assert redirect_infra_bounce("dev_implementation", None) == "dev_implementation"
    assert redirect_infra_bounce("dev_implementation", []) == "dev_implementation"


def test_previous_stages_accepts_both_implementation_predecessors() -> None:
    from backend.app.services.linear_provisioner import previous_stages

    # validation follows BOTH dev_implementation and devops_implementation
    # — the picker filter must accept either breadcrumb (NIT-1 fix).
    assert previous_stages("validation") == [
        "dev_implementation",
        "devops_implementation",
    ]
    # Shared-tail stages past validation have a single predecessor.
    assert previous_stages("code_review") == ["validation"]
    assert previous_stages("auto_merge") == ["code_review"]
    # Implementation stages follow planning.
    assert previous_stages("dev_implementation") == ["planning"]
    assert previous_stages("devops_implementation") == ["planning"]
    # Entry stage has no predecessor.
    assert previous_stages("planning") == []


def test_project_lock_in_flight_includes_devops() -> None:
    from backend.app.services.dispatcher import PROJECT_LOCK_IN_FLIGHT_STAGE_NEXT

    # devops_implementation keeps the project WIP lock while in flight,
    # same as dev_implementation (it's in _STAGE_TO_ROUTINE).
    assert "devops_implementation" in PROJECT_LOCK_IN_FLIGHT_STAGE_NEXT


def test_previous_stage_devops_chain() -> None:
    from backend.app.services.linear_provisioner import previous_stage

    assert previous_stage("devops_implementation") == "planning"
    assert previous_stage("planning") is None
    # Shared-tail stages still resolve (SDLC chain wins; cosmetic).
    assert previous_stage("code_review") == "validation"
    assert previous_stage("validation") == "dev_implementation"


def test_devops_linear_state_and_provisioned() -> None:
    from backend.app.services.linear_provisioner import (
        FSM_TO_LINEAR_STATE,
        SHIP_FSM_STAGES,
    )

    assert FSM_TO_LINEAR_STATE["devops_implementation"] == "In Progress"
    assert "devops_implementation" in SHIP_FSM_STAGES


def test_self_heal_scans_devops_stage() -> None:
    from backend.app.services.fsm_self_heal import SCAN_STAGES

    assert "devops_implementation" in SCAN_STAGES


def test_devops_in_default_bundle() -> None:
    from backend.app.services.lane_recipes import (
        DEFAULT_BUNDLE,
        DEFAULT_BUNDLE_REASONS,
    )

    assert "devops" in DEFAULT_BUNDLE
    assert "devops" in DEFAULT_BUNDLE_REASONS


def test_devops_prompt_carries_build_once_promotion_model() -> None:
    """BS4 — the deploy/promotion model is agent-authored: the DevOps
    prompt must spell out build-once → lower-env → promote-the-same-
    artifact-to-prod behind a GitHub Environment gate (manual default,
    auto-promote opt-in)."""
    from backend.app.services.agent_roles import get_default

    devops = get_default("devops")
    assert devops is not None
    # Normalise whitespace so multi-word phrases match across markdown
    # line wraps (e.g. "Required\n     reviewers").
    prompt = " ".join(devops.prompt.lower().split())
    # Build-once / promote-same-artifact.
    assert "build once" in prompt
    assert "digest" in prompt
    assert "promote" in prompt
    # Gate via GitHub Environment, manual by default.
    assert "github environment" in prompt
    assert "required reviewers" in prompt
    # Auto-promote is opt-in (off by default).
    assert "auto_promote" in prompt or "auto-promote" in prompt
    assert "off by default" in prompt
    assert "opt-in" in prompt
    # The anti-patterns the model exists to prevent — highest-value
    # lines, lock them so a future edit can't silently drop them.
    assert "never rebuild" in prompt
    assert "floating tag" in prompt
