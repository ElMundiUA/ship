"""File-overlap warning telemetry (ELS-156 / A5.3)."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.tenancy import AuditLog
from backend.app.integrations.gateway.code_host import PullRequestRef, RepoRef
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost

log = logging.getLogger(__name__)

ACTION_WARNING = "agent_dispatch.file_overlap_warning"
ACTION_HONOURED = "agent_dispatch.file_overlap_honoured"
ACTION_IGNORED = "agent_dispatch.file_overlap_ignored"
ACTION_HONOUR_SKIPPED = "agent_dispatch.file_overlap_honour_skipped"
ACTION_SKIPPED = "dispatch.file_overlap_skipped"

_PR_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)

_HONOUR_OUTCOMES = frozenset(
    {ACTION_HONOURED, ACTION_IGNORED, ACTION_HONOUR_SKIPPED}
)


def normalize_repo_path(path: str | None) -> str:
    """Repo-relative POSIX path for set intersection."""
    if not path:
        return ""
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def paths_from_pr_files(files: list[dict[str, Any]]) -> set[str]:
    """Current and previous filenames from GitHub PR file entries."""
    out: set[str] = set()
    for item in files:
        for key in ("filename", "previous_filename"):
            raw = item.get(key)
            if isinstance(raw, str) and raw.strip():
                norm = normalize_repo_path(raw)
                if norm:
                    out.add(norm)
    return out


def _severity_for_overlap_kind(overlap_kind: str) -> str:
    if overlap_kind == "hard":
        return "high"
    if overlap_kind == "schema":
        return "medium"
    return "low"


def _conflicted_paths_from_warning_row(row: dict[str, Any]) -> list[str]:
    paths = row.get("conflicted_paths") or row.get("paths") or []
    if not isinstance(paths, list):
        return []
    return [normalize_repo_path(str(p)) for p in paths if p]


def emit_overlap_warnings(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    project_id: str | None,
    run_id: str | None,
    structured_warnings: list[dict[str, Any]],
) -> None:
    """One ``agent_dispatch.file_overlap_warning`` row per sibling overlap."""
    for entry in structured_warnings:
        paths_raw = entry.get("paths") or entry.get("conflicted_paths") or []
        if not isinstance(paths_raw, list):
            paths_raw = []
        conflicted = [
            normalize_repo_path(str(p))
            for p in paths_raw
            if p and normalize_repo_path(str(p))
        ]
        if not conflicted:
            continue
        overlap_kind = str(entry.get("overlap_kind") or "unknown")
        sibling_pr_number = entry.get("pr_number") or entry.get("sibling_pr_number")
        try:
            sibling_pr_number = int(sibling_pr_number) if sibling_pr_number is not None else None
        except (TypeError, ValueError):
            sibling_pr_number = None
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action=ACTION_WARNING,
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "ticket_ref": ticket_ref,
                    "sibling_pr_number": sibling_pr_number,
                    "sibling_ticket_ref": entry.get("sibling_ticket_ref"),
                    "overlap_kind": overlap_kind,
                    "conflicted_paths": conflicted,
                    "run_id": run_id,
                    "project_id": project_id,
                    "severity": _severity_for_overlap_kind(overlap_kind),
                },
            )
        )


async def _latest_dispatch_at(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
) -> datetime | None:
    row = (
        await session.execute(
            select(AuditLog.created_at)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.dispatch",
                AuditLog.target_id == ticket_ref,
            )
            .order_by(desc(AuditLog.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def _load_warnings_for_finish(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    run_id: str,
) -> list[AuditLog]:
    """Warnings for this finish: exact ``run_id`` or unpaired since last dispatch."""
    prior = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action.in_(tuple(_HONOUR_OUTCOMES)),
                AuditLog.target_id == ticket_ref,
            )
        )
    ).scalars().all()
    for row in prior:
        payload = row.payload if isinstance(row.payload, dict) else {}
        if payload.get("run_id") == run_id:
            return []

    dispatch_at = await _latest_dispatch_at(
        session, workspace_id=workspace_id, ticket_ref=ticket_ref
    )

    stmt = (
        select(AuditLog)
        .where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.action == ACTION_WARNING,
            AuditLog.target_id == ticket_ref,
        )
        .order_by(AuditLog.created_at.asc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    matched: list[AuditLog] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        row_run_id = payload.get("run_id")
        if row_run_id == run_id:
            matched.append(row)
            continue
        if row_run_id is not None:
            continue
        if dispatch_at is not None and row.created_at < dispatch_at:
            continue
        matched.append(row)
    return matched


def _warned_paths_from_rows(rows: list[AuditLog]) -> tuple[list[str], list[int | None]]:
    warned: set[str] = set()
    siblings: list[int | None] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        for p in _conflicted_paths_from_warning_row(payload):
            warned.add(p)
        num = payload.get("sibling_pr_number")
        try:
            siblings.append(int(num) if num is not None else None)
        except (TypeError, ValueError):
            siblings.append(None)
    return sorted(warned), siblings


def evaluate_honour(
    warned_paths: list[str],
    pr_paths: set[str],
) -> tuple[Literal["honoured", "ignored"], list[str]]:
    warned_set = {normalize_repo_path(p) for p in warned_paths if p}
    touched = sorted(warned_set & pr_paths)
    if touched:
        return "ignored", touched
    return "honoured", []


async def _resolve_pr_repo(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner: str,
    repo: str,
    settings: Settings,
    client: httpx.AsyncClient | None,
) -> tuple[WorkspaceRepo, GitHubInstallation] | None:
    full_name = f"{owner}/{repo}"
    row = (
        await session.execute(
            select(WorkspaceRepo, GitHubInstallation)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == WorkspaceRepo.installation_id,
            )
            .where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.full_name == full_name,
                WorkspaceRepo.activated_at.is_not(None),
            )
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]


async def record_honour_on_dev_finish(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    run_id: str,
    fsm_stage: str | None,
    comment: str | None,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Emit honoured / ignored / skipped after dev finish with a PR URL."""
    if fsm_stage != "dev_implementation":
        return
    if not comment:
        return
    match = _PR_URL_RE.search(comment)
    if not match:
        return

    warning_rows = await _load_warnings_for_finish(
        session,
        workspace_id=workspace_id,
        ticket_ref=ticket_ref,
        run_id=run_id,
    )
    if not warning_rows:
        return

    warned_paths, sibling_numbers = _warned_paths_from_rows(warning_rows)
    if not warned_paths:
        return

    settings = settings or get_settings()
    owner = match.group("owner")
    repo = match.group("repo")
    pr_number = int(match.group("number"))
    pr_url = match.group(0)

    resolved = await _resolve_pr_repo(
        session,
        workspace_id=workspace_id,
        owner=owner,
        repo=repo,
        settings=settings,
        client=client,
    )
    if resolved is None:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action=ACTION_HONOUR_SKIPPED,
                target_kind="ticket",
                target_id=ticket_ref,
                payload={"run_id": run_id, "reason": "no_code_host"},
            )
        )
        return

    ws_repo, install = resolved
    repo_ref = RepoRef(kind="github", owner=owner, repo=repo)
    host = GitHubCodeHost(
        installation_id=install.installation_id,
        settings=settings,
        client=client,
    )
    try:
        files = await host.list_pull_request_files(
            PullRequestRef(repo=repo_ref, number=pr_number),
            limit=100,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "file_overlap_honour: list PR files failed ws=%s ticket=%s pr=%s err=%s",
            workspace_id,
            ticket_ref,
            pr_number,
            exc,
        )
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action=ACTION_HONOUR_SKIPPED,
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "run_id": run_id,
                    "reason": "pr_files_fetch_failed",
                    "pr_number": pr_number,
                },
            )
        )
        return

    pr_paths = paths_from_pr_files(files)
    outcome, touched = evaluate_honour(warned_paths, pr_paths)
    sibling_nums = sorted({n for n in sibling_numbers if n is not None})
    base_payload: dict[str, Any] = {
        "run_id": run_id,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "warned_paths": warned_paths,
        "sibling_pr_numbers": sibling_nums,
        "repo": ws_repo.full_name,
    }
    if outcome == "honoured":
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action=ACTION_HONOURED,
                target_kind="ticket",
                target_id=ticket_ref,
                payload=base_payload,
            )
        )
    else:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action=ACTION_IGNORED,
                target_kind="ticket",
                target_id=ticket_ref,
                payload={**base_payload, "touched_paths": touched},
            )
        )


async def weekly_file_overlap_metrics(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    days: int = 7,
) -> dict[str, Any]:
    """Counts for the last ``days`` days and honour_rate."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    counts: dict[str, int] = {
        ACTION_WARNING: 0,
        ACTION_HONOURED: 0,
        ACTION_IGNORED: 0,
    }
    rows = (
        await session.execute(
            select(AuditLog.action, func.count())
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.created_at >= since,
                AuditLog.action.in_(
                    (
                        ACTION_WARNING,
                        ACTION_HONOURED,
                        ACTION_IGNORED,
                    )
                ),
            )
            .group_by(AuditLog.action)
        )
    ).all()
    for action, cnt in rows:
        counts[str(action)] = int(cnt)
    honoured = counts[ACTION_HONOURED]
    ignored = counts[ACTION_IGNORED]
    denom = honoured + ignored
    honour_rate: float | None = (honoured / denom) if denom else None
    return {
        "window_days": days,
        "warnings_fired": counts[ACTION_WARNING],
        "honoured": honoured,
        "ignored": ignored,
        "honour_rate": honour_rate,
    }
