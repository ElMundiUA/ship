"""Auto-complete projects whose every ticket is done (2026-05-21).

When the last open child ticket of a project finishes, the project
itself should drop out of the operator's live prioritizer into a
collapsed History section and the Linear project should move to
Completed so it stops cluttering the tracker. Conversely, if a ``done``
project gains a fresh ticket (or a Done child reopens) it must come back
to ``active`` — otherwise the new work is invisible to the picker.

Both directions are evaluated per project by
:func:`evaluate_project_completion`:

* **complete** — every child ticket is terminal (Done / Canceled) and
  at least one is Done → Linear project → Completed, priority row →
  ``done`` + ``completed_at=now``. An empty project (no tickets) or one
  whose anchor is still open (anchor never reaches Done) is left alone.
* **reopen** — the priority row is ``done`` but the project has a
  non-terminal ticket again → priority row → ``active``,
  ``completed_at`` cleared.

The Linear flip is best-effort (cosmetic); the priority-row state is the
load-bearing signal the dashboard + picker read, so a tracker hiccup
never blocks it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.dashboard_priorities import WorkspaceProjectPriority
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_sessionmaker

log = logging.getLogger(__name__)

# States the backstop sweep evaluates. ``planning`` (Drafts) is excluded
# — a project still being shaped must never auto-complete. ``active`` /
# ``parked`` are checked for completion; ``done`` for reopen.
_SWEEP_STATES: tuple[str, ...] = ("active", "parked", "done")
# Per-tick cap so a workspace with hundreds of projects doesn't fan out
# into hundreds of get_project calls in one sweep.
_SWEEP_MAX_PROJECTS_PER_WS = 100

# Linear workflow-state names that count as "this ticket is finished".
_TERMINAL_STATES: frozenset[str] = frozenset({"Done", "Canceled", "Cancelled"})
_DONE_STATES: frozenset[str] = frozenset({"Done"})

EvaluateResult = Literal["completed", "reopened", "noop"]


async def evaluate_project_completion(
    session: AsyncSession,
    *,
    workspace_id,
    gateway: Any,
    project_id: str,
) -> EvaluateResult:
    """Complete or reopen one project based on its tickets' states.

    Best-effort and idempotent: safe to call from the merge webhook
    (fast path for the just-merged ticket's project) and from the cron
    backstop (sweeps every project). Any tracker error degrades to
    ``noop`` without raising."""
    get_project = getattr(gateway, "get_project", None)
    if get_project is None or not project_id:
        return "noop"
    try:
        project = await get_project(str(project_id), issues_limit=50)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.debug(
            "project_completion: get_project failed pid=%s err=%s",
            project_id, exc,
        )
        return "noop"
    issues = (project or {}).get("issues") or []
    states = [str(i.get("state") or "") for i in issues]
    all_terminal = bool(states) and all(s in _TERMINAL_STATES for s in states)
    has_done = any(s in _DONE_STATES for s in states)

    row = (
        await session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace_id,
                WorkspaceProjectPriority.project_native_id == str(project_id),
            )
        )
    ).scalar_one_or_none()

    # Reopen path: a done project that has work again.
    if row is not None and row.state == "done":
        if not all_terminal:
            row.state = "active"
            row.completed_at = None
            session.add(
                AuditLog(
                    workspace_id=workspace_id,
                    action="project.reopened",
                    target_kind="project",
                    target_id=str(project_id),
                    payload={"reason": "non_terminal_ticket"},
                )
            )
            return "reopened"
        return "noop"

    # Complete path: every ticket terminal, at least one Done.
    if all_terminal and has_done:
        complete_fn = getattr(gateway, "complete_project", None)
        linear_ok = False
        if complete_fn is not None:
            try:
                linear_ok = bool(await complete_fn(str(project_id)))
            except Exception as exc:  # noqa: BLE001 — cosmetic, swallow
                log.info(
                    "project_completion: linear complete failed pid=%s err=%s",
                    project_id, exc,
                )
        if row is not None and row.state != "done":
            row.state = "done"
            row.completed_at = datetime.now(timezone.utc)
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="project.auto_completed",
                target_kind="project",
                target_id=str(project_id),
                payload={
                    "ticket_count": len(issues),
                    "linear_completed": linear_ok,
                    "had_priority_row": row is not None,
                },
            )
        )
        return "completed"

    return "noop"


async def sweep_project_completion() -> int:
    """Backstop cron: evaluate every active / parked / done project in
    each Linear-integrated workspace.

    Catches completions the merge webhook missed (Linear replica lag on
    the just-written Done state) and reopens done projects that gained a
    ticket. Returns the number of projects newly completed. Per-workspace
    best-effort: a tracker hiccup on one workspace doesn't sink the rest.
    """
    from backend.app.services.tracker_resolver import resolve_for_workspace

    settings = get_settings()
    completed = 0
    Session = get_sessionmaker()
    async with Session() as session:
        ws_ids = (
            await session.execute(
                select(WorkspaceProjectPriority.workspace_id)
                .where(WorkspaceProjectPriority.state.in_(_SWEEP_STATES))
                .distinct()
            )
        ).scalars().all()
        for ws_id in ws_ids:
            try:
                resolved = await resolve_for_workspace(
                    session=session, settings=settings, workspace_id=ws_id
                )
                if resolved is None:
                    continue
                project_ids = (
                    await session.execute(
                        select(WorkspaceProjectPriority.project_native_id)
                        .where(
                            WorkspaceProjectPriority.workspace_id == ws_id,
                            WorkspaceProjectPriority.state.in_(_SWEEP_STATES),
                        )
                        .limit(_SWEEP_MAX_PROJECTS_PER_WS)
                    )
                ).scalars().all()
                for pid in project_ids:
                    result = await evaluate_project_completion(
                        session,
                        workspace_id=ws_id,
                        gateway=resolved.gateway,
                        project_id=str(pid),
                    )
                    if result == "completed":
                        completed += 1
                await session.commit()
            except Exception as exc:  # noqa: BLE001 — per-ws best-effort
                log.warning(
                    "project_completion sweep failed ws=%s err=%s", ws_id, exc
                )
                await session.rollback()
                continue
    if completed:
        log.info("project_completion sweep: completed %d projects", completed)
    return completed
