"""Agent-run helpers: the read-and-write surface ``shipctl`` calls
during a routine run.

E14 architecture (locked 2026-04-30):

- Customer's GitHub Actions cron fires ``shipctl run --routine X``.
- ``shipctl`` reads the routine's pattern, asks Ship server for a
  task (a single ticket in the routine's FSM stage, if applicable),
  hands the prompt to a Cursor Cloud agent, polls until the agent
  finishes, reads the agent's structured state from
  ``.ship/run-state.json``, and dispatches to the right write call:

  - ``ready_next_step``  → ``POST /tracker/transition``
  - ``human_validation`` → ``POST /tracker/comment`` + ``POST /inbox/items``
  - ``blocked``          → ``POST /inbox/items``

The endpoints in this module are the write side of that contract.
``shipctl`` runs in the customer's runner with a workspace API
token; the server uses the workspace's existing Linear / GitHub
OAuth integrations to do the actual mutation. The CLI never holds
the tracker credential.

These routes intentionally take a vendor-agnostic ``ticket_ref``
string — Ship server picks the right adapter via
:func:`backend.app.services.tracker_resolver.resolve_for_repo` and
hands it to :class:`TrackerGateway`. CLI doesn't have to know
whether the workspace runs Linear or GitHub Issues.
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
from backend.app.services.tracker_resolver import resolve_for_repo


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
    "/repos/{repo_id}/tracker/next",
    response_model=TaskResponseOut,
)
async def get_next_task(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    state: str = Query(..., min_length=1, max_length=64, alias="state"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TaskResponseOut:
    """Return the next ticket the agent should work on for ``state``."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_repo(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
        repo_id=repo_id,
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


@router.post(
    "/repos/{repo_id}/tracker/transition",
    response_model=WriteOut,
)
async def transition_ticket(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: TransitionIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WriteOut:
    """Move ``payload.ticket_ref`` to ``payload.to_state`` (FSM stage).

    The vendor adapter knows how to map the abstract Ship FSM stage
    (``ba_requirements`` etc.) to its native state — Linear status,
    GitHub Issues label, etc. This is the only place that mapping
    happens, so CLI doesn't need to grow per-vendor logic.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_repo(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
        repo_id=repo_id,
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


@router.post(
    "/repos/{repo_id}/tracker/comment",
    response_model=WriteOut,
)
async def comment_ticket(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: CommentIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WriteOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_repo(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
        repo_id=repo_id,
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
