"""Agent-run helpers: the read-and-write surface ``shipctl`` calls
during a routine run.

E14 architecture (locked 2026-04-30):

- A workspace **is** a project. There is exactly **one** tracker
  per workspace (Linear team / Jira project / etc.); the workspace
  may host several repos but they all share the same backlog. The
  endpoints in this module are therefore workspace-scoped — repo
  context is only needed at the agent-runtime level (which checkout
  to spawn), not for tracker resolution.

- Customer's GitHub Actions cron fires ``shipctl run --routine X``.
- ``shipctl`` reads the routine's pattern, asks Ship server for a
  task (a single ticket in the routine's FSM stage, if applicable),
  hands the prompt to a Cursor Cloud agent, polls until the agent
  finishes. Branchless agents (intake, BA, planner) do not commit
  anything to a branch — they call ``POST /agent-runs/finish``
  directly from inside the Cursor runtime to report their outcome.
  Branchful agents (developer, qa) push a code branch *and* call
  ``/agent-runs/finish``; the finish call is the canonical signal,
  the branch is just where the code lives.

  Outcomes the finish endpoint accepts:

  - ``ready_next_step``    → transition ticket to ``stage_next``
                             (and optionally leave a comment).
  - ``needs_clarification`` → tag with ``needs:clarification`` so
                             intake stops re-picking until the
                             human answers; optional comment.
  - ``blocked``             → drop a blocker into the workspace
                             inbox; ticket unchanged.
  - ``out_of_scope``        → close the ticket with optional
                             comment.

``shipctl`` runs in the customer's runner with a workspace API
token; the agent runtime gets the same token in its prompt so it
can call ``/agent-runs/finish``. The server uses the workspace's
Linear OAuth integration to do the actual mutation — the CLI and
the agent never hold the tracker credential.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.services.tracker_resolver import resolve_for_workspace


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["agent-runs"],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TaskTicketOut(BaseModel):
    """A single task the agent will work on, shaped for prompt rendering."""

    ticket_ref: str  # vendor-agnostic id the CLI passes back on writes
    kind: str  # tracker provider — informational, not load-bearing
    title: str
    body: str | None = None
    url: str | None = None
    labels: list[str] = Field(default_factory=list)
    state: str | None = None
    fsm_stage: str | None = None  # echo of the requested stage


class TaskResponseOut(BaseModel):
    """``GET /tracker/next`` response. ``ticket=None`` → no eligible task."""

    ticket: TaskTicketOut | None = None
    fsm_stage: str
    tracker_kind: str | None = None


class TransitionIn(BaseModel):
    ticket_ref: str = Field(min_length=1, max_length=512)
    to_state: str = Field(min_length=1, max_length=64)
    # Optional sanity check: refuse the call if the ticket isn't in this
    # stage on Ship's side. CLI passes its own knowledge of "I just
    # picked this from <stage>" — server uses it to short-circuit a
    # double-fire.
    from_state: str | None = None
    comment: str | None = None  # leave a trail before/with the transition


class CommentIn(BaseModel):
    ticket_ref: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=8000)


class InboxItemIn(BaseModel):
    type: Literal["clarification", "improvement", "blocker", "approval"] = "improvement"
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    ticket_ref: str | None = None


class WriteOut(BaseModel):
    ok: bool = True
    tracker_kind: str | None = None
    note: str | None = None


class FinishIn(BaseModel):
    """Payload the Cursor agent posts to ``/agent-runs/finish`` from
    inside its runtime once it's done with the work.

    A workspace API token is rendered into the agent's prompt, so the
    agent can call this endpoint without holding any tracker credential.
    The server resolves the workspace's Linear OAuth row and applies
    the side-effects implied by ``outcome``.
    """

    run_id: str = Field(min_length=1, max_length=128)
    outcome: Literal[
        "ready_next_step",
        "needs_clarification",
        "blocked",
        "out_of_scope",
    ]
    fsm_stage: str | None = Field(default=None, max_length=64)
    stage_next: str | None = Field(default=None, max_length=64)
    ticket_ref: str | None = Field(default=None, max_length=512)
    comment: str | None = Field(default=None, max_length=8000)
    summary: str | None = Field(default=None, max_length=2000)
    # Shape-the-ticket-itself surface for stages that should rewrite
    # the issue body (intake, BA, planner). When set on a
    # ``ready_next_step`` finish, the server replaces the tracker
    # description with this markdown — comments are reserved for
    # auditable narration ("what I did and why"), the description
    # carries the structured shape (Problem / Goal / AC / Scope /
    # Risks / etc.). Linear keeps prior bodies in the issue activity
    # feed so the operator can always see what changed.
    description: str | None = Field(default=None, max_length=20_000)
    # Which process the run executed under. Defaults to the per-ticket
    # SDLC (``development``); ``decomposition`` (ELS-75) is the
    # project-anchor pipeline. The finish hook reads this to know
    # whether ``stage_next='planning_done'`` should flip the project's
    # dashboard row from Drafts → Parked (the PO promotes Parked →
    # Active manually when ready to ship; ELS-81).
    process: Literal["development", "decomposition"] = "development"
    payload: dict[str, Any] = Field(default_factory=dict)


class FinishOut(BaseModel):
    ok: bool = True
    outcome: str
    run_id: str
    actions: list[str] = Field(default_factory=list)
    tracker_kind: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vendor_kind_to_ticket_kind(vendor_kind: str) -> Literal[
    "github_issues", "linear", "notion", "jira"
]:
    if vendor_kind == "linear":
        return "linear"
    if vendor_kind == "github_issues":
        return "github_issues"
    if vendor_kind == "jira":
        return "jira"
    return "github_issues"  # safe default for the pilot


async def _try_ticket_snapshot(gateway: Any, ref: TicketRef) -> dict[str, Any] | None:
    """Best-effort source-ticket fetch for inbox payloads.

    Adapter is allowed to either expose ``get_ticket_snapshot`` (Linear
    today) or not — and the call itself can fail (Linear timeout,
    deleted ticket). Either way we just return ``None``; the inbox row
    still gets created without a snapshot so the operator at least
    sees the agent's question.
    """
    fn = getattr(gateway, "get_ticket_snapshot", None)
    if fn is None:
        return None
    try:
        return await fn(ref)
    except Exception:
        return None


def _ticket_ref_from(vendor_kind: str, raw: str) -> TicketRef:
    """Hydrate a vendor-agnostic ``ticket_ref`` string into a typed
    :class:`TicketRef` for adapter calls.

    The string format we standardise on:

    - ``linear``        → display id, e.g. ``ENG-42`` (the adapter
      resolves to UUID at call time).
    - ``github_issues`` → ``owner/repo#42``.
    """
    return TicketRef(
        kind=_vendor_kind_to_ticket_kind(vendor_kind),
        workspace_hint=None,
        id=raw,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/tracker/next",
    response_model=TaskResponseOut,
)
async def get_next_task(
    workspace_id: uuid.UUID,
    state: str = Query(..., min_length=1, max_length=64, alias="state"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TaskResponseOut:
    """Return the next ticket the agent should work on for ``state``."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    if resolved is None:
        return TaskResponseOut(ticket=None, fsm_stage=state, tracker_kind=None)

    rows = await resolved.gateway.list_tickets(state=state, limit=10)
    if not rows:
        return TaskResponseOut(
            ticket=None, fsm_stage=state, tracker_kind=resolved.kind
        )

    pick = rows[0]
    return TaskResponseOut(
        fsm_stage=state,
        tracker_kind=resolved.kind,
        ticket=TaskTicketOut(
            ticket_ref=str(pick.get("id") or ""),
            kind=resolved.kind,
            title=str(pick.get("title") or ""),
            body=pick.get("body") if isinstance(pick.get("body"), str) else None,
            url=str(pick["url"]) if pick.get("url") else None,
            labels=list(pick.get("labels") or []),
            state=str(pick.get("status") or "") or None,
            fsm_stage=state,
        ),
    )


