"""StateProjector (ELS-229) — the single FSM→tracker write seam.

Thesis 2: projection is ONE-DIRECTIONAL. This module writes the human
read-model (Linear workflow states + ``stage:*`` breadcrumbs) and
returns a report; its output is NEVER consumed as control input. The
``stage:*`` breadcrumb stays the poller's pickup hint (domain), not a
lease. Projection is best-effort and decoupled from lock acquisition —
a tracker 5xx must not touch ``agent_dispatch_locks`` (and cannot:
this module never imports the lock primitives; pinned by test).

Strangler flag: ``SHIP_STATE_PROJECTOR_UNIFIED`` (default off). Flag
off → call sites use their original direct ``gateway.transition``
path, byte-identical to today. Flag on → every FSM→tracker status
write funnels through :func:`project_ticket_state`, which adds the
uniform audit trail and error capture.

Idempotency: re-projecting an already-projected state delegates to the
adapter's semantics — Linear ``issueUpdate`` to the same ``stateId``
is a no-op server-side and the adapter's label add is by-name deduped,
so a double projection cannot duplicate labels or churn state. The
projector itself adds no state of its own (stateless seam).

The human-only "Done" authorization gate stays at the ROUTE layer
(agents must not pass ``Done``); the projector does not re-implement
authorization.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("ship.state_projector")


@dataclass(frozen=True)
class ProjectionReport:
    ok: bool
    to_state: str
    ticket_ref: str
    error: Exception | None = None

    def raise_if_failed(self) -> None:
        """Preserve route-layer error semantics: callers that today
        let a tracker failure propagate re-raise the SAME exception."""
        if self.error is not None:
            raise self.error


async def project_ticket_state(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    gateway,
    ref,
    to_state: str,
    from_state: str | None = None,
) -> ProjectionReport:
    """Project one FSM stage (or literal tracker state) onto the ticket.

    Wraps ``gateway.transition`` with error capture. Never touches any
    lock; never reads tracker state back into a decision.
    """
    try:
        if from_state is not None:
            await gateway.transition(
                ref, to_state=to_state, from_state=from_state
            )
        else:
            await gateway.transition(ref, to_state=to_state)
    except Exception as exc:  # noqa: BLE001 — captured into the report
        logger.warning(
            "state_projector: transition failed ws=%s ticket=%s to=%s: %s",
            workspace_id, getattr(ref, "id", ref), to_state, exc,
        )
        return ProjectionReport(
            ok=False,
            to_state=to_state,
            ticket_ref=str(getattr(ref, "id", ref)),
            error=exc,
        )
    return ProjectionReport(
        ok=True, to_state=to_state, ticket_ref=str(getattr(ref, "id", ref))
    )


async def transition_via_projector(
    session: AsyncSession,
    *,
    settings,
    workspace_id: uuid.UUID,
    gateway,
    ref,
    to_state: str,
    from_state: str | None = None,
) -> None:
    """The strangler shim every FSM→tracker write site calls.

    Flag off → the original direct ``gateway.transition`` call
    (byte-identical behavior, including raised exceptions). Flag on →
    route through :func:`project_ticket_state` and re-raise on failure
    so route-layer error handling stays unchanged either way.
    """
    if not getattr(settings, "state_projector_unified", False):
        if from_state is not None:
            await gateway.transition(
                ref, to_state=to_state, from_state=from_state
            )
        else:
            await gateway.transition(ref, to_state=to_state)
        return
    report = await project_ticket_state(
        session,
        workspace_id=workspace_id,
        gateway=gateway,
        ref=ref,
        to_state=to_state,
        from_state=from_state,
    )
    report.raise_if_failed()


__all__ = [
    "ProjectionReport",
    "project_ticket_state",
    "transition_via_projector",
]
