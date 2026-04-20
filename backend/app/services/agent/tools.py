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
- :meth:`create_ticket` — open a ticket on the workspace's connected
  tracker (Linear / Notion / GitHub Issues).
- :meth:`create_artifact_feedback` — file feedback against a catalog
  artifact id (``pattern/cloud-base``, …). Persisted to
  :class:`ArtifactFeedback` for the console feedback tab.
- :meth:`list_recent_activity` — last N pipeline runs / PR / workflow
  events for the workspace, so the agent can ground "what's going
  on?" answers without hitting GitHub live.
- :meth:`search_buckets` — vector search over :class:`BucketSummary`
  so the agent can recall previously-packed conversations.

The JSON schemas live next to each method (single source of truth,
no drift). Vendors that can't consume a method share the same
schema; the dispatch layer (:meth:`ToolBox.invoke`) lives here.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.agent_memory import (
    ArtifactFeedback,
    BucketSummary,
    KbChunk,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.pipelines import (
    PipelineRun,
    PullRequest,
    WorkflowRun,
)
from backend.app.db.models.tenancy import Integration
from backend.app.integrations.gateway.code_host import RepoRef
from backend.app.integrations.gateway.tracker import (
    CreatedTicket,
    TrackerGateway,
)
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.integrations.github.issues_tracker import GitHubIssuesTracker
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.integrations.notion.tracker_adapter import NotionTracker
from backend.app.security.encryption import decrypt
from backend.app.services.agent.client import ToolSpec
from backend.app.services.agent.embedding import embed_text


logger = logging.getLogger(__name__)


# Defensive caps so a malformed tool call can't eat the whole context
# window with kilobytes of file / KB chunks. Tuned to leave ~20k
# tokens of headroom for the turn around them.
_MAX_FILE_BYTES_RETURNED = 64 * 1024
_MAX_KB_RESULTS = 8
_MAX_CODE_MAP_ENTRIES = 1500
_MAX_ACTIVITY_ITEMS = 20
_MAX_BUCKET_RESULTS = 8


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
                    "the top-N matching chunks with path + snippet."
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
                    "verbatim code."
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
                    "unfamiliar codebase before `get_repo_file`."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_id": {
                            "type": "string",
                            "description": "UUID of the activated repo.",
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
                    "(pattern/workflow/collection). Visible in the console "
                    "'Feedback' tab; used to drive catalog improvements."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "artifact_id": {
                            "type": "string",
                            "description": (
                                "Artifact identifier, e.g. "
                                "'pattern/cloud-base' or 'workflow/pr-gate'."
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
                    "'what happened recently?'."
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
            "create_ticket": self._tool_create_ticket,
            "create_artifact_feedback": self._tool_create_artifact_feedback,
            "list_recent_activity": self._tool_list_recent_activity,
            "search_buckets": self._tool_search_buckets,
        }

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _tool_search_repo_kb(self, args: dict[str, Any]) -> str:
        query = _require_str(args, "query")
        limit = _clamp_int(args.get("limit"), default=5, low=1, high=_MAX_KB_RESULTS)
        repo_id_raw = args.get("repo_id")
        repo_id: uuid.UUID | None = None
        if repo_id_raw:
            try:
                repo_id = uuid.UUID(str(repo_id_raw))
            except ValueError as exc:
                raise ToolInvocationError(f"invalid repo_id: {repo_id_raw!r}") from exc

        qvec = await embed_text(query, settings=self._settings)
        stmt = (
            select(KbChunk, KbChunk.embedding.cosine_distance(qvec).label("dist"))
            .where(KbChunk.workspace_id == self._workspace_id)
            .order_by("dist")
            .limit(limit)
        )
        if repo_id is not None:
            stmt = stmt.where(KbChunk.repo_id == repo_id)

        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return _json_result({"results": [], "note": "no knowledge indexed"})

        results = []
        for chunk, dist in rows:
            results.append(
                {
                    "repo_id": str(chunk.repo_id),
                    "path": chunk.source_path,
                    "chunk_index": chunk.chunk_index,
                    "content_sha": chunk.content_sha,
                    "snippet": _truncate(chunk.content, 800),
                    "similarity": round(1.0 - float(dist), 4),
                }
            )
        return _json_result({"results": results})

    async def _tool_get_repo_file(self, args: dict[str, Any]) -> str:
        repo_id = _parse_uuid(args, "repo_id")
        path = _require_str(args, "path")
        ref_sha = args.get("ref_sha")

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
                "truncated": truncated,
                "content": content,
            }
        )

    async def _tool_list_code_map(self, args: dict[str, Any]) -> str:
        repo_id = _parse_uuid(args, "repo_id")
        repo, install = await self._resolve_repo_with_install(repo_id)
        gateway = GitHubCodeHost(install.installation_id, settings=self._settings)
        owner, _, name = repo.full_name.partition("/")
        ref = RepoRef(kind="github", owner=owner, repo=name)
        files = await gateway.list_files(ref, ref_sha=repo.default_branch)
        truncated = len(files) > _MAX_CODE_MAP_ENTRIES
        return _json_result(
            {
                "repo_id": str(repo.id),
                "full_name": repo.full_name,
                "default_branch": repo.default_branch,
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

        out: list[dict[str, Any]] = []

        if "pipeline_run" in kinds:
            rows = (
                await self._session.execute(
                    select(PipelineRun)
                    .where(PipelineRun.workspace_id == self._workspace_id)
                    .order_by(desc(PipelineRun.created_at))
                    .limit(limit)
                )
            ).scalars().all()
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
            rows = (
                await self._session.execute(
                    select(PullRequest)
                    .where(PullRequest.workspace_id == self._workspace_id)
                    .order_by(desc(PullRequest.updated_at))
                    .limit(limit)
                )
            ).scalars().all()
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
            rows = (
                await self._session.execute(
                    select(WorkflowRun)
                    .where(WorkflowRun.workspace_id == self._workspace_id)
                    .order_by(desc(WorkflowRun.updated_at))
                    .limit(limit)
                )
            ).scalars().all()
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

        # Merge sort by timestamp across kinds — newest first — then
        # truncate. This gives the agent a unified activity feed
        # rather than N separate tabs.
        out.sort(
            key=lambda x: x.get("updated_at") or x.get("created_at") or "",
            reverse=True,
        )
        return _json_result({"items": out[:limit]})

    async def _tool_search_buckets(self, args: dict[str, Any]) -> str:
        query = _require_str(args, "query")
        limit = _clamp_int(
            args.get("limit"), default=4, low=1, high=_MAX_BUCKET_RESULTS
        )
        qvec = await embed_text(query, settings=self._settings)

        stmt = (
            select(
                BucketSummary,
                KnowledgeBucket.slug,
                KnowledgeBucket.name,
                BucketSummary.embedding.cosine_distance(qvec).label("dist"),
            )
            .join(
                KnowledgeBucket,
                KnowledgeBucket.id == BucketSummary.bucket_id,
            )
            .where(KnowledgeBucket.workspace_id == self._workspace_id)
            .where(KnowledgeBucket.archived_at.is_(None))
            .order_by("dist")
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        results = []
        for summary, slug, name, dist in rows:
            results.append(
                {
                    "bucket_slug": slug,
                    "bucket_name": name,
                    "title": summary.title,
                    "summary": _truncate(summary.summary, 600),
                    "similarity": round(1.0 - float(dist), 4),
                }
            )
        return _json_result({"results": results})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _json_result(payload: Any) -> str:
    # Tool results round-trip as strings; JSON keeps structure usable
    # by the LLM without a second-round parse. ``ensure_ascii=False``
    # preserves non-ASCII source content verbatim (paths, titles).
    import json

    return json.dumps(payload, ensure_ascii=False)


__all__ = ["ToolBox", "ToolInvocationError"]
