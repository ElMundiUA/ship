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
  artifact id (``pattern/common-base``, …). Persisted to
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
- :meth:`search_workspace_kb` — workspace-wide vector search across
  every repo's ``.ship/knowledge`` + workspace-canonical buckets
  (PR-7C), with a repo-match band that prefers hits from the chat's
  active repo. Complements :meth:`search_repo_kb` when the question
  is platform/organisation-wide.
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

Phase 6 additions (Wave A — IA tools surfacing the Inbox / Plays /
Runs / Coverage / Knowledge-bucket reorganisation):

- :meth:`inbox_list` — paginated unified Inbox list (clarifications,
  improvements, failures, approvals, exceptions) with type / status
  / owner / repo / play_key filters and an opaque cursor.
- :meth:`inbox_counts` — sidebar count aggregates by status + type
  for the workspace, scoped to ``me`` or ``all``.
- :meth:`inbox_get` — full detail for one inbox item including its
  event timeline.
- :meth:`inbox_routing_list` — workspace's inbox routing rules plus
  the configuration-health ``handles`` summary.
- :meth:`inbox_routing_preview` — side-effect-free dry run of the
  resolver against a sample item.
- :meth:`plays_coverage` — per-Play coverage rollup (covered vs
  uncovered repos, coverage %, sample uncovered ids).
- :meth:`plays_list` — Plays catalog list (richer than
  :meth:`list_catalog_artifacts` — exposes category, critical,
  default inbox profile).
- :meth:`plays_get` — single Play detail (frontmatter + body).
- :meth:`runs_query` — outcome-first pipeline-run list with play /
  repo / status / trigger / escalations / since filters.
- :meth:`run_detail` — full run payload (RunSummary outcome,
  artifacts, findings, escalations).
- :meth:`automations_list` — combined view of pipelines, lanes,
  and fleet_lanes for the workspace.
- :meth:`repo_intel_get` — current ``repo_intel`` snapshot for one
  repo (languages, frameworks, structure, …).
- :meth:`knowledge_search_v2` — extended workspace knowledge search
  with explicit repo / bucket filters and an optional ``intel_facts``
  flag that prepends a synthesised ``repo_intel`` summary hit.

The JSON schemas live next to each method (single source of truth,
no drift). Vendors that can't consume a method share the same
schema; the dispatch layer (:meth:`ToolBox.invoke`) lives here.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy import desc, func, or_, select, tuple_
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
from backend.app.db.models.fleet_lanes import FleetLane
from backend.app.db.models.inbox import (
    InboxItem,
    InboxItemEvent,
    InboxRoutingRule,
    MemberGroup,
    RunEscalation,
)
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.lanes import Lane
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
# Hard ceiling on the navigator's ``send_email_to_self`` tool. Even
# though the destination is fixed to the caller's own address, a
# misbehaving model could fan out dozens of emails in a single chat
# turn — this cap keeps the worst case to one digest per ~12 minutes.
# Tracked in-process; resets on restart, which is the right "soft
# eventual reset" behaviour for an abuse guardrail.
_NAVIGATOR_EMAIL_HOURLY_CAP = 5
_NAVIGATOR_EMAIL_MAX_SUBJECT = 120
_NAVIGATOR_EMAIL_MAX_BODY = 16_000
_navigator_email_history: dict[uuid.UUID, list[float]] = {}
_KB_GLOB_PREFETCH_CAP = 80
# PR-7C: hard cap on the workspace-knowledge tool's hit list. The LLM
# rarely benefits from more than a handful of results and the 400-char
# snippet per hit adds up fast — 25 keeps the worst case bounded at
# ~10 kB of tool output.
_MAX_WORKSPACE_KB_RESULTS = 25

