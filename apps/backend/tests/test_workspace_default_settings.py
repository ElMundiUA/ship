"""Defaults baked into new workspace.settings on create.

Existing workspaces stay {} so the legacy ``hard_gate`` / ``auto``
resolvers keep current behaviour; new workspaces — both JIT-personal
and explicit-create — get the advisory + evidence-required pair so the
cascade ships fast out of the gate.

These tests pin the constant shape + the deep-copy contract (every
workspace must get its own dict, not a shared reference).
"""

from __future__ import annotations

import copy

from backend.app.api.v1.routes.workspaces import (
    NEW_WORKSPACE_DEFAULT_SETTINGS,
)
from backend.app.api.v1.routes.agent_runs import (
    _resolve_merge_policy,
    _resolve_review_mode,
)


def test_defaults_resolve_through_runtime_resolvers() -> None:
    # The two resolvers in agent_runs are the runtime side that reads
    # workspace.settings; the constant is the persistence side. Pin
    # them as a pair so a future rename on either end can't silently
    # split the contract.
    s = NEW_WORKSPACE_DEFAULT_SETTINGS
    assert _resolve_review_mode(s, "validation") == "advisory"
    assert _resolve_review_mode(s, "code_review") == "advisory"
    assert _resolve_merge_policy(s) == "evidence_required"


def test_defaults_include_all_documented_keys() -> None:
    # Surface guard against accidentally trimming a key during a
    # refactor — every key the resolvers know about should still
    # appear here. ``merge_policy`` is a scalar; ``review_policy`` is
    # a nested dict with one entry per QA gate.
    assert "review_policy" in NEW_WORKSPACE_DEFAULT_SETTINGS
    assert "merge_policy" in NEW_WORKSPACE_DEFAULT_SETTINGS
    rp = NEW_WORKSPACE_DEFAULT_SETTINGS["review_policy"]
    assert {"validation", "code_review"}.issubset(rp.keys())


def test_deep_copy_each_workspace_owns_its_settings() -> None:
    # Sloppy patches that pass the constant by reference to Workspace(
    # settings=...) end up sharing a dict between rows; a later patch
    # mutates every "new" workspace. Verify the helper produces an
    # independent copy.
    a = copy.deepcopy(NEW_WORKSPACE_DEFAULT_SETTINGS)
    b = copy.deepcopy(NEW_WORKSPACE_DEFAULT_SETTINGS)
    a["review_policy"]["validation"] = "hard_gate"
    a["merge_policy"] = "auto"
    assert b["review_policy"]["validation"] == "advisory"
    assert b["merge_policy"] == "evidence_required"
    assert NEW_WORKSPACE_DEFAULT_SETTINGS["review_policy"]["validation"] == "advisory"
    assert NEW_WORKSPACE_DEFAULT_SETTINGS["merge_policy"] == "evidence_required"


def test_existing_workspace_with_empty_settings_keeps_legacy_behaviour() -> None:
    # The new defaults are write-time; we explicitly do NOT migrate
    # already-created workspaces. An empty settings dict (what every
    # existing workspace has) must resolve to the legacy modes.
    assert _resolve_review_mode({}, "validation") == "hard_gate"
    assert _resolve_review_mode({}, "code_review") == "hard_gate"
    assert _resolve_merge_policy({}) == "auto"
