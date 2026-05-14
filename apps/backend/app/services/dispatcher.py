"""Event-driven dispatcher (ELS-122).

Turns a "ticket X transitioned to state Y" event into a real
``workflow_dispatch`` GitHub Actions invocation, with locks + caps
that keep the fleet from stepping on itself.

Public surface (everything else is private to this module):

- :func:`maybe_dispatch` — main entry. Resolves the lock + cap +
  cascade-depth guards and either fires a workflow or refuses with a
  structured reason. Called from the tracker poller (ELS-121 wires
  this up behind ``SHIP_TRACKER_POLL_FIRE``) and from
  ``agent_runs.finish`` once cascade lands.
- :func:`acquire_lock` / :func:`release_lock` — atomic primitives over
  ``agent_dispatch_locks`` for cases that need fine-grained control
  (tests, manual reconciliation jobs). Most callers should use
  ``maybe_dispatch``.

Lock key namespace is opaque to the schema. Today the only key shape
is ``ticket:<ref>`` (per-ticket serialisation); future namespaces
(``daily:<routine>``, ``project:<anchor>``) drop in without a
migration.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Final

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.integrations.github.workflows import (
    WorkflowDispatchError,
    dispatch_workflow,
)


# Stage label (Linear's ``stage:<id>``) → routine id. The dispatcher
# resolves this server-side before firing ``workflow_dispatch`` so
# ``shipctl run`` receives both ``routine_id`` (which prompt to load)
# and ``ticket_ref`` (which ticket to pin to). Bundle stages map to
# their bundle routine; legacy pre-E16 stages map to the bundle they
# were absorbed into, so an in-flight ticket carrying e.g.
# ``stage:task_intake`` still routes to the new ``planning`` bundle.
_STAGE_TO_ROUTINE: dict[str, str] = {
    # E16 bundles.
    "planning": "planning",
    "dev_implementation": "developer",
    "validation": "validation",
    "code_review": "reviewer",
    "decomposition": "decomposition",
    # Pre-E16 legacy stages absorbed into bundles.
    "task_intake": "planning",
    "ba_requirements": "planning",
    "tech_arch_plan": "planning",
    "qa_arch_plan": "planning",
    "qa_manual": "validation",
    "qa_automation": "validation",
    "pr_review": "reviewer",
    "wbs": "decomposition",
    "architecture": "decomposition",
    "test_architecture": "decomposition",
    "tasks": "decomposition",
}


log = logging.getLogger(__name__)


# Workflow file name on the customer side. Hard-coded for now because
# every Ship-seeded repo carries the same file; once ELS-124 cuts to
# ``ship-agent-run.yml`` the constant updates here.
WORKFLOW_FILE: Final[str] = "ship-agent-run.yml"

# Cascade-depth budget — how many dispatches we'll fire for the same
# ticket within :data:`CASCADE_WINDOW_S` seconds before refusing.
# Catches FSM bugs that flip a ticket between two states; without this
# the dispatcher would happily loop forever.
CASCADE_LIMIT: Final[int] = 3
CASCADE_WINDOW_S: Final[int] = 60

# Per-ticket lock TTL. The dispatch fires a single workflow run which
# typically completes in 5-10 min for the SDLC bundles we'll ship in
# ELS-123. Set the lock at 60 min so an agent that legitimately needs
# longer (decomposition bundle) doesn't get its slot re-taken
# mid-run. Operators can override per-row at insert time if needed.
DEFAULT_LOCK_TTL_S: Final[int] = 3600


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Outcome of one ``maybe_dispatch`` call.

    ``reason`` is the structured "why" — drive metrics off this rather
    than parsing free-text log lines.
    """

    fired: bool
    reason: str  # see _Reason for the closed set
    workflow_run_hint: str | None = None
    lock_key: str | None = None


