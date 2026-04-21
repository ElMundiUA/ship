"""Tools the real agent can call (C12).

Each tool is:

- a plain ``async`` method on :class:`ToolBox`,
- exposed to the LLM through a JSON schema (the vendor SDKs pick it
  up via :meth:`ToolBox.specs`),
- scoped to the request's ``(workspace_id, user_id)`` so a tool call
  can't escape the caller's tenancy.

The ``ToolBox`` itself is built once per chat turn — it holds the
database session, settings, and resolved auth, so a single turn can
make many tool calls without re-paying the resolution cost.

Tool inventory (C12 Phase 2.2):

- :meth:`search_repo_kb` — pgvector similarity search over the
  ``.ship/knowledge`` corpus indexed by :mod:`kb_indexer`.
- :meth:`get_repo_file` — raw file contents at HEAD for an activated
  repo. Used when the agent needs to ground an answer in actual
  source.
- :meth:`list_code_map` — flat file list of an activated repo (what
  the Code Map page already renders, but as a tool call).
- :meth:`list_activated_repos` — enumerate the workspace's activated
  repos with their UUIDs, so the LLM can call ``get_repo_file`` /
  ``list_code_map`` without having to ask the user for an id.
- :meth:`create_ticket` — open a ticket on the workspace's connected
  tracker (Linear / Notion / GitHub Issues).
- :meth:`list_tickets` — read back recently-updated tickets from the
  connected tracker (was previously write-only).
- :meth:`create_artifact_feedback` — file feedback against a catalog
  artifact id (``pattern/cloud-base``, …). Persisted to
  :class:`ArtifactFeedback` for the console feedback tab.
- :meth:`list_catalog_artifacts` — enumerate the global Ship catalog
  (patterns / tools / collections) so the agent can recommend or
  feedback on artifacts it couldn't otherwise name.
- :meth:`list_recent_activity` — last N pipeline runs / PR / workflow
  events for the workspace, so the agent can ground "what's going
  on?" answers without hitting GitHub live.
- :meth:`get_pull_request` — detailed PR view (metadata, timeline,
  changed files with diff hunks) via the GitHub App.
- :meth:`list_buckets` — enumerate the workspace's knowledge buckets
  without a semantic query (complement to :meth:`search_buckets`).
- :meth:`search_buckets` — vector search over :class:`BucketArticle`
  so the agent can recall previously-packed conversations.
- :meth:`get_catalog_artifact` — fetch the full ``ARTIFACT.md`` body
  for one catalog entry, for "what does this pattern actually do?"
  follow-ups to :meth:`list_catalog_artifacts`.
- :meth:`list_integrations` — enumerate the workspace's configured
  integrations (Linear / Notion / GitHub / Slack / …) so the agent
  can answer "what's connected?" without guessing.
- :meth:`list_pull_requests` — list cached PRs from the workspace,
  optionally filtered by repo / state / author. Complements
  :meth:`get_pull_request` when the user asks "what's open?".
- :meth:`list_pipelines` / :meth:`list_pipeline_runs` /
  :meth:`get_pipeline_run` — dashboard pipeline surface: which
  lanes are configured, their recent runs, and one run in detail.
- :meth:`list_clarifications` — C9 inbox (open questions the agent
  has asked humans, answered/skipped history).
- :meth:`list_improvements` — C8 inbox (agent-proposed improvements
  and their pending/accepted/declined decisions).
- :meth:`get_metrics_overview` — dashboard aggregates (pipelines,
  runs, clarifications, improvements, chat, DORA) for a window.
- :meth:`search_code` — GitHub code search scoped to one activated
  repo (symbol / string search; complements embedding-only KB search).
- :meth:`list_audit_events` — workspace audit log (admin-only).
- :meth:`list_workspace_members` — roster with roles.
- :meth:`list_workspace_invites` — invite history (admin-only).
- :meth:`get_workspace_settings` — name, slug, catalog_sources.
- :meth:`list_workspace_artifact_repos` — custom catalog source URLs.
- :meth:`get_knowledge_bucket` — one bucket by slug + optional
  summaries list.
- :meth:`list_artifact_feedback` — catalog feedback rows filed from
  the console.

The JSON schemas live next to each method (single source of truth,
no drift). Vendors that can't consume a method share the same
schema; the dispatch layer (:meth:`ToolBox.invoke`) lives here.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.app.core.config import Settings
from backend.app.db.models.agent_memory import (
    ArtifactFeedback,
    BucketArticle,
    BucketArticleStatus,
    BucketSource,
    KbChunk,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import (
    Clarification,
    Improvement,
)
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.pipelines import (
    Pipeline,
    PipelineRun,
    PullRequest,
    WorkflowRun,
)
from backend.app.db.models.tenancy import (
    ApiToken,
    ArtifactRepo,
    AuditLog,
    Integration,
    User as TenancyUser,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
)
from backend.app.integrations.gateway.code_host import PullRequestRef, RepoRef
from backend.app.integrations.gateway.tracker import (
    CreatedTicket,
    TrackerGateway,
)
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.integrations.github.issues_tracker import GitHubIssuesTracker
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.integrations.notion.tracker_adapter import NotionTracker
from backend.app.security.encryption import decrypt
from backend.app.services import catalog as catalog_service
from backend.app.services.agent.client import ToolSpec
from backend.app.services.agent.embedding import embed_text
from backend.app.services.bucket_visibility import visible_to_user_clause


logger = logging.getLogger(__name__)


# Defensive caps so a malformed tool call can't eat the whole context
# window with kilobytes of file / KB chunks. Tuned to leave ~20k
# tokens of headroom for the turn around them.
_MAX_FILE_BYTES_RETURNED = 64 * 1024
_MAX_KB_RESULTS = 8
_MAX_CODE_MAP_ENTRIES = 1500
_MAX_ACTIVITY_ITEMS = 20
_MAX_BUCKET_RESULTS = 8
_MAX_TICKETS = 25
_MAX_REPOS_LISTED = 200
_MAX_CATALOG_ITEMS = 100
_MAX_BUCKETS_LISTED = 100
_MAX_PR_FILES = 50
# Per-file diff hunks get long for big refactors. Keep each one readable
# in the transcript but not so clipped that the agent can't see why a
# file changed. The full diff is still on GitHub for the user to follow.
_MAX_PATCH_CHARS = 4000
_MAX_PR_REVIEWS = 30
_MAX_PR_COMMITS = 50
_MAX_PR_COMMENTS = 30
_MAX_PIPELINES = 50
_MAX_PIPELINE_RUNS = 50
_MAX_CLARIFICATIONS = 50
_MAX_IMPROVEMENTS = 50
_MAX_INTEGRATIONS = 30
_MAX_PRS_LISTED = 50
_MAX_ARTIFACT_BODY_CHARS = 32 * 1024
_MAX_KB_FULL_CHUNK = 12_000
_MAX_CODE_SEARCH = 20
_MAX_AUDIT_EVENTS = 50
_MAX_ARTIFACT_FEEDBACK_LIST = 50
_MAX_BUCKET_SUMMARIES = 40
_KB_GLOB_PREFETCH_CAP = 80


@dataclass(slots=True)
class ToolInvocationError(Exception):
    """Raised when a tool call fails in a way the agent should see.

    The dispatcher turns the message into a ``tool`` role message so
    the LLM can choose to apologise, retry with different args, or
    route around the failure — much more useful than a 500.
    """

    message: str

    def __str__(self) -> str:
        return self.message


class ToolBox:
    """Per-turn toolbox bound to one ``(workspace, user)`` tuple."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self._session = session
        self._settings = settings
        self._workspace_id = workspace_id
        self._user_id = user_id

    # ------------------------------------------------------------------
    # Tool specs — the "what the LLM sees" side of the surface
    # ------------------------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        """Return the JSON-schema specs for every tool on this toolbox.

        One-shot list so the SDK layers (OpenAI / Anthropic) can
        register them on the request. Keep the ordering stable —
        some models pay attention to tool order when deciding which
        to try first.
        """
        return [
            ToolSpec(
                name="search_repo_kb",
                description=(
                    "Semantic search over `.ship/knowledge/**/*.md` for the "
                    "workspace's activated repos. Use for grounded answers "
                    "about the repo's docs, decisions, runbooks. Returns "
                    "the top-N matching chunks with path + snippet. "
                    "Optional ``path_prefix`` / ``path_glob`` narrow results; "
                    "``include_full_content`` returns a longer ``content`` "
                    "field (still capped) for runbook-sized chunks."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language question.",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Optional UUID of a specific activated repo; "
                                "omit to search across all activated repos."
                            ),
                        },
                        "path_prefix": {
                            "type": "string",
                            "description": (
                                "Only chunks whose ``source_path`` is under "
                                "this prefix (e.g. ``runbooks/``)."
                            ),
                        },
                        "path_glob": {
                            "type": "string",
                            "description": (
                                "fnmatch pattern applied after the vector "
                                "search (e.g. ``**/*.md``)."
                            ),
                        },
                        "include_full_content": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Include ``content`` with a larger cap than "
                                "the default snippet."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_KB_RESULTS,
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="get_repo_file",
                description=(
                    "Fetch the current contents of a specific file in an "
                    "activated repo. Prefer `search_repo_kb` first; only "
                    "call this when you already know the path and need "
                    "verbatim code. Use `start_line`/`end_line` to slice "
                    "long files instead of pulling the whole blob."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_id": {
                            "type": "string",
                            "description": "UUID of the activated repo.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Path within the repo, e.g. 'src/main.py'.",
                        },
                        "ref_sha": {
                            "type": "string",
                            "description": (
                                "Optional git ref; defaults to the repo's "
                                "default branch HEAD."
                            ),
                        },
                        "start_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "Optional 1-indexed line where the "
                                "returned content should begin."
                            ),
                        },
                        "end_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "Optional 1-indexed inclusive line where "
                                "the returned content should end."
                            ),
                        },
                    },
                    "required": ["repo_id", "path"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_code_map",
                description=(
                    "Return a flat list of file paths at the default branch "
                    "HEAD for an activated repo. Use for navigating an "
                    "unfamiliar codebase before `get_repo_file`. Supports "
                    "`path_prefix` / `glob` to narrow the result set on "
                    "monorepos."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_id": {
                            "type": "string",
                            "description": "UUID of the activated repo.",
                        },
                        "path_prefix": {
                            "type": "string",
                            "description": (
                                "Return only paths that start with this "
                                "prefix (e.g. ``backend/app/``)."
                            ),
                        },
                        "glob": {
                            "type": "string",
                            "description": (
                                "fnmatch-style filter (e.g. ``**/*.py`` or "
                                "``src/**/test_*.ts``). Applied after "
                                "``path_prefix``."
                            ),
                        },
                        "directories_only": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Return deduplicated directory names "
                                "(top-level folders when no prefix is "
                                "set; otherwise the next segment below "
                                "the prefix). Useful for exploring a "
                                "repo layout."
                            ),
                        },
                    },
                    "required": ["repo_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="create_ticket",
                description=(
                    "Open a ticket on the workspace's connected tracker "
                    "(Linear, Notion, or GitHub Issues). Only call when "
                    "the user has explicitly asked to track work or you "
                    "have their confirmation — never autofile."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "tracker": {
                            "type": "string",
                            "enum": ["linear", "notion", "github_issues"],
                            "description": (
                                "Which tracker to post to. If the workspace "
                                "has only one configured tracker, omit and "
                                "the server picks."
                            ),
                        },
                        "title": {"type": "string"},
                        "body": {
                            "type": "string",
                            "description": "Markdown body.",
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "project_hint": {
                            "type": "string",
                            "description": (
                                "Linear team key or UUID / Notion database id / "
                                "GitHub owner/repo. Omit for single-target "
                                "workspaces."
                            ),
                        },
                    },
                    "required": ["title", "body"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="create_artifact_feedback",
                description=(
                    "File feedback against a Ship catalog artifact "
                    "(pattern/tool/collection). Visible in the console "
                    "'Feedback' tab; used to drive catalog improvements."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "artifact_id": {
                            "type": "string",
                            "description": (
                                "Artifact identifier, e.g. "
                                "'pattern/cloud-base' or 'tool/methodology-api'."
                            ),
                        },
                        "body": {
                            "type": "string",
                            "description": "Markdown feedback body.",
                        },
                        "context": {
                            "type": "object",
                            "description": (
                                "Optional JSON hints (related repo, lane, "
                                "link the feedback was filed from)."
                            ),
                            "additionalProperties": True,
                        },
                    },
                    "required": ["artifact_id", "body"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_recent_activity",
                description=(
                    "Return the last N pipeline runs / PRs / workflow runs "
                    "in the workspace, newest first. Use to answer "
                    "'what happened recently?'. Supports `since` / "
                    "`repo_id` filters for scoped history."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "kinds": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["pipeline_run", "pull_request", "workflow_run"],
                            },
                            "description": (
                                "Restrict to specific activity kinds; omit "
                                "for all three."
                            ),
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Optional UUID of an activated repo. "
                                "Applies to PRs and workflow runs; "
                                "pipeline runs have no repo binding."
                            ),
                        },
                        "since": {
                            "type": "string",
                            "description": (
                                "Optional ISO-8601 UTC timestamp "
                                "(e.g. ``2026-04-01T00:00:00Z``). Drop "
                                "rows older than this."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_ACTIVITY_ITEMS,
                            "default": 10,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="search_buckets",
                description=(
                    "Semantic search over the workspace's knowledge "
                    "buckets (prior conversations, packed summaries). "
                    "Use to recall what was discussed before in a "
                    "related topic."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language query.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_BUCKET_RESULTS,
                            "default": 4,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_activated_repos",
                description=(
                    "List the repositories the workspace has activated for "
                    "Ship, including each repo's UUID (``id``), "
                    "``full_name`` and ``default_branch``, plus "
                    "``kb_chunk_count`` / ``kb_last_indexed_at`` so you can "
                    "tell whether `.ship/knowledge` is indexed. Call this "
                    "before asking the user for a repo id — the other "
                    "repo-scoped tools require the UUID."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_tickets",
                description=(
                    "Read the most-recently-updated tickets from the "
                    "workspace's connected tracker (Linear / Notion / "
                    "GitHub Issues). Use to answer 'what's on my plate?' "
                    "or 'does a ticket already exist for X?' before "
                    "considering ``create_ticket``. Supports coarse "
                    "``state`` filters, optional title ``query``, "
                    "``assignee_me`` (Linear: current user), and "
                    "``assignee`` (GitHub login)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "tracker": {
                            "type": "string",
                            "enum": ["linear", "notion", "github_issues"],
                            "description": (
                                "Which tracker to query. Omit when the "
                                "workspace only has one configured."
                            ),
                        },
                        "project_hint": {
                            "type": "string",
                            "description": (
                                "Forwarded to the tracker for GitHub "
                                "Issues (``owner/repo``); usually "
                                "unnecessary for Linear/Notion."
                            ),
                        },
                        "state": {
                            "type": "string",
                            "enum": ["open", "closed", "all"],
                            "description": (
                                "Ticket lifecycle filter (vendor-specific "
                                "semantics; GitHub maps to open/closed/all)."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Substring / search string for titles "
                                "(Linear native; GitHub uses search API; "
                                "Notion client-side)."
                            ),
                        },
                        "assignee_me": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Only issues assigned to the tracker "
                                "user (Linear only in the pilot)."
                            ),
                        },
                        "assignee": {
                            "type": "string",
                            "description": (
                                "GitHub login for assignee filter "
                                "(GitHub Issues only)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_TICKETS,
                            "default": 10,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="get_pull_request",
                description=(
                    "Fetch a rich view of one pull request: metadata "
                    "(title, state, author, labels, mergeable), the "
                    "timeline (created/updated/merged/closed at) and the "
                    "changed files with additions/deletions and diff "
                    "patches. Use when the user asks about the content "
                    "or duration of a specific PR."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "UUID of the activated repo the PR "
                                "belongs to. Call ``list_activated_repos`` "
                                "first if you don't have one."
                            ),
                        },
                        "number": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Pull request number, e.g. 42.",
                        },
                        "include_files": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Set false to skip the changed-files list "
                                "(cheaper, good for quick metadata reads)."
                            ),
                        },
                        "max_files": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_PR_FILES,
                            "default": 25,
                        },
                        "include_reviews": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Include submitted reviews (APPROVED / "
                                "COMMENTED / CHANGES_REQUESTED)."
                            ),
                        },
                        "include_commits": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include the PR's commit list.",
                        },
                        "include_comments": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Include conversation-tab comments "
                                "(issue comments on the PR)."
                            ),
                        },
                    },
                    "required": ["repo_id", "number"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_catalog_artifacts",
                description=(
                    "Enumerate the Ship global catalog: patterns, "
                    "collections (``preset-*`` included) or tools. "
                    "Use when the user asks what Ship provides, or "
                    "before filing feedback with "
                    "``create_artifact_feedback`` so the id is real."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "pattern",
                                "collection",
                                "tool",
                            ],
                        },
                        "group": {
                            "type": "string",
                            "description": (
                                "Optional filter on the artifact's "
                                "``group`` field (e.g. ``preset`` for the "
                                "preset collections)."
                            ),
                        },
                        "tag": {
                            "type": "string",
                            "description": (
                                "Optional filter matching any of the "
                                "artifact's tags."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_CATALOG_ITEMS,
                            "default": 50,
                        },
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_buckets",
                description=(
                    "Enumerate the workspace's knowledge buckets (packed "
                    "prior conversations). Unlike ``search_buckets`` this "
                    "is a flat list — useful when the user asks 'what do "
                    "you remember?' or wants to pick one by name."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "include_archived": {
                            "type": "boolean",
                            "default": False,
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_BUCKETS_LISTED,
                            "default": 25,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="get_catalog_artifact",
                description=(
                    "Fetch the full ARTIFACT.md body and metadata for one "
                    "catalog entry. Call after ``list_catalog_artifacts`` "
                    "when the user wants the actual playbook text, the "
                    "required secrets, or the install target."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "pattern",
                                "collection",
                                "tool",
                            ],
                        },
                        "id": {
                            "type": "string",
                            "description": (
                                "Artifact id without the kind prefix, "
                                "e.g. ``adoption-minimum`` or ``methodology-api``."
                            ),
                        },
                    },
                    "required": ["kind", "id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_integrations",
                description=(
                    "Return the workspace's configured integrations "
                    "(Linear, Notion, Slack, OTLP exporter, GitHub App, "
                    "…) with their status and last-health timestamps. "
                    "Use to answer 'what's connected?' or to check why "
                    "a tracker call might fail. Never returns secrets."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_INTEGRATIONS,
                            "default": _MAX_INTEGRATIONS,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_pull_requests",
                description=(
                    "List pull requests known to Ship (cached from "
                    "GitHub webhooks), newest-updated first. Supports "
                    "filters by repo, state, author. Cheaper than "
                    "``get_pull_request`` when the user wants an "
                    "overview."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Optional UUID of an activated repo to "
                                "restrict the list."
                            ),
                        },
                        "state": {
                            "type": "string",
                            "enum": ["open", "closed", "merged", "all"],
                            "default": "all",
                        },
                        "author": {
                            "type": "string",
                            "description": (
                                "Optional GitHub login to filter by."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_PRS_LISTED,
                            "default": 20,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_pipelines",
                description=(
                    "Enumerate the workspace's configured pipelines "
                    "(automation lanes): PR gate, daily standup, code "
                    "map, tech-debt, self-heal etc. Each entry carries "
                    "the workflow id, enabled flag, last-run status."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "enabled_only": {
                            "type": "boolean",
                            "default": False,
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_PIPELINES,
                            "default": _MAX_PIPELINES,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_pipeline_runs",
                description=(
                    "List recent pipeline runs, newest first. Filter by "
                    "pipeline UUID or status (``running`` / ``succeeded`` "
                    "/ ``failed`` / ``cancelled``). Use to answer "
                    "'did the PR gate run after my last push?' kinds "
                    "of questions."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "pipeline_id": {
                            "type": "string",
                            "description": (
                                "Optional pipeline UUID from "
                                "``list_pipelines``."
                            ),
                        },
                        "status": {
                            "type": "string",
                            "description": (
                                "Optional run status filter "
                                "(e.g. ``failed``)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_PIPELINE_RUNS,
                            "default": 15,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="get_pipeline_run",
                description=(
                    "Fetch one pipeline run by UUID with its summary "
                    "and payload. Use after ``list_pipeline_runs`` when "
                    "the user asks why a specific run failed or what "
                    "it produced."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "Pipeline run UUID.",
                        },
                    },
                    "required": ["run_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_clarifications",
                description=(
                    "Return the workspace's clarification inbox "
                    "(questions the agent has asked humans, plus "
                    "answered/skipped history). Use to avoid re-asking "
                    "something the user already declined, or to "
                    "surface an open question to the user."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["open", "answered", "skipped", "stale"],
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Optional UUID of an activated repo."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_CLARIFICATIONS,
                            "default": 20,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_improvements",
                description=(
                    "Return agent-proposed improvements with their "
                    "pending/accepted/declined/deferred decisions. "
                    "Use to decide whether to re-propose something "
                    "(declined ones should not resurface) or to show "
                    "the user their current backlog."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "decision": {
                            "type": "string",
                            "enum": [
                                "pending",
                                "accepted",
                                "declined",
                                "deferred",
                            ],
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Optional UUID of an activated repo."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_IMPROVEMENTS,
                            "default": 20,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="get_metrics_overview",
                description=(
                    "Aggregate dashboard metrics for the workspace over "
                    "a rolling window: pipelines, runs, clarifications, "
                    "improvements, chat, DORA approximations. Use when "
                    "the user asks for KPIs or how Ship is performing."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "window": {
                            "type": "string",
                            "enum": ["7d", "30d", "90d"],
                            "default": "30d",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="search_code",
                description=(
                    "Search source code in one activated GitHub repo via "
                    "GitHub's code search API. Use for 'where is X defined?' "
                    "when pgvector KB search is not enough. One call per "
                    "turn — subject to GitHub rate limits."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_id": {
                            "type": "string",
                            "description": "UUID of the activated repo.",
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Search terms (GitHub code search syntax), "
                                "e.g. ``symbol:MyClass`` or a function name."
                            ),
                        },
                        "language": {
                            "type": "string",
                            "description": (
                                "Optional language filter (``Python``, "
                                "``TypeScript``, …)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_CODE_SEARCH,
                            "default": 15,
                        },
                    },
                    "required": ["repo_id", "query"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_audit_events",
                description=(
                    "Read workspace audit log entries (who changed what). "
                    "Requires admin or owner role. Supports the same filters "
                    "as the console audit page."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": (
                                "Prefix or full action key, e.g. ``member`` "
                                "or ``pipeline.run``."
                            ),
                        },
                        "actor": {
                            "type": "string",
                            "description": (
                                "Case-insensitive substring on actor email "
                                "or API token name."
                            ),
                        },
                        "target_kind": {
                            "type": "string",
                            "description": (
                                "Exact ``target_kind`` e.g. ``user``, "
                                "``pipeline``."
                            ),
                        },
                        "since": {
                            "type": "string",
                            "description": "ISO-8601 inclusive lower bound.",
                        },
                        "until": {
                            "type": "string",
                            "description": "ISO-8601 exclusive upper bound.",
                        },
                        "before_id": {
                            "type": "integer",
                            "description": (
                                "Pagination cursor: return rows with id < "
                                "this value."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_AUDIT_EVENTS,
                            "default": 30,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_workspace_members",
                description=(
                    "List workspace members with roles and emails (same as "
                    "the team page). Any member can call."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_workspace_invites",
                description=(
                    "List workspace invites (pending and historical). "
                    "Admin or owner only."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="get_workspace_settings",
                description=(
                    "Return workspace metadata: slug, name, org id, "
                    "catalog_sources map. Read-only."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_workspace_artifact_repos",
                description=(
                    "Custom artifact source repos registered for this "
                    "workspace (catalog mirrors)."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="get_knowledge_bucket",
                description=(
                    "Fetch one knowledge bucket by slug with optional "
                    "packed summaries. Complements ``list_buckets`` / "
                    "``search_buckets`` when the user names a bucket."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "slug": {
                            "type": "string",
                            "description": "Bucket slug (stable handle).",
                        },
                        "include_summaries": {
                            "type": "boolean",
                            "default": True,
                        },
                        "summary_limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_BUCKET_SUMMARIES,
                            "default": 20,
                        },
                    },
                    "required": ["slug"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="list_artifact_feedback",
                description=(
                    "List catalog artifact feedback filed from the console. "
                    "Use before creating duplicate feedback on the same "
                    "pattern/tool."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["open", "triaged", "merged", "closed"],
                        },
                        "artifact_id": {
                            "type": "string",
                            "description": (
                                "Optional filter on ``pattern/foo`` id."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_ARTIFACT_FEEDBACK_LIST,
                            "default": 25,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
        ]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def invoke(self, name: str, arguments: dict[str, Any]) -> str:
        """Route ``name`` to the right tool, return a string for the LLM.

        The LLM consumes tool results as plain strings (OpenAI and
        Anthropic both accept either, but strings round-trip cleanly
        everywhere). We format structured data as JSON so the model
        can parse it back if it wants to.
        """
        handler = self._handlers().get(name)
        if handler is None:
            raise ToolInvocationError(f"unknown tool: {name!r}")
        try:
            return await handler(arguments)
        except ToolInvocationError:
            raise
        except Exception as exc:  # noqa: BLE001 — tool errors are user-visible
            # We intentionally swallow the traceback on the wire.
            # The original error lands in logs for the operator;
            # the LLM sees only the short form.
            logger.exception("tool %s failed", name)
            raise ToolInvocationError(f"{name} failed: {exc}") from exc

    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[str]]]:
        return {
            "search_repo_kb": self._tool_search_repo_kb,
            "get_repo_file": self._tool_get_repo_file,
            "list_code_map": self._tool_list_code_map,
            "list_activated_repos": self._tool_list_activated_repos,
            "create_ticket": self._tool_create_ticket,
            "list_tickets": self._tool_list_tickets,
            "create_artifact_feedback": self._tool_create_artifact_feedback,
            "list_catalog_artifacts": self._tool_list_catalog_artifacts,
            "list_recent_activity": self._tool_list_recent_activity,
            "get_pull_request": self._tool_get_pull_request,
            "list_buckets": self._tool_list_buckets,
            "search_buckets": self._tool_search_buckets,
            "get_catalog_artifact": self._tool_get_catalog_artifact,
            "list_integrations": self._tool_list_integrations,
            "list_pull_requests": self._tool_list_pull_requests,
            "list_pipelines": self._tool_list_pipelines,
            "list_pipeline_runs": self._tool_list_pipeline_runs,
            "get_pipeline_run": self._tool_get_pipeline_run,
            "list_clarifications": self._tool_list_clarifications,
            "list_improvements": self._tool_list_improvements,
            "get_metrics_overview": self._tool_get_metrics_overview,
            "search_code": self._tool_search_code,
            "list_audit_events": self._tool_list_audit_events,
            "list_workspace_members": self._tool_list_workspace_members,
            "list_workspace_invites": self._tool_list_workspace_invites,
            "get_workspace_settings": self._tool_get_workspace_settings,
            "list_workspace_artifact_repos": self._tool_list_workspace_artifact_repos,
            "get_knowledge_bucket": self._tool_get_knowledge_bucket,
            "list_artifact_feedback": self._tool_list_artifact_feedback,
        }

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _tool_search_repo_kb(self, args: dict[str, Any]) -> str:
        import fnmatch

        query = _require_str(args, "query")
        limit = _clamp_int(args.get("limit"), default=5, low=1, high=_MAX_KB_RESULTS)
        include_full = bool(args.get("include_full_content", False))
        path_prefix = args.get("path_prefix")
        path_glob = args.get("path_glob")
        repo_id_raw = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_raw:
            try:
                repo_id = uuid.UUID(str(repo_id_raw))
            except ValueError as exc:
                raise ToolInvocationError(f"invalid repo_id: {repo_id_raw!r}") from exc

        fetch_cap = limit
        if isinstance(path_glob, str) and path_glob.strip():
            fetch_cap = min(_KB_GLOB_PREFETCH_CAP, max(limit * 8, limit))

        qvec = await embed_text(query, settings=self._settings)
        stmt = (
            select(KbChunk, KbChunk.embedding.cosine_distance(qvec).label("dist"))
            .where(KbChunk.workspace_id == self._workspace_id)
            .order_by("dist")
            .limit(fetch_cap)
        )
        if repo_id is not None:
            stmt = stmt.where(KbChunk.repo_id == repo_id)
        if isinstance(path_prefix, str) and path_prefix.strip():
            pref = path_prefix.strip().rstrip("/")
            stmt = stmt.where(
                or_(
                    KbChunk.source_path == pref,
                    KbChunk.source_path.like(pref + "/%"),
                )
            )

        rows = (await self._session.execute(stmt)).all()
        if isinstance(path_glob, str) and path_glob.strip():
            pat = path_glob.strip()
            rows = [
                pair
                for pair in rows
                if fnmatch.fnmatch(pair[0].source_path, pat)
            ]
        rows = rows[:limit]
        if not rows:
            return _json_result({"results": [], "note": "no knowledge indexed"})

        snippet_cap = _MAX_KB_FULL_CHUNK if include_full else 800
        results = []
        for chunk, dist in rows:
            entry: dict[str, Any] = {
                "repo_id": str(chunk.repo_id),
                "path": chunk.source_path,
                "chunk_index": chunk.chunk_index,
                "content_sha": chunk.content_sha,
                "snippet": _truncate(chunk.content, snippet_cap),
                "similarity": round(1.0 - float(dist), 4),
            }
            if include_full:
                entry["content"] = _truncate(chunk.content, _MAX_KB_FULL_CHUNK)
            results.append(entry)
        return _json_result({"results": results})

    async def _tool_get_repo_file(self, args: dict[str, Any]) -> str:
        repo_id = _parse_uuid(args, "repo_id")
        path = _require_str(args, "path")
        ref_sha = args.get("ref_sha")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        start_val = _optional_positive_int(start_line, "start_line")
        end_val = _optional_positive_int(end_line, "end_line")
        if start_val is not None and end_val is not None and end_val < start_val:
            raise ToolInvocationError("end_line must be >= start_line")

        repo, install = await self._resolve_repo_with_install(repo_id)
        gateway = GitHubCodeHost(
            install.installation_id, settings=self._settings
        )
        owner, _, name = repo.full_name.partition("/")
        ref = RepoRef(kind="github", owner=owner, repo=name)
        try:
            blob = await gateway.get_blob(ref, path=path, ref_sha=ref_sha)
        except FileNotFoundError as exc:
            raise ToolInvocationError(str(exc)) from exc
        except IsADirectoryError as exc:
            raise ToolInvocationError(str(exc)) from exc

        if blob.encoding != "utf-8":
            return _json_result(
                {
                    "path": blob.path,
                    "ref": blob.ref,
                    "size": blob.size,
                    "binary": True,
                    "note": "file is binary; contents omitted",
                }
            )

        content = blob.content
        total_lines = content.count("\n") + (0 if content.endswith("\n") else 1)
        slice_applied = False
        returned_start: int | None = None
        returned_end: int | None = None
        if start_val is not None or end_val is not None:
            lines = content.splitlines(keepends=True)
            s = max(1, start_val or 1)
            e = min(len(lines), end_val or len(lines))
            content = "".join(lines[s - 1 : e]) if lines else content
            slice_applied = True
            returned_start = s
            returned_end = e

        truncated = False
        if len(content.encode("utf-8", errors="replace")) > _MAX_FILE_BYTES_RETURNED:
            content = content[:_MAX_FILE_BYTES_RETURNED]
            truncated = True

        return _json_result(
            {
                "path": blob.path,
                "ref": blob.ref,
                "sha": blob.sha,
                "size": blob.size,
                "total_lines": total_lines,
                "sliced": slice_applied,
                "start_line": returned_start,
                "end_line": returned_end,
                "truncated": truncated,
                "content": content,
            }
        )

    async def _tool_list_code_map(self, args: dict[str, Any]) -> str:
        import fnmatch

        repo_id = _parse_uuid(args, "repo_id")
        path_prefix = args.get("path_prefix")
        glob_pat = args.get("glob")
        directories_only = bool(args.get("directories_only", False))

        repo, install = await self._resolve_repo_with_install(repo_id)
        gateway = GitHubCodeHost(install.installation_id, settings=self._settings)
        owner, _, name = repo.full_name.partition("/")
        ref = RepoRef(kind="github", owner=owner, repo=name)
        files = await gateway.list_files(ref, ref_sha=repo.default_branch)

        total_before_filter = len(files)
        if isinstance(path_prefix, str) and path_prefix:
            prefix = path_prefix if path_prefix.endswith("/") else path_prefix
            files = [p for p in files if p.startswith(prefix)]
        if isinstance(glob_pat, str) and glob_pat:
            files = [p for p in files if fnmatch.fnmatch(p, glob_pat)]

        if directories_only:
            prefix_len = 0
            if isinstance(path_prefix, str) and path_prefix:
                pref = path_prefix if path_prefix.endswith("/") else path_prefix + "/"
                prefix_len = len(pref)
                files = [p for p in files if p.startswith(pref)]
            seen: list[str] = []
            seen_set: set[str] = set()
            for p in files:
                tail = p[prefix_len:]
                seg, _, rest = tail.partition("/")
                if not seg or not rest:
                    continue
                if seg not in seen_set:
                    seen.append(seg)
                    seen_set.add(seg)
            truncated = len(seen) > _MAX_CODE_MAP_ENTRIES
            return _json_result(
                {
                    "repo_id": str(repo.id),
                    "full_name": repo.full_name,
                    "default_branch": repo.default_branch,
                    "total_files_before_filter": total_before_filter,
                    "truncated": truncated,
                    "directories": seen[:_MAX_CODE_MAP_ENTRIES],
                }
            )

        truncated = len(files) > _MAX_CODE_MAP_ENTRIES
        return _json_result(
            {
                "repo_id": str(repo.id),
                "full_name": repo.full_name,
                "default_branch": repo.default_branch,
                "total_files_before_filter": total_before_filter,
                "matched": len(files),
                "truncated": truncated,
                "files": files[:_MAX_CODE_MAP_ENTRIES],
            }
        )

    async def _tool_create_ticket(self, args: dict[str, Any]) -> str:
        title = _require_str(args, "title")
        body = _require_str(args, "body")
        labels_raw = args.get("labels") or []
        labels = [str(l) for l in labels_raw if isinstance(l, str)] or None
        project_hint = args.get("project_hint")
        tracker_kind = args.get("tracker")

        tracker = await self._resolve_tracker(tracker_kind, project_hint)
        try:
            created: CreatedTicket = await tracker.create_ticket(
                title=title,
                body=body,
                labels=labels,
                project_hint=project_hint,
            )
        except ValueError as exc:
            raise ToolInvocationError(str(exc)) from exc

        return _json_result(
            {
                "kind": created.ref.kind,
                "display_id": created.display_id,
                "url": created.url,
                "workspace_hint": created.ref.workspace_hint,
                "id": created.ref.id,
            }
        )

    async def _tool_create_artifact_feedback(self, args: dict[str, Any]) -> str:
        artifact_id = _require_str(args, "artifact_id")
        body = _require_str(args, "body")
        context = args.get("context") or {}
        if not isinstance(context, dict):
            raise ToolInvocationError("context must be an object")

        row = ArtifactFeedback(
            workspace_id=self._workspace_id,
            artifact_id=artifact_id,
            created_by_user_id=self._user_id,
            body=body,
            status="open",
            context=context,
        )
        self._session.add(row)
        await self._session.flush()
        return _json_result(
            {
                "id": str(row.id),
                "artifact_id": row.artifact_id,
                "status": row.status,
            }
        )

    async def _tool_list_recent_activity(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(
            args.get("limit"), default=10, low=1, high=_MAX_ACTIVITY_ITEMS
        )
        kinds_raw = args.get("kinds") or ["pipeline_run", "pull_request", "workflow_run"]
        kinds = {str(k) for k in kinds_raw}
        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except ValueError as exc:
                raise ToolInvocationError(
                    f"invalid repo_id: {repo_id_arg!r}"
                ) from exc
        since_dt = _parse_iso_datetime(args.get("since"), "since")

        out: list[dict[str, Any]] = []

        if "pipeline_run" in kinds and repo_id is None:
            stmt = (
                select(PipelineRun)
                .where(PipelineRun.workspace_id == self._workspace_id)
                .order_by(desc(PipelineRun.created_at))
                .limit(limit)
            )
            if since_dt is not None:
                stmt = stmt.where(PipelineRun.created_at >= since_dt)
            rows = (await self._session.execute(stmt)).scalars().all()
            for r in rows:
                out.append(
                    {
                        "kind": "pipeline_run",
                        "id": str(r.id),
                        "status": r.status,
                        "pipeline_id": str(r.pipeline_id),
                        "created_at": r.created_at.isoformat()
                        if r.created_at
                        else None,
                    }
                )

        if "pull_request" in kinds:
            stmt = (
                select(PullRequest)
                .where(PullRequest.workspace_id == self._workspace_id)
                .order_by(desc(PullRequest.updated_at))
                .limit(limit)
            )
            if repo_id is not None:
                stmt = stmt.where(PullRequest.repo_id == repo_id)
            if since_dt is not None:
                stmt = stmt.where(PullRequest.updated_at >= since_dt)
            rows = (await self._session.execute(stmt)).scalars().all()
            for r in rows:
                out.append(
                    {
                        "kind": "pull_request",
                        "id": str(r.id),
                        "title": r.title,
                        "url": r.html_url,
                        "state": r.state,
                        "updated_at": r.updated_at.isoformat()
                        if r.updated_at
                        else None,
                    }
                )

        if "workflow_run" in kinds:
            stmt = (
                select(WorkflowRun)
                .where(WorkflowRun.workspace_id == self._workspace_id)
                .order_by(desc(WorkflowRun.updated_at))
                .limit(limit)
            )
            if repo_id is not None:
                stmt = stmt.where(WorkflowRun.repo_id == repo_id)
            if since_dt is not None:
                stmt = stmt.where(WorkflowRun.updated_at >= since_dt)
            rows = (await self._session.execute(stmt)).scalars().all()
            for r in rows:
                out.append(
                    {
                        "kind": "workflow_run",
                        "id": str(r.id),
                        "name": r.name,
                        "status": r.status,
                        "conclusion": r.conclusion,
                        "url": r.html_url,
                        "updated_at": r.updated_at.isoformat()
                        if r.updated_at
                        else None,
                    }
                )

        out.sort(
            key=lambda x: x.get("updated_at") or x.get("created_at") or "",
            reverse=True,
        )
        return _json_result({"items": out[:limit]})

    async def _tool_list_activated_repos(self, args: dict[str, Any]) -> str:
        del args  # no parameters
        rows = (
            await self._session.execute(
                select(WorkspaceRepo)
                .where(WorkspaceRepo.workspace_id == self._workspace_id)
                .order_by(WorkspaceRepo.full_name)
                .limit(_MAX_REPOS_LISTED)
            )
        ).scalars().all()
        stats_rows = (
            await self._session.execute(
                select(
                    KbChunk.repo_id,
                    func.count(KbChunk.id).label("kb_chunk_count"),
                    func.max(KbChunk.indexed_at).label("kb_last_indexed_at"),
                ).where(KbChunk.workspace_id == self._workspace_id)
                .group_by(KbChunk.repo_id)
            )
        ).all()
        stats_map = {
            str(r.repo_id): (
                int(r.kb_chunk_count),
                r.kb_last_indexed_at.isoformat()
                if r.kb_last_indexed_at
                else None,
            )
            for r in stats_rows
        }
        items = []
        for r in rows:
            cnt, last_ix = stats_map.get(str(r.id), (0, None))
            items.append(
                {
                    "id": str(r.id),
                    "full_name": r.full_name,
                    "default_branch": r.default_branch,
                    "private": bool(r.private),
                    "html_url": r.html_url,
                    "preset": r.preset,
                    "provider": r.provider,
                    "has_github_app": r.installation_id is not None,
                    "kb_chunk_count": cnt,
                    "kb_last_indexed_at": last_ix,
                }
            )
        return _json_result({"repos": items, "count": len(items)})

    async def _tool_list_tickets(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(
            args.get("limit"), default=10, low=1, high=_MAX_TICKETS
        )
        tracker_kind = args.get("tracker")
        project_hint = args.get("project_hint")
        state = args.get("state")
        title_query = args.get("query")
        assignee_me = bool(args.get("assignee_me", False))
        assignee = args.get("assignee")
        assignee_s = str(assignee).strip() if isinstance(assignee, str) else None
        state_s = state.lower().strip() if isinstance(state, str) and state else None
        if state_s and state_s not in {"open", "closed", "all"}:
            raise ToolInvocationError(
                "state must be one of open, closed, all when provided"
            )
        tracker = await self._resolve_tracker(tracker_kind, project_hint)
        try:
            tickets = await tracker.list_tickets(
                limit=limit,
                state=state_s,
                assignee_me=assignee_me,
                query=str(title_query).strip()
                if isinstance(title_query, str)
                else None,
                assignee=assignee_s,
            )
        except Exception as exc:  # noqa: BLE001 — tracker-specific errors
            raise ToolInvocationError(f"tracker list_tickets failed: {exc}") from exc
        # Best-effort label on which backend answered so the model can
        # disambiguate when multiple trackers are wired up.
        kind_hint = tracker_kind or _tracker_kind_of(tracker)
        return _json_result({"tracker": kind_hint, "tickets": tickets})

    async def _tool_get_pull_request(self, args: dict[str, Any]) -> str:
        repo_id = _parse_uuid(args, "repo_id")
        number_raw = args.get("number")
        try:
            number = int(number_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ToolInvocationError(
                f"invalid pull request number: {number_raw!r}"
            ) from exc
        if number < 1:
            raise ToolInvocationError("pull request number must be >= 1")
        include_files = bool(args.get("include_files", True))
        max_files = _clamp_int(
            args.get("max_files"), default=25, low=1, high=_MAX_PR_FILES
        )
        include_reviews = bool(args.get("include_reviews", False))
        include_commits = bool(args.get("include_commits", False))
        include_comments = bool(args.get("include_comments", False))

        repo, install = await self._resolve_repo_with_install(repo_id)
        gateway = GitHubCodeHost(install.installation_id, settings=self._settings)
        owner, _, name = repo.full_name.partition("/")
        ref = PullRequestRef(
            repo=RepoRef(kind="github", owner=owner, repo=name), number=number
        )
        try:
            raw = await gateway.get_pull_request(ref)
        except Exception as exc:  # noqa: BLE001 — GitHub HTTP errors
            raise ToolInvocationError(
                f"failed to fetch PR #{number} in {repo.full_name}: {exc}"
            ) from exc

        created_at = raw.get("created_at")
        updated_at = raw.get("updated_at")
        closed_at = raw.get("closed_at")
        merged_at = raw.get("merged_at")

        summary: dict[str, Any] = {
            "repo": repo.full_name,
            "number": number,
            "title": raw.get("title"),
            "state": raw.get("state"),
            "draft": raw.get("draft"),
            "merged": raw.get("merged"),
            "mergeable": raw.get("mergeable"),
            "mergeable_state": raw.get("mergeable_state"),
            "url": raw.get("html_url"),
            "author": (raw.get("user") or {}).get("login"),
            "base": (raw.get("base") or {}).get("ref"),
            "head": (raw.get("head") or {}).get("ref"),
            "labels": [
                l.get("name") for l in (raw.get("labels") or []) if l.get("name")
            ],
            "assignees": [
                u.get("login")
                for u in (raw.get("assignees") or [])
                if u.get("login")
            ],
            "requested_reviewers": [
                u.get("login")
                for u in (raw.get("requested_reviewers") or [])
                if u.get("login")
            ],
            "comments": raw.get("comments"),
            "review_comments": raw.get("review_comments"),
            "commits": raw.get("commits"),
            "additions": raw.get("additions"),
            "deletions": raw.get("deletions"),
            "changed_files": raw.get("changed_files"),
            "body": _truncate(str(raw.get("body") or ""), 2000),
            "timeline": {
                "created_at": created_at,
                "updated_at": updated_at,
                "closed_at": closed_at,
                "merged_at": merged_at,
                "duration_seconds": _duration_seconds(created_at, merged_at or closed_at),
            },
        }

        if include_files:
            try:
                files = await gateway.list_pull_request_files(ref, limit=max_files)
            except Exception as exc:  # noqa: BLE001
                raise ToolInvocationError(
                    f"failed to list PR files for #{number}: {exc}"
                ) from exc
            for f in files:
                patch = f.get("patch")
                if isinstance(patch, str) and len(patch) > _MAX_PATCH_CHARS:
                    f["patch"] = _truncate(patch, _MAX_PATCH_CHARS)
                    f["patch_truncated"] = True
            summary["files"] = files
            summary["files_truncated"] = (
                isinstance(raw.get("changed_files"), int)
                and raw["changed_files"] > len(files)
            )

        if include_reviews:
            try:
                reviews = await gateway.list_pull_request_reviews(
                    ref, limit=_MAX_PR_REVIEWS
                )
            except Exception as exc:  # noqa: BLE001
                raise ToolInvocationError(
                    f"failed to list PR reviews for #{number}: {exc}"
                ) from exc
            for item in reviews:
                body = item.get("body")
                if isinstance(body, str):
                    item["body"] = _truncate(body, 800)
            summary["reviews"] = reviews

        if include_commits:
            try:
                commits = await gateway.list_pull_request_commits(
                    ref, limit=_MAX_PR_COMMITS
                )
            except Exception as exc:  # noqa: BLE001
                raise ToolInvocationError(
                    f"failed to list PR commits for #{number}: {exc}"
                ) from exc
            for item in commits:
                msg = item.get("message")
                if isinstance(msg, str):
                    item["message"] = _truncate(msg, 400)
            summary["commit_list"] = commits

        if include_comments:
            try:
                comments = await gateway.list_pull_request_issue_comments(
                    ref, limit=_MAX_PR_COMMENTS
                )
            except Exception as exc:  # noqa: BLE001
                raise ToolInvocationError(
                    f"failed to list PR comments for #{number}: {exc}"
                ) from exc
            for item in comments:
                body = item.get("body")
                if isinstance(body, str):
                    item["body"] = _truncate(body, 800)
            summary["issue_comments"] = comments

        return _json_result(summary)

    async def _tool_list_catalog_artifacts(self, args: dict[str, Any]) -> str:
        kind = _require_str(args, "kind").lower()
        if kind not in {"pattern", "collection", "tool"}:
            raise ToolInvocationError(
                f"invalid kind {kind!r}; expected one of "
                "pattern/collection/tool"
            )
        group = args.get("group")
        tag = args.get("tag")
        limit = _clamp_int(
            args.get("limit"), default=50, low=1, high=_MAX_CATALOG_ITEMS
        )

        loader = {
            "pattern": catalog_service.list_patterns,
            "collection": catalog_service.list_collections,
            "tool": catalog_service.list_tools,
        }[kind]
        try:
            entries = loader()
        except catalog_service.CatalogError as exc:
            raise ToolInvocationError(f"catalog unreadable: {exc}") from exc

        if isinstance(group, str) and group:
            entries = [e for e in entries if e.group == group]
        if isinstance(tag, str) and tag:
            entries = [e for e in entries if tag in (e.tags or [])]

        items = [
            {
                "kind": e.kind,
                "id": e.id,
                "artifact_id": f"{e.kind}/{e.id}",
                "name": e.name,
                "version": e.version,
                "channel": e.channel,
                "group": e.group,
                "tags": list(e.tags or []),
                "description": e.description or None,
                "deprecated": e.deprecated,
                "replaced_by": e.replaced_by,
            }
            for e in entries[:limit]
        ]
        return _json_result(
            {
                "kind": kind,
                "total": len(entries),
                "truncated": len(entries) > limit,
                "items": items,
            }
        )

    async def _tool_list_buckets(self, args: dict[str, Any]) -> str:
        include_archived = bool(args.get("include_archived", False))
        limit = _clamp_int(
            args.get("limit"), default=25, low=1, high=_MAX_BUCKETS_LISTED
        )
        stmt = (
            select(KnowledgeBucket)
            .where(KnowledgeBucket.workspace_id == self._workspace_id)
            .order_by(KnowledgeBucket.name)
            .limit(limit)
        )
        if not include_archived:
            stmt = stmt.where(KnowledgeBucket.archived_at.is_(None))
        buckets = (await self._session.execute(stmt)).scalars().all()

        # Phase 5d: ``summary_count`` counts published articles now
        # (mirrored from bucket_summaries in Phase 5b). ``article_count``
        # is the new canonical name — we expose both so the agent can
        # pick up the new one without the old field breaking existing
        # prompts mid-migration.
        items: list[dict[str, Any]] = []
        for b in buckets:
            count_rows = (
                await self._session.execute(
                    select(BucketArticle.id)
                    .where(BucketArticle.bucket_id == b.id)
                    .where(
                        BucketArticle.status == BucketArticleStatus.PUBLISHED
                    )
                    .where(BucketArticle.archived_at.is_(None))
                )
            ).scalars().all()
            items.append(
                {
                    "slug": b.slug,
                    "name": b.name,
                    "description": b.description,
                    "scope_kind": b.scope_kind,
                    "source_kind": b.source_kind,
                    "summary_count": len(count_rows),
                    "article_count": len(count_rows),
                    "archived": b.archived_at is not None,
                    "updated_at": b.updated_at.isoformat() if b.updated_at else None,
                }
            )
        return _json_result({"buckets": items, "count": len(items)})

    async def _tool_get_catalog_artifact(self, args: dict[str, Any]) -> str:
        kind = _require_str(args, "kind").lower()
        artifact_id = _require_str(args, "id")
        if kind not in {"pattern", "collection", "tool"}:
            raise ToolInvocationError(
                f"invalid kind {kind!r}; expected one of "
                "pattern/collection/tool"
            )
        try:
            entries = catalog_service._load_kind(kind)
        except catalog_service.CatalogError as exc:
            raise ToolInvocationError(f"catalog unreadable: {exc}") from exc
        match = next((e for e in entries if e.id == artifact_id), None)
        if match is None:
            raise ToolInvocationError(
                f"{kind}/{artifact_id} not found in catalog"
            )
        body = match.body or ""
        truncated = len(body) > _MAX_ARTIFACT_BODY_CHARS
        if truncated:
            body = body[:_MAX_ARTIFACT_BODY_CHARS]
        return _json_result(
            {
                "kind": match.kind,
                "id": match.id,
                "artifact_id": f"{match.kind}/{match.id}",
                "name": match.name,
                "version": match.version,
                "channel": match.channel,
                "group": match.group,
                "tags": list(match.tags or []),
                "description": match.description or None,
                "deprecated": match.deprecated,
                "replaced_by": match.replaced_by,
                "body": body,
                "body_truncated": truncated,
            }
        )

    async def _tool_list_integrations(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(
            args.get("limit"),
            default=_MAX_INTEGRATIONS,
            low=1,
            high=_MAX_INTEGRATIONS,
        )
        rows = (
            await self._session.execute(
                select(Integration)
                .where(Integration.workspace_id == self._workspace_id)
                .order_by(Integration.kind)
                .limit(limit)
            )
        ).scalars().all()
        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "kind": row.kind,
                    "status": row.status,
                    "has_secret": row.secret_ciphertext is not None,
                    "last_health_at": row.last_health_at.isoformat()
                    if row.last_health_at
                    else None,
                    "last_health_error": row.last_health_error,
                    "config_keys": sorted((row.config or {}).keys()),
                }
            )

        has_github_install = (
            await self._session.execute(
                select(WorkspaceRepo.id)
                .where(
                    WorkspaceRepo.workspace_id == self._workspace_id,
                    WorkspaceRepo.installation_id.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        if has_github_install and not any(i["kind"] == "github_app" for i in items):
            items.append(
                {
                    "kind": "github_app",
                    "status": "active",
                    "has_secret": True,
                    "last_health_at": None,
                    "last_health_error": None,
                    "config_keys": [],
                    "note": (
                        "Derived from activated repos; GitHub App "
                        "credentials come from the installation "
                        "token."
                    ),
                }
            )
        return _json_result({"integrations": items, "count": len(items)})

    async def _tool_list_pull_requests(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(
            args.get("limit"), default=20, low=1, high=_MAX_PRS_LISTED
        )
        state = (args.get("state") or "all").lower()
        if state not in {"open", "closed", "merged", "all"}:
            raise ToolInvocationError(
                f"invalid state {state!r}; expected open/closed/merged/all"
            )
        author = args.get("author")
        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except ValueError as exc:
                raise ToolInvocationError(
                    f"invalid repo_id: {repo_id_arg!r}"
                ) from exc

        stmt = (
            select(PullRequest)
            .where(PullRequest.workspace_id == self._workspace_id)
            .order_by(desc(PullRequest.updated_at))
            .limit(limit)
        )
        if repo_id is not None:
            stmt = stmt.where(PullRequest.repo_id == repo_id)
        if state == "open":
            stmt = stmt.where(PullRequest.state == "open")
        elif state == "closed":
            stmt = stmt.where(PullRequest.state == "closed")
        elif state == "merged":
            stmt = stmt.where(PullRequest.merged.is_(True))
        if isinstance(author, str) and author:
            stmt = stmt.where(PullRequest.author == author)

        rows = (await self._session.execute(stmt)).scalars().all()
        items = [
            {
                "id": str(r.id),
                "number": r.number,
                "repo_full_name": r.repo_full_name,
                "title": r.title,
                "state": r.state,
                "merged": r.merged,
                "draft": r.draft,
                "author": r.author,
                "url": r.html_url,
                "opened_at": r.opened_at.isoformat() if r.opened_at else None,
                "merged_at": r.merged_at.isoformat() if r.merged_at else None,
                "closed_at": r.closed_at.isoformat() if r.closed_at else None,
                "updated_at": r.updated_at_external.isoformat()
                if r.updated_at_external
                else None,
            }
            for r in rows
        ]
        return _json_result({"pull_requests": items, "count": len(items)})

    async def _tool_list_pipelines(self, args: dict[str, Any]) -> str:
        enabled_only = bool(args.get("enabled_only", False))
        limit = _clamp_int(
            args.get("limit"),
            default=_MAX_PIPELINES,
            low=1,
            high=_MAX_PIPELINES,
        )
        stmt = (
            select(Pipeline)
            .where(Pipeline.workspace_id == self._workspace_id)
            .order_by(Pipeline.kind, Pipeline.name)
            .limit(limit)
        )
        if enabled_only:
            stmt = stmt.where(Pipeline.enabled.is_(True))
        rows = (await self._session.execute(stmt)).scalars().all()
        items = [
            {
                "id": str(r.id),
                "kind": r.kind,
                "name": r.name,
                "workflow_id": r.workflow_id,
                "enabled": r.enabled,
                "repo_id": str(r.repo_id) if r.repo_id else None,
                "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
                "last_run_status": r.last_run_status,
            }
            for r in rows
        ]
        return _json_result({"pipelines": items, "count": len(items)})

    async def _tool_list_pipeline_runs(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(
            args.get("limit"),
            default=15,
            low=1,
            high=_MAX_PIPELINE_RUNS,
        )
        pipeline_id_arg = args.get("pipeline_id")
        pipeline_id: uuid.UUID | None = None
        if pipeline_id_arg:
            try:
                pipeline_id = uuid.UUID(str(pipeline_id_arg))
            except ValueError as exc:
                raise ToolInvocationError(
                    f"invalid pipeline_id: {pipeline_id_arg!r}"
                ) from exc
        status_filter = args.get("status")

        stmt = (
            select(PipelineRun)
            .where(PipelineRun.workspace_id == self._workspace_id)
            .order_by(desc(PipelineRun.created_at))
            .limit(limit)
        )
        if pipeline_id is not None:
            stmt = stmt.where(PipelineRun.pipeline_id == pipeline_id)
        if isinstance(status_filter, str) and status_filter:
            stmt = stmt.where(PipelineRun.status == status_filter)

        rows = (await self._session.execute(stmt)).scalars().all()
        items = [
            {
                "id": str(r.id),
                "pipeline_id": str(r.pipeline_id),
                "status": r.status,
                "trigger": r.trigger,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_seconds": (
                    int((r.finished_at - r.started_at).total_seconds())
                    if r.started_at and r.finished_at
                    else None
                ),
                "summary": r.summary,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return _json_result({"runs": items, "count": len(items)})

    async def _tool_get_pipeline_run(self, args: dict[str, Any]) -> str:
        run_id = _parse_uuid(args, "run_id")
        run = (
            await self._session.execute(
                select(PipelineRun).where(
                    PipelineRun.workspace_id == self._workspace_id,
                    PipelineRun.id == run_id,
                )
            )
        ).scalars().first()
        if run is None:
            raise ToolInvocationError(
                f"pipeline run {run_id} not found in this workspace"
            )
        pipeline = await self._session.get(Pipeline, run.pipeline_id)
        return _json_result(
            {
                "id": str(run.id),
                "pipeline_id": str(run.pipeline_id),
                "pipeline_kind": pipeline.kind if pipeline else None,
                "pipeline_name": pipeline.name if pipeline else None,
                "workflow_id": pipeline.workflow_id if pipeline else None,
                "status": run.status,
                "trigger": run.trigger,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "duration_seconds": (
                    int((run.finished_at - run.started_at).total_seconds())
                    if run.started_at and run.finished_at
                    else None
                ),
                "summary": run.summary,
                "payload": run.payload or {},
                "created_at": run.created_at.isoformat() if run.created_at else None,
            }
        )

    async def _tool_list_clarifications(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(
            args.get("limit"),
            default=20,
            low=1,
            high=_MAX_CLARIFICATIONS,
        )
        status_filter = args.get("status")
        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except ValueError as exc:
                raise ToolInvocationError(
                    f"invalid repo_id: {repo_id_arg!r}"
                ) from exc

        stmt = (
            select(Clarification)
            .where(Clarification.workspace_id == self._workspace_id)
            .order_by(desc(Clarification.created_at))
            .limit(limit)
        )
        if isinstance(status_filter, str) and status_filter:
            stmt = stmt.where(Clarification.status == status_filter)
        if repo_id is not None:
            stmt = stmt.where(Clarification.repo_id == repo_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        items = [
            {
                "id": str(r.id),
                "status": r.status,
                "ticket_ref": r.ticket_ref,
                "repo_id": str(r.repo_id) if r.repo_id else None,
                "pipeline_run_id": str(r.pipeline_run_id) if r.pipeline_run_id else None,
                "question": _truncate(r.question, 800),
                "answer": _truncate(r.answer, 800) if r.answer else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "answered_at": r.answered_at.isoformat() if r.answered_at else None,
            }
            for r in rows
        ]
        return _json_result({"clarifications": items, "count": len(items)})

    async def _tool_list_improvements(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(
            args.get("limit"),
            default=20,
            low=1,
            high=_MAX_IMPROVEMENTS,
        )
        decision_filter = args.get("decision")
        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except ValueError as exc:
                raise ToolInvocationError(
                    f"invalid repo_id: {repo_id_arg!r}"
                ) from exc

        stmt = (
            select(Improvement)
            .where(Improvement.workspace_id == self._workspace_id)
            .order_by(desc(Improvement.created_at))
            .limit(limit)
        )
        if isinstance(decision_filter, str) and decision_filter:
            stmt = stmt.where(Improvement.decision == decision_filter)
        if repo_id is not None:
            stmt = stmt.where(Improvement.repo_id == repo_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        items = [
            {
                "id": str(r.id),
                "kind": r.kind,
                "title": r.title,
                "body": _truncate(r.body, 800),
                "impact": r.impact,
                "effort": r.effort,
                "decision": r.decision,
                "decision_reason": r.decision_reason,
                "next_action_url": r.next_action_url,
                "repo_id": str(r.repo_id) if r.repo_id else None,
                "pipeline_run_id": str(r.pipeline_run_id) if r.pipeline_run_id else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            }
            for r in rows
        ]
        return _json_result({"improvements": items, "count": len(items)})

    async def _tool_get_metrics_overview(self, args: dict[str, Any]) -> str:
        window_label = (args.get("window") or "30d").lower()
        if window_label not in {"7d", "30d", "90d"}:
            raise ToolInvocationError(
                f"invalid window {window_label!r}; expected 7d/30d/90d"
            )
        # Lazy import: the metrics route module transitively imports chat.py
        # which imports us, so a top-level import would deadlock at startup.
        from backend.app.api.v1.routes import metrics as metrics_routes

        window = metrics_routes._resolve_window(window_label)
        pipelines_panel = await metrics_routes._pipelines_panel(
            self._session, self._workspace_id
        )
        runs_panel = await metrics_routes._runs_panel(
            self._session, self._workspace_id, window
        )
        clarifications_panel = await metrics_routes._clarifications_panel(
            self._session, self._workspace_id, window
        )
        improvements_panel = await metrics_routes._improvements_panel(
            self._session, self._workspace_id, window
        )
        chat_panel = await metrics_routes._chat_panel(
            self._session, self._workspace_id, window
        )
        dora_panel = await metrics_routes._dora_panel(
            self._session, self._workspace_id, window
        )
        return _json_result(
            {
                "window": window_label,
                "window_days": window.days,
                "window_start": window.start.isoformat(),
                "window_end": window.end.isoformat(),
                "pipelines": pipelines_panel.model_dump(),
                "runs": runs_panel.model_dump(),
                "clarifications": clarifications_panel.model_dump(),
                "improvements": improvements_panel.model_dump(),
                "chat": chat_panel.model_dump(),
                "dora": dora_panel.model_dump(),
            }
        )

    async def _tool_search_code(self, args: dict[str, Any]) -> str:
        repo_id = _parse_uuid(args, "repo_id")
        q = _require_str(args, "query")
        language = args.get("language")
        limit = _clamp_int(
            args.get("limit"), default=15, low=1, high=_MAX_CODE_SEARCH
        )
        repo, install = await self._resolve_repo_with_install(repo_id)
        gateway = GitHubCodeHost(install.installation_id, settings=self._settings)
        owner, _, name = repo.full_name.partition("/")
        ref = RepoRef(kind="github", owner=owner, repo=name)
        lang = str(language).strip() if isinstance(language, str) else None
        try:
            hits = await gateway.search_code(
                ref,
                q,
                language=lang or None,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolInvocationError(
                f"code search failed (GitHub rate limit or query error): {exc}"
            ) from exc
        return _json_result(
            {
                "repo": repo.full_name,
                "query": q,
                "language": lang,
                "hits": hits,
                "count": len(hits),
            }
        )

    async def _tool_list_audit_events(self, args: dict[str, Any]) -> str:
        from backend.app.api.v1.routes import audit as audit_routes
        from backend.app.api.v1.routes.workspaces import ROLES_ADMIN

        await self._require_workspace_role(ROLES_ADMIN)
        limit = _clamp_int(
            args.get("limit"), default=30, low=1, high=_MAX_AUDIT_EVENTS
        )
        before_raw = args.get("before_id")
        before: int | None = None
        if before_raw is not None:
            try:
                before = int(before_raw)
            except (TypeError, ValueError) as exc:
                raise ToolInvocationError("before_id must be an integer") from exc
            if before < 1:
                raise ToolInvocationError("before_id must be >= 1")

        def _audit_call(fn: Any, *fn_args: Any) -> Any:
            try:
                return fn(*fn_args)
            except HTTPException as exc:
                detail = exc.detail
                msg = detail if isinstance(detail, str) else str(detail)
                raise ToolInvocationError(msg) from exc

        action_f = _audit_call(
            audit_routes._validate_action_filter, args.get("action")
        )
        target_f = _audit_call(
            audit_routes._validate_target_kind, args.get("target_kind")
        )
        actor_f = audit_routes._coerce_actor_filter(args.get("actor"))
        since_dt = _audit_call(
            audit_routes._coerce_datetime, args.get("since"), "since"
        )
        until_dt = _audit_call(
            audit_routes._coerce_datetime, args.get("until"), "until"
        )
        if since_dt is not None and until_dt is not None and since_dt > until_dt:
            raise ToolInvocationError("since must be <= until")

        actor_user = aliased(TenancyUser)
        actor_token = aliased(ApiToken)
        stmt = (
            select(AuditLog, actor_user, actor_token)
            .outerjoin(actor_user, actor_user.id == AuditLog.actor_user_id)
            .outerjoin(actor_token, actor_token.id == AuditLog.actor_token_id)
            .where(AuditLog.workspace_id == self._workspace_id)
            .order_by(AuditLog.id.desc())
            .limit(limit + 1)
        )
        if before is not None:
            stmt = stmt.where(AuditLog.id < before)
        if action_f is not None:
            stmt = stmt.where(
                (AuditLog.action == action_f)
                | AuditLog.action.like(f"{action_f}.%")
                | AuditLog.action.like(f"{action_f}%")
            )
        if target_f is not None:
            stmt = stmt.where(AuditLog.target_kind == target_f)
        if actor_f is not None:
            needle = f"%{actor_f}%"
            stmt = stmt.where(
                func.lower(actor_user.email).like(needle)
                | func.lower(actor_token.name).like(needle)
            )
        if since_dt is not None:
            stmt = stmt.where(AuditLog.created_at >= since_dt)
        if until_dt is not None:
            stmt = stmt.where(AuditLog.created_at < until_dt)

        rows = (await self._session.execute(stmt)).all()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = []
        for entry, user, token in visible:
            items.append(
                {
                    "id": entry.id,
                    "action": entry.action,
                    "target_kind": entry.target_kind,
                    "target_id": entry.target_id,
                    "payload": entry.payload or {},
                    "created_at": entry.created_at.isoformat()
                    if entry.created_at
                    else None,
                    "actor": {
                        "user_id": str(user.id) if user is not None else None,
                        "user_email": user.email if user is not None else None,
                        "token_id": str(token.id) if token is not None else None,
                        "token_name": token.name if token is not None else None,
                    },
                }
            )
        next_cursor = items[-1]["id"] if has_more and items else None
        return _json_result(
            {"events": items, "count": len(items), "next_before_id": next_cursor}
        )

    async def _tool_list_workspace_members(self, args: dict[str, Any]) -> str:
        from backend.app.api.v1.routes.workspaces import ROLES_READ

        del args
        await self._require_workspace_role(ROLES_READ)
        stmt = (
            select(WorkspaceMember, TenancyUser)
            .join(TenancyUser, TenancyUser.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == self._workspace_id)
            .order_by(WorkspaceMember.created_at.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        items = [
            {
                "member_id": str(m.id),
                "user_id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "role": m.role,
                "pending": u.external_subject is None and u.password_hash is None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m, u in rows
        ]
        return _json_result({"members": items, "count": len(items)})

    async def _tool_list_workspace_invites(self, args: dict[str, Any]) -> str:
        from backend.app.api.v1.routes.workspaces import ROLES_ADMIN

        del args
        await self._require_workspace_role(ROLES_ADMIN)
        rows = (
            await self._session.execute(
                select(WorkspaceInvite)
                .where(WorkspaceInvite.workspace_id == self._workspace_id)
                .order_by(WorkspaceInvite.created_at.desc())
            )
        ).scalars().all()
        inviter_ids = [r.invited_by_user_id for r in rows if r.invited_by_user_id]
        inviter_map: dict[uuid.UUID, str] = {}
        if inviter_ids:
            urows = (
                await self._session.execute(
                    select(TenancyUser).where(
                        TenancyUser.id.in_({*inviter_ids})
                    )
                )
            ).scalars().all()
            inviter_map = {u.id: u.email for u in urows}
        items = [
            {
                "id": str(r.id),
                "email": r.email,
                "role": r.role,
                "invited_by_email": inviter_map.get(r.invited_by_user_id)
                if r.invited_by_user_id
                else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "accepted_at": r.accepted_at.isoformat() if r.accepted_at else None,
                "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return _json_result({"invites": items, "count": len(items)})

    async def _tool_get_workspace_settings(self, args: dict[str, Any]) -> str:
        from backend.app.api.v1.routes.workspaces import ROLES_READ

        del args
        await self._require_workspace_role(ROLES_READ)
        ws = await self._session.get(Workspace, self._workspace_id)
        if ws is None:
            raise ToolInvocationError("workspace not found")
        return _json_result(
            {
                "id": str(ws.id),
                "org_id": str(ws.org_id),
                "slug": ws.slug,
                "name": ws.name,
                "catalog_sources": dict(ws.catalog_sources or {}),
                "created_at": ws.created_at.isoformat() if ws.created_at else None,
            }
        )

    async def _tool_list_workspace_artifact_repos(self, args: dict[str, Any]) -> str:
        from backend.app.api.v1.routes.workspaces import ROLES_READ

        del args
        await self._require_workspace_role(ROLES_READ)
        rows = (
            await self._session.execute(
                select(ArtifactRepo)
                .where(ArtifactRepo.workspace_id == self._workspace_id)
                .order_by(ArtifactRepo.created_at.asc())
            )
        ).scalars().all()
        items = [
            {
                "id": str(r.id),
                "kind": r.kind,
                "url": r.url,
                "default_branch": r.default_branch,
                "last_sync_at": r.last_sync_at.isoformat()
                if r.last_sync_at
                else None,
                "last_sync_sha": r.last_sync_sha,
                "last_sync_error": r.last_sync_error,
            }
            for r in rows
        ]
        return _json_result({"artifact_repos": items, "count": len(items)})

    async def _tool_get_knowledge_bucket(self, args: dict[str, Any]) -> str:
        # Phase 5d: serves articles from ``bucket_articles``, keeping the
        # ``summaries`` JSON key for backwards-compat with the LLM's
        # frozen tool contract (renaming it would force a model retrain
        # of the agent's tool-use pattern, which isn't worth the win).
        # We also expose ``articles`` alongside it — structurally the
        # same list, but the key is the new canonical name for the
        # Phase 4 frontend rework.
        slug = _require_str(args, "slug")
        include_summaries = bool(args.get("include_summaries", True))
        summary_limit = _clamp_int(
            args.get("summary_limit"),
            default=20,
            low=1,
            high=_MAX_BUCKET_SUMMARIES,
        )
        row = (
            await self._session.execute(
                select(KnowledgeBucket).where(
                    KnowledgeBucket.workspace_id == self._workspace_id,
                    KnowledgeBucket.slug == slug.strip(),
                )
            )
        ).scalars().first()
        if row is None:
            raise ToolInvocationError(f"bucket {slug!r} not found")
        out: dict[str, Any] = {
            "slug": row.slug,
            "name": row.name,
            "description": row.description,
            "scope_kind": row.scope_kind,
            "source_kind": row.source_kind,
            "archived": row.archived_at is not None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "summaries": [],
            "articles": [],
        }
        if include_summaries:
            articles = (
                await self._session.execute(
                    select(BucketArticle)
                    .where(BucketArticle.bucket_id == row.id)
                    .where(
                        BucketArticle.status == BucketArticleStatus.PUBLISHED
                    )
                    .where(BucketArticle.archived_at.is_(None))
                    .order_by(desc(BucketArticle.created_at))
                    .limit(summary_limit)
                )
            ).scalars().all()
            payload = [
                {
                    "id": str(a.id),
                    "slug": a.slug,
                    "title": a.title,
                    # ``summary`` for backward compat; ``body_md`` is
                    # the new canonical field name matching the column.
                    "summary": _truncate(a.body_md, 2000),
                    "body_md": _truncate(a.body_md, 2000),
                    "version": a.version,
                    "created_at": (
                        a.created_at.isoformat() if a.created_at else None
                    ),
                }
                for a in articles
            ]
            out["summaries"] = payload
            out["articles"] = payload
        return _json_result(out)

    async def _tool_list_artifact_feedback(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(
            args.get("limit"), default=25, low=1, high=_MAX_ARTIFACT_FEEDBACK_LIST
        )
        status_filter = args.get("status")
        artifact_id = args.get("artifact_id")
        stmt = (
            select(ArtifactFeedback)
            .where(ArtifactFeedback.workspace_id == self._workspace_id)
            .order_by(desc(ArtifactFeedback.created_at))
            .limit(limit)
        )
        if isinstance(status_filter, str) and status_filter:
            stmt = stmt.where(ArtifactFeedback.status == status_filter)
        if isinstance(artifact_id, str) and artifact_id.strip():
            stmt = stmt.where(ArtifactFeedback.artifact_id == artifact_id.strip())
        rows = (await self._session.execute(stmt)).scalars().all()
        items = [
            {
                "id": str(r.id),
                "artifact_id": r.artifact_id,
                "status": r.status,
                "body": _truncate(r.body, 600),
                "linked_pr_url": r.linked_pr_url,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return _json_result({"feedback": items, "count": len(items)})

    async def _tool_search_buckets(self, args: dict[str, Any]) -> str:
        # Phase 5d: ranks over ``bucket_articles`` instead of
        # ``bucket_summaries``. Mirrors :meth:`TopicService.retrieve_buckets`
        # — same WHERE clause (published + unarchived + embedded +
        # agent_memory scope). The LLM-visible shape is unchanged; only
        # the data source moved. ``bucket_summaries`` is still written
        # by ``pack_topic`` (dual-write), so nothing downstream that
        # reads the legacy table breaks.
        query = _require_str(args, "query")
        limit = _clamp_int(
            args.get("limit"), default=4, low=1, high=_MAX_BUCKET_RESULTS
        )
        qvec = await embed_text(query, settings=self._settings)

        stmt = (
            select(
                BucketArticle,
                KnowledgeBucket.slug,
                KnowledgeBucket.name,
                BucketArticle.embedding.cosine_distance(qvec).label("dist"),
            )
            .join(
                KnowledgeBucket,
                KnowledgeBucket.id == BucketArticle.bucket_id,
            )
            .where(KnowledgeBucket.workspace_id == self._workspace_id)
            .where(KnowledgeBucket.archived_at.is_(None))
            .where(BucketArticle.status == BucketArticleStatus.PUBLISHED)
            .where(BucketArticle.archived_at.is_(None))
            .where(BucketArticle.embedding.isnot(None))
            # Keep the "prior conversations" semantic — the tool spec
            # tells the LLM this is about packed chats. Broadening to
            # repo_files would change the contract.
            .where(KnowledgeBucket.source_kind == BucketSource.AGENT_MEMORY)
            # Phase 8: don't leak another user's ``scope=user`` memory
            # through the agent search surface. Mirrors
            # ``TopicService.retrieve_buckets`` + Phase 3 resolver.
            .where(visible_to_user_clause(self._user_id))
            .order_by("dist")
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        results = []
        for article, slug, name, dist in rows:
            results.append(
                {
                    "bucket_slug": slug,
                    "bucket_name": name,
                    "title": article.title,
                    "summary": _truncate(article.body_md, 600),
                    "similarity": round(1.0 - float(dist), 4),
                }
            )
        return _json_result({"results": results})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _require_workspace_role(
        self, accept: tuple[str, ...]
    ) -> WorkspaceMember:
        row = (
            await self._session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == self._workspace_id,
                    WorkspaceMember.user_id == self._user_id,
                )
            )
        ).scalars().first()
        if row is None:
            raise ToolInvocationError("workspace membership not found")
        if row.role not in accept:
            raise ToolInvocationError(
                f"insufficient role for this tool (need one of {accept}; "
                f"you are {row.role!r})"
            )
        return row

    async def _resolve_repo_with_install(
        self, repo_id: uuid.UUID
    ) -> tuple[WorkspaceRepo, GitHubInstallation]:
        row = (
            await self._session.execute(
                select(WorkspaceRepo).where(
                    WorkspaceRepo.workspace_id == self._workspace_id,
                    WorkspaceRepo.id == repo_id,
                )
            )
        ).scalars().first()
        if row is None:
            raise ToolInvocationError(f"repo {repo_id} not activated for this workspace")
        if row.installation_id is None:
            raise ToolInvocationError(
                f"repo {repo_id} has no GitHub App installation"
            )
        install = await self._session.get(
            GitHubInstallation, row.installation_id
        )
        if install is None or install.suspended_at is not None:
            raise ToolInvocationError(
                "GitHub App installation is missing or suspended; reinstall Ship."
            )
        return row, install

    async def _resolve_tracker(
        self, preferred_kind: str | None, project_hint: str | None
    ) -> TrackerGateway:
        """Pick a tracker for the workspace.

        Priority:

        1. If ``preferred_kind`` is set, use it (fail if it's not
           configured).
        2. Otherwise, if the workspace has exactly one tracker-type
           integration, use it.
        3. Otherwise, raise — the LLM has to ask the user to pick.

        ``project_hint`` is forwarded to the tracker's
        :meth:`create_ticket`; we only use it here to choose a
        GitHub Issues repo (``owner/repo``) when the GitHub tracker
        kind is selected.
        """
        candidates: dict[str, Integration] = {}
        integrations = (
            await self._session.execute(
                select(Integration).where(
                    Integration.workspace_id == self._workspace_id
                )
            )
        ).scalars().all()
        for row in integrations:
            if row.kind in {"linear", "notion"} and row.secret_ciphertext:
                candidates[row.kind] = row

        # Any activated repo gives us a GitHub Issues target without
        # a dedicated Integration row — the App installation token
        # carries ``issues:write``.
        has_github_install = (
            await self._session.execute(
                select(WorkspaceRepo.id).where(
                    WorkspaceRepo.workspace_id == self._workspace_id,
                    WorkspaceRepo.installation_id.is_not(None),
                ).limit(1)
            )
        ).scalar_one_or_none() is not None

        available = set(candidates)
        if has_github_install:
            available.add("github_issues")

        if preferred_kind:
            if preferred_kind not in available:
                raise ToolInvocationError(
                    f"tracker {preferred_kind!r} is not configured for this workspace"
                )
            chosen = preferred_kind
        elif len(available) == 1:
            chosen = next(iter(available))
        else:
            raise ToolInvocationError(
                f"multiple trackers available ({sorted(available)}); pass tracker="
            )

        if chosen in {"linear", "notion"}:
            row = candidates[chosen]
            try:
                token = decrypt(row.secret_ciphertext or b"")
            except Exception as exc:  # noqa: BLE001
                raise ToolInvocationError(
                    f"{chosen} token is unreadable; rotate the integration."
                ) from exc
            if chosen == "linear":
                return LinearTracker(token)
            return NotionTracker(token)

        # GitHub Issues path.
        owner, repo_name = await self._resolve_github_issues_target(project_hint)
        install_id = await self._resolve_github_install_id_for_repo(
            owner, repo_name
        )
        return GitHubIssuesTracker(
            installation_id=install_id,
            owner=owner,
            repo=repo_name,
            settings=self._settings,
        )

    async def _resolve_github_issues_target(
        self, project_hint: str | None
    ) -> tuple[str, str]:
        if project_hint and "/" in project_hint:
            owner, _, repo = project_hint.partition("/")
            if owner and repo:
                return owner, repo
        # Fall back to the single activated repo.
        rows = (
            await self._session.execute(
                select(WorkspaceRepo).where(
                    WorkspaceRepo.workspace_id == self._workspace_id,
                    WorkspaceRepo.installation_id.is_not(None),
                )
            )
        ).scalars().all()
        if len(rows) == 1:
            owner, _, repo = rows[0].full_name.partition("/")
            return owner, repo
        raise ToolInvocationError(
            "multiple GitHub repos activated; pass project_hint='owner/repo'"
        )

    async def _resolve_github_install_id_for_repo(
        self, owner: str, repo: str
    ) -> int:
        full_name = f"{owner}/{repo}"
        row = (
            await self._session.execute(
                select(WorkspaceRepo).where(
                    WorkspaceRepo.workspace_id == self._workspace_id,
                    WorkspaceRepo.full_name == full_name,
                )
            )
        ).scalars().first()
        if row is None or row.installation_id is None:
            raise ToolInvocationError(
                f"repo {full_name} is not activated for this workspace"
            )
        install = await self._session.get(GitHubInstallation, row.installation_id)
        if install is None or install.suspended_at is not None:
            raise ToolInvocationError(
                f"GitHub App installation for {full_name} is missing or suspended"
            )
        return install.installation_id


# ---------------------------------------------------------------------------
# Tiny value helpers — kept private to make the tool methods skimmable
# ---------------------------------------------------------------------------


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolInvocationError(f"missing required string arg: {key!r}")
    return value


def _parse_uuid(args: dict[str, Any], key: str) -> uuid.UUID:
    raw = args.get(key)
    if not isinstance(raw, str) or not raw:
        raise ToolInvocationError(f"missing required UUID arg: {key!r}")
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ToolInvocationError(f"invalid UUID for {key!r}: {raw!r}") from exc


def _clamp_int(value: Any, *, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(n, high))


def _optional_positive_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolInvocationError(f"{key} must be an integer") from exc
    if n < 1:
        raise ToolInvocationError(f"{key} must be >= 1")
    return n


def _parse_iso_datetime(value: Any, key: str):
    """Accept an ISO-8601 string (with optional ``Z``) and return ``datetime``.

    Returns ``None`` when ``value`` is missing / empty; raises
    :class:`ToolInvocationError` for unparseable input so the LLM
    sees the mistake and can retry with a different argument.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ToolInvocationError(f"{key} must be an ISO-8601 string")
    from datetime import datetime, timezone

    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolInvocationError(
            f"{key} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _truncate(text: str | None, max_chars: int) -> str:
    if not text:
        return text or ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _json_result(payload: Any) -> str:
    # Tool results round-trip as strings; JSON keeps structure usable
    # by the LLM without a second-round parse. ``ensure_ascii=False``
    # preserves non-ASCII source content verbatim (paths, titles).
    import json

    return json.dumps(payload, ensure_ascii=False)


def _tracker_kind_of(tracker: TrackerGateway) -> str:
    """Reverse-map a concrete tracker instance to its vendor slug.

    Only used for annotating ``list_tickets`` results when the LLM
    didn't pass an explicit ``tracker`` arg — purely cosmetic, but
    it keeps the model honest about which backend answered.
    """
    if isinstance(tracker, LinearTracker):
        return "linear"
    if isinstance(tracker, NotionTracker):
        return "notion"
    if isinstance(tracker, GitHubIssuesTracker):
        return "github_issues"
    return "unknown"


def _duration_seconds(start_iso: Any, end_iso: Any) -> int | None:
    """Return ``(end - start)`` in whole seconds when both are ISO-8601.

    Used by :meth:`_tool_get_pull_request` to answer "how long did the
    PR stay open?" without the model having to parse timestamps itself.
    Returns ``None`` when either side is missing or unparseable — the
    model then falls back to eyeballing the ``timeline`` dict.
    """
    if not isinstance(start_iso, str) or not isinstance(end_iso, str):
        return None
    from datetime import datetime

    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = end - start
    return int(delta.total_seconds())


__all__ = ["ToolBox", "ToolInvocationError"]
