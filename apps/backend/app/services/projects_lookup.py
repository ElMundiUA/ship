"""Shared find-or-create-by-name flow for projects.

Used by:

  - the Navigator's ``ToolBox.find_or_create_project_by_name`` tool
    (chat-driven planning), and
  - the agent-runs HTTP endpoint that backs ``shipctl project
    find-or-create`` (routine-driven reviewer routing — daily
    tech-reviewer files Tech Debt findings, daily qa-reviewer files
    QA Debt, daily security-officer files Security).

Idempotent on case-insensitive name match against the tracker's
existing projects. On miss, creates the project and inserts the
companion ``WorkspaceProjectPriority`` row in state ``planning``
(the dashboard's Drafts bucket) so the project is visible to the
operator the moment the call returns — no extra round-trip needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.dashboard_priorities import WorkspaceProjectPriority
from backend.app.integrations.gateway.tracker import TrackerGateway
from backend.app.services.tracker_resolver import resolve_for_workspace


@dataclass(frozen=True)
class FindOrCreateResult:
    """Result row returned to the caller — same shape regardless of
    whether the project already existed or was just created."""

    project: dict[str, Any]
    created: bool


class ProjectsLookupError(Exception):
    """Raised when the tracker rejects the operation with a known
    error class (NotImplementedError / ValueError). The caller maps
    this to an HTTP 422 / tool-invocation-error."""


async def find_or_create_project_by_name(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    name: str,
    body: str,
    description: str | None = None,
    originating_thread_id: uuid.UUID | None = None,
    tracker: TrackerGateway | None = None,
) -> FindOrCreateResult:
    """Look up a project by name; create it if absent.

    Pass ``tracker`` when the caller already resolved one (Navigator's
    ToolBox does so for every tool call). Otherwise the helper resolves
    the workspace's bound tracker via ``resolve_for_workspace``.

    ``originating_thread_id`` stamps the Drafts priorities row so the
    dashboard can deep-link the operator back to the originating
    conversation (only used by Navigator). For agent-driven calls
    (routines) this stays ``None``.
    """
    name_clean = name.strip()
    if not name_clean:
        raise ProjectsLookupError("name must not be blank")
    body_clean = body.strip()
    if not body_clean:
        raise ProjectsLookupError("body must not be blank")

    if tracker is None:
        resolved = await resolve_for_workspace(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
        )
        if resolved is None:
            raise ProjectsLookupError("no_tracker_bound")
        tracker = resolved.gateway

    try:
        existing = await tracker.list_projects(limit=100)
    except NotImplementedError as exc:
        raise ProjectsLookupError(str(exc)) from exc
    except ValueError as exc:
        raise ProjectsLookupError(str(exc)) from exc

    wanted = name_clean.casefold()
    for project in existing:
        project_name = project.get("name") or ""
        if (
            isinstance(project_name, str)
            and project_name.casefold() == wanted
        ):
            return FindOrCreateResult(project=dict(project), created=False)

    try:
        created = await tracker.create_project(
            name=name_clean, body=body_clean, description=description
        )
    except NotImplementedError as exc:
        raise ProjectsLookupError(str(exc)) from exc
    except ValueError as exc:
        raise ProjectsLookupError(str(exc)) from exc

    project_native_id = str(created.get("id") or "")
    if project_native_id:
        await _ensure_drafts_priorities_row(
            session=session,
            workspace_id=workspace_id,
            project_native_id=project_native_id,
            originating_thread_id=originating_thread_id,
        )

    return FindOrCreateResult(project=dict(created), created=True)


async def _ensure_drafts_priorities_row(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    project_native_id: str,
    originating_thread_id: uuid.UUID | None,
) -> None:
    """Insert (or no-op) a Drafts-bucket priorities row.

    Idempotent on (workspace_id, project_native_id). New rows go to
    ``MAX(ordinal)+1`` so the project sorts after everything currently
    saved — operator can drag it up if it's high-priority.
    """
    existing = await session.scalar(
        select(WorkspaceProjectPriority).where(
            WorkspaceProjectPriority.workspace_id == workspace_id,
            WorkspaceProjectPriority.project_native_id == project_native_id,
        )
    )
    if existing is not None:
        if (
            existing.originating_thread_id is None
            and originating_thread_id is not None
        ):
            existing.originating_thread_id = originating_thread_id
            await session.flush()
        return

    max_ord = await session.scalar(
        select(WorkspaceProjectPriority.ordinal)
        .where(WorkspaceProjectPriority.workspace_id == workspace_id)
        .order_by(WorkspaceProjectPriority.ordinal.desc())
        .limit(1)
    )
    next_ord = 0 if max_ord is None else int(max_ord) + 1
    session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace_id,
            project_native_id=project_native_id,
            ordinal=next_ord,
            state="planning",
            originating_thread_id=originating_thread_id,
        )
    )
    await session.flush()


__all__ = [
    "FindOrCreateResult",
    "ProjectsLookupError",
    "find_or_create_project_by_name",
]
