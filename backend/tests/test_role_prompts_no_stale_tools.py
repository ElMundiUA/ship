"""Role prompts must not reference removed tools (ELS-87).

ELS-76 / ELS-77 trimmed the Navigator's tool surface. Role files
that still mention dropped names confuse the LLM (it tries to call
them and gets a 422). Pin the deny list so a re-introduction lands
in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_ROLES_DIR = Path(__file__).resolve().parents[1] / "app/resources/agent_roles"


# Tool names that have been removed from ``ToolBox.specs`` and from
# the public dispatch surface — role prompts referencing them sends
# the agent into a 422 dead-end.
#
# Sources of truth:
#   * ELS-76 (commit e41b0c0) — dropped 4 redundant tools
#   * ELS-77 (commit c12a74d) — KB consolidation; ``knowledge_search``
#     replaced ``knowledge_search_v2``, and ``search_buckets`` /
#     ``search_workspace_kb`` / ``list_pipelines`` /
#     ``list_pipeline_runs`` were retired
#   * Phase 2.4 Step D (commit e89ae69) — catalog kill;
#     ``list_catalog_artifacts`` / ``get_catalog_artifact`` are gone
#
# Add new entries here when a tool is removed; CI will catch any
# leftover prompt that still tells the agent to call it.
_REMOVED_TOOL_NAMES: tuple[str, ...] = (
    # ELS-76
    "list_integrations",
    "get_workspace_settings",
    "list_workspace_artifact_repos",
    "inbox_counts",
    # ELS-77
    "search_buckets",
    "search_workspace_kb",
    "list_pipelines",
    "list_pipeline_runs",
    "knowledge_search_v2",
    "search_repo_kb",
    # Phase 2.4 Step D
    "list_catalog_artifacts",
    "get_catalog_artifact",
    # Phase 1a cleanup — Plays/Automations retired (FSM-driven routines
    # replaced them) and bloated read-side tools moved into the session
    # context frame (repo_intel, repo_kb_status, list_activated_repos).
    "plays_list",
    "plays_get",
    "plays_coverage",
    "play_run_now",
    "play_automate",
    "automation_toggle",
    "automations_list",
    "get_pipeline_run",
    "list_clarifications",
    "list_improvements",
    "list_recent_activity",
    "list_activated_repos",
    "list_buckets",
    "search_code",
    "archive_bucket_article",
    "reindex_repo_kb",
    "repo_intel_get",
    "repo_kb_status",
    "inbox_routing_list",
    "inbox_routing_preview",
    "inbox_routing_upsert",
    # Phase 1b — old VERB_NOUN names retired in favour of NOUN_VERB.
    # Adding the OLD names here so any role prompt that re-introduces
    # one fails CI (the NEW names are the canonical ToolBox specs).
    "create_ticket",
    "list_tickets",
    "get_ticket",
    "update_ticket",
    "create_project",
    "list_projects",
    "get_project",
    "find_or_create_project_by_name",
    "append_project_description",
    "set_priority_state",
    "get_repo_file",
    "list_code_map",
    "get_pull_request",
    "list_pull_requests",
    "list_workspace_members",
    "get_dashboard",
    "workspace_audit_search",
    "get_knowledge_bucket",
    "runs_query",
    "run_detail",
    "start_decomposition",
    "consult_specialist",
)


@pytest.mark.parametrize("removed", _REMOVED_TOOL_NAMES)
def test_no_role_prompt_mentions_removed_tool(removed: str) -> None:
    """A role file referencing a removed tool guides the LLM into a
    dead-end call — pin the deny list."""
    pattern = re.compile(rf"\b{re.escape(removed)}\b")
    offenders: list[str] = []
    for md in sorted(_ROLES_DIR.glob("*.md")):
        body = md.read_text(encoding="utf-8")
        for n, line in enumerate(body.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"  {md.name}:{n}: {line.strip()}")
    assert not offenders, (
        f"Role prompts still reference removed tool ``{removed}``:\n"
        + "\n".join(offenders)
        + "\n\nReplace with the canonical equivalent (e.g. "
        f"``knowledge_search`` for KB-search names, drop the bullet "
        f"entirely for catalog-artifact / pipeline-listing names)."
    )