class _Reason:
    """Closed set of ``DispatchResult.reason`` values.

    Add a new member here when a new refuse path appears so tests +
    dashboards stay in sync with the dispatcher's vocabulary.
    """

    FIRED = "fired"
    SHADOW = "shadow"  # SHIP_TRACKER_POLL_FIRE=false — recorded, not dispatched
    LOCK_HELD = "lock_held"  # ticket already in flight
    CAP_EXCEEDED = "cap_exceeded"  # workspace's parallel dispatch limit hit
    CASCADE_BLOCKED = "cascade_blocked"  # too many dispatches for this ticket recently
    NO_REPO = "no_repo"  # workspace has no activated repo to dispatch to
    NO_ROUTINE = "no_routine"  # ticket carries no stage label or unknown stage
    DISPATCH_FAILED = "dispatch_failed"  # GH API rejected the call


# ---------------------------------------------------------------------------
# Lock primitives
# ---------------------------------------------------------------------------


async def acquire_lock(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    key: str,
    ttl_seconds: int = DEFAULT_LOCK_TTL_S,
    run_id: uuid.UUID | None = None,
) -> bool:
    """Try to claim ``(workspace_id, key)`` atomically.

    Returns ``True`` when this caller now owns the slot, ``False``
    when somebody else holds it. Expired rows are reaped first so a
    crashed agent doesn't permanently wedge the lock.

    The implementation is one SQL roundtrip: a DELETE of any expired
    row matching the key, followed by INSERT ... ON CONFLICT DO
    NOTHING. The unique index on ``(workspace_id, key)`` serialises
    racing claims at the database level — no Python-side mutex.
    """
    # Reap an expired row matching this key so we can re-acquire.
    # Bounded to this exact (ws, key) so we don't sweep the whole
    # table on the hot path — the dispatcher's periodic sweep
    # (sweep_expired_locks) handles unrelated stale rows.
    await session.execute(
        text(
            """
            DELETE FROM agent_dispatch_locks
            WHERE workspace_id = :ws AND key = :key AND expires_at <= now()
            """
        ),
        {"ws": workspace_id, "key": key},
    )
    row = (
        await session.execute(
            text(
                """
                INSERT INTO agent_dispatch_locks
                    (workspace_id, key, expires_at, run_id)
                VALUES
                    (:ws, :key, now() + (:ttl_s * interval '1 second'), :run_id)
                ON CONFLICT (workspace_id, key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "ws": workspace_id,
                "key": key,
                "ttl_s": ttl_seconds,
                "run_id": run_id,
            },
        )
    ).scalar_one_or_none()
    return row is not None


async def release_lock(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    key: str,
) -> bool:
    """Drop the lock for ``(workspace_id, key)``.

    Returns ``True`` when a row was actually deleted (i.e. we held the
    lock). Idempotent — calling on a non-existent key is a no-op.
    """
    result = await session.execute(
        text(
            """
            DELETE FROM agent_dispatch_locks
            WHERE workspace_id = :ws AND key = :key
            """
        ),
        {"ws": workspace_id, "key": key},
    )
    return (result.rowcount or 0) > 0


async def sweep_expired_locks(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None = None,
) -> int:
    """Delete every lock past its ``expires_at`` deadline.

    Returns the number of rows reaped. When ``workspace_id`` is
    provided the sweep is scoped to one workspace; without it the
    sweep is global (suitable for a periodic cleanup job).
    """
    if workspace_id is None:
        result = await session.execute(
            text(
                """
                DELETE FROM agent_dispatch_locks
                WHERE expires_at <= now()
                """
            )
        )
    else:
        result = await session.execute(
            text(
                """
                DELETE FROM agent_dispatch_locks
                WHERE workspace_id = :ws AND expires_at <= now()
                """
            ),
            {"ws": workspace_id},
        )
    return int(result.rowcount or 0)


async def count_active_locks(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> int:
    """Count live (non-expired) locks for one workspace."""
    return int(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*)::int
                    FROM agent_dispatch_locks
                    WHERE workspace_id = :ws AND expires_at > now()
                    """
                ),
                {"ws": workspace_id},
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# Cascade guard
# ---------------------------------------------------------------------------


async def _count_recent_dispatches(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    window_s: int = CASCADE_WINDOW_S,
) -> int:
    """How many ``agent_run.dispatch`` rows for this ticket landed in
    the last ``window_s`` seconds?

    Used by the cascade guard. Reading from ``audit_log`` (not from
    ``agent_dispatch_locks``) so a fast acquire+release cycle still
    counts — a stuck FSM that flips state every few seconds would
    otherwise look "open slot, fire again" forever.
    """
    return int(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*)::int
                    FROM audit_log
                    WHERE workspace_id = :ws
                      AND action = 'agent_run.dispatch'
                      AND target_id = :ref
                      AND created_at > now() - (:window_s * interval '1 second')
                    """
                ),
                {
                    "ws": workspace_id,
                    "ref": ticket_ref,
                    "window_s": window_s,
                },
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# Repo resolution
# ---------------------------------------------------------------------------


async def _pick_dispatch_repo(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> tuple[WorkspaceRepo, GitHubInstallation] | None:
    """Pick one activated repo in ``workspace_id`` to dispatch to.

    Today's heuristic: the oldest activated repo wins (deterministic,
    matches the order operators see in the dashboard). The runtime
    inside the workflow re-reads the ticket from Linear and figures
    out what to do; the dispatch target is mostly a runner-host
    choice. ELS-123 may refine this to follow the ticket's
    ``project.repo`` link once we wire decomposition.
    """
    row = (
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
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def maybe_dispatch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    trigger_kind: str,
    fsm_stage: str | None = None,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> DispatchResult:
    """Try to dispatch an agent run for ``ticket_ref`` in ``workspace_id``.

    Decision order (each refusal returns immediately; the next guard
    doesn't run):

    1. **shadow** — ``SHIP_TRACKER_POLL_FIRE`` is off. Record an audit
       trail of "would have dispatched" but don't call GitHub.
    2. **cascade_blocked** — more than :data:`CASCADE_LIMIT` dispatches
       fired for this ticket in :data:`CASCADE_WINDOW_S` seconds.
    3. **lock_held** — another agent run for this ticket is in flight
       (lock not yet expired and not released by finish hook).
    4. **cap_exceeded** — workspace already has the maximum number of
       parallel agent runs we allow (per-workspace override falling
       back to ``SHIP_DEFAULT_WORKSPACE_DISPATCH_CAP``).
    5. **no_repo** — workspace has no activated repos to dispatch
       against. Drop the lock we just acquired and refuse.
    6. **fired** — happy path. Lock held, workflow_dispatch accepted,
       ``agent_run.dispatch`` audit row written.
    7. **dispatch_failed** — GH API rejected the call. Lock released,
       audit row records the upstream status.

    Callers commit the session after this returns; the function never
    commits internally so the caller can roll back atomically if the
    surrounding event handler fails.
    """
    settings = settings or get_settings()
    lock_key = f"ticket:{ticket_ref}"

    # Resolve routine before lock acquire — if the ticket has no
    # stage label we'd rather refuse fast than hold a lock for a
    # ticket nobody can work on.
    routine_id = _STAGE_TO_ROUTINE.get(fsm_stage or "") if fsm_stage else None

    # 1. Shadow mode — recorded, never fires. Includes the resolved
    # routine so shadow audit rows let us validate the routing logic
    # before flipping the fire toggle.
    if not settings.tracker_poll_fire:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="agent_run.dispatch_shadow",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "trigger_kind": trigger_kind,
                    "reason": _Reason.SHADOW,
                    "fsm_stage": fsm_stage,
                    "routine_id": routine_id,
                },
            )
        )
        return DispatchResult(
            fired=False, reason=_Reason.SHADOW, lock_key=lock_key
        )

    if routine_id is None:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="dispatch.no_routine",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "trigger_kind": trigger_kind,
                    "fsm_stage": fsm_stage,
                },
            )
        )
        return DispatchResult(
            fired=False, reason=_Reason.NO_ROUTINE, lock_key=lock_key
        )

    # 2. Cascade-depth guard.
    recent = await _count_recent_dispatches(
        session, workspace_id=workspace_id, ticket_ref=ticket_ref
    )
    if recent >= CASCADE_LIMIT:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="dispatch.cascade_blocked",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "trigger_kind": trigger_kind,
                    "recent_dispatches": recent,
                    "window_s": CASCADE_WINDOW_S,
                },
            )
        )
        return DispatchResult(
            fired=False,
            reason=_Reason.CASCADE_BLOCKED,
            lock_key=lock_key,
        )

    # 3. Lock acquire — refuses fast if someone else holds it.
    got_lock = await acquire_lock(
        session, workspace_id=workspace_id, key=lock_key
    )
    if not got_lock:
        return DispatchResult(
            fired=False, reason=_Reason.LOCK_HELD, lock_key=lock_key
        )

    # 4. Per-workspace cap. Compare AFTER acquire so the count
    # includes our own fresh row — using strictly-greater-than against
    # the cap means "this acquire pushed us past the limit, walk it
    # back". A cap of 4 allows exactly 4 simultaneous locks.
    active = await count_active_locks(session, workspace_id=workspace_id)
    workspace = (
        await session.execute(
            select(Workspace.max_concurrent_dispatches).where(
                Workspace.id == workspace_id
            )
        )
    ).scalar_one_or_none()
    cap = workspace if workspace is not None else settings.default_workspace_dispatch_cap
    if active > cap:
        await release_lock(
            session, workspace_id=workspace_id, key=lock_key
        )
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="dispatch.cap_exceeded",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "trigger_kind": trigger_kind,
                    "active": active,
                    "cap": cap,
                },
            )
        )
        return DispatchResult(
            fired=False,
            reason=_Reason.CAP_EXCEEDED,
            lock_key=lock_key,
        )

    # 5. Repo + install pair to dispatch against.
    target = await _pick_dispatch_repo(session, workspace_id=workspace_id)
    if target is None:
        await release_lock(
            session, workspace_id=workspace_id, key=lock_key
        )
        return DispatchResult(
            fired=False, reason=_Reason.NO_REPO, lock_key=lock_key
        )
    repo, install = target

    # 6. Fire ``workflow_dispatch``. GitHub returns 204 on accept;
    # ``WorkflowDispatchError`` covers 4xx (workflow file missing,
    # branch gone, etc).
    try:
        await dispatch_workflow(
            repo,
            install,
            WORKFLOW_FILE,
            inputs={
                "routine_id": routine_id,
                "ticket_ref": ticket_ref,
            },
            settings=settings,
            client=client,
        )
    except WorkflowDispatchError as exc:
        await release_lock(
            session, workspace_id=workspace_id, key=lock_key
        )
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="dispatch.failed",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "trigger_kind": trigger_kind,
                    "repo": repo.full_name,
                    "workflow_file": WORKFLOW_FILE,
                    "upstream_status": exc.status_code,
                    "error": exc.message[:512],
                },
            )
        )
        return DispatchResult(
            fired=False,
            reason=_Reason.DISPATCH_FAILED,
            lock_key=lock_key,
        )

    # 7. Happy path. Audit the successful fire.
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            action="agent_run.dispatch",
            target_kind="ticket",
            target_id=ticket_ref,
            payload={
                "trigger_kind": trigger_kind,
                "repo": repo.full_name,
                "workflow_file": WORKFLOW_FILE,
                "routine_id": routine_id,
                "fsm_stage": fsm_stage,
            },
        )
    )
    log.info(
        "dispatch fired: ws=%s ticket=%s trigger=%s repo=%s",
        workspace_id,
        ticket_ref,
        trigger_kind,
        repo.full_name,
    )
    return DispatchResult(fired=True, reason=_Reason.FIRED, lock_key=lock_key)


__all__ = [
    "DispatchResult",
    "acquire_lock",
    "count_active_locks",
    "maybe_dispatch",
    "release_lock",
    "sweep_expired_locks",
]
