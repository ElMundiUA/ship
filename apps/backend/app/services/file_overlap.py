"""File-coordination warnings for parallel dev runs (ELS-154 / A5)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Final

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.tenancy import AuditLog
from backend.app.integrations.gateway.code_host import PullRequestRef, RepoRef
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.services.ticket_ref import parse_ticket_refs_from_pr_title

log = logging.getLogger(__name__)

DEV_FILE_OVERLAP_WARNINGS_ENABLED: Final = "dev_file_overlap_warnings_enabled"


def dev_file_overlap_warnings_enabled(settings: dict | None) -> bool:
    """Per-workspace JSONB toggle (ELS-155); default off when absent."""
    return bool((settings or {}).get(DEV_FILE_OVERLAP_WARNINGS_ENABLED))


def file_overlap_warnings_active(
    settings: Settings,
    workspace_settings: dict | None,
) -> bool:
    """True when global config or workspace JSONB enables overlap checks."""
    return bool(
        settings.enable_file_overlap_warnings
        or dev_file_overlap_warnings_enabled(workspace_settings)
    )


_LOCKFILES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
    }
)
_MIGRATIONS_SEGMENT = "migrations/versions/"
_OPEN_PR_LIMIT_PER_REPO = 30


@dataclass(frozen=True, slots=True)
class FileOverlapResult:
    """Rendered warning + structured rows for audit / telemetry."""

    warning_markdown: str | None
    file_overlap_warnings: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _SiblingPr:
    ticket_ref: str
    pr_number: int
    repo_full_name: str
    pr_html_url: str
    paths: list[str]
    extra_ticket_refs: list[str]


async def _list_activated_repos(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[tuple[WorkspaceRepo, GitHubInstallation]]:
    rows = (
        await session.execute(
            select(WorkspaceRepo, GitHubInstallation)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == WorkspaceRepo.installation_id,
            )
            .where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.activated_at.is_not(None),
            )
            .order_by(WorkspaceRepo.activated_at.asc())
        )
    ).all()
    return [(r[0], r[1]) for r in rows]


def _repo_ref_from_full_name(full_name: str) -> RepoRef:
    owner, _, repo = full_name.partition("/")
    return RepoRef(kind="github", owner=owner, repo=repo)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_lockfile_path(path: str) -> bool:
    return _basename(path) in _LOCKFILES


def _is_schema_path(path: str) -> bool:
    return _MIGRATIONS_SEGMENT in path.replace("\\", "/")


def _classify_overlaps(
    siblings: list[_SiblingPr],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (structured warnings, hard-overlap path list)."""
    warnings: list[dict[str, Any]] = []
    path_counts: dict[str, int] = {}

    for sib in siblings:
        for path in sib.paths:
            if not path or _is_lockfile_path(path):
                continue
            path_counts[path] = path_counts.get(path, 0) + 1

    hard_paths = sorted(p for p, n in path_counts.items() if n >= 2)

    for sib in siblings:
        schema_paths = sorted({p for p in sib.paths if _is_schema_path(p)})
        if schema_paths:
            warnings.append(
                {
                    "sibling_ticket_ref": sib.ticket_ref,
                    "pr_number": sib.pr_number,
                    "repo": sib.repo_full_name,
                    "pr_html_url": sib.pr_html_url,
                    "overlap_kind": "schema",
                    "paths": schema_paths,
                }
            )

    if hard_paths:
        warnings.append(
            {
                "overlap_kind": "hard",
                "paths": hard_paths,
            }
        )

    return warnings, hard_paths


def _render_warning_markdown(
    siblings: list[_SiblingPr],
    *,
    structured: list[dict[str, Any]],
    hard_paths: list[str],
) -> str:
    lines: list[str] = [
        "> **File-coordination warning**:",
        ">",
    ]

    schema_siblings = [
        s
        for s in siblings
        if any(_is_schema_path(p) for p in s.paths)
    ]
    for sib in schema_siblings:
        schema_path = next((p for p in sib.paths if _is_schema_path(p)), "")
        idx = schema_path.find(_MIGRATIONS_SEGMENT)
        mig_dir = (
            schema_path[: idx + len(_MIGRATIONS_SEGMENT)]
            if idx >= 0
            else _MIGRATIONS_SEGMENT
        )
        lines.append(
            f"> PR #{sib.pr_number} ({sib.ticket_ref}) is OPEN and modifies "
            f"`{mig_dir}`. If your task also adds a migration, coordinate "
            f"revision numbers with that PR or rebase after it merges. "
            f"Do not independently add another conflicting migration file."
        )
        lines.append(">")

    if hard_paths:
        paths_fmt = ", ".join(f"`{p}`" for p in hard_paths)
        lines.append(
            f"> **Hard path overlap** across sibling open PRs in this project: "
            f"{paths_fmt}. WAIT — align with the other PR(s) before editing "
            f"these paths, or rebase after they merge."
        )
        lines.append(">")

    if len(lines) <= 3 and not structured:
        return ""
    # Drop trailing empty quote line when we only had header
    while lines and lines[-1] == ">":
        lines.pop()
    return "\n".join(lines)