# Phase 6 — caps for the new IA tools. Sized to keep the worst-case
# tool-output payload bounded (each list tool tops out around 25–100
# rows, projected to ~100 chars of JSON each).
_MAX_INBOX_LIST = 100
_DEFAULT_INBOX_LIST = 25
_MAX_RUNS_LIST = 100
_DEFAULT_RUNS_LIST = 25
_MAX_PLAYS_LIST = 200
_DEFAULT_PLAYS_LIST = 50
_MAX_AUTOMATIONS_LIST = 200
_DEFAULT_AUTOMATIONS_LIST = 50
_MAX_PLAYS_COVERAGE_ROWS = 100
_DEFAULT_PLAYS_COVERAGE_ROWS = 25
_MAX_KNOWLEDGE_V2_RESULTS = 20
_DEFAULT_KNOWLEDGE_V2_RESULTS = 5
_MAX_INBOX_EVENTS_RETURNED = 50
_INBOX_TITLE_TRUNC = 200
_RUN_OUTCOME_TEXT_TRUNC = 500
_INBOX_SAMPLE_UNCOVERED = 5


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
        active_repo_id: uuid.UUID | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._workspace_id = workspace_id
        self._user_id = user_id
        # PR-7C: optional "current repo" context for the chat turn.
        # ``search_workspace_kb`` uses this as a fallback when the LLM
        # doesn't pass ``repo_id`` explicitly, so that hits from the
        # repo the user is browsing rank first even on a zero-arg
        # tool call. ``None`` means "workspace-wide, no preferred
        # repo" which is the pre-7C behaviour of every existing tool.
        self._active_repo_id = active_repo_id

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
                                "'pattern/common-base' or 'tool/methodology-api'."
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
                name="search_workspace_kb",
                description=(
                    "Search knowledge across the entire workspace (all "
                    "repos + workspace-canonical buckets). Use this "
                    "when ``search_repo_kb`` returns no hits for the "
                    "current repo, or when the question is "
                    "platform/organisation-wide rather than "
                    "repo-specific. Ranks current-repo matches first, "
                    "then workspace canonical, then other repos as "
                    "hints."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language query.",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Optional repo UUID to prioritise hits "
                                "from. When omitted, the agent runtime "
                                "fills in the chat's active repo if "
                                "known."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Max hits to return "
                                "(default 10, max 25)."
                            ),
                            "default": 10,
                            "minimum": 1,
                            "maximum": _MAX_WORKSPACE_KB_RESULTS,
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
            ToolSpec(
                name="send_email_to_self",
                description=(
                    "Email the signed-in user a Markdown summary of the "
                    "current conversation (or any text you've drafted "
                    "with them). Use ONLY when they explicitly ask you "
                    "to email it — never autosend. The address is fixed "
                    "to the caller's account email; you cannot pick a "
                    "recipient. Subject + body are yours; keep the body "
                    "Markdown-light (headings, lists, fenced code, "
                    "links). Hard-rate-limited per user per hour."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "subject": {
                            "type": "string",
                            "description": (
                                "Email subject line. Aim for <=120 "
                                "characters; longer values are clipped."
                            ),
                        },
                        "body_markdown": {
                            "type": "string",
                            "description": (
                                "Markdown body. Supported subset: "
                                "headings, ordered/unordered lists, "
                                "fenced code blocks, **bold**, "
                                "*italic*, `code`, [links](https://...)."
                            ),
                        },
                    },
                    "required": ["subject", "body_markdown"],
                    "additionalProperties": False,
                },
            ),
            # ----------------------------------------------------------------
            # Phase 6 — new IA tools (Inbox, Plays, Runs, Coverage, Intel)
            # ----------------------------------------------------------------
            ToolSpec(
                name="inbox_list",
                description=(
                    "Paginated unified Inbox list (clarifications, "
                    "improvements, failures, approvals, exceptions). "
                    "Prefer this over ``list_clarifications`` / "
                    "``list_improvements`` when the user asks 'what's in "
                    "my inbox?' or wants to filter by owner / status / "
                    "type / repo / play. Owner defaults to ``me`` so the "
                    "first call answers 'what's on my plate?'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "clarification",
                                "improvement",
                                "failure",
                                "approval",
                                "exception",
                            ],
                            "description": (
                                "Restrict to one inbox-item type."
                            ),
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "new",
                                "snoozed",
                                "resolved",
                                "dismissed",
                                "open",
                                "all",
                            ],
                            "description": (
                                "Filter by lifecycle status. ``open`` "
                                "matches both ``new`` and ``snoozed``; "
                                "``all`` removes the default open-only "
                                "scope."
                            ),
                        },
                        "owner": {
                            "type": "string",
                            "description": (
                                "``me`` (default), ``all``, "
                                "``unassigned``, or a user UUID."
                            ),
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Optional activated-repo UUID.",
                        },
                        "play_key": {
                            "type": "string",
                            "description": (
                                "Optional Play key (pattern id) to "
                                "filter on."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_INBOX_LIST,
                            "default": _DEFAULT_INBOX_LIST,
                        },
                        "cursor": {
                            "type": "string",
                            "description": (
                                "Opaque pagination cursor returned as "
                                "``next_cursor`` from a prior call."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="inbox_counts",
                description=(
                    "Aggregate inbox counts grouped by status and type. "
                    "Use to render badges or to decide whether to call "
                    "``inbox_list`` at all (skip when ``total == 0``)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": (
                                "``me`` (default) or ``all``."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="inbox_get",
                description=(
                    "Full detail of one inbox item including its event "
                    "timeline. Call after ``inbox_list`` when the user "
                    "drills into a specific row."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "inbox_item_id": {
                            "type": "string",
                            "description": "Inbox item UUID.",
                        },
                    },
                    "required": ["inbox_item_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="inbox_routing_list",
                description=(
                    "Workspace inbox routing rules and the configuration-"
                    "health summary (bound / used / orphaned / unbound "
                    "handles). Use to answer 'who picks up X?' or to "
                    "diagnose an inbox item that landed on the wrong "
                    "owner."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="inbox_routing_preview",
                description=(
                    "Side-effect-free dry run of the inbox routing "
                    "resolver against a sample item. Tells you who an "
                    "item with the given ``item_type`` / ``repo_id`` / "
                    "``play_key`` would be assigned to *today* without "
                    "creating anything. Round-robin pointers are NOT "
                    "advanced."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "item_type": {
                            "type": "string",
                            "enum": [
                                "clarification",
                                "improvement",
                                "failure",
                                "approval",
                                "exception",
                            ],
                            "description": (
                                "Inbox type the hypothetical item would "
                                "carry — used to pick the play's emit "
                                "rule when ``play_key`` is set."
                            ),
                        },
                        "handle": {
                            "type": "string",
                            "description": (
                                "Optional symbolic handle to resolve "
                                "(e.g. ``security_officer``). If set, "
                                "wins over the play-derived handle."
                            ),
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Optional activated-repo UUID.",
                        },
                        "play_key": {
                            "type": "string",
                            "description": (
                                "Optional Play key whose ``inbox.profile`` "
                                "supplies the handle when ``handle`` is "
                                "omitted."
                            ),
                        },
                        "payload": {
                            "type": "object",
                            "description": (
                                "Sample ``source_row`` payload used by "
                                "built-in handles (``requested_by`` "
                                "etc.). Forwarded verbatim to the "
                                "resolver."
                            ),
                            "additionalProperties": True,
                        },
                    },
                    "required": ["item_type"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="plays_coverage",
                description=(
                    "Per-Play coverage rollup for the workspace: "
                    "covered vs uncovered repos, coverage percentage, "
                    "sample uncovered repo ids. Use to answer "
                    "'where is play X missing?' or 'what critical "
                    "plays are unconfigured?'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": (
                                "Optional ``spec.category`` filter "
                                "(e.g. ``scan``, ``flow``)."
                            ),
                        },
                        "critical_only": {
                            "type": "boolean",
                            "default": False,
                        },
                        "has_gaps": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Only return rows with "
                                "``coverage_pct < 1.0``."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_PLAYS_COVERAGE_ROWS,
                            "default": _DEFAULT_PLAYS_COVERAGE_ROWS,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="plays_list",
                description=(
                    "Enumerate Plays (catalog patterns) with category / "
                    "critical filters. Richer than "
                    "``list_catalog_artifacts`` — surfaces "
                    "``category``, ``secondary_categories``, "
                    "``critical``, ``default_inbox_profile``. Use this "
                    "when the user asks 'what Plays exist for X?'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": (
                                "Filter by ``spec.category``."
                            ),
                        },
                        "critical_only": {
                            "type": "boolean",
                            "default": False,
                        },
                        "q": {
                            "type": "string",
                            "description": (
                                "Substring matched (case-insensitive) "
                                "against play title and key."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_PLAYS_LIST,
                            "default": _DEFAULT_PLAYS_LIST,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="plays_get",
                description=(
                    "Single Play (catalog pattern) detail including "
                    "frontmatter (category / critical / inbox profile / "
                    "includes) and the full ARTIFACT.md body. Call "
                    "after ``plays_list`` for the playbook text."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "play_key": {
                            "type": "string",
                            "description": (
                                "Play key (pattern id), e.g. "
                                "``flow-pr-self-review``."
                            ),
                        },
                    },
                    "required": ["play_key"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="runs_query",
                description=(
                    "Outcome-first list of pipeline runs across the "
                    "workspace. Filters by ``play_key`` (matches both "
                    "``Pipeline.lane_id`` and the catalog pattern id), "
                    "repo, status (``ok`` / ``fail`` / ``error`` / "
                    "concrete pipeline statuses), trigger, "
                    "``has_escalations``, and a ``since`` ISO "
                    "timestamp. Prefer this over "
                    "``list_pipeline_runs`` when the user asks 'what "
                    "ran?' in outcome / business terms."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "play_key": {
                            "type": "string",
                            "description": (
                                "Filter by Play key — matches the "
                                "pipeline's ``lane_id`` or the lane-"
                                "linked pattern id."
                            ),
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Optional activated-repo UUID.",
                        },
                        "status": {
                            "type": "string",
                            "description": (
                                "Status filter. Accepts the canonical "
                                "Ship statuses (``running``, "
                                "``succeeded``, ``failed``, "
                                "``cancelled``) plus the friendlier "
                                "synonyms ``ok`` (= ``succeeded``), "
                                "``fail`` (= ``failed``), and "
                                "``error`` (= ``failed`` + "
                                "``cancelled``)."
                            ),
                        },
                        "trigger": {
                            "type": "string",
                            "description": (
                                "Trigger filter. ``manual`` / "
                                "``scheduled`` / ``event`` aliases are "
                                "mapped to the underlying Ship "
                                "trigger names (``manual``, ``cron``, "
                                "``webhook``)."
                            ),
                        },
                        "has_escalations": {
                            "type": "boolean",
                            "description": (
                                "Only include runs with at least one "
                                "``run_escalations`` row."
                            ),
                        },
                        "since": {
                            "type": "string",
                            "description": (
                                "ISO-8601 lower bound on "
                                "``started_at`` (or ``created_at`` "
                                "when started is null)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_RUNS_LIST,
                            "default": _DEFAULT_RUNS_LIST,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="run_detail",
                description=(
                    "Full detail of one pipeline run: RunSummary "
                    "outcome JSON (artifacts, findings, headline, "
                    "approval), plus any inbox escalations linked via "
                    "``run_escalations``. Use after ``runs_query`` "
                    "when the user asks why or how a specific run "
                    "ended."
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
                name="automations_list",
                description=(
                    "Single combined surface listing the workspace's "
                    "pipelines, lanes (from ``.ship/config.yml``), and "
                    "fleet_lanes (workspace-level mirror rules). Use "
                    "for 'what automations are configured?' before "
                    "drilling into ``runs_query``. Each row carries "
                    "``kind=pipeline|lane|fleet_lane`` so the LLM can "
                    "differentiate."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["all", "fleet", "repo"],
                            "default": "all",
                            "description": (
                                "``fleet`` returns only ``fleet_lane`` "
                                "rows; ``repo`` returns the per-repo "
                                "``pipeline`` and ``lane`` rows; "
                                "``all`` returns both."
                            ),
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Optional activated-repo UUID.",
                        },
                        "enabled_only": {
                            "type": "boolean",
                            "default": False,
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_AUTOMATIONS_LIST,
                            "default": _DEFAULT_AUTOMATIONS_LIST,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="repo_intel_get",
                description=(
                    "Return the current ``repo_intel`` snapshot for "
                    "one activated repo (languages, frameworks, "
                    "entry points, structure, commit style, visual "
                    "tokens). Returns ``{error: 'not_harvested_yet'}`` "
                    "when the repo has never been harvested. Use to "
                    "ground answers about 'what is this repo built "
                    "with?'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_id": {
                            "type": "string",
                            "description": "Activated-repo UUID.",
                        },
                    },
                    "required": ["repo_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="knowledge_search_v2",
                description=(
                    "Workspace knowledge search with explicit filters "
                    "(``repo_id``, ``bucket_slug``) and an optional "
                    "``intel_facts`` flag that prepends a synthetic "
                    "``repo_intel`` summary hit to the results. Use "
                    "this over ``search_workspace_kb`` when you need "
                    "filtered results or want intel context inline."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language query.",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Restrict (and prioritise) hits to "
                                "this activated repo."
                            ),
                        },
                        "bucket_slug": {
                            "type": "string",
                            "description": (
                                "Restrict to articles from one "
                                "knowledge bucket (slug)."
                            ),
                        },
                        "intel_facts": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, prepend a synthetic "
                                "``repo_intel`` summary hit (built "
                                "from languages + frameworks + entry "
                                "points + structure) for the active "
                                "or supplied ``repo_id``."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_KNOWLEDGE_V2_RESULTS,
                            "default": _DEFAULT_KNOWLEDGE_V2_RESULTS,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            # Phase 6 Wave B — mutating tools (admin-gated, audited)
            ToolSpec(
                name="inbox_dispose",
                description=(
                    "Apply a lifecycle disposition to one inbox item "
                    "(resolve / dismiss / approve / reject / answer / "
                    "accept / retry / acknowledge). Admin-only and "
                    "audited. Set ``dry_run=true`` to preview the "
                    "transition without writing. Use ``inbox_snooze`` "
                    "or ``inbox_reassign`` for those specific shapes."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "inbox_item_id": {
                            "type": "string",
                            "description": "UUID of the inbox item.",
                        },
                        "disposition": {
                            "type": "string",
                            "enum": [
                                "resolve",
                                "dismiss",
                                "approve",
                                "reject",
                                "answer",
                                "accept",
                                "retry",
                                "acknowledge",
                            ],
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "Optional comment / answer text "
                                "(max 4000 chars). Required when "
                                "disposition='answer'."
                            ),
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, validate + summarise the "
                                "would-be transition WITHOUT writing."
                            ),
                        },
                    },
                    "required": ["inbox_item_id", "disposition"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="inbox_snooze",
                description=(
                    "Silence one inbox item until ``until`` (≤ 30 days "
                    "out). Admin-only and audited. Item must currently "
                    "be in status 'new' or 'snoozed'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "inbox_item_id": {"type": "string"},
                        "until": {
                            "type": "string",
                            "description": (
                                "ISO-8601 timestamp in the future."
                            ),
                        },
                    },
                    "required": ["inbox_item_id", "until"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="inbox_reassign",
                description=(
                    "Hand one inbox item to a different workspace "
                    "member. Admin-only and audited. The new owner "
                    "must already be a workspace member."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "inbox_item_id": {"type": "string"},
                        "assignee_user_id": {
                            "type": "string",
                            "description": (
                                "UUID of the workspace member to "
                                "reassign to."
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": (
                                "Optional rationale (max 500 chars)."
                            ),
                        },
                    },
                    "required": ["inbox_item_id", "assignee_user_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="play_run_now",
                description=(
                    "Queue a manual run of a Play (lane) for one repo. "
                    "Admin-only and audited. Returns "
                    "``error='no_automation'`` if the Play is not yet "
                    "automated for this repo (call ``play_automate`` "
                    "first or run via shipctl)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "play_key": {
                            "type": "string",
                            "description": (
                                "Catalog play / lane id (matches "
                                "``Pipeline.lane_id``)."
                            ),
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "UUID of the activated repo.",
                        },
                        "idempotency_key": {
                            "type": "string",
                            "description": (
                                "Optional caller-provided key stored "
                                "on the queued run for client-side "
                                "dedup."
                            ),
                        },
                    },
                    "required": ["play_key", "repo_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="play_automate",
                description=(
                    "Create a Lane (the back-end automation primitive) "
                    "for a Play. ``scope='repo'`` writes a "
                    "``lanes`` row scoped to one repo; "
                    "``scope='fleet'`` writes a workspace-wide "
                    "``fleet_lanes`` row. Admin-only and audited. "
                    "Returns ``error='conflict'`` with "
                    "``existing_lane_id`` if a lane with the same "
                    "``(play_key, scope, repo_id, cadence)`` is "
                    "already present."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "play_key": {"type": "string"},
                        "scope": {
                            "type": "string",
                            "enum": ["repo", "fleet"],
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "UUID — required when scope='repo'."
                            ),
                        },
                        "cadence": {
                            "type": "string",
                            "description": (
                                "manual | on_pr | weekly | daily | "
                                "hourly | <5-field cron>"
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": (
                                "Optional human-readable label "
                                "(defaults to the Play title)."
                            ),
                        },
                    },
                    "required": ["play_key", "scope", "cadence"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="automation_toggle",
                description=(
                    "Enable or disable one Pipeline. Admin-only and "
                    "audited. No-op when the requested state matches "
                    "the current state."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "pipeline_id": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["pipeline_id", "enabled"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="intel_harvest_trigger",
                description=(
                    "Schedule a fresh ``repo_intel`` harvest for one "
                    "repo. Admin-only. Rate limit: at most one "
                    "trigger per repo per workspace per hour "
                    "(rate-limited denials do NOT audit; successes "
                    "do)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_id": {"type": "string"},
                    },
                    "required": ["repo_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="inbox_routing_upsert",
                description=(
                    "Insert or update one inbox routing rule. "
                    "Admin-only and audited. ``name`` is the handle "
                    "key (^[a-z][a-z0-9_]*$). ``then_assign_to`` is "
                    "the dispatch target — see "
                    "``inbox_routing_preview`` for the strategy "
                    "vocabulary. Pass ``rule_id`` to update an "
                    "existing row; omit to insert (returns "
                    "``error='conflict'`` if a row already exists "
                    "for this handle)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "rule_id": {
                            "type": "string",
                            "description": (
                                "Optional UUID of an existing rule "
                                "to update."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "maxLength": 64,
                            "description": (
                                "Handle key the rule binds (becomes "
                                "``handle_key``)."
                            ),
                        },
                        "when": {
                            "type": "object",
                            "description": (
                                "Free-form match shape stored under "
                                "``strategy_config._when`` for "
                                "forward-compatibility (resolver "
                                "currently ignores it)."
                            ),
                        },
                        "then_assign_to": {
                            "type": "object",
                            "description": (
                                "{strategy: 'user'|'group'|"
                                "'round_robin'|'oncall'|'first'|"
                                "'codeowners'|'workspace_admin'|"
                                "'workspace_owner'|'requested_by'|"
                                "'first_admin'|'first_owner', "
                                "user_id?, group_id?, "
                                "strategy_config?}"
                            ),
                        },
                        "priority": {
                            "type": "integer",
                            "default": 100,
                            "description": (
                                "Stored under "
                                "``strategy_config._priority`` for "
                                "forward-compatibility."
                            ),
                        },
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                        },
                    },
                    "required": ["name", "then_assign_to"],
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
            "search_workspace_kb": self._tool_search_workspace_kb,
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
            "send_email_to_self": self._tool_send_email_to_self,
            # Phase 6 — new IA tools (Inbox, Plays, Runs, Coverage, Intel)
            "inbox_list": self._tool_inbox_list,
            "inbox_counts": self._tool_inbox_counts,
            "inbox_get": self._tool_inbox_get,
            "inbox_routing_list": self._tool_inbox_routing_list,
            "inbox_routing_preview": self._tool_inbox_routing_preview,
            "plays_coverage": self._tool_plays_coverage,
            "plays_list": self._tool_plays_list,
            "plays_get": self._tool_plays_get,
            "runs_query": self._tool_runs_query,
            "run_detail": self._tool_run_detail,
            "automations_list": self._tool_automations_list,
            "repo_intel_get": self._tool_repo_intel_get,
            "knowledge_search_v2": self._tool_knowledge_search_v2,
            # Phase 6 Wave B — mutating tools (admin-gated, audited)
            "inbox_dispose": self._tool_inbox_dispose,
            "inbox_snooze": self._tool_inbox_snooze,
            "inbox_reassign": self._tool_inbox_reassign,
            "play_run_now": self._tool_play_run_now,
            "play_automate": self._tool_play_automate,
            "automation_toggle": self._tool_automation_toggle,
            "intel_harvest_trigger": self._tool_intel_harvest_trigger,
            "inbox_routing_upsert": self._tool_inbox_routing_upsert,
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
            .order_by(Pipeline.lane_id, Pipeline.name)
            .limit(limit)
        )
        if enabled_only:
            stmt = stmt.where(Pipeline.enabled.is_(True))
        rows = (await self._session.execute(stmt)).scalars().all()
        items = [
            {
                "id": str(r.id),
                "kind": r.lane_id,
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
                "pipeline_kind": pipeline.lane_id if pipeline else None,
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

    async def _tool_send_email_to_self(self, args: dict[str, Any]) -> str:
        """Email the caller a Markdown summary they explicitly asked for.

        The recipient is hard-coded to the authenticated user's
        account email — the LLM cannot pick a target. This is the
        primary abuse guardrail: even a model that hallucinates
        recipients can only spam its own user.

        Other guards:

        - In-process per-user rate limit
          (:data:`_NAVIGATOR_EMAIL_HOURLY_CAP`).
        - Subject + body are truncated to fixed caps so a runaway
          completion can't ship 1MB of model output to inbox.
        - We require the user row to have a verified-looking email
          (``user.email`` is the source of truth for invites and
          login; we reject blanks defensively).
        - All sends are logged via :class:`AuditLog` (``navigator.email.sent``
          / ``navigator.email.failed``) so the operator can trace
          abuse or transport issues post-hoc.
        """
        import time

        from backend.app.services.email import (
            EmailAddress,
            EmailMessage,
            get_email_sender,
            render_navigator_summary_email,
        )

        subject = _require_str(args, "subject").strip()
        body = _require_str(args, "body_markdown")
        if len(subject) > _NAVIGATOR_EMAIL_MAX_SUBJECT:
            subject = subject[: _NAVIGATOR_EMAIL_MAX_SUBJECT - 1] + "…"
        if len(body) > _NAVIGATOR_EMAIL_MAX_BODY:
            body = body[:_NAVIGATOR_EMAIL_MAX_BODY] + "\n\n…(truncated)"

        provider = (
            (self._settings.email_provider or "log").lower().strip()
        )
        if provider == "none":
            raise ToolInvocationError(
                "Email transport is disabled (EMAIL_PROVIDER=none); "
                "the operator has to enable SendGrid before this tool works."
            )

        user = await self._session.get(TenancyUser, self._user_id)
        if user is None or not (user.email or "").strip():
            raise ToolInvocationError(
                "Could not resolve a destination email for the signed-in user."
            )
        recipient_email = user.email.strip()
        recipient_name = user.display_name or None

        # Per-user rate limit. Trim entries older than the rolling
        # 1-hour window first so the cap is genuinely "last hour".
        now = time.monotonic()
        window = 3600.0
        history = _navigator_email_history.setdefault(self._user_id, [])
        history[:] = [t for t in history if now - t < window]
        if len(history) >= _NAVIGATOR_EMAIL_HOURLY_CAP:
            wait_seconds = int(window - (now - history[0]))
            raise ToolInvocationError(
                f"Hourly email cap reached ({_NAVIGATOR_EMAIL_HOURLY_CAP}). "
                f"Try again in ~{max(60, wait_seconds) // 60} minute(s)."
            )

        rendered = render_navigator_summary_email(
            subject=subject,
            body_markdown=body,
            conversation_url=None,
        )
        message = EmailMessage(
            to=EmailAddress(email=recipient_email, name=recipient_name),
            subject=rendered.subject,
            html=rendered.html,
            text=rendered.text,
            tags={
                "kind": "navigator_summary",
                "workspace_id": str(self._workspace_id),
                "user_id": str(self._user_id),
            },
        )

        sender = get_email_sender(self._settings)
        try:
            result = await sender.send(message)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.exception(
                "navigator email send raised user=%s", self._user_id
            )
            self._session.add(
                AuditLog(
                    workspace_id=self._workspace_id,
                    actor_user_id=self._user_id,
                    actor_token_id=None,
                    action="navigator.email.failed",
                    target_kind="user",
                    target_id=str(self._user_id),
                    payload={
                        "subject": subject,
                        "provider": sender.provider,
                        "detail": f"unhandled: {exc}",
                    },
                )
            )
            raise ToolInvocationError(
                f"send_email_to_self failed: {exc}"
            ) from exc

        history.append(now)

        self._session.add(
            AuditLog(
                workspace_id=self._workspace_id,
                actor_user_id=self._user_id,
                actor_token_id=None,
                action=(
                    "navigator.email.sent"
                    if result.sent
                    else "navigator.email.failed"
                ),
                target_kind="user",
                target_id=str(self._user_id),
                payload={
                    "subject": subject,
                    "provider": result.provider,
                    "detail": result.detail,
                    "message_id": result.message_id,
                },
            )
        )

        if not result.sent:
            return _json_result(
                {
                    "sent": False,
                    "to": recipient_email,
                    "provider": result.provider,
                    "detail": result.detail,
                }
            )
        return _json_result(
            {
                "sent": True,
                "to": recipient_email,
                "provider": result.provider,
                "subject": subject,
                "message_id": result.message_id,
            }
        )

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

    async def _tool_search_workspace_kb(self, args: dict[str, Any]) -> str:
        """Workspace-wide vector search surfaced to the Navigator (PR-7C).

        Thin adapter over
        :func:`backend.app.services.knowledge_search.search_workspace_knowledge`.
        Fallback chain for ``repo_id``:

        1. ``args["repo_id"]`` if the LLM passed one.
        2. Otherwise ``self._active_repo_id`` when the chat runtime
           told us which repo the user is browsing.
        3. Otherwise ``None`` — the service will then produce the
           non-preferred ranking (``workspace`` then ``other_repo``).

        Embedding provider unconfigured is returned as a structured
        ``{"error": "embeddings_unavailable"}`` payload rather than
        raised, so the LLM keeps the turn and can tell the user the
        feature is off instead of seeing an opaque tool-call failure.
        """
        from backend.app.services.knowledge_search import (
            EmbeddingsUnavailable,
            search_workspace_knowledge,
        )

        query = _require_str(args, "query")
        limit = _clamp_int(
            args.get("limit"),
            default=10,
            low=1,
            high=_MAX_WORKSPACE_KB_RESULTS,
        )

        repo_id_raw = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_raw:
            try:
                repo_id = uuid.UUID(str(repo_id_raw))
            except ValueError as exc:
                raise ToolInvocationError(
                    f"invalid repo_id: {repo_id_raw!r}"
                ) from exc
        elif self._active_repo_id is not None:
            repo_id = self._active_repo_id

        try:
            hits = await search_workspace_knowledge(
                self._session,
                workspace_id=self._workspace_id,
                query=query,
                repo_id=repo_id,
                limit=limit,
                settings=self._settings,
            )
        except EmbeddingsUnavailable as exc:
            return _json_result(
                {
                    "error": "embeddings_unavailable",
                    "message": str(exc),
                }
            )

        out_hits: list[dict[str, Any]] = []
        for hit in hits:
            scope = (
                "workspace" if hit.scope_kind == "workspace" else "repo"
            )
            out_hits.append(
                {
                    "title": _truncate(hit.title or "", 200),
                    "source": hit.source,
                    "bucket_slug": hit.bucket_slug,
                    "scope": scope,
                    "repo": _truncate(hit.repo_full_name or "", 200)
                    if hit.repo_full_name
                    else None,
                    "rank_bucket": hit.rank_bucket,
                    "score": hit.score,
                    "snippet": _truncate(hit.snippet or "", 400),
                }
            )
        return _json_result({"query": query, "hits": out_hits})

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

    # ----------------------------------------------------------------
    # Phase 6 — new IA tools (Inbox, Plays, Runs, Coverage, Intel)
    # ----------------------------------------------------------------
    #
    # The block below adds nine read-only tools that surface the new
    # IA built in Phases 1–5. Every method follows the same shape:
    #
    # * args parsed/validated up-front via the ``_require_*`` /
    #   ``_parse_*`` / ``_clamp_int`` helpers; on bad input we return a
    #   structured ``{"error": ..., "message": ...}`` dict (the chat
    #   loop renders this as the tool result, the LLM sees the failure
    #   inline and can recover without dropping the turn).
    # * tenancy: every SELECT filters on ``self._workspace_id``; any
    #   ``repo_id`` argument is verified against ``workspace_repos``
    #   before the tool's main work runs.
    # * caps: every list-style tool clamps its ``limit`` against the
    #   per-tool ``_MAX_*`` constants defined at the top of this
    #   module.
    # * read-only: no ``commit()``. The one exception is
    #   ``inbox_routing_preview``, which wraps its call in a
    #   ``SAVEPOINT`` and rolls back unconditionally so the resolver's
    #   round-robin pointer never moves.

    async def _tool_inbox_list(self, args: dict[str, Any]) -> str:
        from backend.app.services.inbox.profiles import INBOX_TYPES

        limit = _clamp_int(
            args.get("limit"),
            default=_DEFAULT_INBOX_LIST,
            low=1,
            high=_MAX_INBOX_LIST,
        )
        type_arg = args.get("type")
        type_filter: str | None = None
        if type_arg is not None:
            if not isinstance(type_arg, str) or type_arg not in INBOX_TYPES:
                return _json_result({
                    "error": "invalid_type",
                    "message": (
                        f"unknown inbox type {type_arg!r}; expected one "
                        f"of {sorted(INBOX_TYPES)}"
                    ),
                })
            type_filter = type_arg

        status_arg = args.get("status")
        status_in: list[str] | None
        if status_arg is None or status_arg == "open":
            status_in = ["new", "snoozed"]
        elif status_arg == "all":
            status_in = None
        elif isinstance(status_arg, str) and status_arg in {
            "new",
            "snoozed",
            "resolved",
            "dismissed",
        }:
            status_in = [status_arg]
        else:
            return _json_result({
                "error": "invalid_status",
                "message": (
                    f"unknown status {status_arg!r}; expected one of "
                    "new/snoozed/resolved/dismissed/open/all"
                ),
            })

        owner_arg = args.get("owner", "me")
        owner_filter: str | uuid.UUID
        if owner_arg in (None, "me"):
            owner_filter = self._user_id
        elif owner_arg == "all":
            owner_filter = "all"
        elif owner_arg == "unassigned":
            owner_filter = "unassigned"
        else:
            try:
                owner_filter = uuid.UUID(str(owner_arg))
            except (TypeError, ValueError):
                return _json_result({
                    "error": "invalid_owner",
                    "message": (
                        f"owner must be 'me'/'all'/'unassigned' or a "
                        f"user UUID (got {owner_arg!r})"
                    ),
                })

        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg is not None:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except (TypeError, ValueError):
                return _json_result({
                    "error": "invalid_repo_id",
                    "message": f"repo_id is not a UUID: {repo_id_arg!r}",
                })
            if not await self._verify_repo_in_workspace(repo_id):
                return _json_result({
                    "error": "repo_not_in_workspace",
                    "message": (
                        f"repo {repo_id} is not activated for this "
                        "workspace"
                    ),
                })

        play_key = args.get("play_key")
        if play_key is not None and not isinstance(play_key, str):
            return _json_result({
                "error": "invalid_play_key",
                "message": "play_key must be a string when provided",
            })

        cursor = args.get("cursor")
        cursor_ts: datetime | None = None
        cursor_id: uuid.UUID | None = None
        if cursor is not None:
            decoded = _decode_inbox_cursor(cursor)
            if decoded is None:
                return _json_result({
                    "error": "invalid_cursor",
                    "message": (
                        "cursor failed to decode; pass the value "
                        "returned as ``next_cursor`` from the prior "
                        "page"
                    ),
                })
            cursor_ts, cursor_id = decoded

        from backend.app.db.models.tenancy import User

        stmt = (
            select(InboxItem, User)
            .outerjoin(User, User.id == InboxItem.owner_user_id)
            .where(InboxItem.workspace_id == self._workspace_id)
        )
        if isinstance(owner_filter, uuid.UUID):
            stmt = stmt.where(InboxItem.owner_user_id == owner_filter)
        elif owner_filter == "unassigned":
            stmt = stmt.where(InboxItem.owner_user_id.is_(None))
        # "all" — no owner filter.
        if type_filter is not None:
            stmt = stmt.where(InboxItem.type == type_filter)
        if status_in is not None:
            stmt = stmt.where(InboxItem.status.in_(status_in))
        if repo_id is not None:
            stmt = stmt.where(InboxItem.repo_id == repo_id)
        if play_key is not None:
            stmt = stmt.where(InboxItem.play_key == play_key)
        if cursor_ts is not None and cursor_id is not None:
            stmt = stmt.where(
                tuple_(InboxItem.created_at, InboxItem.id)
                < tuple_(cursor_ts, cursor_id)
            )
        stmt = stmt.order_by(
            InboxItem.created_at.desc(), InboxItem.id.desc()
        ).limit(limit + 1)

        rows = (await self._session.execute(stmt)).all()
        has_more = len(rows) > limit
        page_rows = rows[:limit]

        repo_id_set = {item.repo_id for item, _ in page_rows if item.repo_id is not None}
        repo_name_map: dict[uuid.UUID, str] = {}
        if repo_id_set:
            repo_rows = (
                await self._session.execute(
                    select(WorkspaceRepo.id, WorkspaceRepo.full_name).where(
                        WorkspaceRepo.id.in_(repo_id_set)
                    )
                )
            ).all()
            repo_name_map = {rid: name for rid, name in repo_rows}

        items: list[dict[str, Any]] = []
        for item, owner in page_rows:
            items.append(
                {
                    "id": str(item.id),
                    "type": item.type,
                    "status": item.status,
                    "title": _truncate(item.title or "", _INBOX_TITLE_TRUNC),
                    "owner_user_id": (
                        str(item.owner_user_id)
                        if item.owner_user_id is not None
                        else None
                    ),
                    "owner_display": (
                        owner.display_name or owner.email
                        if owner is not None
                        else None
                    ),
                    "repo_id": (
                        str(item.repo_id) if item.repo_id is not None else None
                    ),
                    "repo_name": (
                        repo_name_map.get(item.repo_id)
                        if item.repo_id is not None
                        else None
                    ),
                    "play_key": item.play_key,
                    "intake_handle": item.intake_handle,
                    "intake_reason": item.intake_reason,
                    "created_at": (
                        item.created_at.isoformat()
                        if item.created_at
                        else None
                    ),
                    "snoozed_until": (
                        item.snoozed_until.isoformat()
                        if item.snoozed_until
                        else None
                    ),
                    "due_at": (
                        item.due_at.isoformat() if item.due_at else None
                    ),
                    "resolved_at": (
                        item.resolved_at.isoformat()
                        if item.resolved_at
                        else None
                    ),
                    "resolution": item.resolution,
                }
            )

        next_cursor = (
            _encode_inbox_cursor(
                page_rows[-1][0].created_at, page_rows[-1][0].id
            )
            if has_more and page_rows
            else None
        )

        # ``total_estimate`` is the same predicate count as the page
        # query (sans cursor + limit). At workspace scale the inbox
        # tops out in the low thousands so a fresh COUNT(*) here is
        # cheap and lets the LLM tell the user how much it is paging
        # through.
        count_stmt = select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == self._workspace_id
        )
        if isinstance(owner_filter, uuid.UUID):
            count_stmt = count_stmt.where(
                InboxItem.owner_user_id == owner_filter
            )
        elif owner_filter == "unassigned":
            count_stmt = count_stmt.where(InboxItem.owner_user_id.is_(None))
        if type_filter is not None:
            count_stmt = count_stmt.where(InboxItem.type == type_filter)
        if status_in is not None:
            count_stmt = count_stmt.where(InboxItem.status.in_(status_in))
        if repo_id is not None:
            count_stmt = count_stmt.where(InboxItem.repo_id == repo_id)
        if play_key is not None:
            count_stmt = count_stmt.where(InboxItem.play_key == play_key)
        total_estimate = int(
            (await self._session.execute(count_stmt)).scalar_one()
        )

        return _json_result(
            {
                "items": items,
                "next_cursor": next_cursor,
                "total_estimate": total_estimate,
            }
        )

    async def _tool_inbox_counts(self, args: dict[str, Any]) -> str:
        owner_arg = args.get("owner", "me")
        if owner_arg not in (None, "me", "all"):
            return _json_result({
                "error": "invalid_owner",
                "message": (
                    "owner must be 'me' or 'all' for inbox_counts (got "
                    f"{owner_arg!r})"
                ),
            })
        scope_mine = owner_arg in (None, "me")

        from backend.app.services.inbox.profiles import INBOX_TYPES

        statuses = ("new", "snoozed", "resolved", "dismissed")

        base_filter = [InboxItem.workspace_id == self._workspace_id]
        if scope_mine:
            base_filter.append(InboxItem.owner_user_id == self._user_id)

        by_status_stmt = (
            select(InboxItem.status, func.count(InboxItem.id))
            .where(*base_filter)
            .group_by(InboxItem.status)
        )
        by_status: dict[str, int] = {s: 0 for s in statuses}
        for status_value, count in (
            await self._session.execute(by_status_stmt)
        ).all():
            if status_value in by_status:
                by_status[status_value] = int(count)
        by_status["open"] = by_status["new"] + by_status["snoozed"]

        by_type_stmt = (
            select(InboxItem.type, func.count(InboxItem.id))
            .where(*base_filter, InboxItem.status.in_(("new", "snoozed")))
            .group_by(InboxItem.type)
        )
        by_type: dict[str, int] = {t: 0 for t in INBOX_TYPES}
        for type_value, count in (
            await self._session.execute(by_type_stmt)
        ).all():
            if type_value in by_type:
                by_type[type_value] = int(count)

        total = int(
            (
                await self._session.execute(
                    select(func.count(InboxItem.id)).where(*base_filter)
                )
            ).scalar_one()
        )

        return _json_result(
            {
                "owner": "me" if scope_mine else "all",
                "by_status": by_status,
                "by_type": by_type,
                "total": total,
            }
        )

    async def _tool_inbox_get(self, args: dict[str, Any]) -> str:
        try:
            item_id = _parse_uuid(args, "inbox_item_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "invalid_inbox_item_id",
                "message": str(exc),
            })
        item = (
            await self._session.execute(
                select(InboxItem).where(
                    InboxItem.id == item_id,
                    InboxItem.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            return _json_result({
                "error": "not_found",
                "message": (
                    f"inbox item {item_id} not found in this workspace"
                ),
            })

        from backend.app.db.models.tenancy import User

        owner = (
            await self._session.get(User, item.owner_user_id)
            if item.owner_user_id is not None
            else None
        )
        repo_name: str | None = None
        if item.repo_id is not None:
            repo_row = await self._session.get(WorkspaceRepo, item.repo_id)
            repo_name = repo_row.full_name if repo_row is not None else None

        events_stmt = (
            select(InboxItemEvent)
            .where(InboxItemEvent.item_id == item.id)
            .order_by(
                InboxItemEvent.created_at.asc(),
                InboxItemEvent.id.asc(),
            )
            .limit(_MAX_INBOX_EVENTS_RETURNED)
        )
        event_rows = (
            (await self._session.execute(events_stmt)).scalars().all()
        )

        events: list[dict[str, Any]] = []
        for ev in event_rows:
            events.append(
                {
                    "id": str(ev.id),
                    "actor_kind": ev.actor_kind,
                    "actor_user_id": (
                        str(ev.actor_user_id)
                        if ev.actor_user_id is not None
                        else None
                    ),
                    "action": ev.action,
                    "payload": ev.payload or {},
                    "created_at": (
                        ev.created_at.isoformat()
                        if ev.created_at
                        else None
                    ),
                }
            )

        return _json_result(
            {
                "id": str(item.id),
                "type": item.type,
                "status": item.status,
                "title": item.title,
                "summary": item.summary,
                "payload": item.payload or {},
                "owner_user_id": (
                    str(item.owner_user_id)
                    if item.owner_user_id is not None
                    else None
                ),
                "owner_display": (
                    owner.display_name or owner.email
                    if owner is not None
                    else None
                ),
                "repo_id": (
                    str(item.repo_id) if item.repo_id is not None else None
                ),
                "repo_name": repo_name,
                "play_key": item.play_key,
                "run_id": (
                    str(item.run_id) if item.run_id is not None else None
                ),
                "intake_handle": item.intake_handle,
                "intake_reason": item.intake_reason,
                "source_table": item.source_table,
                "source_id": (
                    str(item.source_id)
                    if item.source_id is not None
                    else None
                ),
                "created_at": (
                    item.created_at.isoformat()
                    if item.created_at
                    else None
                ),
                "due_at": (
                    item.due_at.isoformat() if item.due_at else None
                ),
                "snoozed_until": (
                    item.snoozed_until.isoformat()
                    if item.snoozed_until
                    else None
                ),
                "resolved_at": (
                    item.resolved_at.isoformat()
                    if item.resolved_at
                    else None
                ),
                "resolution": item.resolution,
                "events": events,
            }
        )

    async def _tool_inbox_routing_list(self, args: dict[str, Any]) -> str:
        rules_rows = (
            (
                await self._session.execute(
                    select(InboxRoutingRule)
                    .where(
                        InboxRoutingRule.workspace_id == self._workspace_id
                    )
                    .order_by(
                        InboxRoutingRule.handle_key.asc(),
                        InboxRoutingRule.created_at.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )

        group_rows = (
            await self._session.execute(
                select(MemberGroup.key, MemberGroup.id, MemberGroup.display_name)
                .where(MemberGroup.workspace_id == self._workspace_id)
            )
        ).all()
        group_key_to_id = {key: gid for key, gid, _ in group_rows}
        group_key_to_display = {key: display for key, _, display in group_rows}

        from backend.app.db.models.tenancy import User

        user_ids: set[uuid.UUID] = set()
        for r in rules_rows:
            if r.target_type == "user":
                try:
                    user_ids.add(uuid.UUID(str(r.target_value)))
                except (TypeError, ValueError):
                    continue
        user_emails: dict[uuid.UUID, str] = {}
        if user_ids:
            user_rows = (
                await self._session.execute(
                    select(User.id, User.email).where(User.id.in_(user_ids))
                )
            ).all()
            user_emails = {uid: email for uid, email in user_rows}

        rules_out: list[dict[str, Any]] = []
        for r in rules_rows:
            target_user_id: str | None = None
            target_user_email: str | None = None
            target_group_id: str | None = None
            target_group_key: str | None = None
            target_group_name: str | None = None
            target_strategy: str | None = None
            assigned_label: str
            if r.target_type == "user":
                try:
                    uid = uuid.UUID(str(r.target_value))
                    target_user_id = str(uid)
                    target_user_email = user_emails.get(uid)
                    assigned_label = (
                        f"user:{target_user_email or target_user_id}"
                    )
                except (TypeError, ValueError):
                    assigned_label = f"user:{r.target_value!r}"
            elif r.target_type == "group":
                gid = group_key_to_id.get(r.target_value)
                target_group_id = str(gid) if gid is not None else None
                target_group_key = r.target_value
                target_group_name = group_key_to_display.get(r.target_value)
                strat = r.assignment_strategy or "first"
                assigned_label = (
                    f"group:{target_group_key}:{strat}"
                )
            elif r.target_type == "strategy":
                target_strategy = r.target_value
                assigned_label = f"strategy:{r.target_value}"
            else:
                assigned_label = f"unknown:{r.target_value!r}"

            rules_out.append(
                {
                    "id": str(r.id),
                    "name": r.handle_key,
                    "handle": r.handle_key,
                    "when": {"handle": r.handle_key},
                    "then_assign_to": assigned_label,
                    "target_type": r.target_type,
                    "target_user_id": target_user_id,
                    "target_user_email": target_user_email,
                    "target_group_id": target_group_id,
                    "target_group_key": target_group_key,
                    "target_group_name": target_group_name,
                    "target_strategy": target_strategy,
                    "assignment_strategy": r.assignment_strategy,
                    "strategy_config": r.strategy_config or {},
                    "priority": 0,
                    "enabled": bool(r.is_enabled),
                    "is_enabled": bool(r.is_enabled),
                    "created_at": (
                        r.created_at.isoformat()
                        if r.created_at
                        else None
                    ),
                    "updated_at": (
                        r.updated_at.isoformat()
                        if r.updated_at
                        else None
                    ),
                }
            )

        # Build the same handles summary the HTTP route returns: bound
        # / used / orphaned / unbound. Walking the catalog here keeps
        # the tool self-contained (no shared service for the catalog
        # walk; route-side helpers live in the route module).
        from backend.app.services.inbox.profiles import (
            INBOX_TYPES as _CATALOG_INBOX_TYPES,
            ProfileCatalogError,
            load_profile_catalog,
        )

        bound = {r.handle_key for r in rules_rows if r.handle_key}
        used: set[str] = set()
        try:
            catalog = load_profile_catalog()
        except ProfileCatalogError as exc:
            logger.warning(
                "inbox_routing_list: profile catalog unreadable (%s); "
                "handles summary will report no used handles",
                exc,
            )
            catalog = {}
        for profile_name, body in catalog.items():
            if profile_name == "silent" or not isinstance(body, dict):
                continue
            for key, rule in body.items():
                if key == "inherits" or key not in _CATALOG_INBOX_TYPES:
                    continue
                if not isinstance(rule, dict):
                    continue
                if not rule.get("enabled"):
                    continue
                handle = rule.get("handle")
                if isinstance(handle, str) and handle:
                    used.add(handle)

        return _json_result(
            {
                "rules": rules_out,
                "handles": {
                    "bound": sorted(bound),
                    "used": sorted(used),
                    "orphaned": sorted(bound - used),
                    "unbound": sorted(used - bound),
                },
            }
        )

    async def _tool_inbox_routing_preview(
        self, args: dict[str, Any]
    ) -> str:
        from backend.app.services import catalog as catalog_service
        from backend.app.services.inbox.profiles import (
            INBOX_TYPES,
            ProfileCatalogError,
            resolve_for_pattern,
        )
        from backend.app.services.inbox.routing import (
            RoutingContext,
            RoutingError,
            resolve_handle,
        )

        item_type = args.get("item_type")
        if not isinstance(item_type, str) or item_type not in INBOX_TYPES:
            return _json_result({
                "error": "invalid_item_type",
                "message": (
                    f"item_type must be one of {sorted(INBOX_TYPES)} "
                    f"(got {item_type!r})"
                ),
            })

        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg is not None:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except (TypeError, ValueError):
                return _json_result({
                    "error": "invalid_repo_id",
                    "message": f"repo_id is not a UUID: {repo_id_arg!r}",
                })
            if not await self._verify_repo_in_workspace(repo_id):
                return _json_result({
                    "error": "repo_not_in_workspace",
                    "message": (
                        f"repo {repo_id} is not activated for this "
                        "workspace"
                    ),
                })

        play_key = args.get("play_key")
        if play_key is not None and not isinstance(play_key, str):
            return _json_result({
                "error": "invalid_play_key",
                "message": "play_key must be a string when provided",
            })

        payload = args.get("payload") or {}
        if not isinstance(payload, dict):
            return _json_result({
                "error": "invalid_payload",
                "message": "payload must be an object when provided",
            })

        attempted: list[dict[str, Any]] = []

        explicit_handle = args.get("handle")
        if explicit_handle is not None and not isinstance(
            explicit_handle, str
        ):
            return _json_result({
                "error": "invalid_handle",
                "message": "handle must be a string when provided",
            })

        chosen_handle: str | None = None
        if isinstance(explicit_handle, str) and explicit_handle.strip():
            chosen_handle = explicit_handle.strip()
            attempted.append(
                {"source": "argument", "handle": chosen_handle}
            )
        elif isinstance(play_key, str) and play_key.strip():
            try:
                patterns = catalog_service.list_patterns()
            except catalog_service.CatalogError as exc:
                return _json_result({
                    "error": "catalog_unreadable",
                    "message": str(exc),
                })
            entry = next(
                (p for p in patterns if p.id == play_key), None
            )
            if entry is None:
                return _json_result({
                    "error": "play_not_found",
                    "message": (
                        f"no catalog pattern with id={play_key!r}"
                    ),
                })
            try:
                resolved_profile = resolve_for_pattern(
                    {"id": entry.id, "spec": entry.spec},
                )
            except ProfileCatalogError as exc:
                return _json_result({
                    "error": "profile_unreadable",
                    "message": str(exc),
                })
            rule = resolved_profile.rules.get(item_type)
            if rule is None or not rule.enabled or not rule.handle:
                return _json_result({
                    "error": "no_handle_for_type",
                    "message": (
                        f"play {play_key!r} does not emit "
                        f"{item_type!r} items (profile "
                        f"{resolved_profile.profile_name!r})"
                    ),
                })
            chosen_handle = rule.handle
            attempted.append(
                {
                    "source": "play_profile",
                    "play_key": play_key,
                    "profile": resolved_profile.profile_name,
                    "handle": chosen_handle,
                }
            )
        else:
            return _json_result({
                "error": "missing_handle_source",
                "message": (
                    "supply either ``handle`` or ``play_key`` so the "
                    "preview knows which symbolic handle to resolve"
                ),
            })

        ctx = RoutingContext(
            workspace_id=self._workspace_id,
            repo_id=repo_id,
            run_id=None,
            source_row=payload,
        )

        # The resolver may UPSERT ``group_assignment_state`` on the
        # round_robin path. Wrap the call in a SAVEPOINT so the
        # preview never advances rotation pointers — admins lose
        # trust in the button the moment it nudges future
        # assignments.
        sp = await self._session.begin_nested()
        try:
            try:
                resolved = await resolve_handle(
                    self._session, chosen_handle, ctx
                )
            except RoutingError as exc:
                return _json_result({
                    "error": "routing_error",
                    "message": str(exc),
                })
        finally:
            await sp.rollback()

        from backend.app.db.models.tenancy import User

        resolved_email: str | None = None
        resolved_display: str | None = None
        if resolved.user_id is not None:
            user_row = await self._session.get(User, resolved.user_id)
            if user_row is not None:
                resolved_email = user_row.email
                resolved_display = (
                    user_row.display_name or user_row.email
                )

        # ``intake_reason='unresolved'`` means we walked the rule + the
        # built-in chain and nobody owned the work — surface as
        # ``fallback_used=True`` so the LLM can flag the gap.
        fallback_used = (
            resolved.intake_reason.startswith("fallback:")
            or resolved.intake_reason == "unresolved"
        )

        # Look up the matched rule (if any) so the result tells the
        # operator which row fired. ``intake_handle`` may have been
        # rewritten by the fallback chain, so we look up by the
        # ORIGINAL ``chosen_handle`` first and then by the resolved
        # one if the fallback path did fire.
        rule_lookup_handle = (
            resolved.intake_handle
            if resolved.intake_reason.startswith("rule:")
            or resolved.intake_reason.startswith("group:")
            else chosen_handle
        )
        matched_rule = (
            await self._session.execute(
                select(InboxRoutingRule).where(
                    InboxRoutingRule.workspace_id == self._workspace_id,
                    InboxRoutingRule.handle_key == rule_lookup_handle,
                    InboxRoutingRule.is_enabled.is_(True),
                )
            )
        ).scalar_one_or_none()

        return _json_result(
            {
                "handle": chosen_handle,
                "matched_rule_id": (
                    str(matched_rule.id) if matched_rule else None
                ),
                "matched_rule_name": (
                    matched_rule.handle_key if matched_rule else None
                ),
                "resolved_owner": {
                    "user_id": (
                        str(resolved.user_id)
                        if resolved.user_id is not None
                        else None
                    ),
                    "email": resolved_email,
                    "display": resolved_display,
                    "group_id": (
                        str(resolved.group_id)
                        if resolved.group_id is not None
                        else None
                    ),
                    "fallback_used": fallback_used,
                },
                "intake_handle": resolved.intake_handle,
                "intake_reason": resolved.intake_reason,
                "attempted_strategies": attempted,
            }
        )

    async def _tool_plays_coverage(self, args: dict[str, Any]) -> str:
        from backend.app.services import catalog as catalog_service

        category = args.get("category")
        if category is not None and not isinstance(category, str):
            return _json_result({
                "error": "invalid_category",
                "message": "category must be a string when provided",
            })
        critical_only = bool(args.get("critical_only", False))
        has_gaps = bool(args.get("has_gaps", False))
        limit = _clamp_int(
            args.get("limit"),
            default=_DEFAULT_PLAYS_COVERAGE_ROWS,
            low=1,
            high=_MAX_PLAYS_COVERAGE_ROWS,
        )

        repo_rows = (
            (
                await self._session.execute(
                    select(WorkspaceRepo)
                    .where(WorkspaceRepo.workspace_id == self._workspace_id)
                    .order_by(WorkspaceRepo.full_name.asc())
                )
            )
            .scalars()
            .all()
        )
        repo_id_set: set[uuid.UUID] = {r.id for r in repo_rows}
        activated_total = len(repo_id_set)

        lanes_by_pattern: dict[str, set[uuid.UUID]] = {}
        if repo_id_set:
            lane_rows = (
                (
                    await self._session.execute(
                        select(Lane).where(
                            Lane.workspace_id == self._workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for lane in lane_rows:
                if lane.repo_id not in repo_id_set:
                    continue
                pattern_keys: set[str] = set()
                if lane.pattern:
                    pattern_keys.add(lane.pattern)
                blob = lane.config_blob or {}
                blob_patterns = (
                    blob.get("patterns") if isinstance(blob, dict) else None
                )
                if isinstance(blob_patterns, list):
                    for entry in blob_patterns:
                        if isinstance(entry, str) and entry:
                            pattern_keys.add(entry)
                for key in pattern_keys:
                    lanes_by_pattern.setdefault(key, set()).add(lane.repo_id)

        try:
            patterns = catalog_service.list_patterns()
        except catalog_service.CatalogError as exc:
            return _json_result({
                "error": "catalog_unreadable",
                "message": str(exc),
            })

        rows: list[dict[str, Any]] = []
        for entry in patterns:
            inbox_cfg = (
                entry.spec.get("inbox") if isinstance(entry.spec, dict) else None
            )
            if isinstance(inbox_cfg, dict):
                profile = inbox_cfg.get("profile")
                if isinstance(profile, str) and profile == "silent":
                    continue

            row_category = entry.category or "uncategorized"
            row_critical = bool(
                entry.spec.get("critical")
                if isinstance(entry.spec, dict)
                else False
            )
            covered = lanes_by_pattern.get(entry.id, set()) & repo_id_set
            uncovered = repo_id_set - covered
            coverage_pct = (
                len(covered) / activated_total if activated_total else 0.0
            )
            rows.append(
                {
                    "play_key": entry.id,
                    "play_title": entry.name or entry.id,
                    "category": row_category,
                    "critical": row_critical,
                    "repos_covered_count": len(covered),
                    "repos_uncovered_count": len(uncovered),
                    "coverage_pct": coverage_pct,
                    "sample_uncovered_repo_ids": [
                        str(rid)
                        for rid in list(uncovered)[:_INBOX_SAMPLE_UNCOVERED]
                    ],
                }
            )

        # Sort: critical-with-gaps first, then non-critical-with-gaps,
        # then fully-covered alphabetical.
        def _sort_key(r: dict[str, Any]) -> tuple[int, float, str]:
            has_gaps_local = r["coverage_pct"] < 1.0
            if has_gaps_local and r["critical"]:
                bucket = 0
            elif has_gaps_local:
                bucket = 1
            else:
                bucket = 2
            return (bucket, r["coverage_pct"], r["play_title"].lower())

        rows.sort(key=_sort_key)

        if isinstance(category, str):
            rows = [r for r in rows if r["category"] == category]
        if critical_only:
            rows = [r for r in rows if r["critical"]]
        if has_gaps:
            rows = [r for r in rows if r["coverage_pct"] < 1.0]

        truncated = len(rows) > limit
        rows = rows[:limit]
        return _json_result(
            {
                "rows": rows,
                "activated_repos_total": activated_total,
                "truncated": truncated,
            }
        )

    async def _tool_plays_list(self, args: dict[str, Any]) -> str:
        from backend.app.services import catalog as catalog_service

        category = args.get("category")
        if category is not None and not isinstance(category, str):
            return _json_result({
                "error": "invalid_category",
                "message": "category must be a string when provided",
            })
        critical_only = bool(args.get("critical_only", False))
        q = args.get("q")
        if q is not None and not isinstance(q, str):
            return _json_result({
                "error": "invalid_q",
                "message": "q must be a string when provided",
            })
        q_norm = q.lower().strip() if isinstance(q, str) else None
        limit = _clamp_int(
            args.get("limit"),
            default=_DEFAULT_PLAYS_LIST,
            low=1,
            high=_MAX_PLAYS_LIST,
        )

        try:
            patterns = catalog_service.list_patterns()
        except catalog_service.CatalogError as exc:
            return _json_result({
                "error": "catalog_unreadable",
                "message": str(exc),
            })

        items: list[dict[str, Any]] = []
        for entry in patterns:
            spec = entry.spec if isinstance(entry.spec, dict) else {}
            inbox_cfg = spec.get("inbox") if isinstance(spec, dict) else None
            inbox_profile: str | None = None
            if isinstance(inbox_cfg, dict):
                profile = inbox_cfg.get("profile")
                if isinstance(profile, str):
                    inbox_profile = profile
            secondary_raw = entry.raw.get("secondary_categories") or []
            secondary: list[str] = []
            if isinstance(secondary_raw, list):
                secondary = [
                    str(s) for s in secondary_raw if isinstance(s, str)
                ]
            row = {
                "play_key": entry.id,
                "title": entry.name or entry.id,
                "category": entry.category or "uncategorized",
                "secondary_categories": secondary,
                "critical": bool(spec.get("critical")) if spec else False,
                "summary": entry.description or None,
                "default_inbox_profile": inbox_profile,
            }

            if isinstance(category, str) and row["category"] != category:
                continue
            if critical_only and not row["critical"]:
                continue
            if q_norm:
                title_blob = (row["title"] or "").lower()
                key_blob = (row["play_key"] or "").lower()
                if q_norm not in title_blob and q_norm not in key_blob:
                    continue
            items.append(row)

        items.sort(key=lambda r: (r["category"], r["play_key"]))
        truncated = len(items) > limit
        items = items[:limit]
        return _json_result(
            {"items": items, "truncated": truncated}
        )

    async def _tool_plays_get(self, args: dict[str, Any]) -> str:
        from backend.app.services import catalog as catalog_service

        play_key = args.get("play_key")
        if not isinstance(play_key, str) or not play_key.strip():
            return _json_result({
                "error": "invalid_play_key",
                "message": "play_key is required",
            })

        try:
            patterns = catalog_service.list_patterns()
        except catalog_service.CatalogError as exc:
            return _json_result({
                "error": "catalog_unreadable",
                "message": str(exc),
            })
        entry = next((p for p in patterns if p.id == play_key), None)
        if entry is None:
            return _json_result({
                "error": "not_found",
                "message": f"no catalog pattern with id={play_key!r}",
            })

        spec = entry.spec if isinstance(entry.spec, dict) else {}
        inbox_cfg = spec.get("inbox") if isinstance(spec, dict) else None
        inbox_profile: str | None = None
        if isinstance(inbox_cfg, dict):
            profile = inbox_cfg.get("profile")
            if isinstance(profile, str):
                inbox_profile = profile
        secondary_raw = entry.raw.get("secondary_categories") or []
        secondary: list[str] = []
        if isinstance(secondary_raw, list):
            secondary = [
                str(s) for s in secondary_raw if isinstance(s, str)
            ]
        modes_raw = spec.get("modes") if isinstance(spec, dict) else None
        modes: list[str] = []
        if isinstance(modes_raw, list):
            modes = [str(m) for m in modes_raw if isinstance(m, str)]
        body = entry.body or ""
        body_truncated = len(body) > _MAX_ARTIFACT_BODY_CHARS
        if body_truncated:
            body = body[:_MAX_ARTIFACT_BODY_CHARS]

        return _json_result(
            {
                "play_key": entry.id,
                "title": entry.name or entry.id,
                "category": entry.category or "uncategorized",
                "secondary_categories": secondary,
                "critical": bool(spec.get("critical")) if spec else False,
                "summary": entry.description or None,
                "body": body,
                "body_truncated": body_truncated,
                "includes": list(entry.include),
                "default_execution_mode": modes[0] if modes else None,
                "modes": modes,
                "default_inbox_profile": inbox_profile,
                "default_trigger": entry.default_trigger,
                "lane_id": entry.lane_id,
                "lane_name": entry.lane_name,
            }
        )

    async def _tool_runs_query(self, args: dict[str, Any]) -> str:
        play_key = args.get("play_key")
        if play_key is not None and not isinstance(play_key, str):
            return _json_result({
                "error": "invalid_play_key",
                "message": "play_key must be a string when provided",
            })

        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg is not None:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except (TypeError, ValueError):
                return _json_result({
                    "error": "invalid_repo_id",
                    "message": f"repo_id is not a UUID: {repo_id_arg!r}",
                })
            if not await self._verify_repo_in_workspace(repo_id):
                return _json_result({
                    "error": "repo_not_in_workspace",
                    "message": (
                        f"repo {repo_id} is not activated for this "
                        "workspace"
                    ),
                })

        status_arg = args.get("status")
        status_in: list[str] | None = None
        if status_arg is not None:
            if not isinstance(status_arg, str):
                return _json_result({
                    "error": "invalid_status",
                    "message": "status must be a string when provided",
                })
            alias = {
                "ok": ["succeeded"],
                "fail": ["failed"],
                "error": ["failed", "cancelled"],
            }.get(status_arg)
            status_in = alias if alias is not None else [status_arg]

        trigger_arg = args.get("trigger")
        trigger_in: list[str] | None = None
        if trigger_arg is not None:
            if not isinstance(trigger_arg, str):
                return _json_result({
                    "error": "invalid_trigger",
                    "message": "trigger must be a string when provided",
                })
            alias = {
                "scheduled": ["cron", "schedule"],
                "event": ["webhook", "event"],
            }.get(trigger_arg)
            trigger_in = alias if alias is not None else [trigger_arg]

        has_escalations = args.get("has_escalations")
        if has_escalations is not None and not isinstance(
            has_escalations, bool
        ):
            return _json_result({
                "error": "invalid_has_escalations",
                "message": "has_escalations must be a boolean",
            })

        since_arg = args.get("since")
        since_dt = None
        if since_arg is not None:
            try:
                since_dt = _parse_iso_datetime(since_arg, "since")
            except ToolInvocationError as exc:
                return _json_result({
                    "error": "invalid_since",
                    "message": str(exc),
                })

        limit = _clamp_int(
            args.get("limit"),
            default=_DEFAULT_RUNS_LIST,
            low=1,
            high=_MAX_RUNS_LIST,
        )

        # Pull pipeline metadata in the same query so we can carry
        # ``play_key`` (== Pipeline.lane_id) and ``repo_id`` without an
        # N+1 follow-up. The ``play_key`` filter is applied
        # post-query because Pipeline.lane_id is the "user-facing"
        # play key and it's cheap (limit-bounded).
        stmt = (
            select(PipelineRun, Pipeline)
            .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
            .where(PipelineRun.workspace_id == self._workspace_id)
        )
        if status_in is not None:
            stmt = stmt.where(PipelineRun.status.in_(status_in))
        if trigger_in is not None:
            stmt = stmt.where(PipelineRun.trigger.in_(trigger_in))
        if repo_id is not None:
            stmt = stmt.where(Pipeline.repo_id == repo_id)
        if play_key is not None:
            stmt = stmt.where(Pipeline.lane_id == play_key)
        if since_dt is not None:
            stmt = stmt.where(
                func.coalesce(
                    PipelineRun.started_at, PipelineRun.created_at
                )
                >= since_dt
            )
        stmt = stmt.order_by(
            desc(
                func.coalesce(
                    PipelineRun.started_at, PipelineRun.created_at
                )
            )
        ).limit(limit * 4 if has_escalations else limit)

        rows = (await self._session.execute(stmt)).all()

        run_ids = [run.id for run, _ in rows]
        escalation_count_map: dict[uuid.UUID, int] = {}
        if run_ids:
            esc_rows = (
                await self._session.execute(
                    select(
                        RunEscalation.run_id, func.count(RunEscalation.id)
                    )
                    .where(RunEscalation.run_id.in_(run_ids))
                    .group_by(RunEscalation.run_id)
                )
            ).all()
            escalation_count_map = {rid: int(c) for rid, c in esc_rows}

        repo_id_set = {p.repo_id for _, p in rows if p.repo_id is not None}
        repo_name_map: dict[uuid.UUID, str] = {}
        if repo_id_set:
            repo_rows = (
                await self._session.execute(
                    select(WorkspaceRepo.id, WorkspaceRepo.full_name).where(
                        WorkspaceRepo.id.in_(repo_id_set)
                    )
                )
            ).all()
            repo_name_map = {rid: name for rid, name in repo_rows}

        runs_out: list[dict[str, Any]] = []
        for run, pipeline in rows:
            esc_count = escalation_count_map.get(run.id, 0)
            if has_escalations and esc_count == 0:
                continue
            outcome = run.outcome or {}
            findings_by_sev = (
                outcome.get("findings_by_severity")
                if isinstance(outcome, dict)
                else None
            )
            sev_block: dict[str, int] = {
                "low": 0,
                "medium": 0,
                "high": 0,
                "critical": 0,
            }
            if isinstance(findings_by_sev, dict):
                for k in sev_block:
                    raw = findings_by_sev.get(k)
                    if isinstance(raw, int):
                        sev_block[k] = raw
            artifacts = (
                outcome.get("artifacts") if isinstance(outcome, dict) else None
            )
            artifacts_count = (
                len(artifacts) if isinstance(artifacts, list) else 0
            )
            findings_count_raw = (
                outcome.get("findings_count")
                if isinstance(outcome, dict)
                else None
            )
            runs_out.append(
                {
                    "id": str(run.id),
                    "pipeline_id": str(run.pipeline_id),
                    "play_key": pipeline.lane_id if pipeline else None,
                    "repo_id": (
                        str(pipeline.repo_id)
                        if pipeline and pipeline.repo_id is not None
                        else None
                    ),
                    "repo_name": (
                        repo_name_map.get(pipeline.repo_id)
                        if pipeline and pipeline.repo_id is not None
                        else None
                    ),
                    "status": run.status,
                    "trigger": run.trigger,
                    "started_at": (
                        run.started_at.isoformat()
                        if run.started_at
                        else None
                    ),
                    "finished_at": (
                        run.finished_at.isoformat()
                        if run.finished_at
                        else None
                    ),
                    "outcome_text": _truncate(
                        (
                            outcome.get("outcome_text")
                            if isinstance(outcome, dict)
                            else None
                        )
                        or "",
                        _RUN_OUTCOME_TEXT_TRUNC,
                    )
                    or None,
                    "headline": (
                        outcome.get("headline")
                        if isinstance(outcome, dict)
                        else None
                    ),
                    "findings_count": (
                        int(findings_count_raw)
                        if isinstance(findings_count_raw, int)
                        else None
                    ),
                    "findings_by_severity": sev_block,
                    "escalations_count": esc_count,
                    "artifacts_count": artifacts_count,
                }
            )
            if len(runs_out) >= limit:
                break

        return _json_result({"runs": runs_out})

    async def _tool_run_detail(self, args: dict[str, Any]) -> str:
        try:
            run_id = _parse_uuid(args, "run_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "invalid_run_id",
                "message": str(exc),
            })
        run = (
            await self._session.execute(
                select(PipelineRun).where(
                    PipelineRun.id == run_id,
                    PipelineRun.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if run is None:
            return _json_result({
                "error": "not_found",
                "message": f"run {run_id} not found in this workspace",
            })
        pipeline = await self._session.get(Pipeline, run.pipeline_id)
        repo_name: str | None = None
        repo_id: uuid.UUID | None = pipeline.repo_id if pipeline else None
        if repo_id is not None:
            repo_row = await self._session.get(WorkspaceRepo, repo_id)
            repo_name = repo_row.full_name if repo_row is not None else None

        outcome = run.outcome or {}
        artifacts: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        if isinstance(outcome, dict):
            raw_artifacts = outcome.get("artifacts")
            if isinstance(raw_artifacts, list):
                for a in raw_artifacts:
                    if isinstance(a, dict):
                        artifacts.append(dict(a))
            raw_findings = outcome.get("findings")
            if isinstance(raw_findings, list):
                for f in raw_findings:
                    if isinstance(f, dict):
                        findings.append(dict(f))

        esc_rows = (
            (
                await self._session.execute(
                    select(RunEscalation, InboxItem)
                    .outerjoin(
                        InboxItem, InboxItem.id == RunEscalation.inbox_item_id
                    )
                    .where(RunEscalation.run_id == run.id)
                )
            )
            .all()
        )
        escalations: list[dict[str, Any]] = []
        for esc, item in esc_rows:
            escalations.append(
                {
                    "inbox_item_id": str(esc.inbox_item_id),
                    "escalation_reason": esc.escalation_reason,
                    "item_title": item.title if item is not None else None,
                    "item_status": item.status if item is not None else None,
                    "item_type": item.type if item is not None else None,
                }
            )

        return _json_result(
            {
                "id": str(run.id),
                "pipeline_id": str(run.pipeline_id),
                "play_key": pipeline.lane_id if pipeline else None,
                "repo_id": str(repo_id) if repo_id is not None else None,
                "repo_name": repo_name,
                "status": run.status,
                "trigger": run.trigger,
                "started_at": (
                    run.started_at.isoformat() if run.started_at else None
                ),
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
                "summary": run.summary,
                "outcome": outcome if isinstance(outcome, dict) else {},
                "artifacts": artifacts,
                "findings": findings,
                "escalations": escalations,
            }
        )

    async def _tool_automations_list(self, args: dict[str, Any]) -> str:
        scope = args.get("scope", "all")
        if scope not in (None, "all", "fleet", "repo"):
            return _json_result({
                "error": "invalid_scope",
                "message": (
                    f"scope must be 'all'/'fleet'/'repo' (got {scope!r})"
                ),
            })
        if scope is None:
            scope = "all"

        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg is not None:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except (TypeError, ValueError):
                return _json_result({
                    "error": "invalid_repo_id",
                    "message": f"repo_id is not a UUID: {repo_id_arg!r}",
                })
            if not await self._verify_repo_in_workspace(repo_id):
                return _json_result({
                    "error": "repo_not_in_workspace",
                    "message": (
                        f"repo {repo_id} is not activated for this "
                        "workspace"
                    ),
                })

        enabled_only = bool(args.get("enabled_only", False))
        limit = _clamp_int(
            args.get("limit"),
            default=_DEFAULT_AUTOMATIONS_LIST,
            low=1,
            high=_MAX_AUTOMATIONS_LIST,
        )

        repo_name_map: dict[uuid.UUID, str] = {}
        if scope in ("all", "repo"):
            repo_rows = (
                await self._session.execute(
                    select(WorkspaceRepo.id, WorkspaceRepo.full_name).where(
                        WorkspaceRepo.workspace_id == self._workspace_id
                    )
                )
            ).all()
            repo_name_map = {rid: name for rid, name in repo_rows}

        items: list[dict[str, Any]] = []

        if scope in ("all", "repo"):
            pipeline_stmt = select(Pipeline).where(
                Pipeline.workspace_id == self._workspace_id
            )
            if repo_id is not None:
                pipeline_stmt = pipeline_stmt.where(
                    Pipeline.repo_id == repo_id
                )
            if enabled_only:
                pipeline_stmt = pipeline_stmt.where(Pipeline.enabled.is_(True))
            pipeline_rows = (
                (await self._session.execute(pipeline_stmt))
                .scalars()
                .all()
            )
            for p in pipeline_rows:
                items.append(
                    {
                        "kind": "pipeline",
                        "id": str(p.id),
                        "name": p.name,
                        "play_key": p.lane_id,
                        "scope": "repo",
                        "repo_id": (
                            str(p.repo_id)
                            if p.repo_id is not None
                            else None
                        ),
                        "repo_name": (
                            repo_name_map.get(p.repo_id)
                            if p.repo_id is not None
                            else None
                        ),
                        "enabled": bool(p.enabled),
                        "last_run_id": None,
                        "last_run_status": p.last_run_status,
                        "last_run_at": (
                            p.last_run_at.isoformat()
                            if p.last_run_at
                            else None
                        ),
                    }
                )

            lane_stmt = select(Lane).where(
                Lane.workspace_id == self._workspace_id
            )
            if repo_id is not None:
                lane_stmt = lane_stmt.where(Lane.repo_id == repo_id)
            if enabled_only:
                lane_stmt = lane_stmt.where(Lane.enabled.is_(True))
            lane_rows = (
                (await self._session.execute(lane_stmt)).scalars().all()
            )
            for ln in lane_rows:
                items.append(
                    {
                        "kind": "lane",
                        "id": str(ln.id),
                        "name": ln.lane_id,
                        "play_key": ln.pattern,
                        "scope": "repo",
                        "repo_id": (
                            str(ln.repo_id)
                            if ln.repo_id is not None
                            else None
                        ),
                        "repo_name": (
                            repo_name_map.get(ln.repo_id)
                            if ln.repo_id is not None
                            else None
                        ),
                        "enabled": bool(ln.enabled),
                        "last_run_id": None,
                        "last_run_status": ln.last_run_status,
                        "last_run_at": (
                            ln.last_run_at.isoformat()
                            if ln.last_run_at
                            else None
                        ),
                    }
                )

        if scope in ("all", "fleet"):
            fleet_stmt = select(FleetLane).where(
                FleetLane.workspace_id == self._workspace_id
            )
            if enabled_only:
                fleet_stmt = fleet_stmt.where(FleetLane.enabled.is_(True))
            fleet_rows = (
                (await self._session.execute(fleet_stmt)).scalars().all()
            )
            for fl in fleet_rows:
                items.append(
                    {
                        "kind": "fleet_lane",
                        "id": str(fl.id),
                        "name": fl.name,
                        "play_key": fl.pattern_id,
                        "scope": "fleet",
                        "repo_id": None,
                        "repo_name": None,
                        "enabled": bool(fl.enabled),
                        "cadence": fl.cadence,
                        "last_run_id": None,
                        "last_run_status": None,
                        "last_run_at": None,
                    }
                )

        truncated = len(items) > limit
        items = items[:limit]
        return _json_result(
            {
                "items": items,
                "truncated": truncated,
                "scope": scope,
            }
        )

    async def _tool_repo_intel_get(self, args: dict[str, Any]) -> str:
        try:
            repo_id = _parse_uuid(args, "repo_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "invalid_repo_id",
                "message": str(exc),
            })
        if not await self._verify_repo_in_workspace(repo_id):
            return _json_result({
                "error": "repo_not_in_workspace",
                "message": (
                    f"repo {repo_id} is not activated for this workspace"
                ),
            })

        from backend.app.services.repo_intel import get_current_intel

        intel = await get_current_intel(self._session, repo_id)
        if intel is None:
            return _json_result(
                {
                    "error": "not_harvested_yet",
                    "message": (
                        f"repo {repo_id} has no current repo_intel "
                        "snapshot yet"
                    ),
                    "repo_id": str(repo_id),
                }
            )
        return _json_result(
            {
                "repo_id": str(intel.repo_id),
                "version": intel.version,
                "harvested_at": (
                    intel.harvested_at.isoformat()
                    if intel.harvested_at
                    else None
                ),
                "harvest_error": intel.harvest_error,
                "languages": intel.languages or {},
                "frameworks": list(intel.frameworks or []),
                "package_managers": list(intel.package_managers or []),
                "entry_points": list(intel.entry_points or []),
                "structure": intel.structure or {},
                "commit_style": intel.commit_style or {},
                "visual_tokens": intel.visual_tokens or {},
            }
        )

    async def _tool_knowledge_search_v2(
        self, args: dict[str, Any]
    ) -> str:
        from backend.app.services.knowledge_search import (
            EmbeddingsUnavailable,
            search_workspace_knowledge,
        )

        try:
            query = _require_str(args, "query")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "invalid_query",
                "message": str(exc),
            })

        limit = _clamp_int(
            args.get("limit"),
            default=_DEFAULT_KNOWLEDGE_V2_RESULTS,
            low=1,
            high=_MAX_KNOWLEDGE_V2_RESULTS,
        )

        repo_id_arg = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_arg is not None:
            try:
                repo_id = uuid.UUID(str(repo_id_arg))
            except (TypeError, ValueError):
                return _json_result({
                    "error": "invalid_repo_id",
                    "message": f"repo_id is not a UUID: {repo_id_arg!r}",
                })
            if not await self._verify_repo_in_workspace(repo_id):
                return _json_result({
                    "error": "repo_not_in_workspace",
                    "message": (
                        f"repo {repo_id} is not activated for this "
                        "workspace"
                    ),
                })
        elif self._active_repo_id is not None:
            repo_id = self._active_repo_id

        bucket_slug = args.get("bucket_slug")
        if bucket_slug is not None and not isinstance(bucket_slug, str):
            return _json_result({
                "error": "invalid_bucket_slug",
                "message": "bucket_slug must be a string when provided",
            })

        intel_facts = bool(args.get("intel_facts", False))

        # Over-fetch when a bucket filter is requested so the
        # post-filter still has a chance of returning ``limit`` rows.
        fetch_limit = limit * 4 if isinstance(bucket_slug, str) else limit

        try:
            hits = await search_workspace_knowledge(
                self._session,
                workspace_id=self._workspace_id,
                query=query,
                repo_id=repo_id,
                limit=fetch_limit,
                settings=self._settings,
            )
        except EmbeddingsUnavailable as exc:
            return _json_result(
                {
                    "error": "embeddings_unavailable",
                    "message": str(exc),
                }
            )

        results: list[dict[str, Any]] = []
        for hit in hits:
            if isinstance(bucket_slug, str) and hit.bucket_slug != bucket_slug:
                continue
            results.append(
                {
                    "source": hit.source,
                    "repo_id": (
                        str(hit.repo_id) if hit.repo_id is not None else None
                    ),
                    "bucket_slug": hit.bucket_slug,
                    "source_path": hit.title,
                    "snippet": _truncate(hit.snippet or "", 400),
                    "score": hit.score,
                    "rank_bucket": hit.rank_bucket,
                }
            )
            if len(results) >= limit:
                break

        if intel_facts and repo_id is not None:
            from backend.app.services.repo_intel import get_current_intel

            intel = await get_current_intel(self._session, repo_id)
            if intel is not None:
                snippet = _intel_summary_snippet(intel)
                results.insert(
                    0,
                    {
                        "source": "repo_intel",
                        "repo_id": str(intel.repo_id),
                        "bucket_slug": None,
                        "source_path": None,
                        "snippet": _truncate(snippet, 400),
                        "score": 1.0,
                        "rank_bucket": "intel",
                    },
                )
                if len(results) > limit:
                    results = results[:limit]

        return _json_result(
            {
                "query": query,
                "results": results,
            }
        )

    async def _verify_repo_in_workspace(self, repo_id: uuid.UUID) -> bool:
        """Return True iff ``repo_id`` belongs to the active workspace.

        Helper shared by the Phase-6 tools that take a ``repo_id``
        argument (inbox / runs / repo_intel / knowledge). We check
        existence with ``LIMIT 1`` rather than fetching the full row
        because the caller usually projects ``WorkspaceRepo.full_name``
        in a separate batched query.
        """
        row = (
            await self._session.execute(
                select(WorkspaceRepo.id)
                .where(
                    WorkspaceRepo.id == repo_id,
                    WorkspaceRepo.workspace_id == self._workspace_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return row is not None

    # ------------------------------------------------------------------
    # Phase 6 Wave B — admin gate + audit envelope (cross-cutting)
    # ------------------------------------------------------------------

    async def _require_admin_or_error(
        self, *, tool_name: str
    ) -> dict[str, Any] | None:
        """Resolve workspace role; return a forbidden-error dict on denial.

        Mutating Wave B tools call this at the very top. Returning a
        plain dict (to be JSON-encoded by the caller) keeps the
        contract uniform with the other ``{"error": ...}`` shapes the
        LLM already understands. The query is the same workspace
        membership lookup used by ``_require_membership`` in the HTTP
        surface — no caching: the per-turn ``ToolBox`` is short-lived
        enough that one extra SELECT per Wave B tool is irrelevant.
        """
        member = (
            await self._session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == self._workspace_id,
                    WorkspaceMember.user_id == self._user_id,
                )
            )
        ).scalar_one_or_none()
        if member is None or member.role not in ("owner", "admin"):
            return {
                "error": "forbidden",
                "message": (
                    f"navigator tool {tool_name!r} requires workspace "
                    "admin role"
                ),
            }
        return None

    async def _audit_navigator_tool(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
        target: dict[str, Any],
    ) -> None:
        """Stamp an :class:`AuditLog` row for a successful Wave B mutation.

        ``target`` carries ``{"kind": "...", "id": "..."}`` so the
        chat-turn audit trail looks identical to the equivalent HTTP
        route's audit row (``inbox.disposition.<action>`` / etc.) —
        easier to reason about during a forensic sweep when both
        surfaces touched the same object.

        ``payload`` is redacted in place: any value whose JSON
        encoding exceeds 4 KiB is replaced with
        ``{"_redacted": True, "len": <orig_len>}`` so a chatty body
        argument can't blow up the audit row size.

        No commit — the chat-turn handler in ``chat.py`` owns the
        outer transaction. We ``flush`` so an in-turn read-after-write
        from a follow-up tool call sees the row.
        """
        redacted: dict[str, Any] = {"actor_kind": "navigator"}
        for key, value in (payload or {}).items():
            try:
                encoded = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                encoded = str(value)
            if len(encoded) > 4 * 1024:
                redacted[key] = {"_redacted": True, "len": len(encoded)}
            else:
                redacted[key] = value

        target_kind = str(target.get("kind") or "")[:64] or None
        raw_target_id = target.get("id")
        target_id: str | None = None
        if raw_target_id is not None:
            target_id = str(raw_target_id)[:128]

        self._session.add(
            AuditLog(
                workspace_id=self._workspace_id,
                actor_user_id=self._user_id,
                actor_token_id=None,
                action=f"navigator.tool.{tool_name}",
                target_kind=target_kind,
                target_id=target_id,
                payload=redacted,
            )
        )
        await self._session.flush()

    # ------------------------------------------------------------------
    # Phase 6 Wave B — mutating tools (admin-gated, audited)
    # ------------------------------------------------------------------

    async def _tool_inbox_dispose(self, args: dict[str, Any]) -> str:
        # Admin gate first so a non-admin can't even probe whether
        # an item exists by id-fishing.
        gate_err = await self._require_admin_or_error(tool_name="inbox_dispose")
        if gate_err is not None:
            return _json_result(gate_err)

        try:
            item_id = _parse_uuid(args, "inbox_item_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "validation_failed",
                "message": str(exc),
            })

        disposition = args.get("disposition")
        valid_dispositions = (
            "resolve",
            "dismiss",
            "approve",
            "reject",
            "answer",
            "accept",
            "retry",
            "acknowledge",
        )
        if (
            not isinstance(disposition, str)
            or disposition not in valid_dispositions
        ):
            return _json_result({
                "error": "validation_failed",
                "message": (
                    f"disposition must be one of {list(valid_dispositions)}"
                ),
            })

        body = args.get("body")
        if body is not None:
            if not isinstance(body, str):
                return _json_result({
                    "error": "validation_failed",
                    "message": "body must be a string when provided",
                })
            if len(body) > 4000:
                return _json_result({
                    "error": "validation_failed",
                    "message": "body must be ≤ 4000 chars",
                })

        dry_run = bool(args.get("dry_run", False))

        # Tenancy: the same scoped lookup the HTTP route uses.
        item = (
            await self._session.execute(
                select(InboxItem).where(
                    InboxItem.id == item_id,
                    InboxItem.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            return _json_result({
                "error": "not_found",
                "message": (
                    f"inbox item {item_id} not found in this workspace"
                ),
            })

        # State-machine pre-check mirroring _validate_disposition's
        # OPEN_STATUSES gate. Items that have already been resolved /
        # dismissed cannot accept a new disposition.
        from backend.app.api.v1.routes.inbox import (
            OPEN_STATUSES,
            _ACTION_RESOLUTION,
            _RESOLVABLE_FROM_OPEN,
            _TYPE_GATED_ACTIONS,
        )

        required_type = _TYPE_GATED_ACTIONS.get(disposition)
        if required_type is not None and item.type != required_type:
            return _json_result({
                "error": "precondition_failed",
                "message": (
                    f"disposition {disposition!r} is only valid for items "
                    f"of type {required_type!r} (this item is type "
                    f"{item.type!r})"
                ),
            })
        if disposition in _RESOLVABLE_FROM_OPEN:
            if item.status not in OPEN_STATUSES:
                return _json_result({
                    "error": "precondition_failed",
                    "message": (
                        f"disposition {disposition!r} requires status in "
                        f"{list(OPEN_STATUSES)}; item is currently "
                        f"{item.status!r}"
                    ),
                })
        else:
            if item.status != "new":
                return _json_result({
                    "error": "precondition_failed",
                    "message": (
                        f"disposition {disposition!r} requires status='new';"
                        f" item is currently {item.status!r}"
                    ),
                })

        # ``answer`` requires either a body or a payload.answer; the
        # body field is friendlier for chat callers, so we promote it.
        payload_dict: dict[str, Any] = {}
        if body is not None:
            payload_dict["body"] = body
            if disposition == "answer":
                payload_dict["answer"] = body
        if disposition == "answer" and not payload_dict.get("answer"):
            return _json_result({
                "error": "validation_failed",
                "message": (
                    "disposition 'answer' requires a non-empty body "
                    "with the answer text"
                ),
            })

        resolution = (
            payload_dict.get("resolution")
            if disposition == "resolve" and payload_dict.get("resolution")
            else _ACTION_RESOLUTION[disposition]
        )

        new_status = "resolved" if disposition != "dismiss" else "dismissed"
        side_effect_summary = {
            "approve": "closes any matching run escalations as 'approved'",
            "reject": "closes any matching run escalations as 'rejected'",
            "retry": "records a retry-request event for the underlying run",
        }.get(disposition, "no extra side-effects")

        if dry_run:
            return _json_result({
                "dry_run": True,
                "would_apply": {
                    "inbox_item_id": str(item.id),
                    "current_status": item.status,
                    "new_status": new_status,
                    "applied_disposition": disposition,
                    "resolution": resolution,
                    "side_effects": side_effect_summary,
                },
            })

        from datetime import timezone as _tz
        from backend.app.services.inbox.side_effects import apply_side_effects

        now = datetime.now(_tz.utc)
        merged_payload = (item.payload or {}) | dict(payload_dict)
        item.payload = merged_payload
        item.status = new_status
        item.resolution = resolution
        item.resolved_at = now
        item.resolved_by_user_id = self._user_id

        self._session.add(
            InboxItemEvent(
                item_id=item.id,
                actor_user_id=self._user_id,
                actor_kind="agent",
                action="resolved" if new_status == "resolved" else "dismissed",
                payload={
                    "disposition": disposition,
                    "resolution": resolution,
                    **{
                        k: v
                        for k, v in payload_dict.items()
                        if k != "resolution"
                    },
                },
            )
        )

        report = await apply_side_effects(
            self._session,
            item=item,
            action=disposition,
            payload=payload_dict,
            actor_user_id=self._user_id,
        )

        side_effects: list[dict[str, Any]] = []
        if report.escalations_closed:
            side_effects.append(
                {
                    "kind": "escalations_closed",
                    "count": len(report.escalations_closed),
                }
            )
        if report.legacy_writebacks:
            side_effects.append(
                {
                    "kind": "legacy_writebacks",
                    "count": len(report.legacy_writebacks),
                }
            )
        if report.retry_requests_recorded:
            side_effects.append(
                {
                    "kind": "retry_requests_recorded",
                    "count": len(report.retry_requests_recorded),
                }
            )
        if report.failures:
            side_effects.append(
                {"kind": "failures", "count": len(report.failures)}
            )

        await self._audit_navigator_tool(
            tool_name="inbox_dispose",
            payload={
                "disposition": disposition,
                "resolution": resolution,
                "from_status": "new",
                "body": body,
            },
            target={"kind": "inbox_item", "id": str(item.id)},
        )

        await self._session.flush()
        return _json_result(
            {
                "inbox_item_id": str(item.id),
                "new_status": new_status,
                "applied_disposition": disposition,
                "resolution": resolution,
                "side_effects": side_effects,
            }
        )

    async def _tool_inbox_snooze(self, args: dict[str, Any]) -> str:
        gate_err = await self._require_admin_or_error(tool_name="inbox_snooze")
        if gate_err is not None:
            return _json_result(gate_err)

        try:
            item_id = _parse_uuid(args, "inbox_item_id")
            until = _parse_iso_datetime(args.get("until"), "until")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "validation_failed",
                "message": str(exc),
            })
        if until is None:
            return _json_result({
                "error": "validation_failed",
                "message": "until is required (ISO-8601 timestamp)",
            })

        from datetime import timedelta, timezone as _tz
        from backend.app.api.v1.routes.inbox import OPEN_STATUSES

        now = datetime.now(_tz.utc)
        if until <= now:
            return _json_result({
                "error": "validation_failed",
                "message": "until must be in the future",
            })
        if until - now > timedelta(days=30):
            return _json_result({
                "error": "validation_failed",
                "message": "snooze cap is 30 days; reassign or dismiss instead",
            })

        item = (
            await self._session.execute(
                select(InboxItem).where(
                    InboxItem.id == item_id,
                    InboxItem.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            return _json_result({
                "error": "not_found",
                "message": (
                    f"inbox item {item_id} not found in this workspace"
                ),
            })
        if item.status not in OPEN_STATUSES:
            return _json_result({
                "error": "precondition_failed",
                "message": (
                    f"can only snooze items in status {list(OPEN_STATUSES)};"
                    f" item is currently {item.status!r}"
                ),
            })

        item.status = "snoozed"
        item.snoozed_until = until

        self._session.add(
            InboxItemEvent(
                item_id=item.id,
                actor_user_id=self._user_id,
                actor_kind="agent",
                action="snoozed",
                payload={"snoozed_until": until.isoformat()},
            )
        )

        await self._audit_navigator_tool(
            tool_name="inbox_snooze",
            payload={"snoozed_until": until.isoformat()},
            target={"kind": "inbox_item", "id": str(item.id)},
        )

        await self._session.flush()
        return _json_result(
            {
                "inbox_item_id": str(item.id),
                "snoozed_until": until.isoformat(),
            }
        )

    async def _tool_inbox_reassign(self, args: dict[str, Any]) -> str:
        gate_err = await self._require_admin_or_error(
            tool_name="inbox_reassign"
        )
        if gate_err is not None:
            return _json_result(gate_err)

        try:
            item_id = _parse_uuid(args, "inbox_item_id")
            assignee_user_id = _parse_uuid(args, "assignee_user_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "validation_failed",
                "message": str(exc),
            })
        reason = args.get("reason")
        if reason is not None:
            if not isinstance(reason, str):
                return _json_result({
                    "error": "validation_failed",
                    "message": "reason must be a string when provided",
                })
            if len(reason) > 500:
                return _json_result({
                    "error": "validation_failed",
                    "message": "reason must be ≤ 500 chars",
                })

        item = (
            await self._session.execute(
                select(InboxItem).where(
                    InboxItem.id == item_id,
                    InboxItem.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            return _json_result({
                "error": "not_found",
                "message": (
                    f"inbox item {item_id} not found in this workspace"
                ),
            })

        # Tenancy: target user must be a workspace member.
        member = (
            await self._session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == self._workspace_id,
                    WorkspaceMember.user_id == assignee_user_id,
                )
            )
        ).scalar_one_or_none()
        if member is None:
            return _json_result({
                "error": "validation_failed",
                "message": "assignee_user_id is not a workspace member",
            })

        prior_owner = item.owner_user_id
        item.owner_user_id = assignee_user_id
        item.intake_handle = None
        item.intake_reason = "manual:navigator"

        event_payload: dict[str, Any] = {
            "old_owner_user_id": (
                str(prior_owner) if prior_owner is not None else None
            ),
            "new_owner_user_id": str(assignee_user_id),
            "intake_reason": item.intake_reason,
        }
        if reason:
            event_payload["reason"] = reason

        self._session.add(
            InboxItemEvent(
                item_id=item.id,
                actor_user_id=self._user_id,
                actor_kind="agent",
                action="reassigned",
                payload=event_payload,
            )
        )

        await self._audit_navigator_tool(
            tool_name="inbox_reassign",
            payload=event_payload,
            target={"kind": "inbox_item", "id": str(item.id)},
        )

        await self._session.flush()
        return _json_result(
            {
                "inbox_item_id": str(item.id),
                "prior_owner_id": (
                    str(prior_owner) if prior_owner is not None else None
                ),
                "new_owner_id": str(assignee_user_id),
            }
        )

    async def _tool_play_run_now(self, args: dict[str, Any]) -> str:
        gate_err = await self._require_admin_or_error(
            tool_name="play_run_now"
        )
        if gate_err is not None:
            return _json_result(gate_err)

        try:
            play_key = _require_str(args, "play_key")
            repo_id = _parse_uuid(args, "repo_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "validation_failed",
                "message": str(exc),
            })

        idempotency_key = args.get("idempotency_key")
        if idempotency_key is not None and not isinstance(
            idempotency_key, str
        ):
            return _json_result({
                "error": "validation_failed",
                "message": "idempotency_key must be a string when provided",
            })

        if not await self._verify_repo_in_workspace(repo_id):
            return _json_result({
                "error": "not_found",
                "message": (
                    f"repo {repo_id} is not activated for this workspace"
                ),
            })

        # Resolve play_key → Pipeline via lane_id (Wave A established
        # the same mapping in ``_tool_runs_query``). A play that has
        # never been automated for this repo has no Pipeline row, so
        # we surface ``no_automation`` rather than 404 to point the
        # caller at the right next step.
        pipeline = (
            await self._session.execute(
                select(Pipeline).where(
                    Pipeline.workspace_id == self._workspace_id,
                    Pipeline.repo_id == repo_id,
                    Pipeline.lane_id == play_key,
                )
            )
        ).scalar_one_or_none()
        if pipeline is None:
            return _json_result({
                "error": "no_automation",
                "message": (
                    f"Play {play_key!r} is not yet automated for this "
                    "repo. Use play_automate first or run via shipctl."
                ),
            })
        if not pipeline.enabled:
            return _json_result({
                "error": "conflict",
                "message": (
                    "pipeline is disabled; toggle it on before running"
                ),
            })

        # Reuse the same dispatch path the HTTP "Run now" route walks
        # so a Navigator-initiated run actually lands a
        # ``workflow_dispatch`` on GitHub Actions. Earlier this tool
        # only inserted a ``status='queued'`` row and left dispatch to
        # "whichever scheduler is wired up" — but no such scheduler
        # ever existed, so navigator-queued runs sat forever. Calling
        # the shared helper means the run goes ``queued -> running``
        # in one shot (or surfaces the same precondition / upstream
        # codes the dashboard already knows how to render).
        from fastapi import HTTPException

        from backend.app.api.v1.routes.pipelines import dispatch_pipeline_run

        run_payload: dict[str, Any] = {"source": "navigator"}
        if idempotency_key:
            run_payload["idempotency_key"] = idempotency_key

        audit_extra: dict[str, Any] = {"source": "navigator"}
        if idempotency_key:
            audit_extra["idempotency_key"] = idempotency_key

        try:
            run = await dispatch_pipeline_run(
                self._session,
                self._settings,
                pipeline,
                trigger="manual",
                summary=f"Navigator queued {pipeline.name or play_key}",
                payload=run_payload,
                actor_user_id=self._user_id,
                explicit_repo_id=repo_id,
                audit_extra=audit_extra,
            )
        except HTTPException as exc:
            # Translate FastAPI's structured precondition / upstream
            # errors into the JSON shape Navigator tools use so the
            # LLM can render a useful next-step (Install workflow,
            # Reinstall App, …) instead of a 5xx-shaped surprise.
            detail = exc.detail if isinstance(exc.detail, dict) else {
                "message": str(exc.detail)
            }
            error_code = detail.get("code") or "dispatch_failed"
            response = {
                "error": error_code,
                "message": detail.get("message") or str(exc.detail),
            }
            for k in (
                "workflow_file",
                "repo_full_name",
                "install_endpoint",
                "upstream_status",
                "run_id",
            ):
                if k in detail:
                    response[k] = detail[k]
            return _json_result(response)

        await self._audit_navigator_tool(
            tool_name="play_run_now",
            payload={
                "play_key": play_key,
                "repo_id": str(repo_id),
                "pipeline_id": str(pipeline.id),
                "run_id": str(run.id),
                "idempotency_key": idempotency_key,
            },
            target={"kind": "pipeline", "id": str(pipeline.id)},
        )

        return _json_result(
            {
                "run_id": str(run.id),
                "pipeline_id": str(pipeline.id),
                "status": run.status,
                "play_key": play_key,
                "repo_id": str(repo_id),
            }
        )

    async def _tool_play_automate(self, args: dict[str, Any]) -> str:
        gate_err = await self._require_admin_or_error(
            tool_name="play_automate"
        )
        if gate_err is not None:
            return _json_result(gate_err)

        try:
            play_key = _require_str(args, "play_key")
            cadence = _require_str(args, "cadence")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "validation_failed",
                "message": str(exc),
            })

        scope = args.get("scope")
        if scope not in ("repo", "fleet"):
            return _json_result({
                "error": "validation_failed",
                "message": "scope must be 'repo' or 'fleet'",
            })

        name = args.get("name")
        if name is not None and not isinstance(name, str):
            return _json_result({
                "error": "validation_failed",
                "message": "name must be a string when provided",
            })

        # Resolve the catalog pattern up front so we can stamp a
        # human title on the row even when the caller didn't pass one.
        try:
            patterns = catalog_service.list_patterns()
        except catalog_service.CatalogError as exc:
            return _json_result({
                "error": "internal",
                "message": f"catalog read failed: {exc}",
            })
        pattern = next((p for p in patterns if p.id == play_key), None)
        if pattern is None:
            return _json_result({
                "error": "not_found",
                "message": f"no catalog pattern with id={play_key!r}",
            })
        derived_name = (
            name
            or getattr(pattern, "title", None)
            or play_key
        )

        # Map the free-form cadence onto Lane.kind (once / event /
        # schedule). Cadence strings that look like a cron expression
        # (5 whitespace-separated fields) become schedule + cron.
        cadence_l = cadence.strip().lower()

        def _classify_cadence(c: str) -> tuple[str, str | None]:
            if c == "manual":
                return "once", None
            if c == "on_pr":
                return "event", None
            if c == "weekly":
                return "schedule", "0 9 * * 1"
            if c == "daily":
                return "schedule", "0 9 * * *"
            if c == "hourly":
                return "schedule", "0 * * * *"
            if len(c.split()) == 5:
                return "schedule", c
            return "event", None

        kind, cron = _classify_cadence(cadence_l)

        if scope == "repo":
            try:
                repo_id = _parse_uuid(args, "repo_id")
            except ToolInvocationError as exc:
                return _json_result({
                    "error": "validation_failed",
                    "message": str(exc),
                })
            if not await self._verify_repo_in_workspace(repo_id):
                return _json_result({
                    "error": "not_found",
                    "message": (
                        f"repo {repo_id} is not activated for this "
                        "workspace"
                    ),
                })

            # Synthesise a stable lane_id string keyed by the catalog
            # pattern + cadence so re-invocations with identical args
            # collide on the unique (repo_id, lane_id) constraint.
            cadence_slug = "".join(
                ch if ch.isalnum() else "_" for ch in cadence_l
            )[:20]
            slug_base = "".join(
                ch if (ch.isalnum() or ch == "_") else "_"
                for ch in play_key.lower()
            )[:40]
            lane_key = f"{slug_base}_{cadence_slug}"[:64]

            existing_lane = (
                await self._session.execute(
                    select(Lane).where(
                        Lane.repo_id == repo_id,
                        Lane.lane_id == lane_key,
                    )
                )
            ).scalar_one_or_none()
            if existing_lane is not None:
                return _json_result({
                    "error": "conflict",
                    "message": (
                        "a Lane with this play_key + cadence already "
                        "exists on the repo"
                    ),
                    "existing_lane_id": str(existing_lane.id),
                })

            row = Lane(
                workspace_id=self._workspace_id,
                repo_id=repo_id,
                lane_id=lane_key,
                kind=kind,
                pattern=play_key[:255],
                cron=cron,
                idempotency_key=None,
                enabled=True,
                origin="manual",
                config_blob={
                    "name": derived_name,
                    "play_key": play_key,
                    "cadence": cadence,
                    "source": "navigator",
                },
                sync_source="navigator",
            )
            self._session.add(row)
            await self._session.flush()

            await self._audit_navigator_tool(
                tool_name="play_automate",
                payload={
                    "play_key": play_key,
                    "scope": "repo",
                    "repo_id": str(repo_id),
                    "cadence": cadence,
                    "name": derived_name,
                    "lane_key": lane_key,
                    "kind": kind,
                },
                target={"kind": "lane", "id": str(row.id)},
            )

            return _json_result(
                {
                    "lane_id": str(row.id),
                    "lane_key": lane_key,
                    "play_key": play_key,
                    "scope": "repo",
                    "repo_id": str(repo_id),
                    "cadence": cadence,
                    "status": "synthetic",
                }
            )

        # scope == "fleet" — FleetLane is the workspace-wide primitive.
        # Synthesise the same kind of stable lane_id used for repo
        # scope so duplicate invocations collide on the unique
        # ``(workspace_id, lane_id)`` constraint inside FleetLane.
        cadence_slug = "".join(
            ch if ch.isalnum() else "_" for ch in cadence_l
        )[:20]
        slug_base = "".join(
            ch if (ch.isalnum() or ch == "_") else "_"
            for ch in play_key.lower()
        )[:40]
        fleet_lane_key = f"{slug_base}_{cadence_slug}"[:64]

        existing_fleet = (
            await self._session.execute(
                select(FleetLane).where(
                    FleetLane.workspace_id == self._workspace_id,
                    FleetLane.lane_id == fleet_lane_key,
                )
            )
        ).scalar_one_or_none()
        if existing_fleet is not None:
            return _json_result({
                "error": "conflict",
                "message": (
                    "a Fleet lane with this play_key + cadence already "
                    "exists for the workspace"
                ),
                "existing_lane_id": str(existing_fleet.id),
            })

        fleet_row = FleetLane(
            workspace_id=self._workspace_id,
            kind="mirror_lane",
            name=derived_name,
            pattern_id=play_key,
            lane_id=fleet_lane_key,
            cadence=cadence,
            agent_slug=None,
            inputs={"source": "navigator"},
            enabled=True,
        )
        self._session.add(fleet_row)
        await self._session.flush()

        await self._audit_navigator_tool(
            tool_name="play_automate",
            payload={
                "play_key": play_key,
                "scope": "fleet",
                "cadence": cadence,
                "name": derived_name,
                "lane_key": fleet_lane_key,
            },
            target={"kind": "fleet_lane", "id": str(fleet_row.id)},
        )

        return _json_result(
            {
                "lane_id": str(fleet_row.id),
                "lane_key": fleet_lane_key,
                "play_key": play_key,
                "scope": "fleet",
                "repo_id": None,
                "cadence": cadence,
                "status": "synthetic",
            }
        )

    async def _tool_automation_toggle(self, args: dict[str, Any]) -> str:
        gate_err = await self._require_admin_or_error(
            tool_name="automation_toggle"
        )
        if gate_err is not None:
            return _json_result(gate_err)

        try:
            pipeline_id = _parse_uuid(args, "pipeline_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "validation_failed",
                "message": str(exc),
            })
        enabled = args.get("enabled")
        if not isinstance(enabled, bool):
            return _json_result({
                "error": "validation_failed",
                "message": "enabled must be a boolean",
            })

        pipeline = (
            await self._session.execute(
                select(Pipeline).where(
                    Pipeline.workspace_id == self._workspace_id,
                    Pipeline.id == pipeline_id,
                )
            )
        ).scalar_one_or_none()
        if pipeline is None:
            return _json_result({
                "error": "not_found",
                "message": (
                    f"pipeline {pipeline_id} not found in this workspace"
                ),
            })

        prior_enabled = pipeline.enabled
        if prior_enabled != enabled:
            from datetime import timezone as _tz

            pipeline.enabled = enabled
            pipeline.updated_at = datetime.now(_tz.utc)
            await self._audit_navigator_tool(
                tool_name="automation_toggle",
                payload={
                    "pipeline_id": str(pipeline_id),
                    "lane_id": pipeline.lane_id,
                    "enabled": enabled,
                    "prior_enabled": prior_enabled,
                },
                target={"kind": "pipeline", "id": str(pipeline.id)},
            )
            await self._session.flush()

        return _json_result(
            {
                "pipeline_id": str(pipeline.id),
                "enabled": enabled,
                "prior_enabled": prior_enabled,
            }
        )

    async def _tool_intel_harvest_trigger(self, args: dict[str, Any]) -> str:
        gate_err = await self._require_admin_or_error(
            tool_name="intel_harvest_trigger"
        )
        if gate_err is not None:
            return _json_result(gate_err)

        try:
            repo_id = _parse_uuid(args, "repo_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "validation_failed",
                "message": str(exc),
            })
        if not await self._verify_repo_in_workspace(repo_id):
            return _json_result({
                "error": "not_found",
                "message": (
                    f"repo {repo_id} is not activated for this workspace"
                ),
            })

        # Per-repo per-workspace 1/hour rate limit. We encode it as a
        # lookup against our own audit rows so the limit survives a
        # process restart and is observable in the audit timeline
        # (rate-limit denials themselves do NOT audit, per spec).
        from datetime import timedelta, timezone as _tz

        now = datetime.now(_tz.utc)
        cutoff = now - timedelta(hours=1)
        recent = (
            await self._session.execute(
                select(AuditLog)
                .where(
                    AuditLog.workspace_id == self._workspace_id,
                    AuditLog.action
                    == "navigator.tool.intel_harvest_trigger",
                    AuditLog.target_id == str(repo_id),
                    AuditLog.created_at >= cutoff,
                )
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if recent is not None:
            elapsed = now - recent.created_at
            retry_after = max(
                1, int(timedelta(hours=1).total_seconds() - elapsed.total_seconds())
            )
            return _json_result(
                {
                    "error": "rate_limited",
                    "message": (
                        "harvest already triggered in the last hour"
                    ),
                    "retry_after_seconds": retry_after,
                }
            )

        from backend.app.db.models.repo_intel import RepoIntelTriggeredBy
        from backend.app.services.repo_intel import enqueue_harvest

        # Settings carries the redis pool reference in the production
        # path; fall back to ``None`` (inline asyncio.create_task) if
        # the attribute isn't wired up — see ``enqueue_harvest`` for
        # the documented inline-fallback contract.
        redis_pool = getattr(self._settings, "redis_pool", None)
        try:
            await enqueue_harvest(
                redis_pool,
                self._workspace_id,
                repo_id,
                triggered_by=RepoIntelTriggeredBy.MANUAL_REFRESH,
            )
        except Exception as exc:  # noqa: BLE001 — surface as tool error
            logger.exception(
                "intel_harvest_trigger: enqueue failed (repo=%s)", repo_id
            )
            return _json_result({
                "error": "internal",
                "message": f"enqueue failed: {exc}",
            })

        await self._audit_navigator_tool(
            tool_name="intel_harvest_trigger",
            payload={"repo_id": str(repo_id)},
            target={"kind": "workspace_repo", "id": str(repo_id)},
        )

        return _json_result(
            {
                "repo_id": str(repo_id),
                "status": "queued",
                "triggered_by": "navigator",
            }
        )

    async def _tool_inbox_routing_upsert(self, args: dict[str, Any]) -> str:
        gate_err = await self._require_admin_or_error(
            tool_name="inbox_routing_upsert"
        )
        if gate_err is not None:
            return _json_result(gate_err)

        # Compromise: the underlying ``inbox_routing_rules`` schema
        # doesn't have ``name`` / ``when`` / ``priority`` columns —
        # the table is keyed by ``handle_key`` (one rule per handle
        # per workspace) and the admin surface in
        # ``app/api/v1/routes/inbox_routing.py`` exposes only
        # handle / target_type / target_value / assignment_strategy /
        # strategy_config / is_enabled. We map the spec's friendlier
        # vocabulary onto those fields:
        #
        #   - ``name`` becomes the ``handle_key`` (must therefore
        #     match the handle character class ``^[a-z][a-z0-9_]*$``).
        #   - ``then_assign_to`` is unpacked into target_type +
        #     target_value via the same packing rules the HTTP route
        #     uses.
        #   - ``when`` and ``priority`` are stored under
        #     ``strategy_config['_when']`` / ``strategy_config['_priority']``
        #     for forward-compatibility — the resolver currently
        #     ignores them but the DB round-trips them so a future
        #     migration can promote them to first-class columns.
        name = args.get("name")
        if not isinstance(name, str) or not name:
            return _json_result({
                "error": "validation_failed",
                "message": "name is required",
            })
        if len(name) > 64:
            return _json_result({
                "error": "validation_failed",
                "message": (
                    "name maps to handle_key (max 64 chars)"
                ),
            })
        import re

        if not re.match(r"^[a-z][a-z0-9_]*$", name):
            return _json_result({
                "error": "validation_failed",
                "message": (
                    "name must match ^[a-z][a-z0-9_]*$ "
                    "(it maps to the routing-rule handle_key)"
                ),
            })

        when = args.get("when")
        if when is None:
            when = {}
        if not isinstance(when, dict):
            return _json_result({
                "error": "validation_failed",
                "message": "when must be an object",
            })

        then_assign_to = args.get("then_assign_to")
        if not isinstance(then_assign_to, dict) or not then_assign_to:
            return _json_result({
                "error": "validation_failed",
                "message": "then_assign_to is required (object)",
            })
        strategy = then_assign_to.get("strategy")
        if not isinstance(strategy, str) or not strategy:
            return _json_result({
                "error": "validation_failed",
                "message": (
                    "then_assign_to.strategy is required (e.g. 'user', "
                    "'group', 'round_robin', 'oncall', 'first', "
                    "'codeowners', ...)"
                ),
            })

        priority = args.get("priority", 100)
        if not isinstance(priority, int) or isinstance(priority, bool):
            return _json_result({
                "error": "validation_failed",
                "message": "priority must be an integer",
            })

        enabled = args.get("enabled", True)
        if not isinstance(enabled, bool):
            return _json_result({
                "error": "validation_failed",
                "message": "enabled must be a boolean",
            })

        # Map ``then_assign_to`` onto target_type + target_value +
        # assignment_strategy. We mirror the strategy taxonomy from
        # ``services.inbox.routing`` (which documents the built-in
        # handle resolvers) plus the per-group strategies from the
        # HTTP routing surface.
        target_type: str
        target_value: str
        assignment_strategy: str | None = None

        builtin_strategies = {
            "codeowners",
            "workspace_admin",
            "workspace_owner",
            "requested_by",
            "first_admin",
            "first_owner",
        }
        group_strategies = {"round_robin", "oncall", "first"}

        if strategy == "user":
            user_id_raw = then_assign_to.get("user_id")
            if not isinstance(user_id_raw, str) or not user_id_raw:
                return _json_result({
                    "error": "validation_failed",
                    "message": (
                        "then_assign_to.strategy='user' requires user_id"
                    ),
                })
            try:
                user_uuid = uuid.UUID(user_id_raw)
            except ValueError:
                return _json_result({
                    "error": "validation_failed",
                    "message": "user_id is not a UUID",
                })
            member = (
                await self._session.execute(
                    select(WorkspaceMember).where(
                        WorkspaceMember.workspace_id == self._workspace_id,
                        WorkspaceMember.user_id == user_uuid,
                    )
                )
            ).scalar_one_or_none()
            if member is None:
                return _json_result({
                    "error": "validation_failed",
                    "message": (
                        "target user_id is not a member of this workspace"
                    ),
                })
            target_type = "user"
            target_value = str(user_uuid)

        elif strategy == "group" or strategy in group_strategies:
            group_id_raw = then_assign_to.get("group_id")
            if not isinstance(group_id_raw, str) or not group_id_raw:
                return _json_result({
                    "error": "validation_failed",
                    "message": (
                        f"then_assign_to.strategy={strategy!r} requires "
                        "group_id"
                    ),
                })
            try:
                group_uuid = uuid.UUID(group_id_raw)
            except ValueError:
                return _json_result({
                    "error": "validation_failed",
                    "message": "group_id is not a UUID",
                })
            group_row = (
                await self._session.execute(
                    select(MemberGroup).where(
                        MemberGroup.id == group_uuid,
                        MemberGroup.workspace_id == self._workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if group_row is None:
                return _json_result({
                    "error": "validation_failed",
                    "message": (
                        "target group_id does not exist in this workspace"
                    ),
                })
            target_type = "group"
            target_value = group_row.key
            if strategy in group_strategies:
                assignment_strategy = strategy

        elif strategy in builtin_strategies:
            target_type = "strategy"
            target_value = strategy

        else:
            return _json_result({
                "error": "validation_failed",
                "message": (
                    f"unknown strategy {strategy!r} (allowed: 'user', "
                    "'group', plus per-group "
                    f"{sorted(group_strategies)} or built-in "
                    f"{sorted(builtin_strategies)})"
                ),
            })

        strategy_config: dict[str, Any] = {}
        extra_cfg = then_assign_to.get("strategy_config")
        if isinstance(extra_cfg, dict):
            strategy_config.update(extra_cfg)
        if when:
            strategy_config["_when"] = when
        if priority != 100:
            strategy_config["_priority"] = priority

        rule_id_arg = args.get("rule_id")
        rule: InboxRoutingRule | None = None
        if rule_id_arg is not None:
            if not isinstance(rule_id_arg, str) or not rule_id_arg:
                return _json_result({
                    "error": "validation_failed",
                    "message": "rule_id must be a UUID string when provided",
                })
            try:
                rule_uuid = uuid.UUID(rule_id_arg)
            except ValueError:
                return _json_result({
                    "error": "validation_failed",
                    "message": "rule_id is not a UUID",
                })
            rule = (
                await self._session.execute(
                    select(InboxRoutingRule).where(
                        InboxRoutingRule.id == rule_uuid,
                        InboxRoutingRule.workspace_id == self._workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if rule is None:
                return _json_result({
                    "error": "not_found",
                    "message": (
                        f"routing rule {rule_uuid} not found in this "
                        "workspace"
                    ),
                })

        action: str
        if rule is None:
            # Conflict-friendly insert — surface the dup as a 409-shaped
            # error rather than letting an IntegrityError escape.
            existing = (
                await self._session.execute(
                    select(InboxRoutingRule).where(
                        InboxRoutingRule.workspace_id == self._workspace_id,
                        InboxRoutingRule.handle_key == name,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _json_result({
                    "error": "conflict",
                    "message": (
                        "a routing rule for this handle already exists; "
                        "pass its rule_id to update it instead"
                    ),
                    "existing_rule_id": str(existing.id),
                })
            rule = InboxRoutingRule(
                workspace_id=self._workspace_id,
                handle_key=name,
                target_type=target_type,
                target_value=target_value,
                assignment_strategy=assignment_strategy,
                strategy_config=strategy_config,
                is_enabled=enabled,
            )
            self._session.add(rule)
            await self._session.flush()
            action = "created"
        else:
            rule.target_type = target_type
            rule.target_value = target_value
            rule.assignment_strategy = assignment_strategy
            rule.strategy_config = strategy_config
            rule.is_enabled = enabled
            action = "updated"
            await self._session.flush()

        await self._audit_navigator_tool(
            tool_name="inbox_routing_upsert",
            payload={
                "rule_id": str(rule.id),
                "action": action,
                "handle": name,
                "target_type": target_type,
                "target_value": target_value,
                "assignment_strategy": assignment_strategy,
                "strategy_config": strategy_config,
                "is_enabled": enabled,
            },
            target={"kind": "inbox_routing_rule", "id": str(rule.id)},
        )

        return _json_result(
            {
                "rule_id": str(rule.id),
                "action": action,
                "name": name,
                "priority": priority,
                "enabled": enabled,
            }
        )


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


# ---------------------------------------------------------------------------
# Phase 6 helpers — inbox cursor + repo_intel summary projection
# ---------------------------------------------------------------------------


def _encode_inbox_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    """Pack the keyset tuple ``(created_at, id)`` as a URL-safe blob.

    Mirrors the encoding used by ``GET /v1/workspaces/{ws}/inbox`` so
    cursors handed back by the tool can be round-tripped through the
    HTTP surface and vice-versa.
    """
    raw = json.dumps([created_at.isoformat(), str(item_id)])
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_inbox_cursor(
    cursor: Any,
) -> tuple[datetime, uuid.UUID] | None:
    """Inverse of :func:`_encode_inbox_cursor`. Returns None on failure.

    Returning ``None`` (instead of raising) lets the tool surface
    ``{"error": "invalid_cursor"}`` without forcing the LLM to handle
    a tool-call failure.
    """
    if not isinstance(cursor, str) or not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts_str, id_str = json.loads(raw)
        ts = datetime.fromisoformat(ts_str)
        item_id = uuid.UUID(id_str)
    except (ValueError, binascii.Error, json.JSONDecodeError, TypeError):
        return None
    return ts, item_id


def _intel_summary_snippet(intel: Any) -> str:
    """Build a one-paragraph synopsis of a :class:`RepoIntel` row.

    Used by :meth:`ToolBox._tool_knowledge_search_v2` when
    ``intel_facts=True`` to inject a synthetic "intel summary" hit at
    the top of the results list. The exact phrasing is intentionally
    plain English — the LLM treats it as a summary it can quote
    verbatim, not as a structured payload.
    """
    parts: list[str] = []
    languages = intel.languages or {}
    if isinstance(languages, dict) and languages:
        top = sorted(
            (
                (k, float(v))
                for k, v in languages.items()
                if isinstance(k, str) and isinstance(v, (int, float))
            ),
            key=lambda kv: kv[1],
            reverse=True,
        )[:3]
        if top:
            parts.append(
                "Primary languages: "
                + ", ".join(
                    f"{name} ({round(share * 100)}%)" for name, share in top
                )
                + "."
            )
    frameworks = intel.frameworks or []
    if isinstance(frameworks, list) and frameworks:
        readable = [str(f) for f in frameworks if isinstance(f, str)][:6]
        if readable:
            parts.append("Frameworks: " + ", ".join(readable) + ".")
    entry_points = intel.entry_points or []
    if isinstance(entry_points, list) and entry_points:
        paths: list[str] = []
        for ep in entry_points[:5]:
            if isinstance(ep, dict):
                path = ep.get("path")
                if isinstance(path, str):
                    paths.append(path)
            elif isinstance(ep, str):
                paths.append(ep)
        if paths:
            parts.append("Entry points: " + ", ".join(paths) + ".")
    structure = intel.structure or {}
    if isinstance(structure, dict):
        top_dirs = structure.get("top_level_dirs")
        file_count = structure.get("file_count")
        if isinstance(top_dirs, list) and top_dirs:
            readable_dirs = [
                str(d) for d in top_dirs[:6] if isinstance(d, str)
            ]
            if readable_dirs:
                parts.append(
                    "Top-level dirs: " + ", ".join(readable_dirs) + "."
                )
        if isinstance(file_count, int):
            parts.append(f"File count: ~{file_count}.")
    if intel.harvested_at is not None:
        parts.append(
            f"Harvested at {intel.harvested_at.isoformat()} "
            f"(version {intel.version})."
        )
    if not parts:
        return (
            f"repo_intel snapshot v{intel.version} has no harvested "
            "facts yet."
        )
    return " ".join(parts)


__all__ = ["ToolBox", "ToolInvocationError"]
