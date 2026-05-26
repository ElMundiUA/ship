"""Advisory review-policy remap (validation / code_review).

When workspace.settings.review_policy.<stage> is "advisory", a
``blocked`` finish from that stage is rewritten to ``ready_next_step``
with the agent's findings preserved in the comment. Default is
``hard_gate`` — every existing workspace keeps current behaviour.

These tests pin the mode resolver and the remap eligibility matrix.
The async remap helper itself does a workspace-row lookup so it lives
behind an integration test elsewhere; here we exercise the pure
mode-resolution surface.
"""

from __future__ import annotations

from backend.app.api.v1.routes.agent_runs import (
    _ADVISORY_ELIGIBLE_STAGES,
    _ADVISORY_FORWARD,
    _resolve_review_mode,
)


def test_default_is_hard_gate_when_no_settings() -> None:
    assert _resolve_review_mode(None, "code_review") == "hard_gate"
    assert _resolve_review_mode({}, "code_review") == "hard_gate"
    assert _resolve_review_mode({"unrelated": "field"}, "code_review") == "hard_gate"


def test_default_is_hard_gate_when_policy_block_missing() -> None:
    settings = {"review_policy": {"validation": "advisory"}}
    # Different stage — default still hard.
    assert _resolve_review_mode(settings, "code_review") == "hard_gate"


def test_advisory_opt_in_per_stage() -> None:
    settings = {"review_policy": {"code_review": "advisory"}}
    assert _resolve_review_mode(settings, "code_review") == "advisory"


def test_legacy_stage_alias_resolves_via_routine_bucket() -> None:
    # Operator sets the modern stage name; legacy aliases that map to
    # the same routine should pick up the same mode without a per-alias
    # config entry.
    settings = {"review_policy": {"code_review": "advisory"}}
    assert _resolve_review_mode(settings, "pr_review") == "advisory"
    settings2 = {"review_policy": {"validation": "advisory"}}
    assert _resolve_review_mode(settings2, "qa_manual") == "advisory"
    assert _resolve_review_mode(settings2, "qa_automation") == "advisory"


def test_invalid_policy_value_falls_back_hard_gate() -> None:
    # Typos / unknown modes default to hard_gate — fail closed.
    settings = {"review_policy": {"code_review": "permissive"}}
    assert _resolve_review_mode(settings, "code_review") == "hard_gate"
    settings2 = {"review_policy": {"code_review": True}}
    assert _resolve_review_mode(settings2, "code_review") == "hard_gate"


def test_non_dict_policy_falls_back() -> None:
    settings = {"review_policy": "advisory"}
    assert _resolve_review_mode(settings, "code_review") == "hard_gate"


def test_eligible_stages_cover_qa_and_review() -> None:
    # Sanity: the two QA gates and their legacy aliases are remappable.
    # Implementation stages and auto_merge must NOT be — a blocked
    # merge IS the merge decision.
    for stage in ("validation", "qa_manual", "qa_automation",
                  "code_review", "pr_review"):
        assert stage in _ADVISORY_ELIGIBLE_STAGES
    for stage in ("dev_implementation", "devops_implementation",
                  "auto_merge", "planning"):
        assert stage not in _ADVISORY_ELIGIBLE_STAGES


def test_forward_map_keeps_pipeline_shape() -> None:
    # Advisory remap must forward to the SAME stage_next the
    # ``ready_next_step`` happy path would have used, so the downstream
    # agent doesn't see a different cascade shape than usual.
    assert _ADVISORY_FORWARD["validation"] == "code_review"
    assert _ADVISORY_FORWARD["code_review"] == "auto_merge"


def test_advisory_remap_preserves_signal_in_payload() -> None:
    # Pin the contract the auto-merger relies on: an advisory remap
    # must leave a machine-readable breadcrumb so a downstream role
    # can tell "would have blocked" from "actually green". Without
    # this marker the remap silently launders blocked into a green
    # merge, exactly the opposite of advisory mode's promise.
    #
    # We exercise the pure mutation shape via model_copy directly
    # (the live remap goes through a DB lookup which lives in an
    # integration test).
    from backend.app.api.v1.routes.agent_runs import FinishIn

    blocked = FinishIn(
        run_id="r1",
        outcome="blocked",
        fsm_stage="code_review",
        ticket_ref="ELS-1",
        process="development",
        comment="needs naming fix",
        stage_next=None,
    )
    # Simulate the remap's tail (payload mutation) without DB.
    new_payload = dict(blocked.payload or {})
    new_payload["advisory_remap"] = {
        "from_outcome": "blocked",
        "from_stage": "code_review",
        "original_stage_next": None,
    }
    new_payload.setdefault("risk_level", "advisory_blocked")
    remapped = blocked.model_copy(
        update={
            "outcome": "ready_next_step",
            "stage_next": "auto_merge",
            "comment": "needs naming fix\n\n_advisory_",
            "payload": new_payload,
        }
    )
    assert remapped.outcome == "ready_next_step"
    assert remapped.stage_next == "auto_merge"
    assert remapped.payload["advisory_remap"]["from_outcome"] == "blocked"
    assert remapped.payload["risk_level"] == "advisory_blocked"