async def build_file_coordination_warning(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    project_id: str | None,
    tracker_kind: str,
    snapshot_fn: Any,
    settings: Settings | None = None,
    workspace_settings: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> FileOverlapResult:
    """Discover sibling open PR overlaps; return blockquote markdown or None."""
    settings = settings or get_settings()
    if not file_overlap_warnings_active(settings, workspace_settings):
        return FileOverlapResult(warning_markdown=None, file_overlap_warnings=[])
    if not project_id:
        return FileOverlapResult(warning_markdown=None, file_overlap_warnings=[])

    repos = await _list_activated_repos(session, workspace_id=workspace_id)
    if not repos:
        return FileOverlapResult(warning_markdown=None, file_overlap_warnings=[])

    project_cache: dict[str, str | None] = {}
    siblings: list[_SiblingPr] = []

    async def _project_id_for_ref(ref: str) -> str | None:
        if ref in project_cache:
            return project_cache[ref]
        pid: str | None = None
        if snapshot_fn is not None:
            try:
                snap = await snapshot_fn(
                    TicketRef(kind=tracker_kind, workspace_hint=None, id=ref)
                )
                if snap:
                    pid = snap.get("project_id")
                    if pid is not None:
                        pid = str(pid)
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "file_overlap: snapshot failed ref=%s err=%s", ref, exc
                )
        project_cache[ref] = pid
        return pid

    for wr, install in repos:
        host = GitHubCodeHost(
            install.installation_id, settings=settings, client=client
        )
        repo_ref = _repo_ref_from_full_name(wr.full_name)
        try:
            open_prs = await host.list_open_pull_requests(
                repo_ref, limit=_OPEN_PR_LIMIT_PER_REPO
            )
        except Exception as exc:  # noqa: BLE001
            log.debug(
                "file_overlap: list open PRs failed repo=%s err=%s",
                wr.full_name,
                exc,
            )
            continue

        for pr in open_prs:
            title = str(pr.get("title") or "")
            refs = parse_ticket_refs_from_pr_title(title)
            if not refs:
                continue
            primary = refs[0]
            if primary == ticket_ref:
                continue
            sib_project = await _project_id_for_ref(primary)
            if sib_project != project_id:
                continue
            pr_number = int(pr.get("number") or 0)
            if not pr_number:
                continue
            try:
                files = await host.list_pull_request_files(
                    PullRequestRef(repo=repo_ref, number=pr_number),
                    limit=100,
                )
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "file_overlap: list PR files failed repo=%s pr=%s err=%s",
                    wr.full_name,
                    pr_number,
                    exc,
                )
                continue
            paths = [
                str(f.get("filename") or "")
                for f in files
                if f.get("filename")
            ]
            siblings.append(
                _SiblingPr(
                    ticket_ref=primary,
                    pr_number=pr_number,
                    repo_full_name=wr.full_name,
                    pr_html_url=str(pr.get("html_url") or ""),
                    paths=paths,
                    extra_ticket_refs=refs[1:],
                )
            )

    if not siblings:
        return FileOverlapResult(warning_markdown=None, file_overlap_warnings=[])

    structured, hard_paths = _classify_overlaps(siblings)
    if not structured:
        return FileOverlapResult(warning_markdown=None, file_overlap_warnings=[])

    md = _render_warning_markdown(
        siblings, structured=structured, hard_paths=hard_paths
    )
    if not md.strip():
        return FileOverlapResult(warning_markdown=None, file_overlap_warnings=[])

    return FileOverlapResult(
        warning_markdown=md,
        file_overlap_warnings=structured,
    )


async def load_file_coordination_warning_from_audit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
) -> str | None:
    """Return the warning stored on the latest ``agent_run.dispatch`` audit."""
    from sqlalchemy import desc

    row = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.dispatch",
                AuditLog.target_id == ticket_ref,
            )
            .order_by(desc(AuditLog.created_at))
            .limit(1)
        )
    ).scalars().first()
    if row is None or not isinstance(row.payload, dict):
        return None
    warn = row.payload.get("file_coordination_warning")
    return str(warn) if warn else None
