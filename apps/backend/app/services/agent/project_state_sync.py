"""Project state ↔ child-ticket state sync (Linear ELS-91).

When the PO flips a project's ``WorkspaceProjectPriority.state``
between ``active`` / ``planning`` (Drafts) / ``parked``, the picker
already gates pickup correctly (Linear ELS-80 / ELS-83 / ELS-84 /
ELS-92). But the **tickets stay in their Linear workflow state** —
opening Linear, the operator sees a queue of "Todo" tickets that
agents are silently ignoring. This module closes that loop by
moving children together with the project:

* ``active``                 — children in ``Backlog`` → ``Todo``
                                (ready, picker takes them).
* ``planning`` / ``parked``  — children in ``Todo`` → ``Backlog``
                                (frozen, picker skips them, operator
                                sees a clean Backlog with no
                                unaddressable queue).

Tickets in ``In Progress`` / ``Review`` / ``Done`` are **never moved**
— parking a project doesn't yank an in-flight ticket back to Backlog
(that would lose work and require manual recovery); it just stops
*new* pickups. The single in-flight ticket completes, then no new
work starts on that project until the operator re-promotes.

Best-effort: an adapter that doesn't model project containment, or a
tracker 5xx mid-sync, returns ``ProjectSyncReport(errored=…)`` and
the caller logs but does **not** roll back the priorities row flip.
The audit trail captures both halves so a re-run reconciles cleanly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import AuditLog
from backend.app.integrations.gateway.tracker import TicketRef


logger = logging.getLogger(__name__)


# Project priority state → (from-Linear-state, to-Linear-state) for
# the sync transition. ``None`` means "no sync action for this state"
# — useful for terminal states or transitions we deliberately ignore.
_TRANSITION_PLAN: dict[str, tuple[str, str] | None] = {
    "active": ("Backlog", "Todo"),
    "planning": ("Todo", "Backlog"),
    "parked": ("Todo", "Backlog"),
}


@dataclass(slots=True)
class ProjectSyncReport:
    """Return value of :func:`sync_project_tickets_for_state`.

    ``moved`` and ``errored`` count individual tickets the helper
    walked. ``skipped_reason`` is set on the *whole-call* skip paths
    (no adapter support, unknown new state, no project_id) — it does
    NOT accumulate per ticket.
    """

    project_id: str
    new_state: str
    moved: int = 0
    errored: int = 0
    skipped_reason: str | None = None
    moved_tickets: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "new_state": self.new_state,
            "moved": self.moved,
            "errored": self.errored,
            "skipped_reason": self.skipped_reason,
            "moved_tickets": list(self.moved_tickets),
        }


def _derive_tracker_kind(gateway: Any) -> str:
    """Best-effort kind from the gateway's class name.

    ``LinearTracker`` → ``"linear"``, ``NotionTracker`` → ``"notion"``.
    Used when callers pass the bare adapter without a paired
    ``ResolvedTracker.kind``. Returns ``"unknown"`` if the class
    doesn't follow the ``<Vendor>Tracker`` convention — non-fatal
    (the kind only flavours the audit row + TicketRef).
    """
    name = type(gateway).__name__
    suffix = "Tracker"
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    return name.lower() or "unknown"


async def sync_project_tickets_for_state(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: str | None,
    new_state: str,
    gateway,  # TrackerGateway adapter (e.g. LinearTracker)
    tracker_kind: str | None = None,
    actor_user_id: uuid.UUID,
    actor_token_id: uuid.UUID | None = None,
) -> ProjectSyncReport:
    """Move child tickets to match the project's new priority state.

    Returns a :class:`ProjectSyncReport`. Always returns — even on
    full failure — so callers can audit the outcome alongside the
    priorities-row flip.

    ``gateway`` is the bare tracker adapter (LinearTracker instance).
    ``tracker_kind`` is optional; when omitted we derive it from the
    gateway's class name. The kind only flavours the audit row + the
    ``TicketRef`` used for transitions, not the dispatch logic.

    The function flushes its audit rows but does **not** commit; the
    caller's transaction owns the lifecycle so a tracker error
    rolling back the flip also rolls back the partial sync audit.
    """
    report = ProjectSyncReport(project_id=project_id or "", new_state=new_state)
    if tracker_kind is None and gateway is not None:
        tracker_kind = _derive_tracker_kind(gateway)

    if not project_id:
        report.skipped_reason = "no_project_id"
        return report
    if gateway is None:
        report.skipped_reason = "no_tracker_bound"
        return report

    plan = _TRANSITION_PLAN.get(new_state)
    if plan is None:
        report.skipped_reason = f"unknown_state:{new_state}"
        return report
    from_state_name, to_state_name = plan

    list_fn = getattr(gateway, "list_project_tickets_in_state", None)
    if list_fn is None:
        report.skipped_reason = (
            f"adapter_no_project_list:{tracker_kind or 'unknown'}"
        )
        return report

    try:
        rows = await list_fn(
            project_id=project_id,
            linear_state_name=from_state_name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project_state_sync: list_project_tickets failed "
            "workspace=%s project=%s from=%s err=%s",
            workspace_id,
            project_id,
            from_state_name,
            exc,
        )
        report.errored = 1
        report.skipped_reason = "list_failed"
        return report

    for row in rows:
        ticket_uuid = row.get("id") or ""
        ticket_ref = row.get("identifier") or ticket_uuid
        if not ticket_uuid:
            continue
        try:
            await gateway.transition(
                TicketRef(
                    kind=tracker_kind or "linear",
                    workspace_hint=None,
                    id=str(ticket_uuid),
                ),
                to_state=to_state_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "project_state_sync: transition failed "
                "workspace=%s project=%s ticket=%s from=%s to=%s err=%s",
                workspace_id,
                project_id,
                ticket_ref,
                from_state_name,
                to_state_name,
                exc,
            )
            report.errored += 1
            session.add(
                AuditLog(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    actor_token_id=actor_token_id,
                    action="priorities.synced_ticket_state_failed",
                    target_kind="ticket",
                    target_id=str(ticket_ref),
                    payload={
                        "project_id": project_id,
                        "from_linear_state": from_state_name,
                        "to_linear_state": to_state_name,
                        "trigger_state": new_state,
                        "error": str(exc)[:500],
                    },
                )
            )
            continue
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_token_id=actor_token_id,
                action="priorities.synced_ticket_state",
                target_kind="ticket",
                target_id=str(ticket_ref),
                payload={
                    "project_id": project_id,
                    "from_linear_state": from_state_name,
                    "to_linear_state": to_state_name,
                    "trigger_state": new_state,
                },
            )
        )
        report.moved += 1
        report.moved_tickets.append(str(ticket_ref))

    if report.moved or report.errored:
        await session.flush()
    return report


async def apply_project_state_to_ticket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: str,
    ticket_ref: str,
    ticket_uuid: str,
    current_linear_state: str | None,
    gateway,
    tracker_kind: str | None = None,
    actor_user_id: uuid.UUID,
    actor_token_id: uuid.UUID | None = None,
) -> str | None:
    """Single-ticket counterpart of :func:`sync_project_tickets_for_state`.

    Used by ticket-write paths (``ticket-action assign``,
    ``tracker/tickets`` create) to bring one freshly-affected ticket
    into line with its project's priority state. The bulk variant
    needs a project-wide ``list_project_tickets_in_state`` call;
    here the caller already knows which ticket to move, so we skip
    the listing and just dispatch a single ``transition`` call.

    Returns the linear state name we transitioned to (``"Todo"``
    or ``"Backlog"``) when a transition fired, or ``None`` when no
    move was needed (state already correct, project's priority row
    missing, transition rule unknown).
    """
    from sqlalchemy import select as sa_select

    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    if not project_id or not ticket_uuid:
        return None
    if gateway is None:
        return None

    priority_state = await session.scalar(
        sa_select(WorkspaceProjectPriority.state).where(
            WorkspaceProjectPriority.workspace_id == workspace_id,
            WorkspaceProjectPriority.project_native_id == project_id,
        )
    )
    if not priority_state:
        return None

    plan = _TRANSITION_PLAN.get(priority_state)
    if plan is None:
        return None
    from_state_name, to_state_name = plan

    # Skip if the ticket is already in the target state OR isn't
    # the from-state we'd care about. ``current_linear_state`` is the
    # caller's snapshot; an empty / unknown value still falls through
    # to the transition since the gateway's transition is idempotent.
    if current_linear_state and current_linear_state == to_state_name:
        return None
    if current_linear_state and current_linear_state != from_state_name:
        # Ticket is mid-pipeline (In Progress / Review / Done) — never
        # yank it back. The project state only governs Backlog ↔ Todo
        # at the boundary; in-flight work is sacrosanct.
        return None

    if tracker_kind is None:
        tracker_kind = _derive_tracker_kind(gateway)

    try:
        await gateway.transition(
            TicketRef(
                kind=tracker_kind or "linear",
                workspace_hint=None,
                id=str(ticket_uuid),
            ),
            to_state=to_state_name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "apply_project_state_to_ticket: transition failed "
            "workspace=%s project=%s ticket=%s to=%s err=%s",
            workspace_id,
            project_id,
            ticket_ref,
            to_state_name,
            exc,
        )
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_token_id=actor_token_id,
                action="priorities.synced_ticket_state_failed",
                target_kind="ticket",
                target_id=str(ticket_ref),
                payload={
                    "project_id": project_id,
                    "to_linear_state": to_state_name,
                    "trigger_state": priority_state,
                    "trigger_source": "single_ticket_write",
                    "error": str(exc)[:500],
                },
            )
        )
        await session.flush()
        return None

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_token_id=actor_token_id,
            action="priorities.synced_ticket_state",
            target_kind="ticket",
            target_id=str(ticket_ref),
            payload={
                "project_id": project_id,
                "from_linear_state": current_linear_state,
                "to_linear_state": to_state_name,
                "trigger_state": priority_state,
                "trigger_source": "single_ticket_write",
            },
        )
    )
    await session.flush()
    return to_state_name


__all__ = [
    "ProjectSyncReport",
    "apply_project_state_to_ticket",
    "sync_project_tickets_for_state",
]