@router.post("/tracker/transition", response_model=WriteOut)
async def transition_ticket(
    workspace_id: uuid.UUID,
    payload: TransitionIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WriteOut:
    """Move ``payload.ticket_ref`` to ``payload.to_state`` (FSM stage).

    The vendor adapter knows how to map the abstract Ship FSM stage
    (``ba_requirements`` etc.) to its native state — Linear status
    name, etc. This is the only place that mapping happens, so CLI
    doesn't need to grow per-vendor logic.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "no_tracker_bound"},
        )

    ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
    if payload.comment:
        await resolved.gateway.comment(ref, body=payload.comment)
    await resolved.gateway.transition(ref, to_state=payload.to_state)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.transition",
            target_kind="ticket",
            target_id=payload.ticket_ref,
            payload={
                "tracker_kind": resolved.kind,
                "from_state": payload.from_state,
                "to_state": payload.to_state,
                "had_comment": bool(payload.comment),
            },
        )
    )
    await session.flush()
    return WriteOut(ok=True, tracker_kind=resolved.kind)


@router.post("/tracker/comment", response_model=WriteOut)
async def comment_ticket(
    workspace_id: uuid.UUID,
    payload: CommentIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WriteOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "no_tracker_bound"},
        )
    ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
    await resolved.gateway.comment(ref, body=payload.body)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.comment",
            target_kind="ticket",
            target_id=payload.ticket_ref,
            payload={
                "tracker_kind": resolved.kind,
                "body_chars": len(payload.body),
            },
        )
    )
    await session.flush()
    return WriteOut(ok=True, tracker_kind=resolved.kind)


@router.post("/inbox/items", response_model=WriteOut)
async def post_inbox_item(
    workspace_id: uuid.UUID,
    payload: InboxItemIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> WriteOut:
    """Drop a free-form item into the workspace inbox.

    Used by:
      - context-free routines (daily digest, learning capture) that
        don't transition tickets — they leave their output as an
        operator-facing inbox row.
      - ``shipctl run`` when the agent's state is ``blocked`` or
        ``human_validation`` — captures the question or the missing
        prerequisite as an inbox item the operator can act on.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    item = InboxItem(
        workspace_id=workspace_id,
        repo_id=None,
        type=payload.type,
        title=payload.title[:300],
        summary=(payload.summary or "")[:2000] or None,
        payload={
            **payload.payload,
            "ticket_ref": payload.ticket_ref,
            "produced_at": datetime.now(timezone.utc).isoformat(),
        },
        status="new",
        intake_handle=None,
        intake_reason="agent_run",
    )
    session.add(item)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.inbox_item",
            target_kind="inbox_item",
            target_id=None,
            payload={
                "type": payload.type,
                "title": payload.title[:300],
                "ticket_ref": payload.ticket_ref,
            },
        )
    )
    await session.flush()
    return WriteOut(ok=True, note=f"inbox item created (type={payload.type})")


