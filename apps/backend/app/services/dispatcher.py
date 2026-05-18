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
import re
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
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.github.workflows import (
    WorkflowDispatchError,
    dispatch_workflow,
)
from backend.app.services import tracker_resolver as tracker_resolver_module


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

# Per-project lock TTL. Holds across the full ticket lifecycle until
# the next ``pull_request.closed merged=true`` webhook releases it
# (``apps/backend/app/api/v1/routes/github_app.py`` →
# ``_release_project_lock_for_merged_pr``). 24h is the belt-and-
# braces upper bound — for a non-anchor ticket that legitimately
# takes that long, the operator already has a human PR-review in
# flight and can extend via re-trigger. Without this fallback an
# abandoned PR strands every other ticket in the project for
# weeks.
PROJECT_LOCK_TTL_S: Final[int] = 24 * 60 * 60

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


async def _emit_env_separation_warning(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: str,
    project_name: str,
) -> None:
    """One-time inbox warning when auto-dispatch first touches a project.

    The operator opted into Ship by clicking through the wizard, but
    that consent doesn't extend to "let agents merge PRs into prod
    main on day one". When the per-project WIP gate first acquires
    the lock for a project we drop a single warning into the inbox
    surfacing the env-separation expectation. Idempotent via
    ``InboxItem.intake_handle`` — a second dispatch on the same
    project doesn't re-spam.
    """
    # ``InboxItem.intake_handle`` is VARCHAR(64); two full UUIDs +
    # prefix overflow at 82 chars and the poller's transaction
    # rolls back with ``StringDataRightTruncationError``, stalling
    # the entire dispatch chain (caught on askslayer 2026-05-15
    # right after this guard was added). Short-prefix the IDs —
    # collision risk is per-workspace + per-project and 8 hex chars
    # of each is well under 2⁻³² per pair within a single tenant.
    handle = f"env-warn:{str(workspace_id)[:8]}:{str(project_id)[:8]}"
    existing = (
        await session.execute(
            select(InboxItem.id).where(InboxItem.intake_handle == handle).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    from backend.app.services.inbox.headline import derive_headline

    warn_title = (
        f"Agents will auto-merge PRs in project {project_name!r}"
    )[:300]
    warn_summary = (
        "Ship just started dispatching agent work on a ticket in "
        f"project {project_name!r}. Tickets in the project run "
        "one-at-a-time and the planned auto-merger bundle will "
        "ship PRs into your default branch when the reviewer "
        "is confident. **Strongly recommended before you let "
        "this run unattended:**\n\n"
        "1. Use a non-prod default branch (``develop`` / "
        "``main`` with deploy gates downstream) and let the "
        "agent open PRs against it — not directly into prod.\n"
        "2. Make sure your DB / API base URL / payment keys "
        "in this repo's GH Actions secrets are dev/staging, "
        "not prod credentials.\n"
        "3. Keep ``CODEOWNERS`` pointed at a human reviewer "
        "for risky paths (auth, migrations, payments) so the "
        "auto-merger can't skip required review.\n\n"
        "Acknowledge this once and Ship won't warn again for "
        "this project."
    )
    session.add(
        InboxItem(
            workspace_id=workspace_id,
            repo_id=None,
            type="warning",
            title=warn_title,
            headline=derive_headline(summary=warn_summary, title=warn_title),
            summary=warn_summary,
            payload={
                "project_id": project_id,
                "project_name": project_name,
                "reason": "auto_dispatch_first_touch",
            },
            status="new",
            intake_handle=handle,
            intake_reason="env_separation_warning",
        )
    )


async def _pick_dispatch_repo(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_name_hint: str | None = None,
) -> tuple[WorkspaceRepo, GitHubInstallation] | None:
    """Pick one activated repo in ``workspace_id`` to dispatch to.

    Preference order:

    1. **Project name match** — when ``project_name_hint`` is provided
       (the Linear project the ticket lives in), pick a repo whose
       ``full_name`` contains a slug-shaped token from the project
       name. E.g. project ``"Переписывание visitor-back на Golang"``
       matches ``askslayer/visitor-back``. This is the "follow the
       ticket's repo" rule callers used to have to read from a
       per-project binding row — keeping it heuristic for now
       sidesteps a schema migration while still routing PAC tickets
       to the visitor-back repo where the agent secrets actually
       live (askslayer 2026-05-15: visitor-mob was getting every
       dispatch because it was oldest-activated, but ANTHROPIC_API_KEY
       only lived on visitor-back).
    2. **Oldest activated** — original deterministic fallback. The
       runtime inside the workflow re-reads the ticket from Linear
       so the dispatch target is mostly a runner-host choice; this
       falls through when no hint is supplied (workspace bundles,
       legacy callers) or no repo name matches.
    """
    rows = (
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
        )
    ).all()
    if not rows:
        return None
    if project_name_hint:
        # Tokenize on whitespace / punctuation, keep slug-shaped chunks
        # (at least 4 chars) so common Russian / English connecting
        # words don't accidentally match short repo names.
        tokens = [
            t.lower() for t in re.split(r"[^A-Za-z0-9_-]+", project_name_hint)
            if len(t) >= 4
        ]
        for r in rows:
            name = r[0].full_name.lower()
            if any(t in name for t in tokens):
                return r[0], r[1]
    return rows[0][0], rows[0][1]


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
        project_name_hint=project_name_hint,
    )
    if target is None:
        await release_lock(
            session, workspace_id=workspace_id, key=lock_key
        )
        if project_id and not is_anchor:
            await release_lock(
                session, workspace_id=workspace_id, key=f"project:{project_id}"
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
                # Stash project_id so /tracker/next can fall back to
                # it when synthesising a row from audit on a stale
                # tracker replica (orphan gate needs project_id).
                "project_id": project_id,
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
    repo, install = target

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
    "release_lock",
    "sweep_expired_locks",
]
