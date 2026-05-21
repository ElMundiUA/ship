"""File-overlap warning telemetry (ELS-156 / A5.3).

Audit rows for dispatch warnings and dev-finish honour/ignore evaluation.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog
from backend.app.integrations.gateway.code_host import PullRequestRef, RepoRef
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost

log = logging.getLogger(__name__)

ACTION_WARNING: Final = "agent_dispatch.file_overlap_warning"
ACTION_HONOURED: Final = "agent_dispatch.file_overlap_honoured"
ACTION_IGNORED: Final = "agent_dispatch.file_overlap_ignored"
ACTION_HONOUR_SKIPPED: Final = "agent_dispatch.file_overlap_honour_skipped"

_HONOUR_OUTCOMES: Final = frozenset({ACTION_HONOURED, ACTION_IGNORED})

_PR_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/pull/(?P<num>\d+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WeeklyFileOverlapMetrics:
    warnings_fired: int
    honoured: int
    ignored: int
    honour_rate: float | None


def normalize_repo_path(path: str) -> str:
    """Repo-relative POSIX path; strip leading ``./``."""
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def paths_from_pr_file_entries(files: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for item in files:
        filename = item.get("filename")
        if filename:
            out.add(normalize_repo_path(str(filename)))
        previous = item.get("previous_filename")
        if previous:
            out.add(normalize_repo_path(str(previous)))
    return out


def intersect_warned_paths(
    warned_paths: set[str], pr_paths: set[str]
) -> list[str]:
    return sorted(warned_paths & pr_paths)


def parse_github_pr_url(url: str) -> tuple[str, str, int] | None:
    match = _PR_URL_RE.search(url)
    if not match:
        return None
    return match.group("owner"), match.group("repo"), int(match.group("num"))


def _severity_for_overlap_kind(overlap_kind: str) -> str:
    return "hard" if overlap_kind == "hard" else "soft"


def emit_file_overlap_warnings(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    project_id: str,
    run_id: str,
    warnings: list[dict[str, Any]],
) -> int:
    """Write one ``agent_dispatch.file_overlap_warning`` row per structured warning."""
    emitted = 0
    for warning in warnings:
        raw_paths = warning.get("paths") or []
        conflicted = [
            normalize_repo_path(str(p)) for p in raw_paths if str(p).strip()
        ]
        if not conflicted:
            continue
        pr_number = warning.get("pr_number")
        sibling_numbers: list[int] = []
        if pr_number is not None:
            sibling_numbers.append(int(pr_number))
        overlap_kind = str(warning.get("overlap_kind") or "unknown")
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action=ACTION_WARNING,
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "ticket_ref": ticket_ref,
                    "sibling_pr_number": sibling_numbers[0] if sibling_numbers else None,
                    "sibling_pr_numbers": sibling_numbers,
                    "overlap_kind": overlap_kind,
                    "conflicted_paths": conflicted,
                    "run_id": run_id,
                    "project_id": project_id,
                    "severity": _severity_for_overlap_kind(overlap_kind),
                },
            )
        )
        emitted += 1
    return emitted


async def _load_warning_rows(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    run_id: str,
) -> list[AuditLog]:
    rows = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == ACTION_WARNING,
                AuditLog.target_id == ticket_ref,
                AuditLog.payload["run_id"].astext == run_id,
            )
        )
    ).scalars().all()
    return list(rows)


def _union_warned_paths(warnings: list[AuditLog]) -> tuple[frozenset[str], list[int]]:
    paths: set[str] = set()
    sibling_numbers: set[int] = set()
    for row in warnings:
        payload = row.payload if isinstance(row.payload, dict) else {}
        for path in payload.get("conflicted_paths") or []:
            if path:
                paths.add(normalize_repo_path(str(path)))
        for num in payload.get("sibling_pr_numbers") or []:
            sibling_numbers.add(int(num))
        single = payload.get("sibling_pr_number")
        if single is not None:
            sibling_numbers.add(int(single))
    return frozenset(paths), sorted(sibling_numbers)


async def _honour_already_recorded(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    run_id: str,
) -> bool:
    row = (
        await session.execute(
            select(AuditLog.id).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action.in_(tuple(_HONOUR_OUTCOMES)),
                AuditLog.payload["run_id"].astext == run_id,
            ).limit(1)
        )
    ).first()
    return row is not None


async def _resolve_github_install_for_repo(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_full_name: str,
) -> tuple[WorkspaceRepo, GitHubInstallation] | None:
    row = (
        await session.execute(
            select(WorkspaceRepo, GitHubInstallation)
            .join(
                GitHubInstallation,
                WorkspaceRepo.installation_id == GitHubInstallation.id,
            )
            .where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.full_name == repo_full_name,
                WorkspaceRepo.provider == "github",
                WorkspaceRepo.activated_at.is_not(None),
            )
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]


async def evaluate_file_overlap_honour(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    run_id: str,
    fsm_stage: str | None,
    comment: str | None,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Emit honoured/ignored/skipped audit rows when a dev run finishes with a PR."""
    if fsm_stage != "dev_implementation":
        return None
    if not comment or not _PR_URL_RE.search(comment):
        return None
    if await _honour_already_recorded(
        session, workspace_id=workspace_id, run_id=run_id
    ):
        return None

    warnings = await _load_warning_rows(
        session,
        workspace_id=workspace_id,
        ticket_ref=ticket_ref,
        run_id=run_id,
    )
    if not warnings:
        return None

    parsed = parse_github_pr_url(comment)
    if parsed is None:
        return None
    owner, repo, pr_number = parsed
    repo_full_name = f"{owner}/{repo}"

    install_row = await _resolve_github_install_for_repo(
        session,
        workspace_id=workspace_id,
        repo_full_name=repo_full_name,
    )
    if install_row is None:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action=ACTION_HONOUR_SKIPPED,
                target_kind="ticket",
                target_id=ticket_ref,
                payload={"run_id": run_id, "reason": "no_code_host"},
            )
        )
        return ACTION_HONOUR_SKIPPED

    _repo_row, install = install_row
    gateway = GitHubCodeHost(
        install.installation_id, settings=settings, client=client
    )
    try:
        files = await gateway.list_pull_request_files(
            PullRequestRef(
                repo=RepoRef(kind="github", owner=owner, repo=repo),
                number=pr_number,
            )
        )
    except Exception as exc:  # noqa: BLE001 — must not block finish
        log.warning(
            "file_overlap_telemetry: list_pull_request_files failed "
            "ws=%s ticket=%s pr=%s err=%s",
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
        return ACTION_HONOUR_SKIPPED

    warned_paths, sibling_numbers = _union_warned_paths(warnings)
    pr_paths = paths_from_pr_file_entries(files)
    touched = intersect_warned_paths(set(warned_paths), pr_paths)
    pr_url = f"https://github.com/{repo_full_name}/pull/{pr_number}"
    base_payload: dict[str, Any] = {
        "run_id": run_id,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "warned_paths": sorted(warned_paths),
        "sibling_pr_numbers": sibling_numbers,
    }

    if touched:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action=ACTION_IGNORED,
                target_kind="ticket",
                target_id=ticket_ref,
                payload={**base_payload, "touched_paths": touched},
            )
        )
        return ACTION_IGNORED

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            action=ACTION_HONOURED,
            target_kind="ticket",
            target_id=ticket_ref,
            payload=base_payload,
        )
    )
    return ACTION_HONOURED


async def weekly_file_overlap_metrics(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    days: int = 7,
) -> WeeklyFileOverlapMetrics:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await session.execute(
            select(AuditLog.action, AuditLog.id).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action.in_(
                    (ACTION_WARNING, ACTION_HONOURED, ACTION_IGNORED)
                ),
                AuditLog.created_at >= cutoff,
            )
        )
    ).all()
    warnings = honoured = ignored = 0
    for action, _ in rows:
        if action == ACTION_WARNING:
            warnings += 1
        elif action == ACTION_HONOURED:
            honoured += 1
        elif action == ACTION_IGNORED:
            ignored += 1
    denom = honoured + ignored
    honour_rate = (honoured / denom) if denom else None
    return WeeklyFileOverlapMetrics(
        warnings_fired=warnings,
        honoured=honoured,
        ignored=ignored,
        honour_rate=honour_rate,
    )