async def _flip_drafts_row_to_parked(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    resolved,  # ResolvedTracker; avoid circular type import
) -> bool:
    """Flip the project's priorities row from ``planning`` → ``parked``.

    Called when a decomposition run reaches the terminal stage. The PO
    explicitly promotes ``parked`` → ``active`` from the dashboard
    when ready to ship — Ship doesn't auto-activate. (ELS-81; previous
    behaviour was to auto-flip to ``active``.)

    Walks: ticket_ref → planning anchor's project_id → priorities row
    → state. Best-effort: a missing priorities row, a deleted project,
    or an adapter that can't tell us the project all log + return False
    instead of failing the finish handler.
    """
    from sqlalchemy import select

    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    # The ticket_ref the agent passes is the anchor's identifier
    # (e.g. ``ELS-83``). Snapshot the issue to read its project_id,
    # then look up the priorities row by the project's native id
    # (Linear UUID / Jira project key — the same id we stamped on
    # the row in PR1's ``_tool_create_project``).
    ref = _ticket_ref_from(resolved.kind, ticket_ref)
    snapshot_fn = getattr(resolved.gateway, "get_ticket_snapshot", None)
    if snapshot_fn is None:
        logger.warning(
            "decomposition completion: tracker_kind=%s lacks "
            "get_ticket_snapshot — cannot resolve project for "
            "ticket=%s; row not flipped",
            resolved.kind,
            ticket_ref,
        )
        return False
    try:
        snapshot = await snapshot_fn(ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "decomposition completion: get_ticket_snapshot failed "
            "ticket=%s err=%s",
            ticket_ref,
            exc,
        )
        return False
    if not snapshot:
        return False
    project_id = snapshot.get("project_id")
    if not project_id:
        logger.warning(
            "decomposition completion: anchor ticket=%s has no project_id; "
            "row not flipped",
            ticket_ref,
        )
        return False

    row = (
        await session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace_id,
                WorkspaceProjectPriority.project_native_id == str(project_id),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        logger.warning(
            "decomposition completion: no priorities row for "
            "workspace=%s project=%s; row not flipped",
            workspace_id,
            project_id,
        )
        return False
    if row.state == "parked":
        return False
    row.state = "parked"
    await session.flush()
    return True


@router.post("/agent-runs/finish", response_model=FinishOut)
async def finish_agent_run(
    workspace_id: uuid.UUID,
    payload: FinishIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinishOut:
    """One-shot terminal endpoint the Cursor agent calls when done.

    Replaces the old ``.ship/run-state.json`` file contract — branchless
    agents (intake, BA, planner) don't commit anything; they POST here
    with the outcome they reached. ``shipctl agent-run`` no longer reads
    a state file; the server is the single source of truth for what the
    run did.

    Idempotency: if the same ``run_id`` has already finished, this is a
    no-op so duplicate ``finish`` calls (network retries from inside
    the agent) don't double-write.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    # Idempotency: bail out if we've already recorded a finish for this
    # run_id under the same workspace.
    from sqlalchemy import select as sa_select
    prev = (
        await session.execute(
            sa_select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.finish",
                AuditLog.target_id == payload.run_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if prev is not None:
        return FinishOut(
            ok=True,
            outcome=payload.outcome,
            run_id=payload.run_id,
            actions=["duplicate_ignored"],
            tracker_kind=(prev.payload or {}).get("tracker_kind"),
        )

    actions: list[str] = []
    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    tracker_kind = resolved.kind if resolved else None

    # Outcomes that need a tracker but the workspace has none → drop an
    # inbox item so the operator notices, but still record the run.
    if payload.outcome in {"ready_next_step", "needs_clarification", "out_of_scope"} \
            and resolved is None:
        session.add(
            InboxItem(
                workspace_id=workspace_id,
                repo_id=None,
                type="blocker",
                title=f"agent finished but no tracker bound ({payload.outcome})"[:300],
                summary=(payload.summary or payload.comment or "")[:2000] or None,
                payload={
                    "run_id": payload.run_id,
                    "fsm_stage": payload.fsm_stage,
                    "ticket_ref": payload.ticket_ref,
                    "outcome": payload.outcome,
                    **payload.payload,
                },
                status="new",
                intake_handle=None,
                intake_reason="agent_run_no_tracker",
            )
        )
        actions.append("inbox:no_tracker_bound")

    elif payload.outcome == "ready_next_step":
        # Context-free routines (daily_*, audits with no findings, intake
        # on an empty queue) finish with ``ready_next_step`` + no ticket
        # — there's no work to transition. We accept this as a tracker
        # no-op: only the audit row at the bottom of this handler runs.
        # No inbox row, because "agent did its job and nothing was due"
        # is not a thing that needs human attention.
        if not payload.ticket_ref:
            actions.append("noop:no_ticket")
        else:
            if not payload.stage_next:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "stage_next_required", "outcome": payload.outcome},
                )
            ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
            # Description rewrite goes first so a downstream comment can
            # reference "see updated description above" without the
            # comment being older than the body it points at. Adapter
            # may not implement the verb yet (jira / github_issues /
            # notion in the pilot) — best-effort, log and continue.
            if payload.description:
                setter = getattr(resolved.gateway, "set_description", None)
                if setter is None:
                    logger.warning(
                        "agent_run.finish: set_description not implemented "
                        "for tracker_kind=%s; ticket=%s — description ignored",
                        resolved.kind,
                        payload.ticket_ref,
                    )
                else:
                    await setter(ref, body=payload.description)
                    actions.append("tracker:set_description")
            if payload.comment:
                await resolved.gateway.comment(ref, body=payload.comment)
                actions.append("tracker:comment")
            await resolved.gateway.transition(ref, to_state=payload.stage_next)
            actions.append(f"tracker:transition:{payload.stage_next}")

            # Decomposition completion hook (ELS-75). When the planning
            # anchor reaches the terminal stage, flip the project's
            # dashboard row Drafts → Parked so the project sits ready
            # for the PO to promote when they decide it's worth the
            # capacity (ELS-81; the previous behaviour auto-activated,
            # which let agents start chewing on every project the
            # moment its decomposition finished). We key on the
            # explicit ``process='decomposition'`` flag rather than
            # sniffing labels — the runtime that's executing the run
            # knows which process it's under, the server should not
            # have to re-derive that. Best-effort: a missing priorities
            # row logs and continues; the audit trail at the bottom of
            # this handler still records what happened.
            if (
                payload.process == "decomposition"
                and payload.stage_next == "planning_done"
            ):
                flipped = await _flip_drafts_row_to_parked(
                    session, workspace_id, payload.ticket_ref, resolved
                )
                if flipped:
                    actions.append("priorities:parked")

    elif payload.outcome == "needs_clarification":
        if not payload.ticket_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "ticket_ref_required", "outcome": payload.outcome},
            )
        ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
        if payload.comment:
            await resolved.gateway.comment(ref, body=payload.comment)
            actions.append("tracker:comment")
        # Linear adapter exposes ``add_signal_label``; other adapters
        # need to grow it before this outcome works for them.
        adder = getattr(resolved.gateway, "add_signal_label", None)
        if adder is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "code": "needs_clarification_unsupported",
                    "tracker_kind": resolved.kind,
                },
            )
        await adder(ref, key="needs_clarification")
        actions.append("tracker:label:needs_clarification")
        # Snapshot the source ticket onto the inbox row so the
        # operator can read the original ask without flipping to
        # Linear — without it, the clarification card is just the
        # agent's question with no context for what it's about.
        ticket_snapshot = await _try_ticket_snapshot(resolved.gateway, ref)
        # Mirror to inbox so the operator sees it without scanning the
        # tracker — the agent's question is the inbox row's summary.
        session.add(
            InboxItem(
                workspace_id=workspace_id,
                repo_id=None,
                type="clarification",
                title=f"clarification: {payload.ticket_ref}"[:300],
                summary=(payload.summary or payload.comment or "")[:2000] or None,
                payload={
                    "run_id": payload.run_id,
                    "fsm_stage": payload.fsm_stage,
                    "ticket_ref": payload.ticket_ref,
                    **({"source_ticket": ticket_snapshot} if ticket_snapshot else {}),
                    **payload.payload,
                },
                status="new",
                intake_handle=None,
                intake_reason="agent_run_clarification",
            )
        )
        actions.append("inbox:clarification")

    elif payload.outcome == "blocked":
        # No ticket move — operator sees the blocker via inbox. Snapshot
        # the source ticket if there was one (blockers tied to a
        # specific ticket get the same context as clarifications).
        ticket_snapshot = None
        if payload.ticket_ref and resolved is not None:
            ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
            ticket_snapshot = await _try_ticket_snapshot(resolved.gateway, ref)
        session.add(
            InboxItem(
                workspace_id=workspace_id,
                repo_id=None,
                type="blocker",
                title=f"agent blocked: {payload.fsm_stage or 'run'}"[:300],
                summary=(payload.summary or payload.comment or "")[:2000] or None,
                payload={
                    "run_id": payload.run_id,
                    "fsm_stage": payload.fsm_stage,
                    "ticket_ref": payload.ticket_ref,
                    **({"source_ticket": ticket_snapshot} if ticket_snapshot else {}),
                    **payload.payload,
                },
                status="new",
                intake_handle=None,
                intake_reason="agent_run_blocked",
            )
        )
        actions.append("inbox:blocker")

    elif payload.outcome == "out_of_scope":
        if not payload.ticket_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "ticket_ref_required", "outcome": payload.outcome},
            )
        ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
        if payload.comment:
            await resolved.gateway.comment(ref, body=payload.comment)
            actions.append("tracker:comment")
        # ``Done`` is the legacy workflow-state path in LinearTracker —
        # it resolves the state by literal name, not via FSM map.
        await resolved.gateway.transition(ref, to_state="Done")
        actions.append("tracker:transition:Done")

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.finish",
            target_kind="agent_run",
            target_id=payload.run_id,
            payload={
                "tracker_kind": tracker_kind,
                "outcome": payload.outcome,
                "fsm_stage": payload.fsm_stage,
                "stage_next": payload.stage_next,
                "ticket_ref": payload.ticket_ref,
                "actions": actions,
                "had_comment": bool(payload.comment),
            },
        )
    )
    await session.flush()
    return FinishOut(
        ok=True,
        outcome=payload.outcome,
        run_id=payload.run_id,
        actions=actions,
        tracker_kind=tracker_kind,
    )
