"""WorkflowDispatchGate — the single control-plane chokepoint (W8.2).

EVERY workflow leaf (coding OR reasoning) passes through
:func:`gate_step_dispatch` before it spawns. The gate REUSES the
dispatcher primitives — it never reimplements them:

- lease: :func:`dispatcher.acquire_lock` under the
  ``workflow:<run_id>:<step_id>`` key namespace, so workflow locks
  are countable + sweepable exactly like ``ticket:*`` and bundle
  keys (the ELS-264 TTL sweeper covers them for free);
- cap: :func:`dispatcher.count_active_locks` with the ``workflow:``
  prefix — a SEPARATE accounting pool from the SDLC ``ticket:`` cap
  (mirrors WORKSPACE_BUNDLE_CAP's separation) — checked AFTER acquire
  with walk-back-on-exceed, byte-for-byte the ``maybe_dispatch``
  pattern;
- cascade: :func:`dispatcher._count_recent_dispatches` over audit_log
  against ``CASCADE_LIMIT`` — counted on RECURSION EDGES (a ``ship``
  self-spawn leaf or a nested workflow), keyed on the lineage root,
  so Ship-spawning-Ship-spawning-Ship is refused at depth 3 while an
  ordinary reasoning fan-out is not a recursion;
- idempotency: the durable ``agent_workflow_step_runs`` row keyed
  UNIQUE (workflow_run_id, step_id, attempt) — a retried reconcile
  tick finds the row and does NOT double-spawn; re-running a failed
  step requires a NEW attempt.

FOUNDER DECISION: the control plane is strictly off-limits to the
autonomy dial. This module contains NO profile branch — every
workspace, including ``high``, hits the same cap and cascade. (A test
greps this file to keep it that way.)
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.db.models.workflow import AgentWorkflowStepRun
from backend.app.services.dispatcher import (
    CASCADE_LIMIT,
    _count_recent_dispatches,
    acquire_lock,
    count_active_locks,
    release_lock,
)


class GateReason:
    """Closed vocabulary — mirrors dispatcher._Reason so dashboards
    can union the two."""

    GRANTED = "granted"
    DUPLICATE_ATTEMPT = "duplicate_attempt"
    LOCK_HELD = "lock_held"
    CAP_EXCEEDED = "cap_exceeded"
    CASCADE_BLOCKED = "cascade_blocked"


# Providers whose spawn is a RECURSION edge: the spawned process is
# itself capable of firing more Ship work (T6 self-spawn). These — and
# nested workflow invocations — are what the cascade guard counts.
RECURSIVE_PROVIDERS = ("ship",)


def workflow_lock_key(workflow_run_id: uuid.UUID, step_id: str) -> str:
    return f"workflow:{workflow_run_id}:{step_id}"


@dataclass(frozen=True, slots=True)
class GateDecision:
    granted: bool
    reason: str
    lock_key: str
    step_row_id: uuid.UUID | None = None
    # Correlation id for the spawned leaf (CI agent runs report back
    # with it via /agent-runs/finish).
    run_id: str | None = None


async def gate_step_dispatch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    step_id: str,
    attempt: int,
    kind: str,
    agent_provider: str | None,
    cascade_key: str | None = None,
    is_nested_workflow: bool = False,
    settings: Settings | None = None,
) -> GateDecision:
    """Decide whether one step attempt may spawn. Returns a granted
    decision carrying the durable step row id + correlation run_id,
    or a refusal in the dispatcher's reason vocabulary — the runtime
    records a blocked/skipped step instead of crashing.
    """
    settings = settings or get_settings()
    lock_key = workflow_lock_key(workflow_run_id, step_id)
    cascade_key = cascade_key or f"workflow:{workflow_run_id}"

    # 1. Idempotency — the durable step row is the source of truth.
    existing = (
        await session.execute(
            select(AgentWorkflowStepRun).where(
                AgentWorkflowStepRun.workflow_run_id == workflow_run_id,
                AgentWorkflowStepRun.step_id == step_id,
                AgentWorkflowStepRun.attempt == attempt,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return GateDecision(
            granted=False,
            reason=GateReason.DUPLICATE_ATTEMPT,
            lock_key=lock_key,
            step_row_id=existing.id,
            run_id=existing.run_id,
        )

    async def _blocked(reason: str) -> GateDecision:
        row = AgentWorkflowStepRun(
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            attempt=attempt,
            kind=kind,
            agent_provider=agent_provider,
            status="blocked",
            reason=reason,
            lock_key=lock_key,
            finished_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="workflow.step_blocked",
                target_kind="workflow_step",
                target_id=f"{workflow_run_id}:{step_id}",
                payload={
                    "workflow_run_id": str(workflow_run_id),
                    "step_id": step_id,
                    "attempt": attempt,
                    "reason": reason,
                },
            )
        )
        await session.flush()
        return GateDecision(
            granted=False,
            reason=reason,
            lock_key=lock_key,
            step_row_id=row.id,
        )

    # 2. Cascade guard — recursion edges only (ship self-spawn leaves
    # and nested workflow invocations). Counted over audit_log via the
    # dispatcher's own counter, keyed on the lineage root, so the
    # guard survives fast acquire/release cycles.
    is_recursion_edge = (
        (agent_provider in RECURSIVE_PROVIDERS) or is_nested_workflow
    )
    if is_recursion_edge:
        recent = await _count_recent_dispatches(
            session, workspace_id=workspace_id, ticket_ref=cascade_key
        )
        if recent >= CASCADE_LIMIT:
            return await _blocked(GateReason.CASCADE_BLOCKED)

    # 3. Lease — refuses fast when this exact step is already in
    # flight (e.g. a parallel reconcile tick racing us).
    got_lock = await acquire_lock(
        session, workspace_id=workspace_id, key=lock_key
    )
    if not got_lock:
        return await _blocked(GateReason.LOCK_HELD)

    # 4. Per-workspace cap on the ``workflow:`` prefix — checked AFTER
    # acquire so the count includes our own row; strictly-greater
    # means "this acquire pushed us past the limit, walk it back"
    # (the maybe_dispatch pattern, dispatcher.py step 4).
    active = await count_active_locks(
        session, workspace_id=workspace_id, key_prefix="workflow:"
    )
    ws_cap = (
        await session.execute(
            select(Workspace.max_concurrent_dispatches).where(
                Workspace.id == workspace_id
            )
        )
    ).scalar_one_or_none()
    cap = ws_cap if ws_cap is not None else settings.default_workspace_dispatch_cap
    if active > cap:
        await release_lock(session, workspace_id=workspace_id, key=lock_key)
        return await _blocked(GateReason.CAP_EXCEEDED)

    # 5. Granted — persist the durable step row + audit, mint the
    # correlation run_id the leaf reports back with.
    run_id = f"run_{secrets.token_hex(8)}"
    row = AgentWorkflowStepRun(
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        attempt=attempt,
        kind=kind,
        agent_provider=agent_provider,
        status="dispatched",
        lock_key=lock_key,
        run_id=run_id,
    )
    session.add(row)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            action="workflow.step_dispatched",
            target_kind="workflow_step",
            target_id=f"{workflow_run_id}:{step_id}",
            payload={
                "workflow_run_id": str(workflow_run_id),
                "step_id": step_id,
                "attempt": attempt,
                "agent_provider": agent_provider,
                "lock_key": lock_key,
                "run_id": run_id,
            },
        )
    )
    if is_recursion_edge:
        # The cascade counter reads agent_run.dispatch rows — write
        # one per recursion edge so a Ship-spawning-Ship chain is
        # counted by the SAME counter ticket dispatch uses.
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="agent_run.dispatch",
                target_kind="workflow",
                target_id=cascade_key,
                payload={
                    "workflow_run_id": str(workflow_run_id),
                    "step_id": step_id,
                    "recursion_edge": True,
                },
            )
        )
    await session.flush()
    return GateDecision(
        granted=True,
        reason=GateReason.GRANTED,
        lock_key=lock_key,
        step_row_id=row.id,
        run_id=run_id,
    )


async def release_step_lock(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    step_id: str,
) -> bool:
    """Release one step's workflow lock (leaf finished/failed) —
    mirrors how ticket dispatch releases on /agent-runs/finish."""
    return await release_lock(
        session,
        workspace_id=workspace_id,
        key=workflow_lock_key(workflow_run_id, step_id),
    )
