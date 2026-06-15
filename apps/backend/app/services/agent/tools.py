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

The canonical tool inventory lives in :meth:`ToolBox.specs` — the
list this docstring used to enumerate drifted out of sync every
time a tool was added, renamed, or retired (phase 1a deleted 21,
phase 1c collapsed 6 into 3 polymorphic CRUDs, phase 1b renamed
22). ``test_navigator_tool_inventory.EXPECTED_TOOLS`` pins the
current set; consult it when you need the punch list.

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
from sqlalchemy import desc, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import (
    Clarification,
    Improvement,
)
from backend.app.db.models.inbox import (
    InboxItem,
    InboxItemEvent,
    InboxRoutingRule,
    MemberGroup,
    RunEscalation,
)
from backend.app.db.models.integrations import (
    GitHubInstallation,
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
    WorkspaceRepo,
)
from backend.app.db.models.dashboard_priorities import WorkspaceProjectPriority
from backend.app.db.models.lanes import Routine, RoutineRun
from backend.app.db.models.pipelines import (
    PullRequest,
    WorkflowRun,
)
from backend.app.db.models.tenancy import (
    AuditLog,
    Integration,
    User as TenancyUser,
    Workspace,
    WorkspaceMember,
)
from backend.app.integrations.gateway.code_host import PullRequestRef, RepoRef
from backend.app.integrations.gateway.tracker import (
    CreatedTicket,
    TrackerGateway,
)
from backend.app.services.tracker_ticket_context import (
    serialize_ticket_comments,
    ticket_ref_from,
)
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.integrations.github.issues_tracker import GitHubIssuesTracker
from backend.app.integrations.jira.tracker_adapter import JiraTracker
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
_MAX_CLARIFICATIONS = 50
_MAX_IMPROVEMENTS = 50
_MAX_PRS_LISTED = 50
_MAX_ARTIFACT_BODY_CHARS = 32 * 1024
_MAX_KB_FULL_CHUNK = 12_000
_MAX_CODE_SEARCH = 20
_MAX_BUCKET_SUMMARIES = 40
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
        thread_id: uuid.UUID | None = None,
        thread_intent: str | None = None,
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
        # ELS-74 (drafting mode): ``_tool_project_create`` writes
        # ``originating_thread_id`` onto the priorities row when these
        # are set, so the dashboard can later render a "Continue
        # shaping" link that re-opens the originating Navigator thread
        # instead of fragmenting the conversation. ``thread_intent``
        # is exposed so individual tools could specialise their copy
        # in drafting mode (e.g. confirmation strings) — today only
        # ``_tool_project_create`` reads it.
        self._thread_id = thread_id
        self._thread_intent = thread_intent
        # Navigator overhaul PR3: re-entry guard for ``consult_specialist``.
        # A subagent calling ``consult_specialist`` is filtered out at
        # the tool-spec layer so the LLM literally can't emit the name,
        # but we belt-and-suspender it here for hallucinations: the
        # handler refuses when this flag is True.
        self._subagent_active = False

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
                name="repo_file_get",
                description=(
                    "Fetch the current contents of a specific file in an "
                    "activated repo. Prefer knowledge search first; only "
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
                name="repo_tree",
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
                name="repo_symbols",
                description=(
                    "Resolve symbol names → file/line/signature without a "
                    "preindex. On-demand tree-sitter parse of the requested "
                    "files (Python / TypeScript / TSX / Go in v1). Two modes:\n"
                    "  • ``paths``: pass a list of repo-relative files, get "
                    "every symbol in those files.\n"
                    "  • ``query``: pass a symbol name (e.g. ``UserRepo``); "
                    "GitHub code search narrows down to candidate files, "
                    "then the parser filters per-file results to symbols "
                    "whose name matches (case-insensitive substring).\n\n"
                    "Use this when you need to *find* a symbol by name "
                    "(``where is class Foo defined?``) or *enumerate* the "
                    "symbols in a known file. For full file contents, fall "
                    "back to ``get_repo_file``. Output rows: ``{file, "
                    "symbol, kind, line, signature}`` with ``kind`` ∈ "
                    "``function|class|method|interface|type|struct|enum|"
                    "var|const``."
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
                                "Symbol name (case-insensitive substring) "
                                "to search for. Required when ``paths`` is "
                                "omitted."
                            ),
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Repo-relative file paths to parse. Use "
                                "instead of ``query`` when you already know "
                                "which files to inspect (faster, deterministic)."
                            ),
                        },
                        "kinds": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional filter on symbol kind, e.g. "
                                "``[\"function\", \"method\"]`` to skip "
                                "classes / vars."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                            "description": (
                                "Max symbols to return across all parsed "
                                "files."
                            ),
                        },
                        "max_files": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 25,
                            "description": (
                                "Max files to fetch + parse per call. "
                                "Keeps latency bounded; reduce when only a "
                                "few hits are needed."
                            ),
                        },
                    },
                    "required": ["repo_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="ticket_create",
                description=(
                    "Open a ticket on the workspace's connected tracker "
                    "(Linear, Notion, Jira, or GitHub Issues). Only call when "
                    "the user has explicitly asked to track work or you "
                    "have their confirmation — never autofile. Pass "
                    "``project_id`` to attach the new ticket to an epic so "
                    "child tickets stay short and pull motivation / scope "
                    "from the project body. Pass ``type`` (``bug`` / "
                    "``feature`` / ``task``) to classify the work so "
                    "downstream triage dashboards key off the tracker-"
                    "native field (Jira issuetype, Linear native issue "
                    "type when enabled, otherwise a ``type:<value>`` "
                    "label on Linear / GitHub / Notion ``Type`` select)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
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
                                "Jira project key / GitHub owner/repo. Omit for single-target "
                                "workspaces."
                            ),
                        },
                        "project_id": {
                            "type": "string",
                            "description": (
                                "Tracker-native project (epic) UUID. From "
                                "``list_projects``. Omit for standalone "
                                "tickets not part of an epic."
                            ),
                        },
                        "type": {
                            "type": "string",
                            "enum": ["bug", "feature", "task"],
                            "description": (
                                "Optional classification — ``bug`` / "
                                "``feature`` / ``task``. Maps to the "
                                "tracker-native field where supported "
                                "(Jira issuetype, Linear native issue "
                                "type when enabled, Notion ``Type`` "
                                "select); falls back to a ``type:<value>`` "
                                "label otherwise. Omit to keep today's "
                                "default (Jira → ``Task``, others → no "
                                "extra label)."
                            ),
                        },
                    },
                    "required": ["title", "body"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="project_list",
                description=(
                    "List active projects (epics) on the workspace's "
                    "connected tracker. Use this BEFORE creating an epic "
                    "to check whether one already exists, and BEFORE "
                    "creating a child ticket to find the right epic to "
                    "attach to. Filter with ``state`` (e.g. ``backlog``, "
                    "``started``, ``planned``) or ``query`` (case-"
                    "insensitive name match)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 20,
                        },
                        "state": {
                            "type": "string",
                            "description": (
                                "Linear project state filter: ``backlog`` / "
                                "``planned`` / ``started`` / ``paused`` / "
                                "``completed`` / ``canceled``."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": "Case-insensitive name contains.",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="project_get",
                description=(
                    "Fetch one project's body (markdown content), short "
                    "description, lead, and recently-updated linked "
                    "tickets. Use to (a) read the epic before drafting "
                    "child tickets so you don't repeat motivation already "
                    "captured, or (b) verify the right epic before "
                    "appending PO ideas to its body."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project UUID from ``list_projects``.",
                        },
                        "issues_limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 25,
                        },
                    },
                    "required": ["project_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="project_create",
                description=(
                    "Create a new project (epic) on the workspace's "
                    "connected tracker. ``body`` is the markdown epic "
                    "body — put motivation / scope / decisions / "
                    "constraints here so future tickets can pull "
                    "context from it. Only call when the user has "
                    "asked to start a new initiative; never autofile."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Project name.",
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "Markdown content (full epic body). PO "
                                "ideas / scope / motivation / decisions go "
                                "here, not in chat."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "Optional one-liner blurb (Linear caps at "
                                "240 chars). When omitted, derived from "
                                "the first body line."
                            ),
                        },
                    },
                    "required": ["name", "body"],
                    "additionalProperties": False,
                },
            ),
            # ``project_find_or_create`` deliberately NOT advertised to
            # the LLM planner (no ToolSpec entry). Its case-insensitive
            # exact-match find-or-create is the right primitive for
            # scaffolders that know the project name is unique
            # (``tools/scripts/create_e17_*.py``, daily-retro
            # reviewers), but a Linear workspace can legitimately host
            # multiple projects with similar names (e.g. Ship-on-Ship's
            # Linear has two ``Tech Debt`` projects). For the
            # interactive Navigator flow we want the agent to do
            # ``project_list(query=…)`` then offer the matches to the
            # operator + create only on explicit ack — that handles
            # the duplicate-name case the atomic tool can't. The
            # dispatch table still exposes the handler for direct
            # calls from scaffolder code paths.
            ToolSpec(
                name="inbox_create",
                description=(
                    "Drop a new item in the operator's inbox. The inbox "
                    "is a mailbox-style read surface — supporting "
                    "routines (daily-retro, learning-capture, "
                    "process-reviewer) file ``type=report`` items with a "
                    "full markdown body the operator reads like a "
                    "letter; agentic actions that need a yes/no go "
                    "through the typed surface (clarification, "
                    "approval, improvement). Use ``type=report`` when "
                    "the only action the operator should take is "
                    "reading and acknowledging."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "report",
                                "improvement",
                                "approval",
                                "exception",
                            ],
                            "description": (
                                "Inbox item type. ``report`` for read-"
                                "only digests (daily/retro/process-"
                                "review). ``improvement`` for "
                                "actionable suggestions. ``approval`` "
                                "for gated decisions. ``exception`` "
                                "for policy edge cases."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": (
                                "One-line subject (≤300 chars). Like "
                                "an email subject — operator scans "
                                "this in the list view."
                            ),
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "Markdown body. Rendered as the letter "
                                "content in the preview pane. For "
                                "reports: digest sections, links, "
                                "evidence. For approvals: what's being "
                                "approved + rationale."
                            ),
                        },
                        "summary": {
                            "type": "string",
                            "description": (
                                "Optional short blurb shown next to "
                                "the title in the list view (≤2KB). "
                                "Falls back to the first 200 chars of "
                                "``body`` when omitted."
                            ),
                        },
                    },
                    "required": ["type", "title", "body"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="propose_mass_plan",
                description=(
                    "Extract a structured project + epics + dependencies "
                    "plan from a requirements PDF the user has attached. "
                    "Use this when the user uploads a PRD / spec / RFC "
                    "and asks you to plan a project, scope out epics, "
                    "or break work into phases. The Console renders the "
                    "preview as a card the user edits + commits.\n\n"
                    "Returns ``{proposal_id, project_name, epic_count, "
                    "dep_count, summary}``. Tell the user: \"Here's a "
                    "draft plan — open the preview, edit if needed, and "
                    "hit Commit.\" Do NOT verbose-dump the proposal in "
                    "the chat reply — the preview card is the surface."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "attachment_id": {
                            "type": "string",
                            "description": (
                                "UUID of the chat attachment containing "
                                "the requirements doc. Must be of kind "
                                "``pdf``."
                            ),
                        },
                    },
                    "required": ["attachment_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="project_update",
                description=(
                    "Mutate an existing project. Two sub-ops, set "
                    "either or both in the same call:\n"
                    " * ``body_append``: append the given markdown to "
                    "the project body (accumulates PO ideas / decisions "
                    "/ constraints across sessions). Read the project "
                    "first via ``project_get`` if you need to avoid "
                    "duplicating a section.\n"
                    " * ``priority_state``: move the project between "
                    "dashboard buckets (``active`` / ``planning`` (UI "
                    "label: **Drafts**) / ``parked``). Creates a "
                    "priorities row at MAX+1 ordinal if none exists.\n"
                    "**Mutating; admin-only**. Verify-before-mutate: "
                    "describe the change and wait for explicit OK "
                    "unless the user gave a direct command."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": (
                                "Project identifier — Linear project "
                                "UUID or Jira project key. Used as "
                                "both project_id (body_append path) "
                                "and project_native_id (priority path)."
                            ),
                        },
                        "body_append": {
                            "type": "string",
                            "description": (
                                "Markdown to append to project body. "
                                "A blank line is inserted between "
                                "existing content and the new block."
                            ),
                        },
                        "priority_state": {
                            "type": "string",
                            "enum": ["active", "planning", "parked"],
                            "description": (
                                "Target dashboard bucket. ``planning`` "
                                "is the internal name for the "
                                "**Drafts** UI label."
                            ),
                        },
                    },
                    "required": ["project_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="ticket_list",
                description=(
                    "Read the most-recently-updated tickets from the "
                    "workspace's connected tracker (Linear / Notion / Jira / "
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
                        "project_hint": {
                            "type": "string",
                            "description": (
                                "Forwarded to the tracker for GitHub "
                                "Issues (``owner/repo``), Jira project key, "
                                "or Notion database id."
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
                name="pr_get",
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
                name="pr_list",
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
                name="members_list",
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
                name="knowledge_bucket_get",
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
                name="runs_list",
                description=(
                    "Outcome-first list of routine runs across the "
                    "workspace. Filters by ``play_key`` (matches "
                    "``Routine.lane_id``), repo, status (``ok`` / "
                    "``fail`` / ``error`` / concrete run statuses), "
                    "trigger, ``has_escalations``, and a ``since`` ISO "
                    "timestamp. Prefer this when the user asks 'what "
                    "ran?' in outcome / business terms."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "play_key": {
                            "type": "string",
                            "description": (
                                "Filter by Play key — matches the "
                                "routine's ``lane_id``."
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
                name="runs_get",
                description=(
                    "Full detail of one routine run: RunSummary "
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
                            "description": "Routine run UUID.",
                        },
                    },
                    "required": ["run_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="knowledge_search",
                description=(
                    "Workspace knowledge search with explicit filters "
                    "(``repo_id``, ``bucket_slug``) and an optional "
                    "``intel_facts`` flag that prepends hits from the "
                    "``repository-context`` bucket. Single search "
                    "surface — covers published articles, packed "
                    "buckets (`source='bucket_article'` in results), "
                    "and topic views in one call. When ``repo_id`` is "
                    "omitted the runtime fills in the chat's active "
                    "repo so current-repo hits rank first."
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
                                "When true, prepend published articles from "
                                "the ``repository-context`` bucket for the "
                                "active or supplied ``repo_id``."
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
                name="inbox_update",
                description=(
                    "Mutate one inbox item. ``action`` picks the shape: "
                    "**dispose** applies a lifecycle disposition "
                    "(resolve / dismiss / approve / reject / answer / "
                    "accept / retry / acknowledge — pass via "
                    "``disposition``); **snooze** silences the item until "
                    "``until`` (≤ 30 days out); **reassign** hands it "
                    "to a different workspace member named by "
                    "``assignee_user_id``. All actions are admin-only and "
                    "audited. ``dry_run=true`` (dispose only) previews "
                    "the transition WITHOUT writing."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "inbox_item_id": {
                            "type": "string",
                            "description": "UUID of the inbox item.",
                        },
                        "action": {
                            "type": "string",
                            "enum": ["dispose", "snooze", "reassign"],
                            "description": (
                                "Which kind of mutation. Determines "
                                "which other fields are required."
                            ),
                        },
                        # action=dispose
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
                            "description": (
                                "Required when action='dispose'."
                            ),
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "Optional comment / answer text for "
                                "action='dispose' (max 4000 chars). "
                                "Required when disposition='answer'."
                            ),
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "action='dispose' only: validate + "
                                "summarise the would-be transition "
                                "WITHOUT writing."
                            ),
                        },
                        # action=snooze
                        "until": {
                            "type": "string",
                            "description": (
                                "ISO-8601 timestamp in the future. "
                                "Required when action='snooze'."
                            ),
                        },
                        # action=reassign
                        "assignee_user_id": {
                            "type": "string",
                            "description": (
                                "UUID of the workspace member to "
                                "reassign to. Required when "
                                "action='reassign'."
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": (
                                "Optional rationale for action='reassign' "
                                "(max 500 chars)."
                            ),
                        },
                    },
                    "required": ["inbox_item_id", "action"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="ticket_get",
                description=(
                    "Fetch one ticket's current state from the workspace's "
                    "bound tracker. Use when the user names an id (``ELS-99``) "
                    "or you've resolved one from ``list_tickets`` and need "
                    "the body / labels / state to act. Read-only; cheaper "
                    "than ``list_tickets`` when you already know which "
                    "ticket. Returns ``{ticket_ref, title, description, "
                    "url, state, labels, project_id?, comments?}``. Set "
                    "``include_comments=true`` when answering clarifications "
                    "or citing prior ``[Ship SDLC:role-…]`` verdicts."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ticket_ref": {
                            "type": "string",
                            "description": (
                                "Tracker-native identifier — Linear's "
                                "``ELS-99`` form or the issue's UUID. "
                                "Other adapters use their native "
                                "identifier (Jira ``ENG-42``, GitHub "
                                "Issues ``owner/repo#42``)."
                            ),
                        },
                        "include_comments": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Include the recent comment thread "
                                "(newest capped at 20, chronological)."
                            ),
                        },
                    },
                    "required": ["ticket_ref"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="dashboard_get",
                description=(
                    "One-call denormalised snapshot of the workspace's "
                    "current state — priorities (active / drafts / parked "
                    "buckets), inbox totals + by-type, recent activity "
                    "(top 5 mixed: pipeline runs, PRs, workflow runs), "
                    "open PR count, and 24h shipped count. Use to answer "
                    "'what's on my plate?' / 'what's the state of the "
                    "workspace?' without dialing five separate tools. "
                    "Read-only."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="audit_search",
                description=(
                    "Search the workspace audit log. Use to answer 'who "
                    "changed setting X?' / 'when did the priorities row "
                    "for project Y land?' / 'has anyone run the daily "
                    "play this week?'. Returns rows ``{action, "
                    "target_kind, target_id, actor_user_id, payload, "
                    "created_at}`` newest first. Read-only."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": (
                                "Optional exact-match filter on the "
                                "audit ``action`` (e.g. "
                                "``dashboard.priorities.reorder``, "
                                "``navigator.consult_specialist``). "
                                "Omit for any action."
                            ),
                        },
                        "target_kind": {
                            "type": "string",
                            "description": (
                                "Optional filter on ``target_kind`` "
                                "(e.g. ``workspace``, ``inbox_item``, "
                                "``agent_run``)."
                            ),
                        },
                        "target_id": {
                            "type": "string",
                            "description": (
                                "Optional filter on ``target_id`` — "
                                "the audited entity's id as a string."
                            ),
                        },
                        "since": {
                            "type": "string",
                            "description": (
                                "ISO-8601 timestamp lower bound "
                                "(``2026-05-01T00:00:00Z``). Omit to "
                                "scan the last 30 days by default."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 25,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="ticket_update",
                description=(
                    "Edit an existing ticket in the workspace's bound "
                    "tracker — title, body, labels, and/or workflow "
                    "state in one call. **Mutating; admin-only**. "
                    "Verify-before-mutate: describe the change and "
                    "wait for explicit OK unless the user gave a "
                    "direct command (\"rename to X\", \"close it\", "
                    "etc.). ``labels`` is a FULL replacement set — "
                    "the existing label list is overwritten. State "
                    "accepts a Ship FSM stage (``ba_requirements``, "
                    "``dev_implementation``, …) or a Linear workflow "
                    "state name (``Done``, ``In Progress``)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ticket_ref": {
                            "type": "string",
                            "description": (
                                "Tracker-native identifier "
                                "(``ELS-99`` for Linear)."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": "New title. Omit to leave as-is.",
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "New markdown description. Replaces "
                                "the previous body verbatim — Linear "
                                "keeps history server-side, so the "
                                "activity feed shows what changed."
                            ),
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "FULL replacement set of label names. "
                                "The existing label list is "
                                "overwritten. Unknown labels are "
                                "silently dropped."
                            ),
                        },
                        "state": {
                            "type": "string",
                            "description": (
                                "Optional state transition. Accepts "
                                "a Ship FSM stage or a Linear "
                                "workflow state name. Triggers a "
                                "label swap + state move."
                            ),
                        },
                    },
                    "required": ["ticket_ref"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="run_subagent",
                description=(
                    "Spawn a subagent. ``kind`` selects which:\n"
                    " * ``decomposition`` — kicks off the autonomous "
                    "BA → Architect → QA-Architect → Developer chain "
                    "on a Drafts-bucket planning anchor. **Strict "
                    "verify-before-mutate** — the chain runs without "
                    "further confirmations after this call. Always "
                    "OK with the user first.\n"
                    " * a specialist slug (``designer``, "
                    "``tech-architect``, ``qa-architect``, ``ba``, "
                    "``developer``) — hands a focused task to one "
                    "specialist subagent. The specialist runs as an "
                    "isolated agent loop with its own role prompt and "
                    "the same workspace tool surface (minus this "
                    "tool, to prevent recursion) and returns one "
                    "final report. The subagent has no memory of "
                    "this conversation — ``task`` + ``context_hint`` "
                    "is everything it sees.\n"
                    "Decomposition path uses ``project_native_id``; "
                    "specialist path uses ``task`` (+ optional "
                    "``context_hint``)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "decomposition",
                                "designer",
                                "tech-architect",
                                "qa-architect",
                                "ba",
                                "developer",
                            ],
                            "description": (
                                "What to spawn. ``decomposition`` is "
                                "the autonomous chain; any other slug "
                                "is a one-shot specialist consult."
                            ),
                        },
                        # decomposition path
                        "project_native_id": {
                            "type": "string",
                            "description": (
                                "Tracker-native project id. Required "
                                "when kind='decomposition'. Project "
                                "must be on the dashboard in the "
                                "Drafts bucket "
                                "(``priority_state='planning'``); "
                                "the call refuses for ``active`` / "
                                "``parked`` states."
                            ),
                        },
                        # specialist path
                        "task": {
                            "type": "string",
                            "description": (
                                "Required for specialist consults. "
                                "Concrete task / question — state "
                                "scope, constraints, what 'done' "
                                "looks like. The subagent won't have "
                                "your conversation history."
                            ),
                            "minLength": 8,
                            "maxLength": 8000,
                        },
                        "context_hint": {
                            "type": "string",
                            "description": (
                                "Optional extra context for "
                                "specialist consults — file paths, "
                                "ticket ids, decisions already made "
                                "— forwarded verbatim to the subagent."
                            ),
                            "maxLength": 12000,
                        },
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="run_workflow",
                description=(
                    "Fire a deterministic bounded workflow (thesis 8): "
                    "a named multi-agent pipeline from the workspace's "
                    "workflow registry (e.g. ``pr-review``, "
                    "``codebase-audit``). The chat stays LOCK-FREE — "
                    "this tool only QUEUES the run and returns its id; "
                    "the control plane (cap / cascade / leases) governs "
                    "every spawn afterwards. Admin-only, "
                    "internal/dogfood at launch."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "workflow_name": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                            "description": (
                                "Registered workflow spec name. Unknown "
                                "names return the available list."
                            ),
                        },
                        "inputs": {
                            "type": "object",
                            "description": (
                                "Input values declared by the spec "
                                "(e.g. {\"pr_url\": …})."
                            ),
                        },
                    },
                    "required": ["workflow_name"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="web_fetch",
                description=(
                    "Fetch one URL via Firecrawl and return its "
                    "content as markdown (default), HTML, or plain "
                    "text. Works on JS-rendered pages and PDFs out "
                    "of the box. Use when you have a specific URL "
                    "(from the user, from ``web_search``, from a "
                    "ticket / doc) and need to read the body. Don't "
                    "spam — one fetch per URL per session is usually "
                    "enough."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": (
                                "Fully-qualified URL (http:// or "
                                "https://). Firecrawl handles "
                                "redirects + JS rendering."
                            ),
                            "minLength": 4,
                            "maxLength": 2048,
                        },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "html", "text"],
                            "default": "markdown",
                            "description": (
                                "Output format. ``markdown`` is the "
                                "token-efficient default; switch to "
                                "``html`` only when structure matters "
                                "and to ``text`` to strip everything."
                            ),
                        },
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="config_help",
                description=(
                    "Discover this workspace's configurable settings. "
                    "Pass ``scope`` (e.g. ``agent.provider``, "
                    "``agent.default_profile``, ``catalog.sources``) "
                    "to get its JSONSchema + current value. Omit "
                    "``scope`` to enumerate every available scope "
                    "with a one-line description. Read-only; use "
                    "``config_put`` to mutate."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": (
                                "Dotted scope slug. Omit to list all "
                                "scopes."
                            ),
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="config_put",
                description=(
                    "Change one workspace setting. Pass ``scope`` "
                    "(slug from ``config_help``) and ``value`` "
                    "matching that scope's JSONSchema. Validated + "
                    "audited under the scope's canonical action "
                    "name (e.g. ``workspace.agent_provider.set``). "
                    "**Mutating; admin-only**. Always describe the "
                    "change and wait for OK before calling."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string"},
                        "value": {
                            "description": (
                                "New value — shape depends on the "
                                "scope. Call ``config_help(scope)`` "
                                "first to see the JSONSchema."
                            )
                        },
                    },
                    "required": ["scope", "value"],
                    "additionalProperties": False,
                },
            ),
            # E17/ELS-128 — Navigator memory recall surface.
            ToolSpec(
                name="recall",
                description=(
                    "Semantic search across the PO's durable facts "
                    "(per-message extractions Ship has accumulated). "
                    "Call this when the conversation drifts to a "
                    "topic NOT covered by the ``{{MEMORY_CONTEXT}}`` "
                    "system message prefetched at session start. "
                    "Do NOT ask the operator something memory likely "
                    "already answers — use this first. Returns "
                    "``[{id, fact_text, project_native_id, "
                    "source_thread_id, captured_at}]`` ranked by "
                    "similarity. ``id`` is what ``recall_context`` "
                    "takes."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Natural-language search query "
                                "(short — 3-10 words)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 25,
                            "default": 10,
                        },
                        "project_native_id": {
                            "type": "string",
                            "description": (
                                "Optional project id to boost. "
                                "Omit for general-purpose recall."
                            ),
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="recall_context",
                description=(
                    "Pull ±5 surrounding chat messages around the "
                    "source of a specific fact. Use sparingly — the "
                    "bare fact text is usually enough. Call this "
                    "when nuance matters (e.g. you need to know what "
                    "the operator was responding to when they said "
                    "X). Returns the source message body + up to 5 "
                    "messages before and after it from the same "
                    "thread."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "fact_id": {
                            "type": "string",
                            "description": (
                                "``id`` from a ``recall`` result "
                                "(or from the ``{{MEMORY_CONTEXT}}`` "
                                "block in the system prompt)."
                            ),
                        },
                    },
                    "required": ["fact_id"],
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
            "repo_file_get": self._tool_repo_file_get,
            "repo_tree": self._tool_repo_tree,
            "repo_symbols": self._tool_repo_symbols,
            "ticket_create": self._tool_ticket_create,
            "ticket_list": self._tool_ticket_list,
            "project_list": self._tool_project_list,
            "project_get": self._tool_project_get,
            "project_create": self._tool_project_create,
            "project_find_or_create": self._tool_project_find_or_create,
            "inbox_create": self._tool_inbox_create,
            "propose_mass_plan": self._tool_propose_mass_plan,
            "project_update": self._tool_project_update,
            # Legacy dispatch keys for tests that call the sub-ops
            # directly. Not in specs() — invisible to the LLM.
            "project_description_append": self._tool_project_description_append,
            "pr_get": self._tool_pr_get,
            "pr_list": self._tool_pr_list,
            "members_list": self._tool_members_list,
            "knowledge_bucket_get": self._tool_knowledge_bucket_get,
            # Phase 6 — new IA tools (Inbox, Plays, Runs, Coverage, Intel)
            "inbox_list": self._tool_inbox_list,
            "inbox_get": self._tool_inbox_get,
            "runs_list": self._tool_runs_list,
            "runs_get": self._tool_runs_get,
            "knowledge_search": self._tool_knowledge_search_v2,
            # Legacy dispatch aliases — kept invisible to the LLM (no
            # ToolSpec) but still callable via ``box.invoke(...)`` from
            # ``test_agent_tools_bucket_cutover``. Production callers
            # use ``knowledge_search`` and ``get_knowledge_bucket``.
            "search_buckets": self._tool_search_buckets,
            "search_workspace_kb": self._tool_search_workspace_kb,
            "list_buckets": self._tool_list_buckets,
            # Phase 6 Wave B — mutating tools (admin-gated, audited)
            "inbox_update": self._tool_inbox_update,
            # Legacy dispatch keys — kept invisible to the LLM (no
            # ToolSpec entry) but callable directly via ``box.invoke``
            # so existing tests continue to work without retesting the
            # admin-gate / dispatcher / audit-log behaviour through
            # the polymorphic ``inbox_update`` wrapper.
            "inbox_dispose": self._tool_inbox_dispose,
            "inbox_snooze": self._tool_inbox_snooze,
            "inbox_reassign": self._tool_inbox_reassign,
            # ELS-62 — on-demand repo KB indexing surface
            "ticket_get": self._tool_ticket_get,
            "dashboard_get": self._tool_dashboard_get,
            "audit_search": self._tool_audit_search,
            "ticket_update": self._tool_ticket_update,
            "project_priority_set": self._tool_project_priority_set,
            "run_subagent": self._tool_run_subagent,
            "run_workflow": self._tool_run_workflow,
            "config_help": self._tool_config_help,
            "config_put": self._tool_config_put,
            "web_fetch": self._tool_web_fetch,
            # Legacy dispatch keys for direct test calls. Not in
            # specs() — invisible to the LLM.
            "decomposition_start": self._tool_decomposition_start,
            "specialist_consult": self._tool_specialist_consult,
            # E17/ELS-128 — Navigator memory recall surface
            "recall": self._tool_recall,
            "recall_context": self._tool_recall_context,
        }

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _tool_search_repo_kb(self, args: dict[str, Any]) -> str:
        query = _require_str(args, "query")
        limit = _clamp_int(args.get("limit"), default=5, low=1, high=_MAX_KB_RESULTS)
        include_full = bool(args.get("include_full_content", False))
        bucket_slug = args.get("bucket_slug")
        if bucket_slug is not None and not isinstance(bucket_slug, str):
            return _json_result({
                "error": "invalid_bucket_slug",
                "message": "bucket_slug must be a string when provided",
            })
        repo_id_raw = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_raw:
            try:
                repo_id = uuid.UUID(str(repo_id_raw))
            except ValueError as exc:
                raise ToolInvocationError(f"invalid repo_id: {repo_id_raw!r}") from exc
        elif self._active_repo_id is not None:
            repo_id = self._active_repo_id

        from backend.app.services.knowledge_search import (
            EmbeddingsUnavailable,
            search_workspace_knowledge,
        )

        try:
            hits = await search_workspace_knowledge(
                self._session,
                workspace_id=self._workspace_id,
                query=query,
                repo_id=repo_id,
                bucket_slug=bucket_slug.strip() if isinstance(bucket_slug, str) and bucket_slug.strip() else None,
                limit=limit,
                settings=self._settings,
            )
        except EmbeddingsUnavailable as exc:
            return _json_result(
                {"error": "embeddings_unavailable", "message": str(exc)}
            )
        repo_hits = [hit for hit in hits if repo_id is None or hit.repo_id == repo_id]
        if not repo_hits:
            return _json_result({"results": [], "note": "no repository knowledge indexed"})

        snippet_cap = _MAX_KB_FULL_CHUNK if include_full else 800
        results = []
        for hit in repo_hits[:limit]:
            entry: dict[str, Any] = {
                "repo_id": str(hit.repo_id) if hit.repo_id is not None else None,
                "bucket_slug": hit.bucket_slug,
                "title": hit.title,
                "snippet": _truncate(hit.snippet, snippet_cap),
                "similarity": hit.score,
            }
            if include_full:
                entry["content"] = _truncate(hit.snippet, _MAX_KB_FULL_CHUNK)
            results.append(entry)
        return _json_result({"results": results})



    async def _tool_repo_file_get(self, args: dict[str, Any]) -> str:
        repo_id = _parse_uuid(args, "repo_id")
        path = _require_str(args, "path")
        ref_sha = args.get("ref_sha")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        start_val = _optional_positive_int(start_line, "start_line")
        end_val = _optional_positive_int(end_line, "end_line")
        if start_val is not None and end_val is not None and end_val < start_val:
            raise ToolInvocationError("end_line must be >= start_line")

        gateway, ref, _ = await self._resolve_code_host_gateway(repo_id)
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

    async def _tool_repo_tree(self, args: dict[str, Any]) -> str:
        import fnmatch

        repo_id = _parse_uuid(args, "repo_id")
        path_prefix = args.get("path_prefix")
        glob_pat = args.get("glob")
        directories_only = bool(args.get("directories_only", False))

        gateway, ref, default_branch = await self._resolve_code_host_gateway(
            repo_id
        )
        files = await gateway.list_files(ref, ref_sha=default_branch)

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
                    "repo_id": str(repo_id),
                    "full_name": ref.full_name,
                    "default_branch": default_branch,
                    "total_files_before_filter": total_before_filter,
                    "truncated": truncated,
                    "directories": seen[:_MAX_CODE_MAP_ENTRIES],
                }
            )

        truncated = len(files) > _MAX_CODE_MAP_ENTRIES
        return _json_result(
            {
                "repo_id": str(repo_id),
                "full_name": ref.full_name,
                "default_branch": default_branch,
                "total_files_before_filter": total_before_filter,
                "matched": len(files),
                "truncated": truncated,
                "files": files[:_MAX_CODE_MAP_ENTRIES],
            }
        )

    async def _tool_repo_symbols(self, args: dict[str, Any]) -> str:
        """On-demand symbol extraction (ELS-72).

        Resolves a symbol name → ``[{file, symbol, kind, line, signature}]``
        by parsing the requested files with tree-sitter on the fly. The
        agent points this tool at a known file (``paths=["backend/app/main.py"]``)
        OR a free-text ``query`` — the latter pre-filters with GitHub
        code search to find candidate files, then parses only those.

        No preindex, no DB writes — same fetch path
        ``_tool_repo_file_get`` already uses. Languages in v1: Python,
        TypeScript / TSX, Go.
        """
        from backend.app.services.agent.symbol_parser import (
            LANGUAGE_BY_EXTENSION,
            extract_symbols,
            language_for_path,
        )

        repo_id = _parse_uuid(args, "repo_id")
        query = args.get("query")
        if query is not None and not isinstance(query, str):
            raise ToolInvocationError("query must be a string when provided")
        query_str = query.strip() if isinstance(query, str) else ""
        paths_arg = args.get("paths")
        paths: list[str] = []
        if isinstance(paths_arg, list):
            for raw in paths_arg:
                if isinstance(raw, str) and raw.strip():
                    paths.append(raw.strip())
        kinds_arg = args.get("kinds")
        kinds: set[str] | None = None
        if isinstance(kinds_arg, list):
            picked = {
                str(k).strip().lower() for k in kinds_arg if isinstance(k, str)
            }
            kinds = picked or None
        # Hard cap on rows so a giant file doesn't blow the response
        # budget. Default 50 keeps the agent's context tight; the tool
        # caller can ask for up to 200 explicitly.
        limit = _clamp_int(args.get("limit"), default=50, low=1, high=200)
        # Hard cap on files we'll parse per call. Tree-sitter is fast
        # but each file is one GitHub fetch, so per-call latency scales
        # linearly. 25 files is enough for "find me the Foo class"
        # without becoming a denial-of-service against ourselves.
        max_files = _clamp_int(
            args.get("max_files"), default=25, low=1, high=50
        )

        if not paths and not query_str:
            raise ToolInvocationError(
                "repo_symbols needs either ``paths`` (list of repo-relative "
                "files to parse) or ``query`` (symbol name; we'll find the "
                "candidate files via code search before parsing)"
            )

        gateway, ref, _ = await self._resolve_code_host_gateway(repo_id)

        # Build the list of files to parse. Explicit ``paths`` win;
        # otherwise GitHub code search narrows down by ``query``.
        selected: list[str] = []
        skipped_unsupported: list[str] = []
        if paths:
            for p in paths:
                if language_for_path(p) is None:
                    skipped_unsupported.append(p)
                    continue
                selected.append(p)
        elif query_str:
            # Pull candidates from GitHub code search, then filter to the
            # extensions we actually parse. Code search returns up to 100
            # rows; we cap at ``max_files`` after filtering.
            try:
                candidates = await gateway.search_code(
                    ref, query=query_str, limit=max_files * 4
                )
            except Exception as exc:  # noqa: BLE001
                raise ToolInvocationError(
                    f"code search failed: {exc}"
                ) from exc
            for hit in candidates:
                path = (
                    hit.get("path") if isinstance(hit, dict) else None
                ) or ""
                if not path or language_for_path(path) is None:
                    continue
                if path not in selected:
                    selected.append(path)
                if len(selected) >= max_files:
                    break

        selected = selected[:max_files]

        rows: list[dict[str, Any]] = []
        files_parsed: list[str] = []
        files_failed: list[dict[str, Any]] = []
        for path in selected:
            try:
                blob = await gateway.get_blob(ref, path=path)
            except FileNotFoundError:
                files_failed.append({"path": path, "reason": "not_found"})
                continue
            except IsADirectoryError:
                files_failed.append({"path": path, "reason": "directory"})
                continue
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "repo_symbols: fetch failed path=%s err=%s", path, exc
                )
                files_failed.append({"path": path, "reason": "fetch_error"})
                continue
            if blob.encoding != "utf-8":
                files_failed.append({"path": path, "reason": "binary"})
                continue
            files_parsed.append(path)
            for sym in extract_symbols(file=path, content=blob.content):
                if kinds and sym.kind not in kinds:
                    continue
                if query_str and query_str.lower() not in sym.symbol.lower():
                    # When the agent asked by name, filter the per-file
                    # results down to ones whose symbol matches —
                    # otherwise a "find Foo" query that picks 25 files
                    # via code-search returns *every* symbol in those
                    # files, which is the wrong shape.
                    continue
                rows.append(sym.as_dict())
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break

        truncated = len(rows) >= limit
        return _json_result(
            {
                "repo_id": str(repo_id),
                "full_name": ref.full_name,
                "query": query_str or None,
                "kinds": sorted(kinds) if kinds else None,
                "supported_extensions": sorted(LANGUAGE_BY_EXTENSION.keys()),
                "files_requested": len(selected),
                "files_parsed": len(files_parsed),
                "matched": len(rows),
                "truncated": truncated,
                "skipped_unsupported": skipped_unsupported or None,
                "files_failed": files_failed or None,
                "symbols": rows,
            }
        )

    async def _tool_ticket_create(self, args: dict[str, Any]) -> str:
        title = _require_str(args, "title")
        body = _require_str(args, "body")
        labels_raw = args.get("labels") or []
        labels = [str(l) for l in labels_raw if isinstance(l, str)] or None
        project_hint = args.get("project_hint")
        project_id = args.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            raise ToolInvocationError("project_id must be a string")
        ticket_type_raw = args.get("type")
        if ticket_type_raw is not None and ticket_type_raw not in (
            "bug",
            "feature",
            "task",
        ):
            raise ToolInvocationError(
                "type must be one of 'bug', 'feature', 'task'"
            )
        ticket_type = ticket_type_raw
        tracker_kind = args.get("tracker")

        tracker = await self._resolve_tracker(tracker_kind, project_hint)
        try:
            created: CreatedTicket = await tracker.create_ticket(
                title=title,
                body=body,
                labels=labels,
                project_hint=project_hint,
                project_id=project_id,
                ticket_type=ticket_type,
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

    async def _tool_project_list(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(args.get("limit"), default=20, low=1, high=100)
        state = args.get("state")
        if state is not None and not isinstance(state, str):
            raise ToolInvocationError("state must be a string")
        query = args.get("query")
        if query is not None and not isinstance(query, str):
            raise ToolInvocationError("query must be a string")

        tracker = await self._resolve_tracker(None, None)
        try:
            projects = await tracker.list_projects(
                limit=limit, state=state, query=query
            )
        except NotImplementedError as exc:
            raise ToolInvocationError(str(exc)) from exc
        except ValueError as exc:
            raise ToolInvocationError(str(exc)) from exc

        return _json_result({"projects": projects})

    async def _tool_project_get(self, args: dict[str, Any]) -> str:
        project_id = _require_str(args, "project_id")
        issues_limit = _clamp_int(
            args.get("issues_limit"), default=25, low=1, high=50
        )

        tracker = await self._resolve_tracker(None, None)
        try:
            project = await tracker.get_project(
                project_id, issues_limit=issues_limit
            )
        except NotImplementedError as exc:
            raise ToolInvocationError(str(exc)) from exc
        except ValueError as exc:
            raise ToolInvocationError(str(exc)) from exc

        return _json_result(project)

    async def _tool_project_create(self, args: dict[str, Any]) -> str:
        name = _require_str(args, "name")
        body = _require_str(args, "body")
        description = args.get("description")
        if description is not None and not isinstance(description, str):
            raise ToolInvocationError("description must be a string")

        tracker = await self._resolve_tracker(None, None)
        try:
            project = await tracker.create_project(
                name=name, body=body, description=description
            )
        except NotImplementedError as exc:
            raise ToolInvocationError(str(exc)) from exc
        except ValueError as exc:
            raise ToolInvocationError(str(exc)) from exc

        # Project-first delivery side-effects: a freshly-created project
        # has to land on the dashboard immediately (in the Drafts bucket
        # — DB enum value ``planning``) AND carry exactly one anchor
        # issue tagged ``planning:anchor``. Without the priorities row,
        # the project is invisible to the operator until they manually
        # drag it; without the anchor, the decomposition FSM later
        # has nothing to attach to. Both are best-effort: a tracker
        # that can't model anchors raises NotImplementedError and we
        # skip cleanly. A failure on either side is non-fatal — the
        # project itself was created successfully and we don't want to
        # roll that back over plumbing.
        project_native_id = str(project.get("id") or "")
        anchor_payload: dict[str, Any] | None = None
        if project_native_id:
            anchor_payload = await self._ensure_planning_anchor(
                tracker,
                project_id=project_native_id,
                project_name=name,
            )
            await self._ensure_drafts_priorities_row(
                project_native_id=project_native_id
            )

        result = dict(project)
        if anchor_payload is not None:
            result["anchor"] = anchor_payload
            # Surface the dashboard hint so the agent can phrase its
            # confirmation reliably ("Created · drafted in Drafts ·
            # ready for hand-off").
            result["dashboard_bucket"] = "drafts"

        # E17/ELS-129 — escape drafting mode on success.
        # The thread's ``intent='shape_project'`` was the reason
        # Navigator was biased toward shaping a brief. Now that the
        # brief has graduated into a real Linear project, leaving the
        # intent sticky makes the agent start drafting a SECOND
        # project on the next user turn — the symptom that's been
        # painful for the PO. Reset to ``None`` so the system prompt
        # drops the drafting-mode block on the next ``assemble_messages``.
        # Plus: write a mem0 fact tagged with the just-created
        # ``project_native_id`` so future chats about this project
        # surface the drafting context via the retrieval boost.
        if (
            project_native_id
            and self._thread_id is not None
            and self._thread_intent == "shape_project"
        ):
            try:
                from backend.app.db.models.agent_surface import ChatThread

                thread_row = await self._session.get(
                    ChatThread, self._thread_id
                )
                if thread_row is not None and thread_row.intent == "shape_project":
                    thread_row.intent = None
                    await self._session.flush()
                    # Track in-memory copy too so any later tool call in
                    # the same turn sees the reset.
                    self._thread_intent = None
            except Exception:  # noqa: BLE001 — never fail the create over the reset
                logger.exception(
                    "shape_project intent reset failed for thread %s",
                    self._thread_id,
                )

            try:
                from backend.app.services.agent import memory as navigator_memory

                brief_excerpt = body[:500] if body else ""
                fact_body = (
                    f"Drafted project '{name}' (linear id {project_native_id}). "
                    f"Brief excerpt: {brief_excerpt}"
                ).strip()
                await navigator_memory.add(
                    self._session,
                    workspace_id=self._workspace_id,
                    owner_user_id=self._user_id,
                    message=fact_body,
                    source_thread_id=self._thread_id,
                    source_message_id=None,
                    source_message_position=None,
                    project_native_id=project_native_id,
                    intent_at_capture="shape_project",
                    settings=self._settings,
                )
            except Exception:  # noqa: BLE001 — never fail create over a mem0 hiccup
                logger.exception(
                    "shape_project brief mem0.add failed for project %s",
                    project_native_id,
                )

        return _json_result(result)

    async def _ensure_planning_anchor(
        self,
        tracker: TrackerGateway,
        *,
        project_id: str,
        project_name: str,
    ) -> dict[str, Any] | None:
        """Idempotent: return an existing anchor or create one.

        Returns ``None`` when the tracker doesn't model anchors
        (Notion, future GitHub Issues), so the caller can skip the
        confirmation block in the tool result rather than lying to
        the agent about an artefact that wasn't created.
        """
        try:
            existing = await tracker.get_planning_anchor(project_id)
        except NotImplementedError:
            return None
        except Exception as exc:  # noqa: BLE001 — don't fail create over a probe
            logger.warning(
                "planning anchor probe failed for project=%s err=%s",
                project_id,
                exc,
            )
            existing = None
        if existing:
            return existing
        anchor_body = (
            f"Decomposition anchor for **{project_name}**.\n\n"
            "This issue tracks the planning pipeline (BA → Architect → "
            "QA-Architect → QA + Developer). Stage transitions on this "
            "anchor drive what specialists run; the project body grows "
            "section-by-section as each stage emits its artefact."
        )
        try:
            return await tracker.create_planning_anchor(
                project_id,
                title=f"Anchor: {project_name}",
                body=anchor_body,
                labels=None,
            )
        except NotImplementedError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "planning anchor creation failed for project=%s err=%s",
                project_id,
                exc,
            )
            return None

    async def _ensure_drafts_priorities_row(
        self, *, project_native_id: str
    ) -> None:
        """Insert (or no-op) a Drafts-bucket priorities row.

        Idempotent on (workspace_id, project_native_id) — a re-run of
        ``create_project`` for the same Linear project doesn't double-
        insert. Ordinal goes to MAX+1 across the whole workspace so
        the row sorts after everything currently saved; the operator
        can drag it up if the project is high-priority.

        When the toolbox knows the thread that drove this call (i.e.
        the Navigator was in drafting mode) we stamp it onto the row
        as ``originating_thread_id`` so the dashboard can render a
        **Continue shaping** link straight back to that conversation.
        """
        existing = await self._session.scalar(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == self._workspace_id,
                WorkspaceProjectPriority.project_native_id
                == project_native_id,
            )
        )
        if existing is not None:
            # Backfill the thread link if the row was created earlier
            # without one (e.g. the agent created the project in a
            # default thread, then later re-created in a drafting
            # thread). Don't overwrite an existing link — first thread
            # wins so the deep-link doesn't drift.
            if existing.originating_thread_id is None and self._thread_id is not None:
                existing.originating_thread_id = self._thread_id
                await self._session.flush()
            return
        max_ord = await self._session.scalar(
            select(WorkspaceProjectPriority.ordinal)
            .where(
                WorkspaceProjectPriority.workspace_id == self._workspace_id
            )
            .order_by(WorkspaceProjectPriority.ordinal.desc())
            .limit(1)
        )
        next_ord = 0 if max_ord is None else int(max_ord) + 1
        self._session.add(
            WorkspaceProjectPriority(
                workspace_id=self._workspace_id,
                project_native_id=project_native_id,
                ordinal=next_ord,
                state="planning",
                originating_thread_id=self._thread_id,
            )
        )
        await self._session.flush()

    async def _tool_project_find_or_create(
        self, args: dict[str, Any]
    ) -> str:
        from backend.app.services.projects_lookup import (
            ProjectsLookupError,
            find_or_create_project_by_name,
        )

        name = _require_str(args, "name")
        body = _require_str(args, "body")
        description = args.get("description")
        if description is not None and not isinstance(description, str):
            raise ToolInvocationError("description must be a string")

        tracker = await self._resolve_tracker(None, None)
        try:
            outcome = await find_or_create_project_by_name(
                session=self._session,
                settings=self._settings,
                workspace_id=self._workspace_id,
                name=name,
                body=body,
                description=description,
                originating_thread_id=self._thread_id,
                tracker=tracker,
            )
        except ProjectsLookupError as exc:
            raise ToolInvocationError(str(exc)) from exc

        return _json_result({**outcome.project, "created": outcome.created})

    async def _tool_inbox_create(self, args: dict[str, Any]) -> str:
        type_ = _require_str(args, "type")
        if type_ not in {"report", "improvement", "approval", "exception"}:
            raise ToolInvocationError(
                "type must be one of report | improvement | approval | exception"
            )
        title = _require_str(args, "title")
        body = _require_str(args, "body")
        summary = args.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ToolInvocationError("summary must be a string")

        title_clean = title.strip()
        if not title_clean:
            raise ToolInvocationError("title must not be blank")
        body_clean = body.strip()
        if not body_clean:
            raise ToolInvocationError("body must not be blank")

        from backend.app.services.inbox.headline import derive_headline
        from backend.app.services.inbox.intake import (
            _SUMMARY_MAX_LEN,
            _TITLE_MAX_LEN,
            _truncate,
        )

        if summary is None:
            summary_text = body_clean[:200]
        else:
            summary_text = summary

        title_stored = _truncate(title_clean, _TITLE_MAX_LEN)
        summary_stored = _truncate(summary_text, _SUMMARY_MAX_LEN) or None
        headline_raw = args.get("headline")
        headline_arg = headline_raw if isinstance(headline_raw, str) else None

        if type_ == "exception":
            from backend.app.core.sentry import record_inbox_exception_breadcrumb

            record_inbox_exception_breadcrumb(
                source="agent_tool.inbox_create",
                title=title_clean,
                summary=summary_text,
            )
            return _json_result(
                {
                    "type": type_,
                    "status": "breadcrumb_only",
                    "title": title_clean,
                }
            )

        item = InboxItem(
            workspace_id=self._workspace_id,
            repo_id=self._active_repo_id,
            type=type_,
            title=title_stored,
            headline=derive_headline(
                headline=headline_arg,
                summary=summary_stored,
                title=title_stored,
            ),
            summary=summary_stored,
            payload={"body": body_clean, "source": "agent_tool"},
            status="new",
        )
        self._session.add(item)
        await self._session.flush()
        self._session.add(
            InboxItemEvent(
                item_id=item.id,
                actor_user_id=None,
                actor_kind="agent",
                action="created",
                payload={"via": "inbox_create"},
            )
        )
        await self._session.flush()

        return _json_result(
            {
                "id": str(item.id),
                "type": item.type,
                "status": item.status,
                "title": item.title,
            }
        )

    async def _tool_propose_mass_plan(self, args: dict[str, Any]) -> str:
        """ELS-171 / M4 — extract a mass-planning proposal from an
        attached PDF and persist it as a draft.

        Resolves the attachment by id, reads its bytes via the
        configured storage backend, calls the vision extractor
        (M1), persists the result as a ``planning_proposal`` row
        the Console renders as a preview card.

        Returns a JSON blob the agent uses to compose its reply.
        Crucially carries ``proposal_id`` so the Console knows
        which row to load.
        """
        from sqlalchemy import select as _select

        from backend.app.db.models.agent_surface import ChatAttachment
        from backend.app.db.models.planning_proposals import PlanningProposal
        from backend.app.services.attachments.storage import (
            get_default_storage,
        )
        from backend.app.services.planning.requirements_extraction import (
            extract_proposal_from_pdf,
        )

        attachment_id = _require_str(args, "attachment_id")
        try:
            att_uuid = uuid.UUID(attachment_id)
        except ValueError as exc:
            raise ToolInvocationError(
                f"attachment_id must be a UUID, got {attachment_id!r}"
            ) from exc

        att = (
            await self._session.execute(
                _select(ChatAttachment).where(
                    ChatAttachment.id == att_uuid,
                    ChatAttachment.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if att is None:
            raise ToolInvocationError(
                f"attachment {attachment_id!r} not found in this workspace"
            )
        if att.kind != "pdf":
            raise ToolInvocationError(
                f"attachment {attachment_id!r} is kind={att.kind!r}; "
                "propose_mass_plan needs a PDF"
            )

        api_key = (self._settings.anthropic_api_key or "").strip()
        if not api_key:
            raise ToolInvocationError(
                "Workspace has no ANTHROPIC_API_KEY configured; mass-plan "
                "extraction needs vision access."
            )

        try:
            pdf_bytes = await get_default_storage().read(att.storage_path)
        except Exception as exc:  # noqa: BLE001 — surface to operator
            raise ToolInvocationError(
                f"Could not read PDF bytes: {exc}"
            ) from exc

        try:
            result = await extract_proposal_from_pdf(
                pdf_bytes, api_key=api_key
            )
        except ValueError as exc:
            raise ToolInvocationError(str(exc)) from exc

        # Persist as a draft so the Console preview pane can load it.
        proposal_row = PlanningProposal(
            workspace_id=self._workspace_id,
            thread_id=self._thread_id,
            source_kind="pdf",
            source_ref=str(att.id),
            payload=result.proposal.model_dump(),
            created_by=self._user_id,
        )
        self._session.add(proposal_row)
        await self._session.flush()

        # M8 — cost audit row.
        from backend.app.db.models.tenancy import AuditLog as _AL

        self._session.add(
            _AL(
                workspace_id=self._workspace_id,
                actor_user_id=self._user_id,
                actor_token_id=None,
                action="mass_planning.extraction.cost",
                target_kind="planning_proposal",
                target_id=str(proposal_row.id),
                payload={
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "file_bytes": result.file_bytes,
                    "duration_ms": result.duration_ms,
                    "model_id": result.model_id,
                    "fallback_used": result.fallback_used,
                    "epic_count": len(result.proposal.epics),
                },
            )
        )
        await self._session.flush()

        epic_count = len(result.proposal.epics)
        dep_count = sum(len(e.depends_on) for e in result.proposal.epics)
        return _json_result(
            {
                "proposal_id": str(proposal_row.id),
                "project_name": result.proposal.project.name,
                "epic_count": epic_count,
                "dep_count": dep_count,
                "summary": (
                    f"{epic_count} epics with {dep_count} blocks-deps "
                    "drafted from the PDF. Operator should open the "
                    "preview card to review + commit."
                ),
            }
        )

    async def _tool_project_update(self, args: dict[str, Any]) -> str:
        """Polymorphic project mutation. Two sub-ops can fire in the
        same call: ``body_append`` (delegates to the existing append
        path) and ``priority_state`` (delegates to the priority-set
        path). Each underlying handler keeps its own admin gate +
        audit-log action name so historical queries stay stable.

        ``project_id`` is the same identifier the LLM gets from
        ``project_list`` / ``project_get`` — for Linear that's the
        project UUID, for Jira the project key. Both underlying
        handlers happen to accept the same value under different
        parameter names (``project_id`` vs ``project_native_id``);
        we map it here once."""
        project_id = args.get("project_id")
        if not project_id or not isinstance(project_id, str):
            return _json_result(
                {
                    "error": "missing_project_id",
                    "message": "project_id is required",
                }
            )
        body_append = args.get("body_append")
        priority_state = args.get("priority_state")
        if body_append is None and priority_state is None:
            return _json_result(
                {
                    "error": "nothing_to_update",
                    "message": (
                        "set at least one of body_append, "
                        "priority_state"
                    ),
                }
            )
        results: dict[str, Any] = {}
        if body_append is not None:
            results["body_append"] = await self._tool_project_description_append(
                {"project_id": project_id, "body": body_append}
            )
        if priority_state is not None:
            results["priority_state"] = await self._tool_project_priority_set(
                {
                    "project_native_id": project_id,
                    "state": priority_state,
                }
            )
        return _json_result(results)

    async def _tool_project_description_append(self, args: dict[str, Any]) -> str:
        project_id = _require_str(args, "project_id")
        body = _require_str(args, "body")

        tracker = await self._resolve_tracker(None, None)
        try:
            await tracker.append_project_description(project_id, body=body)
        except NotImplementedError as exc:
            raise ToolInvocationError(str(exc)) from exc
        except ValueError as exc:
            raise ToolInvocationError(str(exc)) from exc

        return _json_result({"ok": True, "project_id": project_id})



    async def _tool_ticket_list(self, args: dict[str, Any]) -> str:
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

    async def _tool_pr_get(self, args: dict[str, Any]) -> str:
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

        gateway, repo_ref, _ = await self._resolve_code_host_gateway(repo_id)
        ref = PullRequestRef(repo=repo_ref, number=number)
        try:
            raw = await gateway.get_pull_request(ref)
        except Exception as exc:  # noqa: BLE001 — GitHub HTTP errors
            raise ToolInvocationError(
                f"failed to fetch PR #{number} in {repo_ref.full_name}: {exc}"
            ) from exc

        created_at = raw.get("created_at")
        updated_at = raw.get("updated_at")
        closed_at = raw.get("closed_at")
        merged_at = raw.get("merged_at")

        summary: dict[str, Any] = {
            "repo": repo_ref.full_name,
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


    async def _tool_pr_list(self, args: dict[str, Any]) -> str:
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





    async def _tool_members_list(self, args: dict[str, Any]) -> str:
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

    async def _tool_knowledge_bucket_get(self, args: dict[str, Any]) -> str:
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
                    # ``article_id`` is the load-bearing handle for any
                    # follow-up tool call (e.g. ``archive_bucket_article``).
                    # Pre-fix the result shape was missing it, so the
                    # agent had to round-trip through
                    # ``get_knowledge_bucket`` just to recover an id it
                    # had already loaded — produced a visible
                    # search → ship-choice → not_found → re-list dance
                    # the operator caught in 2026-05-02 dogfood.
                    "article_id": str(article.id),
                    "article_slug": article.slug,
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

    async def _tool_list_buckets(self, args: dict[str, Any]) -> str:
        include_archived = bool(args.get("include_archived", False))
        limit = _clamp_int(
            args.get("limit"), default=25, low=1, high=_MAX_BUCKETS_LISTED
        )
        stmt = (
            select(KnowledgeBucket)
            .where(KnowledgeBucket.workspace_id == self._workspace_id)
            .where(KnowledgeBucket.scope_kind == BucketScope.WORKSPACE)
            .where(KnowledgeBucket.source_kind != BucketSource.REPO_FILES)
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

        try:
            hits = await search_workspace_knowledge(
                self._session,
                workspace_id=self._workspace_id,
                query=query,
                repo_id=repo_id,
                bucket_slug=(
                    bucket_slug.strip()
                    if isinstance(bucket_slug, str) and bucket_slug.strip()
                    else None
                ),
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

        results: list[dict[str, Any]] = []
        for hit in hits:
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
            try:
                intel_hits = await search_workspace_knowledge(
                    self._session,
                    workspace_id=self._workspace_id,
                    query=query,
                    repo_id=repo_id,
                    bucket_slug="repository-context",
                    limit=3,
                    settings=self._settings,
                )
            except EmbeddingsUnavailable:
                intel_hits = []
            for hit in reversed(intel_hits):
                results.insert(
                    0,
                    {
                        "source": hit.source,
                        "repo_id": str(hit.repo_id) if hit.repo_id is not None else None,
                        "bucket_slug": hit.bucket_slug,
                        "source_path": hit.title,
                        "snippet": _truncate(hit.snippet or "", 400),
                        "score": hit.score,
                        "rank_bucket": "repository_context",
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

    async def _resolve_code_host_gateway(
        self, repo_id: uuid.UUID
    ) -> tuple[Any, RepoRef, str]:
        """Return ``(gateway, repo_ref, default_branch)`` for a workspace
        repo, routing through the memory adapter when the laptop-offline
        profile is active.

        Memory mode (``settings.use_memory_adapters``): looks up the
        workspace's ``MemoryGitRepo`` (by id when present, otherwise the
        single seeded row) and returns a :class:`MemoryCodeHost`. The
        e2e-navigator sandbox seeds a real ``workspace_repos`` row
        whose ``provider`` is ``"memory"`` so the agent's ``repo_id``
        argument resolves to a stable id between calls.

        Prod mode: existing flow — ``_resolve_repo_with_install`` plus
        a fresh :class:`GitHubCodeHost`. Behaviour is unchanged for
        every non-memory workspace.
        """
        settings = self._settings
        if getattr(settings, "use_memory_adapters", False):
            from backend.app.db.models.memory_adapters import MemoryGitRepo
            from backend.app.integrations.local.code_host import MemoryCodeHost

            # The agent typically passes ``workspace_repos.id`` from
            # dashboard_get. Resolve it to (owner, name) so we can
            # find the matching MemoryGitRepo row.
            owner: str | None = None
            name: str | None = None
            ws_repo = (
                await self._session.execute(
                    select(WorkspaceRepo).where(
                        WorkspaceRepo.workspace_id == self._workspace_id,
                        WorkspaceRepo.id == repo_id,
                    )
                )
            ).scalar_one_or_none()
            if ws_repo is not None:
                owner, _, name = ws_repo.full_name.partition("/")
            mem_row: MemoryGitRepo | None = None
            if owner and name:
                mem_row = (
                    await self._session.execute(
                        select(MemoryGitRepo).where(
                            MemoryGitRepo.workspace_id == self._workspace_id,
                            MemoryGitRepo.owner == owner,
                            MemoryGitRepo.name == name,
                        )
                    )
                ).scalar_one_or_none()
            if mem_row is None:
                # Fall back to the workspace's first MemoryGitRepo —
                # what the agent likely wants when there's only one
                # seeded repo.
                mem_row = (
                    await self._session.execute(
                        select(MemoryGitRepo)
                        .where(MemoryGitRepo.workspace_id == self._workspace_id)
                        .order_by(MemoryGitRepo.created_at.asc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if mem_row is None:
                raise ToolInvocationError(
                    "no MemoryGitRepo seeded for this workspace; "
                    "run tools/scripts/seed_e2e_navigator_state.py"
                )
            return (
                MemoryCodeHost(
                    session=self._session, workspace_id=self._workspace_id
                ),
                RepoRef(kind="github", owner=mem_row.owner, repo=mem_row.name),
                mem_row.default_branch,
            )
        # Production path. Same as the four inline constructions this
        # helper replaces.
        repo, install = await self._resolve_repo_with_install(repo_id)
        owner, _, name = repo.full_name.partition("/")
        return (
            GitHubCodeHost(install.installation_id, settings=settings),
            RepoRef(kind="github", owner=owner, repo=name),
            repo.default_branch,
        )

    async def _resolve_tracker(
        self, preferred_kind: str | None, project_hint: str | None
    ) -> TrackerGateway:
        """Return the workspace's bound tracker.

        Delegates to :func:`tracker_resolver.resolve_for_workspace` so
        Navigator, dashboard, and agent-runs all read the same row.
        Workspaces bind to exactly one tracker (Linear today, Jira
        tomorrow once the operator switches the native installation);
        we don't peer-rank GitHub Issues / Notion / legacy fallbacks
        — if the bound tracker isn't reachable, the tool surfaces
        ``no tracker bound`` and the LLM asks the operator to fix it.

        ``preferred_kind`` lets a tool pin "I want Linear" (rare). We
        error if the bound tracker doesn't match. ``project_hint`` is
        accepted for API compatibility but unused — the bound tracker
        is workspace-scoped, not per-project.
        """
        del project_hint
        from backend.app.services.tracker_resolver import resolve_for_workspace

        resolved = await resolve_for_workspace(
            session=self._session,
            settings=self._settings,
            workspace_id=self._workspace_id,
        )
        if resolved is None:
            raise ToolInvocationError(
                "no tracker is bound to this workspace; connect Linear "
                "(or another supported tracker) at the workspace level "
                "and retry."
            )
        if preferred_kind and preferred_kind != resolved.kind:
            raise ToolInvocationError(
                f"tracker {preferred_kind!r} is not the workspace's "
                f"bound tracker ({resolved.kind}); rebind it via the "
                "workspace settings before using this tool."
            )
        return resolved.gateway

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






    async def _tool_runs_list(self, args: dict[str, Any]) -> str:
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

        # Pull routine metadata in the same query so we can carry
        # ``play_key`` (== Routine.lane_id) and ``repo_id`` without an
        # N+1 follow-up. The ``play_key`` filter is applied at the
        # WHERE level — ``Routine.lane_id`` is the user-facing play key.
        stmt = (
            select(RoutineRun, Routine)
            .join(Routine, Routine.id == RoutineRun.routine_id)
            .where(RoutineRun.workspace_id == self._workspace_id)
        )
        if status_in is not None:
            stmt = stmt.where(RoutineRun.status.in_(status_in))
        if trigger_in is not None:
            stmt = stmt.where(RoutineRun.trigger.in_(trigger_in))
        if repo_id is not None:
            stmt = stmt.where(Routine.repo_id == repo_id)
        if play_key is not None:
            stmt = stmt.where(Routine.lane_id == play_key)
        if since_dt is not None:
            stmt = stmt.where(
                func.coalesce(
                    RoutineRun.started_at, RoutineRun.created_at
                )
                >= since_dt
            )
        stmt = stmt.order_by(
            desc(
                func.coalesce(
                    RoutineRun.started_at, RoutineRun.created_at
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

        repo_id_set = {r.repo_id for _, r in rows if r.repo_id is not None}
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
        for run, routine in rows:
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
                    "routine_id": str(run.routine_id),
                    "play_key": routine.lane_id if routine else None,
                    "repo_id": (
                        str(routine.repo_id)
                        if routine and routine.repo_id is not None
                        else None
                    ),
                    "repo_name": (
                        repo_name_map.get(routine.repo_id)
                        if routine and routine.repo_id is not None
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

    async def _tool_runs_get(self, args: dict[str, Any]) -> str:
        try:
            run_id = _parse_uuid(args, "run_id")
        except ToolInvocationError as exc:
            return _json_result({
                "error": "invalid_run_id",
                "message": str(exc),
            })
        run = (
            await self._session.execute(
                select(RoutineRun).where(
                    RoutineRun.id == run_id,
                    RoutineRun.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if run is None:
            return _json_result({
                "error": "not_found",
                "message": f"run {run_id} not found in this workspace",
            })
        routine = await self._session.get(Routine, run.routine_id)
        repo_name: str | None = None
        repo_id: uuid.UUID | None = routine.repo_id if routine else None
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
                "routine_id": str(run.routine_id),
                "play_key": routine.lane_id if routine else None,
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

    async def _tool_inbox_update(self, args: dict[str, Any]) -> str:
        """Polymorphic inbox mutation. Branches on ``action`` to the
        underlying dispose / snooze / reassign handler — each keeps its
        own admin gate, audit-log action name (``inbox.dispose`` etc.),
        and side-effect dispatcher so historical queries against the
        audit table stay stable."""
        action = args.get("action")
        if action == "dispose":
            return await self._tool_inbox_dispose(args)
        if action == "snooze":
            return await self._tool_inbox_snooze(args)
        if action == "reassign":
            return await self._tool_inbox_reassign(args)
        return _json_result(
            {
                "error": "invalid_action",
                "message": (
                    f"action must be one of 'dispose', 'snooze', "
                    f"'reassign' (got {action!r})"
                ),
            }
        )

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






    async def _tool_ticket_get(self, args: dict[str, Any]) -> str:
        ticket_ref = _require_str(args, "ticket_ref").strip()
        include_comments = bool(args.get("include_comments", False))
        tracker = await self._resolve_tracker(None, None)
        ref = ticket_ref_from(_tracker_kind_of(tracker), ticket_ref)
        snapshot_fn = getattr(tracker, "get_ticket_snapshot", None)
        if snapshot_fn is None:
            raise ToolInvocationError(
                "this tracker does not implement single-ticket lookup"
            )
        snapshot = await snapshot_fn(ref)
        if snapshot is None:
            return _json_result(
                {
                    "error": "ticket_not_found",
                    "ticket_ref": ticket_ref,
                    "message": (
                        f"the bound tracker has no ticket matching "
                        f"{ticket_ref!r}"
                    ),
                }
            )
        result: dict[str, Any] = dict(snapshot)
        if include_comments:
            list_fn = getattr(tracker, "list_comments", None)
            if list_fn is None:
                result["comments_error"] = "comments_unsupported"
            else:
                try:
                    raw_comments = await list_fn(ref)
                except Exception as exc:  # noqa: BLE001 — surface vendor errors
                    raise ToolInvocationError(
                        f"tracker list_comments failed: {exc}"
                    ) from exc
                rows, truncated = serialize_ticket_comments(
                    raw_comments or []
                )
                result["comments"] = rows
                if truncated:
                    result["comments_truncated"] = True
        return _json_result(result)

    async def _tool_dashboard_get(self, args: dict[str, Any]) -> str:
        """Stitch the workspace's headline state into one payload.

        Composes cheap queries against ``WorkspaceProjectPriority``,
        ``InboxItem``, ``PullRequest``, ``RoutineRun``, and
        ``WorkflowRun``. The shape is deliberately slim — the agent
        gets enough to answer "what's on my plate?" without a five-
        tool fan-out, but not the full ``/dashboard/ops`` payload
        (which is route-only, requires HTTP auth context, and carries
        per-repo enrichment the agent rarely needs in chat).
        """
        del args  # no parameters

        from datetime import datetime as _dt, timedelta, timezone

        from backend.app.db.models.dashboard_priorities import (
            WorkspaceProjectPriority,
        )

        now = _dt.now(timezone.utc)
        cutoff_24h = now - timedelta(days=1)
        cutoff_recent = now - timedelta(days=7)

        # Priorities — group by state, ordered.
        priority_rows = (
            await self._session.execute(
                select(WorkspaceProjectPriority)
                .where(
                    WorkspaceProjectPriority.workspace_id
                    == self._workspace_id
                )
                .order_by(
                    WorkspaceProjectPriority.state,
                    WorkspaceProjectPriority.ordinal.asc(),
                )
            )
        ).scalars().all()
        by_state: dict[str, list[dict[str, Any]]] = {
            "active": [],
            "planning": [],
            "parked": [],
        }
        for row in priority_rows:
            bucket = by_state.setdefault(row.state, [])
            bucket.append(
                {
                    "project_native_id": row.project_native_id,
                    "ordinal": row.ordinal,
                    "originating_thread_id": (
                        str(row.originating_thread_id)
                        if row.originating_thread_id
                        else None
                    ),
                }
            )

        # Inbox snapshot. We intentionally include this even though
        # the session-context frame already has it — when the user
        # asks "what's on my plate?" they want a structured
        # response that doesn't depend on the agent regurgitating
        # the frame back. The detail here also exposes a 24-hour
        # arrival count the frame doesn't carry.
        inbox_total = (
            await self._session.execute(
                select(func.count(InboxItem.id)).where(
                    InboxItem.workspace_id == self._workspace_id,
                    InboxItem.status.in_(("new", "snoozed")),
                )
            )
        ).scalar_one() or 0
        inbox_by_type_rows = (
            await self._session.execute(
                select(InboxItem.type, func.count(InboxItem.id))
                .where(
                    InboxItem.workspace_id == self._workspace_id,
                    InboxItem.status.in_(("new", "snoozed")),
                )
                .group_by(InboxItem.type)
            )
        ).all()
        inbox_by_type = {
            str(row[0]): int(row[1])
            for row in inbox_by_type_rows
            if row[0]
        }
        inbox_arrived_24h = (
            await self._session.execute(
                select(func.count(InboxItem.id)).where(
                    InboxItem.workspace_id == self._workspace_id,
                    InboxItem.created_at >= cutoff_24h,
                )
            )
        ).scalar_one() or 0

        # Open PRs + recent merged.
        open_pr_count = (
            await self._session.execute(
                select(func.count(PullRequest.id)).where(
                    PullRequest.workspace_id == self._workspace_id,
                    PullRequest.state == "open",
                )
            )
        ).scalar_one() or 0
        shipped_24h = (
            await self._session.execute(
                select(func.count(PullRequest.id)).where(
                    PullRequest.workspace_id == self._workspace_id,
                    PullRequest.merged.is_(True),
                    PullRequest.merged_at >= cutoff_24h,
                )
            )
        ).scalar_one() or 0

        # Recent activity — top 5 mixed across routine runs + PRs +
        # workflow runs in the last week. Cheap unioned listing; the
        # agent calls ``runs_query`` / ``list_pull_requests`` if it
        # needs detail beyond the headline.
        recent: list[dict[str, Any]] = []
        recent_runs = (
            await self._session.execute(
                select(RoutineRun)
                .where(
                    RoutineRun.workspace_id == self._workspace_id,
                    RoutineRun.created_at >= cutoff_recent,
                )
                .order_by(desc(RoutineRun.created_at))
                .limit(5)
            )
        ).scalars().all()
        for r in recent_runs:
            recent.append(
                {
                    "kind": "routine_run",
                    "id": str(r.id),
                    "status": r.status,
                    "trigger": r.trigger,
                    "created_at": r.created_at.isoformat()
                    if r.created_at
                    else None,
                }
            )
        recent_prs = (
            await self._session.execute(
                select(PullRequest)
                .where(
                    PullRequest.workspace_id == self._workspace_id,
                    PullRequest.updated_at_external >= cutoff_recent,
                )
                .order_by(desc(PullRequest.updated_at_external))
                .limit(5)
            )
        ).scalars().all()
        for pr in recent_prs:
            recent.append(
                {
                    "kind": "pull_request",
                    "repo_full_name": pr.repo_full_name,
                    "number": pr.number,
                    "title": pr.title,
                    "state": pr.state,
                    "merged": pr.merged,
                    "url": pr.html_url,
                    "updated_at": (
                        pr.updated_at_external.isoformat()
                        if pr.updated_at_external
                        else None
                    ),
                }
            )
        # Sort merged stream by timestamp desc; trim to top 5.
        recent.sort(
            key=lambda x: x.get("updated_at") or x.get("created_at") or "",
            reverse=True,
        )
        recent = recent[:5]

        return _json_result(
            {
                "now": now.isoformat(),
                "priorities": by_state,
                "inbox": {
                    "open_total": int(inbox_total),
                    "by_type": inbox_by_type,
                    "arrived_24h": int(inbox_arrived_24h),
                },
                "pull_requests": {
                    "open_total": int(open_pr_count),
                    "shipped_24h": int(shipped_24h),
                },
                "recent_activity": recent,
            }
        )

    async def _tool_audit_search(
        self, args: dict[str, Any]
    ) -> str:
        from datetime import datetime as _dt, timedelta, timezone

        action = args.get("action")
        if action is not None and not isinstance(action, str):
            raise ToolInvocationError("action must be a string when provided")
        target_kind = args.get("target_kind")
        if target_kind is not None and not isinstance(target_kind, str):
            raise ToolInvocationError(
                "target_kind must be a string when provided"
            )
        target_id = args.get("target_id")
        if target_id is not None and not isinstance(target_id, str):
            raise ToolInvocationError(
                "target_id must be a string when provided"
            )
        since_dt = _parse_iso_datetime(args.get("since"), "since")
        if since_dt is None:
            since_dt = _dt.now(timezone.utc) - timedelta(days=30)
        limit = _clamp_int(
            args.get("limit"), default=25, low=1, high=100
        )

        stmt = (
            select(AuditLog)
            .where(
                AuditLog.workspace_id == self._workspace_id,
                AuditLog.created_at >= since_dt,
            )
            .order_by(desc(AuditLog.created_at))
            .limit(limit)
        )
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if target_kind:
            stmt = stmt.where(AuditLog.target_kind == target_kind)
        if target_id:
            stmt = stmt.where(AuditLog.target_id == target_id)

        rows = (await self._session.execute(stmt)).scalars().all()
        items = [
            {
                "id": int(r.id),
                "action": r.action,
                "target_kind": r.target_kind,
                "target_id": r.target_id,
                "actor_user_id": (
                    str(r.actor_user_id) if r.actor_user_id else None
                ),
                "payload": r.payload or {},
                "created_at": r.created_at.isoformat()
                if r.created_at
                else None,
            }
            for r in rows
        ]
        return _json_result({"audit_log": items, "count": len(items)})

    # ------------------------------------------------------------------
    # Mutating tools (Navigator tool review PR-C2, ELS-78):
    # ``update_ticket``, ``set_priority_state``, ``start_decomposition``.
    # All admin-gated and audited. The agentic prompt's
    # ``Verify before mutate`` rule (PR2 of the overhaul, navigator.md)
    # tells the agent to confirm before calling these unless the user
    # gave a direct command — the tools execute when called; the
    # gating lives in the prompt.
    # ------------------------------------------------------------------

    async def _tool_ticket_update(self, args: dict[str, Any]) -> str:
        from backend.app.api.v1.routes.workspaces import ROLES_ADMIN

        await self._require_workspace_role(ROLES_ADMIN)

        ticket_ref = _require_str(args, "ticket_ref").strip()
        title = args.get("title")
        body = args.get("body")
        labels = args.get("labels")
        state = args.get("state")
        if title is not None and not isinstance(title, str):
            raise ToolInvocationError("title must be a string")
        if body is not None and not isinstance(body, str):
            raise ToolInvocationError("body must be a string")
        if labels is not None:
            if not isinstance(labels, list) or not all(
                isinstance(x, str) for x in labels
            ):
                raise ToolInvocationError(
                    "labels must be a list of strings"
                )
        if state is not None and not isinstance(state, str):
            raise ToolInvocationError("state must be a string")
        if title is None and body is None and labels is None and state is None:
            raise ToolInvocationError(
                "update_ticket needs at least one of title, body, labels, "
                "or state to update"
            )

        tracker = await self._resolve_tracker(None, None)
        ref = ticket_ref_from(_tracker_kind_of(tracker), ticket_ref)
        actions: list[str] = []

        # Single ``issueUpdate`` call covers title/body/labels.
        if title is not None or body is not None or labels is not None:
            update_fn = getattr(tracker, "update_ticket", None)
            if update_fn is None:
                raise ToolInvocationError(
                    "this tracker does not implement ticket updates"
                )
            try:
                await update_fn(
                    ref, title=title, body=body, labels=labels
                )
            except Exception as exc:  # noqa: BLE001 — surface vendor errors
                raise ToolInvocationError(
                    f"tracker rejected update: {exc}"
                ) from exc
            if title is not None:
                actions.append("title")
            if body is not None:
                actions.append("body")
            if labels is not None:
                actions.append(f"labels:{len(labels)}")

        # State transitions go through ``transition`` (label swap +
        # workflow state move per FSM_TO_LINEAR_STATE).
        if state is not None:
            try:
                await tracker.transition(ref, to_state=state)
            except Exception as exc:  # noqa: BLE001
                raise ToolInvocationError(
                    f"tracker rejected transition to {state!r}: {exc}"
                ) from exc
            actions.append(f"state:{state}")

        self._session.add(
            AuditLog(
                workspace_id=self._workspace_id,
                actor_user_id=self._user_id,
                actor_token_id=None,
                action="navigator.update_ticket",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "ticket_ref": ticket_ref,
                    "actions": actions,
                    "had_title": title is not None,
                    "had_body": body is not None,
                    "labels_count": (
                        len(labels) if isinstance(labels, list) else None
                    ),
                    "state_target": state,
                },
            )
        )
        await self._session.flush()

        return _json_result(
            {"ticket_ref": ticket_ref, "actions": actions}
        )

    async def _tool_project_priority_set(self, args: dict[str, Any]) -> str:
        from backend.app.api.v1.routes.workspaces import ROLES_ADMIN

        from backend.app.db.models.dashboard_priorities import (
            WorkspaceProjectPriority,
        )

        await self._require_workspace_role(ROLES_ADMIN)

        project_native_id = _require_str(args, "project_native_id").strip()
        state = _require_str(args, "state").strip()
        if state not in ("active", "planning", "parked"):
            raise ToolInvocationError(
                f"state must be one of active|planning|parked (got {state!r})"
            )

        existing = (
            await self._session.execute(
                select(WorkspaceProjectPriority).where(
                    WorkspaceProjectPriority.workspace_id == self._workspace_id,
                    WorkspaceProjectPriority.project_native_id
                    == project_native_id,
                )
            )
        ).scalar_one_or_none()
        prior_state: str | None = None
        if existing is None:
            # Create new row at MAX+1 ordinal so the project shows up
            # without disturbing existing ordering.
            max_ord = await self._session.scalar(
                select(WorkspaceProjectPriority.ordinal)
                .where(
                    WorkspaceProjectPriority.workspace_id == self._workspace_id
                )
                .order_by(WorkspaceProjectPriority.ordinal.desc())
                .limit(1)
            )
            next_ord = 0 if max_ord is None else int(max_ord) + 1
            self._session.add(
                WorkspaceProjectPriority(
                    workspace_id=self._workspace_id,
                    project_native_id=project_native_id,
                    ordinal=next_ord,
                    state=state,
                )
            )
        else:
            prior_state = existing.state
            existing.state = state

        self._session.add(
            AuditLog(
                workspace_id=self._workspace_id,
                actor_user_id=self._user_id,
                actor_token_id=None,
                action="navigator.set_priority_state",
                target_kind="workspace_project_priority",
                target_id=project_native_id,
                payload={
                    "project_native_id": project_native_id,
                    "state": state,
                    "prior_state": prior_state,
                    "created_row": existing is None,
                },
            )
        )
        await self._session.flush()

        # ELS-91: bring child tickets into line with the new state.
        # Best-effort — tracker errors log + audit but do not roll back
        # the operator's flip.
        from backend.app.services.agent.project_state_sync import (
            sync_project_tickets_for_state,
        )

        gateway = await self._resolve_tracker(None, None)
        sync_report = await sync_project_tickets_for_state(
            self._session,
            workspace_id=self._workspace_id,
            project_id=project_native_id,
            new_state=state,
            gateway=gateway,
            actor_user_id=self._user_id,
            actor_token_id=None,
        )

        return _json_result(
            {
                "project_native_id": project_native_id,
                "state": state,
                "prior_state": prior_state,
                "synced_tickets": sync_report.as_dict(),
            }
        )

    async def _tool_web_fetch(self, args: dict[str, Any]) -> str:
        """Firecrawl ``/v1/scrape``. Returns markdown by default; HTML
        and text are opt-in via ``format``. The response body is
        capped server-side to whatever Firecrawl returns — we don't
        post-process beyond projecting the keys."""
        from backend.app.services.firecrawl_client import (
            FirecrawlClient,
            FirecrawlError,
        )
        from backend.app.services.firecrawl_resolver import (
            resolve_firecrawl_key,
        )

        url = args.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return _json_result(
                {
                    "error": "invalid_url",
                    "message": (
                        "url must start with http:// or https://"
                    ),
                }
            )
        fmt = args.get("format") or "markdown"
        if fmt not in {"markdown", "html", "text"}:
            return _json_result(
                {
                    "error": "invalid_format",
                    "message": (
                        "format must be one of 'markdown', 'html', "
                        "'text'"
                    ),
                }
            )
        # Firecrawl uses ``rawHtml`` for raw and ``markdown`` /
        # ``html`` for the post-extracted shapes. Map our user-facing
        # vocab onto the wire shape.
        firecrawl_format = {"markdown": "markdown", "html": "html", "text": "markdown"}[fmt]

        resolved = await resolve_firecrawl_key(self._session, self._workspace_id)
        if resolved is None:
            return _json_result(
                {
                    "error": "firecrawl_unconfigured",
                    "message": (
                        "Firecrawl API key not set. Add one in "
                        "Settings → Integrations or set "
                        "``FIRECRAWL_API_KEY`` in the deploy env."
                    ),
                }
            )
        try:
            async with FirecrawlClient(api_key=resolved.api_key) as client:
                raw = await client.scrape(url=url, formats=[firecrawl_format])
        except FirecrawlError as exc:
            return _json_result(
                {
                    "error": exc.code,
                    "message": exc.message,
                    "status": exc.status,
                    "url": url,
                }
            )

        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            data = {}
        # Project safe keys only — Firecrawl's full response carries
        # a lot of metadata we don't want streaming into the LLM
        # context every fetch.
        content = data.get(firecrawl_format) or ""
        if fmt == "text" and isinstance(content, str):
            # We asked Firecrawl for markdown but the LLM said "text" —
            # strip the markdown noise pragmatically. Heavy stripping
            # would live in a dedicated helper; for now, a few cheap
            # cleanups handle the common cases.
            import re

            content = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", content)  # images
            content = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", content)  # links
            content = re.sub(r"^#+\s+", "", content, flags=re.MULTILINE)
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        await self._audit_navigator_tool(
            tool_name="web_fetch",
            target={"kind": "external_url", "id": ""},
            payload={
                "url": url[:500],
                "format": fmt,
                "bytes": len(content) if isinstance(content, str) else 0,
                "source": resolved.source,
            },
        )
        return _json_result(
            {
                "url": url,
                "format": fmt,
                "content": content,
                "metadata": {
                    "title": metadata.get("title"),
                    "language": metadata.get("language"),
                    "ogTitle": metadata.get("ogTitle"),
                    "ogDescription": metadata.get("ogDescription"),
                },
            }
        )

    async def _tool_config_help(self, args: dict[str, Any]) -> str:
        """Discover scopes / read one. Delegates to
        :mod:`backend.app.services.config_registry` so adding a new
        scope is a one-line change there with no tool-surface churn."""
        from backend.app.db.models.tenancy import Workspace
        from backend.app.services.config_registry import (
            help_scope,
            list_scopes,
        )

        scope = args.get("scope")
        if not scope:
            return _json_result({"scopes": list_scopes()})
        workspace = await self._session.get(Workspace, self._workspace_id)
        if workspace is None:
            return _json_result(
                {
                    "error": "workspace_not_found",
                    "message": (
                        "Active workspace row missing — re-auth and "
                        "try again."
                    ),
                }
            )
        try:
            return _json_result(await help_scope(self._session, workspace, str(scope)))
        except KeyError:
            return _json_result(
                {
                    "error": "unknown_scope",
                    "message": (
                        f"scope {scope!r} is not registered. Call "
                        "``config_help`` with no args to list "
                        "available scopes."
                    ),
                }
            )

    async def _tool_config_put(self, args: dict[str, Any]) -> str:
        """Validate + write one scope. Admin-only."""
        from backend.app.db.models.tenancy import Workspace
        from backend.app.services.config_registry import put_scope

        gate_err = await self._require_admin_or_error(tool_name="config_put")
        if gate_err is not None:
            return _json_result(gate_err)

        scope = args.get("scope")
        if not scope or not isinstance(scope, str):
            return _json_result(
                {
                    "error": "missing_scope",
                    "message": "scope is required (string)",
                }
            )
        if "value" not in args:
            return _json_result(
                {
                    "error": "missing_value",
                    "message": (
                        "value is required (shape depends on scope — "
                        "call ``config_help`` first)"
                    ),
                }
            )
        workspace = await self._session.get(Workspace, self._workspace_id)
        if workspace is None:
            return _json_result(
                {
                    "error": "workspace_not_found",
                    "message": (
                        "Active workspace row missing — re-auth and "
                        "try again."
                    ),
                }
            )
        try:
            result = await put_scope(
                self._session,
                workspace,
                scope,
                args["value"],
                actor_user_id=self._user_id,
                actor_token_id=None,
            )
        except KeyError:
            return _json_result(
                {
                    "error": "unknown_scope",
                    "message": (
                        f"scope {scope!r} is not registered. Call "
                        "``config_help`` with no args to list "
                        "available scopes."
                    ),
                }
            )
        except ValueError as exc:
            return _json_result(
                {
                    "error": "invalid_value",
                    "message": str(exc),
                }
            )
        return _json_result(result)

    async def _tool_run_workflow(self, args: dict[str, Any]) -> str:
        """W8.4 (ELS-260) — chat trigger for the workflow primitive.

        The chat loop stays LOCK-FREE (thesis 3a): this handler only
        persists a ``queued`` run row and schedules a background task
        that drives the runtime THROUGH the dispatch gate — no
        ``agent_dispatch_locks`` row is taken in the chat process.
        Escalation, not in-loop dispatch (the a→b rule).
        """
        if self._subagent_active:
            raise ToolInvocationError(
                "nested run_workflow is not allowed (already running "
                "inside a subagent)"
            )
        from backend.app.api.v1.routes.workspaces import ROLES_ADMIN

        await self._require_workspace_role(ROLES_ADMIN)

        from backend.app.db.models.workflow import AgentWorkflowRun
        from backend.app.services.workflow.registry import (
            list_available_specs,
            resolve_spec,
        )

        workflow_name = _require_str(args, "workflow_name").strip()
        inputs_raw = args.get("inputs")
        inputs = inputs_raw if isinstance(inputs_raw, dict) else {}

        spec = resolve_spec(workflow_name)
        if spec is None:
            return _json_result(
                {
                    "error": "unknown_workflow",
                    "message": f"no workflow named '{workflow_name}'",
                    "available": list_available_specs(),
                }
            )

        run = AgentWorkflowRun(
            workspace_id=self._workspace_id,
            spec_name=spec.name,
            spec_version=spec.version,
            inputs=inputs,
            trigger_kind="chat",
            triggered_by=f"user:{self._user_id}" if self._user_id else "chat",
            status="queued",
        )
        self._session.add(run)
        await self._session.flush()

        self._session.add(
            AuditLog(
                workspace_id=self._workspace_id,
                actor_user_id=self._user_id,
                action="workflow.run_queued",
                target_kind="workflow_run",
                target_id=str(run.id),
                payload={
                    "spec_name": spec.name,
                    "trigger_kind": "chat",
                    "inputs": {k: str(v)[:200] for k, v in inputs.items()},
                },
            )
        )
        await self._session.flush()

        import asyncio as _asyncio

        from backend.app.services.workflow.runtime import advance_run_by_id

        _asyncio.create_task(
            advance_run_by_id(run.id, actor_user_id=self._user_id),
            name=f"ship.workflow.{run.id}",
        )
        return _json_result(
            {
                "workflow_run_id": str(run.id),
                "spec_name": spec.name,
                "status": "queued",
                "note": (
                    "queued; spawns go through the dispatch gate "
                    "(cap/cascade apply). Check status via the audit "
                    "log (workflow.step_*) or the run row."
                ),
            }
        )

    async def _tool_run_subagent(self, args: dict[str, Any]) -> str:
        """Polymorphic subagent spawner. Branches on ``kind`` to
        decomposition (project-anchor chain) or specialist consult
        (one-shot focused expert). The underlying handlers keep their
        own admin gates + audit-log action names so callers querying
        ``navigator.decomposition_start`` or
        ``navigator.specialist_consult`` historically still match."""
        kind = args.get("kind")
        if kind == "decomposition":
            project_native_id = args.get("project_native_id")
            if not project_native_id:
                return _json_result(
                    {
                        "error": "missing_project_native_id",
                        "message": (
                            "kind='decomposition' requires "
                            "project_native_id"
                        ),
                    }
                )
            return await self._tool_decomposition_start(
                {"project_native_id": project_native_id}
            )
        specialist_slugs = {
            "designer",
            "tech-architect",
            "qa-architect",
            "ba",
            "developer",
        }
        if kind in specialist_slugs:
            task = args.get("task")
            if not task:
                return _json_result(
                    {
                        "error": "missing_task",
                        "message": (
                            f"kind={kind!r} (specialist consult) "
                            "requires task"
                        ),
                    }
                )
            return await self._tool_specialist_consult(
                {
                    "specialist": kind,
                    "task": task,
                    **(
                        {"context_hint": args["context_hint"]}
                        if "context_hint" in args
                        else {}
                    ),
                }
            )
        return _json_result(
            {
                "error": "invalid_kind",
                "message": (
                    f"kind must be 'decomposition' or one of "
                    f"{sorted(specialist_slugs)} (got {kind!r})"
                ),
            }
        )

    async def _tool_decomposition_start(self, args: dict[str, Any]) -> str:
        from backend.app.api.v1.routes.workspaces import ROLES_ADMIN

        from backend.app.db.models.dashboard_priorities import (
            WorkspaceProjectPriority,
        )

        await self._require_workspace_role(ROLES_ADMIN)

        project_native_id = _require_str(args, "project_native_id").strip()

        row = (
            await self._session.execute(
                select(WorkspaceProjectPriority).where(
                    WorkspaceProjectPriority.workspace_id == self._workspace_id,
                    WorkspaceProjectPriority.project_native_id
                    == project_native_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ToolInvocationError(
                f"project {project_native_id!r} is not on the dashboard "
                f"priorities; create_project (or drag-in via the UI) "
                f"first"
            )
        if row.state != "planning":
            raise ToolInvocationError(
                f"project is in state={row.state!r} — only Drafts "
                f"(state='planning') projects can hand off to "
                f"decomposition"
            )

        tracker = await self._resolve_tracker(None, None)
        get_anchor_fn = getattr(tracker, "get_planning_anchor", None)
        if get_anchor_fn is None:
            raise ToolInvocationError(
                "this tracker does not model planning anchors"
            )
        anchor = await get_anchor_fn(project_native_id)
        if anchor is None:
            raise ToolInvocationError(
                f"project has no planning anchor — was it created via the "
                f"drafting flow (`create_project` from Navigator)?"
            )

        anchor_id = str(anchor.get("id") or "")
        anchor_identifier = str(
            anchor.get("identifier") or anchor.get("id") or ""
        )
        if not anchor_id:
            raise ToolInvocationError(
                "planning anchor has no id; tracker returned malformed shape"
            )

        ref = ticket_ref_from(_tracker_kind_of(tracker), anchor_id)
        try:
            # E16/ELS-123 collapsed decomposition into one bundle stage.
            # The entry is ``stage:decomposition`` — NOT the pre-E16
            # ``wbs`` (which the post-cutover label resolver no longer
            # recognizes, so the anchor would park unroutable; ELS-308).
            await tracker.transition(ref, to_state="decomposition")
        except Exception as exc:  # noqa: BLE001
            raise ToolInvocationError(
                f"tracker rejected transition to stage:decomposition: {exc}"
            ) from exc

        self._session.add(
            AuditLog(
                workspace_id=self._workspace_id,
                actor_user_id=self._user_id,
                actor_token_id=None,
                action="navigator.start_decomposition",
                target_kind="workspace_project_priority",
                target_id=project_native_id,
                payload={
                    "project_native_id": project_native_id,
                    "anchor_issue_id": anchor_id,
                    "anchor_identifier": anchor_identifier,
                },
            )
        )
        await self._session.flush()

        # Auto-start the decomposition routine inline instead of waiting
        # for the diff-based poller to notice the new stage (ELS-320).
        # The poller only fires on a NET observed state change, so the
        # anchor used to park at stage:decomposition until a manual
        # maybe_dispatch kick. Fire it here. Best-effort: the anchor is
        # already transitioned, so a dispatch hiccup (shadow / cap / lock
        # / transient) just falls back to the poller — never fail the
        # handoff. All gates still apply inside maybe_dispatch.
        dispatched = False
        try:
            from backend.app.services.dispatcher import maybe_dispatch

            result = await maybe_dispatch(
                self._session,
                workspace_id=self._workspace_id,
                ticket_ref=anchor_identifier,
                trigger_kind="decomposition_start",
                fsm_stage="decomposition",
            )
            dispatched = bool(getattr(result, "fired", False))
        except Exception:  # noqa: BLE001
            logger.exception(
                "decomposition_start: inline dispatch failed ws=%s anchor=%s",
                self._workspace_id,
                anchor_identifier,
            )

        return _json_result(
            {
                "project_native_id": project_native_id,
                "anchor_issue_id": anchor_id,
                "anchor_identifier": anchor_identifier,
                "process": "decomposition",
                "dispatched": dispatched,
            }
        )

    # ------------------------------------------------------------------
    # consult_specialist — subagent loop (Navigator overhaul PR3, ELS-74).
    # Hands a focused task off to a role-prompted subagent that runs
    # against the same workspace tool surface (minus this tool, to
    # prevent recursion) and returns a single final report.
    # ------------------------------------------------------------------

    _SUBAGENT_ALLOWED_SPECIALISTS: tuple[str, ...] = (
        "designer",
        "tech-architect",
        "qa-architect",
        "ba",
        "developer",
    )
    _SUBAGENT_MAX_TOOL_CALLS: int = 25
    _SUBAGENT_MAX_SECONDS: float = 300.0

    async def _tool_specialist_consult(self, args: dict[str, Any]) -> str:
        if self._subagent_active:
            # Defensive: tool spec is filtered out of the subagent's
            # tool list so the LLM literally can't emit this name, but
            # belt-and-suspender for hallucinations.
            raise ToolInvocationError(
                "nested consult_specialist is not allowed (already running "
                "inside a subagent)"
            )
        specialist = _require_str(args, "specialist").strip()
        task = _require_str(args, "task").strip()
        context_hint_raw = args.get("context_hint")
        context_hint = (
            context_hint_raw.strip()
            if isinstance(context_hint_raw, str) and context_hint_raw.strip()
            else None
        )
        if specialist not in self._SUBAGENT_ALLOWED_SPECIALISTS:
            raise ToolInvocationError(
                f"unknown specialist {specialist!r}; allowed: "
                + ", ".join(self._SUBAGENT_ALLOWED_SPECIALISTS)
            )

        # Load the role file's prompt body. ``agent_roles_svc.get_default``
        # reads the seeded markdown corpus we ship under
        # ``backend/app/resources/agent_roles/``.
        from backend.app.services import agent_roles as agent_roles_svc

        role = agent_roles_svc.get_default(specialist)
        if role is None or not role.prompt.strip():
            raise ToolInvocationError(
                f"specialist {specialist!r} is not registered as a default "
                f"agent role"
            )

        # Subagent system prompt = role prompt + a short framing block.
        # We don't try to render ``{{ISSUE}}`` / ``{{BASE}}`` substitutions
        # here — the role files use those for SDLC ticket invocation
        # context that doesn't apply to a Navigator-spawned consultation.
        # Leaving the placeholders raw is fine; the LLM treats them as
        # template fragments and the framing tells it to ignore.
        framing = (
            "## You are running as a subagent\n\n"
            "A parent Navigator agent invoked you for one focused task. "
            "Rules:\n\n"
            "- You will NOT be re-invoked. Produce one final report at "
            "the end of this run; the parent receives only your last "
            "message.\n"
            "- Tool surface: same as the parent (workspace context, KB, "
            "tracker, repos) MINUS ``consult_specialist`` itself. Use "
            "tools to gather evidence — don't guess.\n"
            "- Template placeholders in the role prompt below "
            "(``{{ISSUE}}``, ``{{BASE}}``, ``{{TITLE}}``, "
            "``{{DESCRIPTION}}``) are SDLC fixtures from the role's "
            "ticket-mode invocation; they don't apply to this "
            "consultation. Read past them.\n"
            "- Stop when you've answered the parent's task. End your "
            "final message with: ``[Ship subagent:" + specialist + "]``.\n\n"
            "## Role prompt\n\n"
        )
        system_prompt = framing + role.prompt.strip()

        user_message_parts = [
            "## Task from the parent agent",
            "",
            task,
        ]
        if context_hint:
            user_message_parts.extend(
                ["", "## Context the parent passed", "", context_hint]
            )
        user_message = "\n".join(user_message_parts)

        # Filter out ``consult_specialist`` from the tool spec list the
        # subagent sees. Recursion guard #1 (the LLM can't emit a name
        # it doesn't know about); the ``_subagent_active`` flag is
        # guard #2.
        all_specs = self.specs()
        # run_workflow joins the recursion-guard exclusion set (W8.4):
        # a subagent must never fan out further agents.
        sub_specs = [
            s
            for s in all_specs
            if s.name not in ("consult_specialist", "run_workflow")
        ]

        self._subagent_active = True
        try:
            outcome = await self._run_subagent_loop(
                system_prompt=system_prompt,
                user_message=user_message,
                tool_specs=sub_specs,
            )
        finally:
            self._subagent_active = False

        # Audit row: subagent runs are operator-visible work and must
        # be debuggable from the audit log without grepping LLM logs.
        try:
            self._session.add(
                AuditLog(
                    workspace_id=self._workspace_id,
                    actor_user_id=self._user_id,
                    actor_token_id=None,
                    action="navigator.consult_specialist",
                    target_kind="agent_role",
                    target_id=specialist,
                    payload={
                        "specialist": specialist,
                        "task_preview": task[:300],
                        "tool_calls_used": outcome["tool_calls_used"],
                        "finish_reason": outcome["finish_reason"],
                        "had_error": bool(outcome.get("error")),
                    },
                )
            )
            await self._session.flush()
        except Exception as exc:  # noqa: BLE001 — audit failure must not sink the result
            logger.warning(
                "consult_specialist: audit insert failed specialist=%s err=%s",
                specialist,
                exc,
            )

        return _json_result(
            {
                "specialist": specialist,
                "report": outcome["text"],
                "tool_calls_used": outcome["tool_calls_used"],
                "finish_reason": outcome["finish_reason"],
                **({"error": outcome["error"]} if outcome.get("error") else {}),
            }
        )

    async def _run_subagent_loop(
        self,
        *,
        system_prompt: str,
        user_message: str,
        tool_specs: list[ToolSpec],
    ) -> dict[str, Any]:
        """Drive the subagent's astream→tool→astream cycle to completion.

        Mirrors the chat-stream tool loop in
        ``backend/app/api/v1/routes/chat.py`` but synchronously: no SSE
        streaming, no per-event yield, just collect text + run tools
        until the model stops or the budget runs out. Returns a dict
        with ``text``, ``tool_calls_used``, ``finish_reason``, and an
        optional ``error`` key when the loop bailed for non-success
        reasons.

        Budgets:

        - ``_SUBAGENT_MAX_TOOL_CALLS`` (25) — caps the chain so a
          runaway specialist can't burn the whole context window.
        - ``_SUBAGENT_MAX_SECONDS`` (300s) — wall-clock cap so a
          Navigator request waiting on a stuck subagent surfaces the
          timeout instead of hanging indefinitely.
        """
        import time as _time

        from backend.app.services.agent.client import (
            ChatMessage as _CM,
            End,
            TextDelta,
            ToolCall,
            pick_default_client,
        )

        try:
            client = pick_default_client(self._settings)
        except RuntimeError as exc:
            return {
                "text": "",
                "tool_calls_used": 0,
                "finish_reason": "agent_unavailable",
                "error": f"agent client unavailable: {exc}",
            }

        messages: list[_CM] = [
            _CM(role="system", content=system_prompt),
            _CM(role="user", content=user_message),
        ]

        text_chunks: list[str] = []
        tool_calls_used = 0
        deadline = _time.monotonic() + self._SUBAGENT_MAX_SECONDS

        while True:
            if _time.monotonic() > deadline:
                return {
                    "text": ("".join(text_chunks)).strip(),
                    "tool_calls_used": tool_calls_used,
                    "finish_reason": "deadline_exceeded",
                    "error": (
                        f"subagent ran past {int(self._SUBAGENT_MAX_SECONDS)}s"
                    ),
                }
            if tool_calls_used >= self._SUBAGENT_MAX_TOOL_CALLS:
                return {
                    "text": ("".join(text_chunks)).strip(),
                    "tool_calls_used": tool_calls_used,
                    "finish_reason": "tool_loop_exceeded",
                    "error": (
                        f"subagent hit the {self._SUBAGENT_MAX_TOOL_CALLS}-"
                        "tool-call cap"
                    ),
                }

            turn_text: list[str] = []
            turn_tool_calls: list[ToolCall] = []
            try:
                stream = await client.astream(messages, tools=tool_specs)
                async for ev in stream:
                    if isinstance(ev, TextDelta):
                        turn_text.append(ev.text)
                    elif isinstance(ev, ToolCall):
                        turn_tool_calls.append(ev)
                    elif isinstance(ev, End):
                        # We don't surface ``finish_reason`` from End
                        # directly — the loop's own counters give the
                        # parent a stable contract regardless of which
                        # vendor's SDK we're behind.
                        pass
            except Exception as exc:  # noqa: BLE001 — vendor + transport errors
                logger.warning("subagent astream failed: %s", exc)
                return {
                    "text": ("".join(text_chunks + turn_text)).strip(),
                    "tool_calls_used": tool_calls_used,
                    "finish_reason": "llm_error",
                    "error": str(exc),
                }

            text_chunks.extend(turn_text)

            if not turn_tool_calls:
                # Model produced text and no tool calls → final answer.
                return {
                    "text": ("".join(text_chunks)).strip(),
                    "tool_calls_used": tool_calls_used,
                    "finish_reason": "stop",
                }

            # Append the assistant turn (with the tool_calls in
            # OpenAI-style envelope) and run each tool, then loop.
            openai_style = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in turn_tool_calls
            ]
            messages.append(
                _CM(
                    role="assistant",
                    content="".join(turn_text),
                    tool_calls=openai_style,
                )
            )
            for tc in turn_tool_calls:
                tool_calls_used += 1
                try:
                    result = await self.invoke(tc.name, tc.arguments)
                except ToolInvocationError as exc:
                    result = json.dumps({"error": str(exc)}, ensure_ascii=False)
                except Exception as exc:  # noqa: BLE001 — defensive
                    logger.exception(
                        "subagent tool %s failed", tc.name
                    )
                    result = json.dumps(
                        {"error": f"{tc.name} failed: {exc}"},
                        ensure_ascii=False,
                    )
                messages.append(
                    _CM(
                        role="tool",
                        name=tc.name,
                        tool_call_id=tc.id,
                        content=result,
                    )
                )

    # ------------------------------------------------------------------
    # Navigator memory recall surface (E17/ELS-128)
    # ------------------------------------------------------------------

    async def _tool_recall(self, args: dict[str, Any]) -> str:
        """Semantic search across the PO's mem0 facts.

        Returned JSON list keeps each item compact — the agent doesn't
        need the embedding or the full source-message body here,
        only enough to decide whether the fact is relevant and
        whether to drill into ``recall_context`` for the source.
        """
        from backend.app.services.agent import memory as navigator_memory

        query = _require_str(args, "query").strip()
        limit = _clamp_int(args.get("limit"), default=10, low=1, high=25)
        project_native_id_raw = args.get("project_native_id")
        if project_native_id_raw is not None and not isinstance(
            project_native_id_raw, str
        ):
            raise ToolInvocationError(
                "project_native_id must be a string when provided"
            )
        project_native_id = (
            project_native_id_raw.strip()
            if isinstance(project_native_id_raw, str)
            else None
        )
        hits = await navigator_memory.search(
            self._session,
            workspace_id=self._workspace_id,
            owner_user_id=self._user_id,
            query=query,
            project_native_id=project_native_id or None,
            limit=limit,
        )
        return _json_result(
            [
                {
                    "id": str(h.row.id),
                    "fact_text": h.row.fact_text,
                    "project_native_id": h.row.project_native_id,
                    "source_thread_id": str(h.row.source_thread_id)
                    if h.row.source_thread_id
                    else None,
                    "source_message_id": str(h.row.source_message_id)
                    if h.row.source_message_id
                    else None,
                    "captured_at": h.row.created_at.isoformat()
                    if h.row.created_at
                    else None,
                    "score": round(h.score, 3),
                }
                for h in hits
            ]
        )

    async def _tool_recall_context(self, args: dict[str, Any]) -> str:
        """Pull ±5 surrounding messages around a fact's source.

        The fact has to belong to the calling user — the
        access-control check happens via the same
        ``(owner_user_id, workspace_id)`` filter the rest of the
        memory surface uses. A foreign fact id returns ``not_found``
        rather than the surrounding context for someone else's chat.
        """
        from backend.app.db.models.agent_surface import ChatMessage as ChatMessageRow
        from backend.app.db.models.navigator_memory import NavigatorMemory

        fact_id_raw = _require_str(args, "fact_id").strip()
        try:
            fact_id = uuid.UUID(fact_id_raw)
        except (ValueError, TypeError):
            raise ToolInvocationError("fact_id must be a UUID string")

        fact = (
            await self._session.execute(
                select(NavigatorMemory).where(
                    NavigatorMemory.id == fact_id,
                    NavigatorMemory.owner_user_id == self._user_id,
                    NavigatorMemory.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if fact is None:
            return _json_result({"error": "not_found"})
        if fact.source_thread_id is None or fact.source_message_id is None:
            # Backfilled facts that lost their pointer when the
            # original chat was deleted (SET NULL cascade) — we have
            # the text but no neighbourhood to fetch.
            return _json_result(
                {
                    "fact_text": fact.fact_text,
                    "context_messages": [],
                    "note": "source thread / message no longer available",
                }
            )

        anchor = (
            await self._session.execute(
                select(ChatMessageRow).where(
                    ChatMessageRow.id == fact.source_message_id
                )
            )
        ).scalar_one_or_none()
        if anchor is None:
            return _json_result(
                {
                    "fact_text": fact.fact_text,
                    "context_messages": [],
                    "note": "source message deleted",
                }
            )

        # Pull 5 before + the anchor + 5 after by ``created_at``.
        before = (
            (
                await self._session.execute(
                    select(ChatMessageRow)
                    .where(
                        ChatMessageRow.thread_id == fact.source_thread_id,
                        ChatMessageRow.created_at < anchor.created_at,
                    )
                    .order_by(ChatMessageRow.created_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        after = (
            (
                await self._session.execute(
                    select(ChatMessageRow)
                    .where(
                        ChatMessageRow.thread_id == fact.source_thread_id,
                        ChatMessageRow.created_at > anchor.created_at,
                    )
                    .order_by(ChatMessageRow.created_at.asc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        window = list(reversed(before)) + [anchor] + list(after)
        return _json_result(
            {
                "fact_text": fact.fact_text,
                "source_message_id": str(anchor.id),
                "source_thread_id": str(fact.source_thread_id),
                "context_messages": [
                    {
                        "id": str(m.id),
                        "role": m.role,
                        "body": _truncate(m.body or "", 1000),
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                        "is_anchor": m.id == anchor.id,
                    }
                    for m in window
                ],
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


def _build_linear_tracker(token: str, config: dict[str, Any]) -> LinearTracker:
    """Construct a ``LinearTracker`` with the workspace's configured defaults.

    The Navigator's ``create_ticket`` tool dropped through to a naked
    ``LinearTracker(token)`` for both the native and legacy integration
    rows, which meant a workspace with multiple Linear teams got
    "Linear workspace has multiple teams; pass project_hint=..."
    every time the LLM forgot to pass a hint — even though the operator
    already configured the default team during OAuth probe. The
    Inbox/clarifications path (``tracker_resolver.py``) already wired
    these fields; this helper hoists the same shape so every Linear
    construction site reads from the same config keys.
    """
    return LinearTracker(
        token,
        team_id=config.get("team_id"),
        team_key=config.get("team_key"),
        label_id_by_stage=config.get("label_id_by_stage") or {},
        state_id_by_name=config.get("state_id_by_name") or {},
        signal_label_ids=config.get("signal_label_ids") or {},
    )


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
    if isinstance(tracker, JiraTracker):
        return "jira"
    if isinstance(tracker, GitHubIssuesTracker):
        return "github_issues"
    return "unknown"


def _duration_seconds(start_iso: Any, end_iso: Any) -> int | None:
    """Return ``(end - start)`` in whole seconds when both are ISO-8601.

    Used by :meth:`_tool_pr_get` to answer "how long did the
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
