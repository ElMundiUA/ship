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
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Final

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
    WorkspaceRepoRouting,
)
from backend.app.db.models.tenancy import AuditLog, Workspace
from sqlalchemy.orm.attributes import flag_modified
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.github.workflows import (
    WorkflowDispatchError,
    dispatch_workflow,
)
from backend.app.services import tracker_resolver as tracker_resolver_module
from backend.app.services.file_overlap import (
    build_file_coordination_warning,
    file_overlap_warnings_active,
)
from backend.app.services.file_overlap_telemetry import emit_file_overlap_warnings
from backend.app.services.seed_bundle import (
    bundle_supports_file_overlap_warnings,
    bundle_supports_ship_run_id,
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
    "auto_merge": "auto-merger",
    "decomposition": "decomposition",
    # Infra / deployment path. Planning routes a ticket it classifies
    # as ``infra`` to this stage instead of ``dev_implementation``; it
    # rejoins the shared tail (validation → code_review → auto_merge).
    "devops_implementation": "devops",
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


def normalize_routine_id(value: str) -> str:
    """Map an FSM *stage* label to its canonical *routine* id, or pass a
    routine id through unchanged.

    Callers of the manual dispatch endpoint may pass either the stage
    label (``dev_implementation``) or the routine id (``developer``).
    The runner names the working branch ``ship-<routine_id>-<ticket>``,
    so a stage label forks a divergent branch and a duplicate PR for a
    ticket already in flight on its canonical branch. ``maybe_dispatch``
    resolves stage→routine before firing; the manual endpoint must do
    the same. Idempotent — a value that's already a routine id (or an
    unknown string) is returned unchanged.
    """
    return _STAGE_TO_ROUTINE.get(value, value)


_DEVOPS_BREADCRUMB: Final[str] = "stage:devops_implementation"


def redirect_infra_bounce(
    stage_next: str | None, labels: list[str] | None
) -> str | None:
    """Redirect an implementation bounce to devops for infra tickets.

    The auto-merger / B2 auto-cascade hardcode ``dev_implementation`` when
    they bounce a ticket back to implementation. An infra ticket must
    bounce to DevOps, not the feature developer. We detect "this is an
    infra ticket" by the ``stage:devops_implementation`` breadcrumb the
    devops stage already left on the live ticket — no extra label needed.
    Server-side so the agent prompts can't forget. ``dev_implementation``
    and ``devops_implementation`` both map to Linear "In Progress" and
    both keep the project lock, so only the cascade's routine changes.
    """
    if stage_next == "dev_implementation" and _DEVOPS_BREADCRUMB in (
        labels or []
    ):
        return "devops_implementation"
    return stage_next


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

# FSM ``stage_next`` values that keep the per-project WIP lock while
# the same ticket's SDLC chain is still in flight. Anything else on
# ``ready_next_step`` (``merged``, ``planning_done``, …) releases at
# finish so a stalled sibling can dispatch. Keys mirror
# ``_STAGE_TO_ROUTINE`` plus ``code_review`` (bundle id, not legacy
# ``pr_review``).
PROJECT_LOCK_IN_FLIGHT_STAGE_NEXT: Final[frozenset[str]] = frozenset(
    set(_STAGE_TO_ROUTINE) | {"code_review"}
)

_PLANNING_ANCHOR_LABEL: Final[str] = "planning:anchor"

# Per-project lock TTL. Holds across the full ticket lifecycle until
# merge webhook or finish-time release (``maybe_release_project_lock_on_finish``,
# ``github_app._release_project_lock_for_merged_pr``). 60 minutes is
# the belt-and-braces upper bound matching the ticket-level lock TTL.
# Was 24h before ELS-150; that was too long — when the explicit
# release paths missed (crashed agent, picker null without release,
# GH dispatch without workflow_run, post-mortem 2026-05-18), the lock
# stranded every sibling ticket in the project for a working day.
# A 60-min TTL means even exotic uncovered cases self-heal within an
# hour. Cascades within an active chain bypass the lock (don't
# re-acquire), so a multi-stage chain doesn't lose its slot at the
# 60-min mark; the first non-cascade dispatch is the lock-acquirer
# and subsequent stages don't touch it.
PROJECT_LOCK_TTL_S: Final[int] = 60 * 60

# Per-ticket lock TTL. The dispatch fires a single workflow run which
# typically completes in 5-10 min for the SDLC bundles we'll ship in
# ELS-123. 20 min keeps decomposition (the slow tail) inside the
# window while making sure a runner that died early (Cursor crash,
# CLI exited usage) doesn't strand the next transition for an hour —
# we release explicitly from the ``workflow_run.completed`` webhook
# (``github_app._release_dispatch_lock_for_workflow_run``), and the
# TTL is the belt-and-braces fallback in case the webhook never
# arrives (customer egress firewall, GitHub outage). Pre-2026-05-15
# this was 60 min and stranded askslayer's PAC-23 retry for the full
# hour after the first GHA run died on ``unknown argument: --ticket``.
DEFAULT_LOCK_TTL_S: Final[int] = 20 * 60


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
    PROJECT_BUSY = "project_busy"  # another non-anchor ticket in the same Linear project is in flight
    CASCADE_BLOCKED = "cascade_blocked"  # too many dispatches for this ticket recently
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"  # one of the ticket's Linear `blocks` relations is unresolved
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
    run_id: int | None = None,
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


def should_release_project_lock(
    *,
    outcome: str,
    stage_next: str | None,
    labels: list[str] | None = None,
) -> bool:
    """Whether ``/agent-runs/finish`` should drop ``project:<id>``.

    Anchors never hold the project lock — callers still skip release
    when ``planning:anchor`` is present. Stall / clarify / OOS ends
    sibling starvation; ``ready_next_step`` keeps the lock only while
    ``stage_next`` names the next in-flight bundle stage.
    """
    if _PLANNING_ANCHOR_LABEL in (labels or []):
        return False
    if outcome in ("blocked", "needs_clarification", "out_of_scope"):
        return True
    if outcome == "noop":
        return True
    if outcome != "ready_next_step":
        return False
    if not stage_next:
        return True
    return stage_next not in PROJECT_LOCK_IN_FLIGHT_STAGE_NEXT


async def maybe_release_project_lock_on_finish(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    outcome: str,
    stage_next: str | None,
    labels: list[str] | None = None,
    project_id: str | None = None,
    settings: Any | None = None,
) -> bool:
    """Best-effort ``project:<linear_project_id>`` release after finish.

    Returns ``True`` when a lock row was deleted. Idempotent when the
    lock was already released (merge webhook, prior finish, TTL).
    """
    if not should_release_project_lock(
        outcome=outcome, stage_next=stage_next, labels=labels
    ):
        return False

    resolved_project_id = project_id
    if resolved_project_id is None or labels is None:
        try:
            from backend.app.core.config import get_settings as _gs

            _settings = settings if settings is not None else _gs()
            resolved = await tracker_resolver_module.resolve_for_workspace(
                session=session,
                settings=_settings,
                workspace_id=workspace_id,
            )
            if resolved is None:
                return False
            snap_fn = getattr(resolved.gateway, "get_ticket_snapshot", None)
            if snap_fn is None:
                return False
            ticket = TicketRef(
                kind=resolved.kind,
                workspace_hint=None,
                id=ticket_ref,
            )
            snap = await snap_fn(ticket)
            if not snap:
                return False
            if labels is None:
                labels = list(snap.get("labels") or [])
            if _PLANNING_ANCHOR_LABEL in labels:
                return False
            if not resolved_project_id and snap.get("project_id"):
                resolved_project_id = str(snap["project_id"])
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.debug(
                "project_lock finish release lookup failed ws=%s ticket=%s err=%s",
                workspace_id,
                ticket_ref,
                exc,
            )
            return False

    if not resolved_project_id:
        return False

    released = await release_lock(
        session,
        workspace_id=workspace_id,
        key=f"project:{resolved_project_id}",
    )
    if released:
        log.info(
            "agent_run.finish → released project lock ws=%s ticket=%s project=%s outcome=%s stage_next=%s",
            workspace_id,
            ticket_ref,
            resolved_project_id,
            outcome,
            stage_next,
        )
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="dispatch.project_lock_released",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "via": "agent_run.finish",
                    "outcome": outcome,
                    "stage_next": stage_next,
                    "project_id": resolved_project_id,
                },
            )
        )
    return released


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
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    key_prefix: str | None = None,
) -> int:
    """Count live (non-expired) locks for one workspace.

    ``key_prefix`` filters by lock key namespace so the SDLC cap
    (counting ``ticket:*`` keys) and the workspace-bundle cap
    (counting ``daily-digest:*`` / ``weekly-audit:*`` / ``self-heal:*``
    keys) don't compete on the same counter.
    """
    if key_prefix is None:
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
    return int(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*)::int
                    FROM agent_dispatch_locks
                    WHERE workspace_id = :ws
                      AND expires_at > now()
                      AND key LIKE :prefix
                    """
                ),
                {"ws": workspace_id, "prefix": f"{key_prefix}%"},
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


ENV_SEPARATION_ACK_KEY = "env_separation_acknowledged"
ENV_SEPARATION_PENDING_KEY = "env_separation_pending"


def env_separation_handle(*, workspace_id: uuid.UUID, project_id: str) -> str:
    """Stable per-project handle (fits workspace settings + legacy intake_handle)."""
    return f"env-warn:{str(workspace_id)[:8]}:{str(project_id)[:8]}"


async def _emit_env_separation_warning(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: str,
    project_name: str,
) -> None:
    """Queue a one-time Console env-separation modal for this project.

    Idempotent via ``env-warn:<ws8>:<proj8>`` stored in
    ``workspace.settings`` — no inbox row (ELS-144).
    """
    handle = env_separation_handle(
        workspace_id=workspace_id, project_id=project_id
    )
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        return

    settings = dict(workspace.settings or {})
    acknowledged = set(settings.get(ENV_SEPARATION_ACK_KEY) or [])
    if handle in acknowledged:
        return

    pending = list(settings.get(ENV_SEPARATION_PENDING_KEY) or [])
    if any(entry.get("handle") == handle for entry in pending):
        return

    pending.append(
        {
            "handle": handle,
            "project_id": project_id,
            "project_name": project_name,
        }
    )
    settings[ENV_SEPARATION_PENDING_KEY] = pending
    workspace.settings = settings
    flag_modified(workspace, "settings")


async def _pick_dispatch_repo(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: str | None = None,
) -> tuple[WorkspaceRepo, GitHubInstallation, str] | None:
    """Resolve the dispatch target via explicit ``workspace_repo_routing``.

    Precedence:

    1. **Per-project binding** — ``project_native_id == project_id``.
    2. **Workspace default** — the ``project_native_id IS NULL`` row;
       catch-all for unbound / projectless tickets and workspace bundles.
    3. **Unresolved → ``None``** — the caller files a clarification so
       the operator binds a repo, rather than dumping the run on an
       arbitrary one.

    Replaces the retired name-heuristic + oldest-activated fallback,
    which routed askslayer's projectless infra tickets onto a repo
    missing CURSOR_API_KEY and froze the workspace (2026-05-24). Backfill
    seeds bindings from the old heuristic so existing routing is kept.

    Returns ``(repo, install, route)`` where ``route`` is
    ``"binding"`` | ``"default"`` for the dispatch audit.
    """
    route = "binding"
    repo_id: uuid.UUID | None = None
    if project_id:
        repo_id = (
            await session.execute(
                select(WorkspaceRepoRouting.repo_id).where(
                    WorkspaceRepoRouting.workspace_id == workspace_id,
                    WorkspaceRepoRouting.project_native_id == project_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
    if repo_id is None:
        route = "default"
        repo_id = (
            await session.execute(
                select(WorkspaceRepoRouting.repo_id).where(
                    WorkspaceRepoRouting.workspace_id == workspace_id,
                    WorkspaceRepoRouting.project_native_id.is_(None),
                ).limit(1)
            )
        ).scalar_one_or_none()
    if repo_id is None:
        # Transition guard: a workspace with NO routing configured at all
        # keeps the pre-Variant-A behaviour (oldest-activated) so the
        # rollout can't break un-backfilled workspaces. The moment ANY
        # routing row exists (a default or a binding) this is skipped and
        # an unresolved ticket files a clarification instead. Remove once
        # every workspace has routing seeded
        # (tools/scripts/backfill_repo_routing.py).
        any_row = (
            await session.execute(
                select(WorkspaceRepoRouting.id).where(
                    WorkspaceRepoRouting.workspace_id == workspace_id
                ).limit(1)
            )
        ).first()
        if any_row is not None:
            return None
        oldest = (
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
        if oldest is None:
            return None
        return oldest[0], oldest[1], "transition_oldest"
    row = (
        await session.execute(
            select(WorkspaceRepo, GitHubInstallation)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == WorkspaceRepo.installation_id,
            )
            .where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.activated_at.is_not(None),
            )
            .limit(1)
        )
    ).first()
    if row is None:
        # Bound repo was deactivated / uninstalled — treat as unresolved
        # so the operator re-binds rather than silently mis-routing.
        return None
    return row[0], row[1], route


async def _file_no_target_repo_letter(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    project_id: str | None,
    project_name: str | None,
) -> None:
    """Ask the operator to bind a repo when routing can't resolve one.

    Deduped per ticket via ``no-target-repo:<ticket>``. Beats the old
    behaviour of dumping the run on an arbitrary (often key-less) repo.
    """
    from backend.app.db.models.inbox import InboxItem

    handle = f"no-target-repo:{ticket_ref}"
    existing = (
        await session.execute(
            select(InboxItem.id).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.intake_handle == handle,
                InboxItem.status == "new",
            )
        )
    ).first()
    if existing is not None:
        return
    proj = project_name or project_id or "(no project)"
    session.add(
        InboxItem(
            workspace_id=workspace_id,
            repo_id=None,
            type="clarification",
            title=f"{ticket_ref}: no target repo — pick one"[:300],
            summary=(
                f"Ship can't dispatch {ticket_ref}: its project ({proj}) has "
                f"no repo binding and the workspace has no default repo set. "
                f"Bind the project to a repo, or set a workspace default repo, "
                f"in Settings → repo routing — then it dispatches on the next "
                f"tick."
            )[:2000],
            payload={
                "ticket_ref": ticket_ref,
                "project_id": project_id,
                "project_name": project_name,
            },
            status="new",
            category="decision_needed",
            priority=8,
            intake_handle=handle,
            intake_reason="no_target_repo",
        )
    )


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
        # Audit the refusal so operators don't see a silent black hole
        # when a transition arrives but no GH workflow_dispatch follows.
        # Without this row the only signal that we refused was an
        # absence of ``agent_run.dispatch`` next to ``tracker.event.
        # received`` — which is what stalled askslayer's PAC-23 test
        # on 2026-05-15: the previous lock from a failed run never
        # released and every poll tick refused silently for the next
        # hour.
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="dispatch.lock_held",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "trigger_kind": trigger_kind,
                    "fsm_stage": fsm_stage,
                    "routine_id": routine_id,
                    "lock_key": lock_key,
                },
            )
        )
        return DispatchResult(
            fired=False, reason=_Reason.LOCK_HELD, lock_key=lock_key
        )

    # 4. Per-workspace cap. Compare AFTER acquire so the count
    # includes our own fresh row — using strictly-greater-than against
    # the cap means "this acquire pushed us past the limit, walk it
    # back". A cap of 4 allows exactly 4 simultaneous locks.
    # ``key_prefix="ticket:"`` scopes the count to SDLC dispatches so
    # workspace-bundle dispatches (daily-digest / weekly-audit /
    # self-heal) live on their own slot count via
    # ``maybe_dispatch_workspace_bundle``.
    active = await count_active_locks(
        session, workspace_id=workspace_id, key_prefix="ticket:"
    )
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

    # 5. Project context — fetch the ticket's Linear project + labels
    # once and use the result for (a) project-WIP gate (one in-flight
    # non-anchor ticket per project so a 12-ticket queue doesn't
    # cascade into 12 parallel PRs touching the same files) and
    # (b) project-aware repo selection.
    project_id: str | None = None
    project_name_hint: str | None = None
    ticket_labels: list[str] = []
    try:
        resolved_tracker = await tracker_resolver_module.resolve_for_workspace(
            session=session, settings=settings, workspace_id=workspace_id
        )
        snapshot_fn = (
            getattr(resolved_tracker.gateway, "get_ticket_snapshot", None)
            if resolved_tracker is not None
            else None
        )
        if snapshot_fn is not None:
            ticket = TicketRef(
                kind=resolved_tracker.kind,
                workspace_hint=None,
                id=ticket_ref,
            )
            snap = await snapshot_fn(ticket)
            if snap:
                ticket_labels = list(snap.get("labels") or [])
                if snap.get("project_id"):
                    project_id = str(snap["project_id"])
                    get_project_fn = getattr(
                        resolved_tracker.gateway, "get_project", None
                    )
                    if get_project_fn is not None:
                        proj = await get_project_fn(project_id)
                        if isinstance(proj, dict):
                            project_name_hint = proj.get("name") or None
    except Exception as exc:  # noqa: BLE001 — best-effort hint
        log.debug(
            "dispatcher: project context lookup failed ticket=%s err=%s",
            ticket_ref, exc,
        )

    # 5a. Dependency gate. Linear models hard prerequisites via the
    # `blocks` relation (issue A blocks issue B → B can't start until
    # A is done). Three parallel devs picked up sibling inbox tickets
    # in the same project and each wrote a conflicting Alembic
    # `0074_*` migration because no stage checked the relation
    # (caught 2026-05-18 on ELS-143/144/147). Refuse the dispatch
    # while any blocker is in a non-terminal state. ``canceled``
    # counts as resolved — the work is no longer expected, so the
    # dependency is conceptually gone. Best-effort: if the adapter
    # can't answer (non-Linear tracker, GraphQL hiccup), we proceed.
    if resolved_tracker is not None:
        get_blockers_fn = getattr(
            resolved_tracker.gateway, "get_ticket_blockers", None
        )
        if get_blockers_fn is not None:
            try:
                blockers = await get_blockers_fn(
                    TicketRef(
                        kind=resolved_tracker.kind,
                        workspace_hint=None,
                        id=ticket_ref,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — best-effort
                log.debug(
                    "dispatcher: get_ticket_blockers failed ticket=%s err=%s",
                    ticket_ref, exc,
                )
                blockers = None
            unresolved = [
                b for b in (blockers or []) if not b.get("completed")
            ]
            if unresolved:
                await release_lock(
                    session, workspace_id=workspace_id, key=lock_key
                )
                session.add(
                    AuditLog(
                        workspace_id=workspace_id,
                        action="dispatch.blocked_by_dep",
                        target_kind="ticket",
                        target_id=ticket_ref,
                        payload={
                            "trigger_kind": trigger_kind,
                            "fsm_stage": fsm_stage,
                            "blockers": [
                                {
                                    "identifier": b.get("identifier"),
                                    "state_name": b.get("state_name"),
                                }
                                for b in unresolved
                            ],
                        },
                    )
                )
                return DispatchResult(
                    fired=False,
                    reason=_Reason.BLOCKED_BY_DEPENDENCY,
                    lock_key=lock_key,
                )

    # 5b. Project-WIP gate. One non-anchor ticket per Linear project
    # at a time — anchors decompose their project and must stay
    # parallelisable; everything else queues. ``planning:anchor``
    # carries the decomposition chain, never enters this gate.
    # Without it 12 askslayer PAC tickets carrying ``stage:task_intake``
    # would cascade into 12 parallel planning bundles, each with
    # ``stage_next=dev_implementation`` triggering 12 parallel dev
    # bundles, all opening PRs against the same ``internal/query``
    # surface — a guaranteed merge-conflict pile-up that no human can
    # review (caught at askslayer/PAC-23 design 2026-05-15).
    is_anchor = "planning:anchor" in ticket_labels
    # Cascade dispatches (a finish handler firing the next stage of
    # the *same* ticket) must NOT re-take the project lock — the
    # original dispatch on this ticket already holds it. Treating
    # the cascade as a fresh entrant would refuse every stage past
    # planning with ``project_busy``, exactly the loop we just hit
    # on PAC-23: planning finished, cascade fired dev_implementation,
    # the same ticket's project lock was still held → silent refusal
    # → chain stalls one stage in.
    is_cascade = trigger_kind == "cascade"
    if project_id and not is_anchor and not is_cascade:
        project_lock_key = f"project:{project_id}"
        got_project_lock = await acquire_lock(
            session, workspace_id=workspace_id, key=project_lock_key,
            ttl_seconds=PROJECT_LOCK_TTL_S,
        )
        if not got_project_lock:
            await release_lock(
                session, workspace_id=workspace_id, key=lock_key
            )
            session.add(
                AuditLog(
                    workspace_id=workspace_id,
                    action="dispatch.project_busy",
                    target_kind="ticket",
                    target_id=ticket_ref,
                    payload={
                        "trigger_kind": trigger_kind,
                        "fsm_stage": fsm_stage,
                        "routine_id": routine_id,
                        "project_id": project_id,
                        "project_name": project_name_hint,
                    },
                )
            )
            return DispatchResult(
                fired=False,
                reason=_Reason.PROJECT_BUSY,
                lock_key=lock_key,
            )
        # First non-anchor dispatch on this project for this workspace
        # → drop a one-time inbox warning that auto-merge will run
        # against the operator's branch. Dedup via
        # ``intake_handle="env-warn:<workspace>:<project>"`` so re-
        # dispatches don't re-spam the inbox.
        await _emit_env_separation_warning(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            project_name=project_name_hint or project_id,
        )
    target = await _pick_dispatch_repo(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if target is None:
        await release_lock(
            session, workspace_id=workspace_id, key=lock_key
        )
        if project_id and not is_anchor:
            await release_lock(
                session, workspace_id=workspace_id, key=f"project:{project_id}"
            )
        # No binding + no workspace default → ask the operator to bind a
        # repo instead of dumping the run on an arbitrary one.
        await _file_no_target_repo_letter(
            session,
            workspace_id=workspace_id,
            ticket_ref=ticket_ref,
            project_id=project_id,
            project_name=project_name_hint,
        )
        return DispatchResult(
            fired=False, reason=_Reason.NO_REPO, lock_key=lock_key
        )
    repo, install, dispatch_route = target

    dispatch_run_id = f"run_{secrets.token_hex(8)}"
    file_coordination_warning: str | None = None
    file_overlap_warnings: list[dict[str, Any]] = []
    ws_settings_row = (
        await session.execute(
            select(Workspace.settings).where(Workspace.id == workspace_id)
        )
    ).scalar_one_or_none()
    overlap_warnings_active = file_overlap_warnings_active(
        settings, ws_settings_row
    )
    if (
        routine_id == "developer"
        and not is_anchor
        and overlap_warnings_active
        and project_id
        and resolved_tracker is not None
    ):
        snapshot_fn = getattr(
            resolved_tracker.gateway, "get_ticket_snapshot", None
        )
        overlap = await build_file_coordination_warning(
            session,
            workspace_id=workspace_id,
            ticket_ref=ticket_ref,
            project_id=project_id,
            tracker_kind=resolved_tracker.kind,
            snapshot_fn=snapshot_fn,
            settings=settings,
            workspace_settings=ws_settings_row,
            client=client,
        )
        file_coordination_warning = overlap.warning_markdown
        file_overlap_warnings = overlap.file_overlap_warnings
        if file_overlap_warnings:
            session.add(
                AuditLog(
                    workspace_id=workspace_id,
                    action="dispatch.file_overlap_warning",
                    target_kind="ticket",
                    target_id=ticket_ref,
                    payload={
                        "trigger_kind": trigger_kind,
                        "fsm_stage": fsm_stage,
                        "file_overlap_warnings": file_overlap_warnings,
                        "file_coordination_warning": file_coordination_warning,
                    },
                )
            )
            if project_id:
                emit_file_overlap_warnings(
                    session,
                    workspace_id=workspace_id,
                    ticket_ref=ticket_ref,
                    project_id=project_id,
                    run_id=dispatch_run_id,
                    warnings=file_overlap_warnings,
                )

    # 6. Fire ``workflow_dispatch``. GitHub returns 204 on accept;
    # ``WorkflowDispatchError`` covers 4xx (workflow file missing,
    # branch gone, etc). ``ship_run_id`` is only declared by the
    # bundle from 0.37 on; sending it to an older repo trips GitHub's
    # 422 ``Unexpected inputs provided`` and fails every dispatch, so
    # gate it on the installed bundle version.
    dispatch_inputs: dict[str, str] = {
        "routine_id": routine_id,
        "ticket_ref": ticket_ref,
    }
    if bundle_supports_ship_run_id(repo.installed_bundle_version):
        dispatch_inputs["ship_run_id"] = dispatch_run_id
    if (
        file_coordination_warning
        and bundle_supports_file_overlap_warnings(repo.installed_bundle_version)
    ):
        dispatch_inputs["file_overlap_warnings"] = file_coordination_warning
    try:
        await dispatch_workflow(
            repo,
            install,
            WORKFLOW_FILE,
            inputs=dispatch_inputs,
            settings=settings,
            client=client,
        )
    except WorkflowDispatchError as exc:
        await release_lock(
            session, workspace_id=workspace_id, key=lock_key
        )
        if project_id and not is_anchor:
            await release_lock(
                session, workspace_id=workspace_id, key=f"project:{project_id}"
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
    dispatch_audit = AuditLog(
        workspace_id=workspace_id,
        action="agent_run.dispatch",
        target_kind="ticket",
        target_id=ticket_ref,
        payload={
            "trigger_kind": trigger_kind,
            "repo": repo.full_name,
            "route": dispatch_route,
            "workflow_file": WORKFLOW_FILE,
            "routine_id": routine_id,
            "fsm_stage": fsm_stage,
            # Stash project_id so /tracker/next can fall back to
            # it when synthesising a row from audit on a stale
            # tracker replica (orphan gate needs project_id).
            "project_id": project_id,
            "file_coordination_warning": file_coordination_warning,
            "file_overlap_warnings": file_overlap_warnings or None,
            "run_id": dispatch_run_id,
        },
    )
    session.add(dispatch_audit)
    # ELS-149 followup: pre-merge attempt to backfill the dispatch
    # audit id onto agent_dispatch_locks.run_id hit a schema mismatch
    # — audit_log.id is bigint in prod, lock.run_id is uuid. Reverted.
    # The sweeper falls back to project_id heuristics, which covers
    # 99% of cases. Schema-aligning the two columns is a separate
    # migration tracked in the lock-lifecycle epic.
    log.info(
        "dispatch fired: ws=%s ticket=%s trigger=%s repo=%s",
        workspace_id,
        ticket_ref,
        trigger_kind,
        repo.full_name,
    )
    return DispatchResult(fired=True, reason=_Reason.FIRED, lock_key=lock_key)


# ---------------------------------------------------------------------------
# Workspace-bundle dispatch (ELS-125)
# ---------------------------------------------------------------------------


# Workspace-level bundle ids → routine id used inside ``shipctl run
# --routine X``. The role file (slug) under ``agent_roles/`` matches
# the routine id 1:1. Cron cadence per bundle lives in
# ``daily_scheduler.py`` (the only place where time-driven cron
# survives after ELS-124).
_WORKSPACE_BUNDLE_IDS: Final[frozenset[str]] = frozenset(
    {"daily-digest", "weekly-audit", "self-heal"}
)

# Separate cap for workspace-bundle dispatches so they don't compete
# with SDLC ticket dispatches for the workspace's primary 4 slots.
# One slot means "at most one daily-digest / weekly-audit / self-heal
# in flight per workspace at a time" — these bundles run minutes,
# not minutes-of-CPU, and back-to-back daily-digest invocations make
# no sense anyway.
WORKSPACE_BUNDLE_CAP: Final[int] = 1


async def maybe_dispatch_workspace_bundle(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    bundle_id: str,
    trigger_kind: str,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> DispatchResult:
    """Dispatch a workspace-scope bundle (no ticket pin).

    Mirrors :func:`maybe_dispatch` but with workspace-scope locks
    (``<bundle_id>:scheduled``) and a separate cap counter that
    doesn't compete with the SDLC ticket cap.

    Bundle ids: ``daily-digest`` / ``weekly-audit`` / ``self-heal``.
    Each fires the same workflow as ticket-dispatched bundles
    (``ship-agent-run.yml``) but with ``ticket_ref=""`` so the
    runner knows there's no FSM ticket to pin to and the bundle
    operates on the workspace as a whole.
    """
    settings = settings or get_settings()

    if bundle_id not in _WORKSPACE_BUNDLE_IDS:
        return DispatchResult(
            fired=False,
            reason=_Reason.NO_ROUTINE,
            lock_key=None,
        )

    lock_key = f"{bundle_id}:scheduled"

    # 1. Shadow mode — recorded, never fires.
    if not settings.tracker_poll_fire:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="agent_run.dispatch_shadow",
                target_kind="workspace_bundle",
                target_id=bundle_id,
                payload={
                    "trigger_kind": trigger_kind,
                    "reason": _Reason.SHADOW,
                    "bundle_id": bundle_id,
                },
            )
        )
        return DispatchResult(
            fired=False, reason=_Reason.SHADOW, lock_key=lock_key
        )

    # 2. Lock acquire — the unique (workspace_id, key) index enforces
    # one concurrent run per bundle per workspace.
    got_lock = await acquire_lock(
        session, workspace_id=workspace_id, key=lock_key
    )
    if not got_lock:
        return DispatchResult(
            fired=False, reason=_Reason.LOCK_HELD, lock_key=lock_key
        )

    # 3. Separate cap — workspace bundles cap=1 by default. Counts
    # only ``<bundle_id>:`` keys via prefix filter so SDLC dispatches
    # don't push us over.
    active = await count_active_locks(
        session,
        workspace_id=workspace_id,
        key_prefix=f"{bundle_id}:",
    )
    if active > WORKSPACE_BUNDLE_CAP:
        await release_lock(
            session, workspace_id=workspace_id, key=lock_key
        )
        return DispatchResult(
            fired=False,
            reason=_Reason.CAP_EXCEEDED,
            lock_key=lock_key,
        )

    # 4. Pick a repo to dispatch the workflow on. Workspace bundles
    # operate over every repo in the workspace, but the
    # ``workflow_dispatch`` call needs a single repo target for the
    # GH Actions API. Use the oldest activated repo (deterministic);
    # the runner inside the workflow iterates over every activated
    # repo via Ship's API. The choice of dispatch target affects
    # only which runner identity owns the GHA log.
    target = await _pick_dispatch_repo(session, workspace_id=workspace_id)
    if target is None:
        await release_lock(
            session, workspace_id=workspace_id, key=lock_key
        )
        return DispatchResult(
            fired=False, reason=_Reason.NO_REPO, lock_key=lock_key
        )
    repo, install, _route = target

    try:
        await dispatch_workflow(
            repo,
            install,
            WORKFLOW_FILE,
            inputs={
                "routine_id": bundle_id,
                # Workspace-bundles have no ticket, but ship-agent-run.yml
                # declares ``ticket_ref`` as ``required: true`` and
                # customer repos still carry that copy of the workflow.
                # GitHub rejects an empty string for a required input
                # (422 "Required input 'ticket_ref' not provided"), so
                # pass the bundle id as a placeholder — the runner's
                # workspace-scope branch (run.mjs ``isWorkspaceScope``)
                # ignores ``ticket_ref`` and writes its own empty value
                # into the synthetic task, and the GHA concurrency
                # group ends up as ``ship-agent-run-<bundle>`` which
                # also gives us per-bundle serialisation for free.
                "ticket_ref": bundle_id,
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
                target_kind="workspace_bundle",
                target_id=bundle_id,
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

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            action="agent_run.dispatch",
            target_kind="workspace_bundle",
            target_id=bundle_id,
            payload={
                "trigger_kind": trigger_kind,
                "repo": repo.full_name,
                "workflow_file": WORKFLOW_FILE,
                "routine_id": bundle_id,
            },
        )
    )
    log.info(
        "workspace dispatch fired: ws=%s bundle=%s trigger=%s",
        workspace_id,
        bundle_id,
        trigger_kind,
    )
    return DispatchResult(fired=True, reason=_Reason.FIRED, lock_key=lock_key)


__all__ = [
    "DispatchResult",
    "WORKSPACE_BUNDLE_CAP",
    "acquire_lock",
    "count_active_locks",
    "maybe_dispatch",
    "maybe_dispatch_workspace_bundle",
    "normalize_routine_id",
    "redirect_infra_bounce",
    "release_lock",
    "sweep_expired_locks",
]
