"""Tracker-backed work-in-progress rows for the ops dashboard.

Uses the same per-repo effective binding as ``GET .../repos/{id}/tracker``:
``linear`` | ``github`` | ``jira`` (workspace default falls back when the repo
has no row). Listing goes through :class:`ToolBox` so native OAuth + GitHub App
paths match the agent / process ticket picker.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.routes.tracker_binding import (
    _repo_binding,
    _workspace_default_kind,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.tenancy import Integration
from backend.app.services.agent.tools import ToolBox, ToolInvocationError

TRACKER_DISPLAY: dict[str, str] = {
    "linear": "Linear",
    "github": "GitHub Issues",
    "jira": "Jira",
}


@dataclass(frozen=True, slots=True)
class TrackerWipCandidate:
    """Normalised ticket row before optional PR enrichment."""

    name: str
    status: Literal["in_progress", "review"]
    repo_full_name: str | None
    updated_at: datetime
    href: str | None
    ticket_ref: str | None
    tracker_kind: str
    board_column: str | None
    active_agent: str | None = None


def tracker_label(kind: str) -> str:
    return TRACKER_DISPLAY.get(kind, kind.replace("_", " ").title())


def _parse_updated_at(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        s = raw.strip().replace("Z", "+00:00")
        if s.endswith("+0000"):
            s = s[:-5] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def workspace_has_tracker_binding(
    session: AsyncSession, workspace_id: uuid.UUID
) -> bool:
    """True if any activated repo or the workspace default selects a ticket router."""
    ws_kind, _ = await _workspace_default_kind(session, workspace_id)
    if ws_kind is not None:
        return True
    row = (
        await session.execute(
            select(Integration.id).where(
                Integration.workspace_id == workspace_id,
                Integration.repo_id.is_not(None),
                Integration.kind.in_(("linear", "github", "jira")),
            ).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def effective_tracker_for_repo(
    session: AsyncSession, workspace_id: uuid.UUID, repo_id: uuid.UUID
) -> tuple[str | None, dict]:
    row = await _repo_binding(session, workspace_id, repo_id)
    if row is not None:
        return row.kind, row.config or {}
    return await _workspace_default_kind(session, workspace_id)


async def collect_tracker_wip_candidates(
    session: AsyncSession,
    settings: Any,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    cutoff_wip: datetime,
    listing_cap: int = 15,
) -> list[TrackerWipCandidate]:
    repos = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.installation_id.is_not(None),
            )
        )
    ).scalars().all()

    by_repo_kind: dict[uuid.UUID, tuple[str | None, dict]] = {}
    for repo in repos:
        by_repo_kind[repo.id] = await effective_tracker_for_repo(
            session, workspace_id, repo.id
        )

    want_linear = any(k == "linear" for k, _ in by_repo_kind.values())
    want_jira = any(k == "jira" for k, _ in by_repo_kind.values())
    if not want_linear or not want_jira:
        ws_kind, _ = await _workspace_default_kind(session, workspace_id)
        if not want_linear and ws_kind == "linear":
            want_linear = True
        if not want_jira and ws_kind == "jira":
            want_jira = True

    toolbox = ToolBox(
        session,
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
    )

    candidates: list[TrackerWipCandidate] = []
    seen: set[tuple[str, str | None]] = set()

    def _add(
        *,
        tracker_kind: str,
        ticket_id: str | None,
        title: str | None,
        url: str | None,
        status_name: str | None,
        updated_raw: Any,
        repo_full_name: str | None,
    ) -> None:
        updated_at = _parse_updated_at(updated_raw)
        if updated_at is None or updated_at < cutoff_wip:
            return
        ref = (str(ticket_id).strip() if ticket_id else None) or None
        dedupe = (tracker_kind, ref or (url or "") or title or "")
        if dedupe in seen:
            return
        seen.add(dedupe)
        clean_title = (title or "").strip() or (ref or "Ticket")
        name = f"{ref}: {clean_title}" if ref else clean_title
        candidates.append(
            TrackerWipCandidate(
                name=name,
                status="in_progress",
                repo_full_name=repo_full_name,
                updated_at=updated_at,
                href=url,
                ticket_ref=ref,
                tracker_kind=tracker_kind,
                board_column=(status_name or "").strip() or None,
            )
        )

    for repo in repos:
        kind, _cfg = by_repo_kind[repo.id]
        if kind != "github":
            continue
        try:
            raw = await toolbox._tool_list_tickets(  # noqa: SLF001
                {
                    "tracker": "github_issues",
                    "project_hint": repo.full_name,
                    "state": "open",
                    "limit": listing_cap,
                }
            )
        except ToolInvocationError:
            continue
        try:
            tickets = json.loads(raw).get("tickets") or []
        except json.JSONDecodeError:
            continue
        for t in tickets:
            _add(
                tracker_kind="github",
                ticket_id=t.get("id"),
                title=t.get("title"),
                url=t.get("url"),
                status_name=t.get("status"),
                updated_raw=t.get("updated_at"),
                repo_full_name=repo.full_name,
            )

    if want_linear:
        try:
            raw = await toolbox._tool_list_tickets(  # noqa: SLF001
                {
                    "tracker": "linear",
                    "state": "open",
                    "limit": listing_cap,
                }
            )
        except ToolInvocationError:
            raw = '{"tickets":[]}'
        try:
            tickets = json.loads(raw).get("tickets") or []
        except json.JSONDecodeError:
            tickets = []
        anchor_repo = next((r.full_name for r in repos), None)
        for t in tickets:
            _add(
                tracker_kind="linear",
                ticket_id=t.get("id"),
                title=t.get("title"),
                url=t.get("url"),
                status_name=t.get("status"),
                updated_raw=t.get("updated_at"),
                repo_full_name=anchor_repo,
            )

    if want_jira:
        try:
            raw = await toolbox._tool_list_tickets(  # noqa: SLF001
                {
                    "tracker": "jira",
                    "state": "open",
                    "limit": listing_cap,
                }
            )
        except ToolInvocationError:
            raw = '{"tickets":[]}'
        try:
            tickets = json.loads(raw).get("tickets") or []
        except json.JSONDecodeError:
            tickets = []
        anchor_repo = next((r.full_name for r in repos), None)
        for t in tickets:
            _add(
                tracker_kind="jira",
                ticket_id=t.get("id"),
                title=t.get("title"),
                url=t.get("url"),
                status_name=t.get("status"),
                updated_raw=t.get("updated_at"),
                repo_full_name=anchor_repo,
            )

    candidates.sort(key=lambda c: c.updated_at, reverse=True)
    return candidates


__all__ = [
    "TRACKER_DISPLAY",
    "TrackerWipCandidate",
    "collect_tracker_wip_candidates",
    "effective_tracker_for_repo",
    "workspace_has_tracker_binding",
    "tracker_label",
]
