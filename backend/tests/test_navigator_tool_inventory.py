"""Pin the Navigator tool inventory.

The 2026-05-02 dogfood transcript caught the agent self-reporting "46
tools available" when the registry actually had 48 (post ELS-52
trim 52→48), then adding ``archive_bucket_article`` brought it to
49. We don't have any way to stop the LLM from miscounting at
runtime, but we *can* pin the canonical list at registration time
so:

1. Anyone adding / removing a tool sees the assertion fire and
   updates the canonical list (and their CLAUDE/memory note) in the
   same commit.
2. ``ToolBox.specs()`` and the dispatch table stay in lockstep — a
   missing dispatch = a tool the LLM can request but we'll
   ToolInvocationError on.
3. Every admin-mutating tool name listed in the static system
   prompt's "Hard rules" still exists. Drifting wording in either
   side caught here so the prompt never lies to the model about
   what it can call.
"""

from __future__ import annotations

import re

from backend.app.services.agent.tools import ToolBox
from backend.app.services.agent.topic import _load_navigator_prompt
from backend.app.services.policies_seed import default_policies


# Canonical list — keep alphabetised for diff readability. When you
# add or remove a tool, update this list in the same commit.
EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        "run_subagent",
        "project_create",
        "ticket_create",
        "project_find_or_create",
        "dashboard_get",
        "knowledge_bucket_get",
        "project_get",
        "pr_get",
        "repo_file_get",
        "ticket_get",
        "inbox_create",
        "inbox_get",
        "inbox_list",
        "inbox_update",
        "knowledge_search",
        "repo_tree",
        "project_list",
        "pr_list",
        "ticket_list",
        "members_list",
        "repo_symbols",
        "runs_get",
        "runs_list",
        "project_update",
        "ticket_update",
        "audit_search",
    }
)


def _toolbox():
    """Build a ToolBox with no real session — we only call ``.specs()``,
    which doesn't touch the DB."""
    return ToolBox(
        session=None,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        workspace_id=None,  # type: ignore[arg-type]
        user_id=None,  # type: ignore[arg-type]
    )


def test_specs_match_expected_inventory() -> None:
    box = _toolbox()
    actual = {spec.name for spec in box.specs()}
    extra = actual - EXPECTED_TOOLS
    missing = EXPECTED_TOOLS - actual
    assert not extra, (
        f"New tool(s) not in EXPECTED_TOOLS: {sorted(extra)}. "
        "Add them here AND update the Navigator memory note so the "
        "agent's self-reported tool count stays accurate."
    )
    assert not missing, (
        f"Tool(s) removed from registry but still in EXPECTED_TOOLS: "
        f"{sorted(missing)}. Drop them here too if the removal is "
        "intentional."
    )


def test_specs_have_no_duplicate_names() -> None:
    """Two ``ToolSpec`` rows with the same ``name`` would silently
    let the second clobber the first in the dispatch table — and
    the JSON schemas the LLM sees might not match the handler. Pin
    name uniqueness so an accidental copy-paste during a refactor
    surfaces immediately."""
    names = [spec.name for spec in _toolbox().specs()]
    assert len(names) == len(set(names)), (
        f"duplicate tool names: "
        f"{[n for n in names if names.count(n) > 1]}"
    )


def test_dispatch_covers_every_spec() -> None:
    """Every name in ``specs()`` must have a handler in
    ``_handlers()``. The opposite direction — a handler that's not
    registered as a spec — is allowed (legacy private hooks) but a
    missing handler would land an LLM tool call straight into a
    ``ToolInvocationError`` at runtime."""
    box = _toolbox()
    spec_names = {spec.name for spec in box.specs()}
    handlers = box._handlers()
    for name in spec_names:
        assert name in handlers, f"spec {name!r} has no handler in _handlers()"


def test_admin_mutating_tools_in_prompt_match_registry() -> None:
    """The Hard rules block names every admin-mutating tool so the
    LLM knows which calls require workspace admin. If a new
    mutating tool lands in the registry without being added to the
    prompt, the LLM won't gate it on ``ship-choice`` confirmation.
    Conversely, if the prompt names a tool that no longer exists,
    we mislead the model about what it can call.

    This test pins the union — extracted from the prompt's mutating
    tools list — against the actual registry."""
    # The mutating-tools list now lives in the workspace-policy seed
    # (``navigator-admin-only-mutators``), not the prompt body.
    policy = next(
        (p for p in default_policies()
         if p.applies_to_roles
         and "navigator" in p.applies_to_roles
         and "Mutating tools" in p.body),
        None,
    )
    assert policy is not None, (
        "navigator-admin-only-mutators seed policy is missing — the "
        "rule used to gate mutating tools is gone."
    )
    block = re.search(
        r"Mutating tools \(([^)]+)\) require workspace admin",
        policy.body,
    )
    assert block is not None, (
        "Hard rules section no longer names the mutating tools list. "
        "If the format changed, update this test alongside; the rule "
        "itself is load-bearing for ship-choice gating."
    )
    listed = set(re.findall(r"``([a-z_]+)``", block.group(1)))
    assert listed, "no tool names parsed from the mutating tools block"
    box = _toolbox()
    registry = {spec.name for spec in box.specs()}
    drift = listed - registry
    assert not drift, (
        f"Prompt lists mutating tools that don't exist in the "
        f"registry: {sorted(drift)}"
    )
