"""Public callback endpoints for Cursor-Cloud-launched agents.

A routine run hands the agent a short-lived bearer (see
:mod:`backend.app.services.routine_run_token`) plus a small set of
``curl`` recipes. The agent calls these endpoints to write back into
Ship — comment on the bound ticket, transition the FSM stage, drop
items into the workspace inbox — without ever holding a Linear or
GitHub credential. Ship server uses the workspace's existing OAuth
integration to do the actual write through :class:`TrackerGateway`.

All endpoints here are **session-less**: auth is the routine-run JWT
in ``Authorization: Bearer <token>``. They live under ``/v1/`` (not
``/v1/workspaces/{ws}/...``) so the caller doesn't need to know the
workspace UUID — it's encoded in the bearer.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.db.session import get_session
from backend.app.integrations.gateway.tracker import TicketRef, TrackerGateway
from backend.app.integrations.github.issues_tracker import GitHubIssuesTracker
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.services.routine_run_token import RoutineRunClaims, decode


router = APIRouter(prefix="/routine-callbacks", tags=["routine-callbacks"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


async def _claims(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> RoutineRunClaims:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing routine bearer token",
        )
    token = authorization.split(None, 1)[1].strip()
    return decode(token=token, settings=settings)


# ---------------------------------------------------------------------------
# Tracker gateway resolution
# ---------------------------------------------------------------------------


async def _resolve_tracker(
    *,
    session: AsyncSession,
    settings: Settings,
    claims: RoutineRunClaims,
) -> tuple[TrackerGateway, str]:
    """Pick the tracker the agent should write to.

    Preference order: Linear (workspace-level OAuth) → GitHub Issues
    (the bound code host on the repo). Mirrors the routine dispatch
    side of E14 — we resolve the same way at write-back time.
    """
    from backend.app.api.v1.routes.integrations import decrypt  # lazy

    # 1. Linear.
    linear_row = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == claims.workspace_id,
                Integration.kind == "linear",
                Integration.repo_id.is_(None),
            )
        )
    ).scalars().first()
    if linear_row is not None and linear_row.secret_ciphertext:
        try:
            token = decrypt(linear_row.secret_ciphertext)
        except Exception as exc:  # noqa: BLE001
            logger.warning("linear token unreadable: %s", exc)
        else:
            return LinearTracker(token), "linear"

    # 2. GitHub Issues for the bound repo.
    repo_row = await session.get(WorkspaceRepo, claims.repo_id)
    if repo_row is None or repo_row.workspace_id != claims.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="repo not bound to workspace",
        )
    install = await session.get(GitHubInstallation, repo_row.installation_id)
    if install is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="github installation missing",
        )
    owner, _, name = (repo_row.full_name or "").partition("/")
    if not owner or not name:
        raise HTTPException(
            status_code=500, detail="invalid full_name"
        )
    return (
        GitHubIssuesTracker(
            installation_id=install.installation_id,
            owner=owner,
            repo=name,
            settings=settings,
        ),
        "github_issues",
    )


def _ticket_ref(claims: RoutineRunClaims, override: str | None) -> TicketRef:
    raw = override or claims.ticket_id
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no ticket bound to this run; pass ticket_id explicitly",
        )
    # Format we use: ``owner/repo#N`` for github, ``ENG-12`` for Linear.
    kind = "github_issues" if "#" in raw and "/" in raw else "linear"
    return TicketRef(kind=kind, raw=raw)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    # Optional override; default = the ticket bound to the run.
    ticket_id: str | None = None


class TransitionIn(BaseModel):
    to_state: str = Field(min_length=1, max_length=64)
    ticket_id: str | None = None


class InboxItemIn(BaseModel):
    type: str = Field(default="improvement", min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)


class CallbackOut(BaseModel):
    ok: bool = True
    provider: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/comment", response_model=CallbackOut)
async def post_comment(
    payload: CommentIn,
    claims: RoutineRunClaims = Depends(_claims),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CallbackOut:
    gateway, provider = await _resolve_tracker(
        session=session, settings=settings, claims=claims
    )
    ref = _ticket_ref(claims, payload.ticket_id)
    await gateway.comment(ref, body=payload.body)
    session.add(
        AuditLog(
            workspace_id=claims.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="routine_callback.comment",
            target_kind="ticket",
            target_id=ref.raw,
            payload={
                "routine_id": claims.routine_id,
                "pattern": claims.pattern,
                "agent_id": claims.agent_id,
                "provider": provider,
                "body_chars": len(payload.body),
            },
        )
    )
    await session.flush()
    return CallbackOut(ok=True, provider=provider)


@router.post("/transition", response_model=CallbackOut)
async def post_transition(
    payload: TransitionIn,
    claims: RoutineRunClaims = Depends(_claims),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CallbackOut:
    gateway, provider = await _resolve_tracker(
        session=session, settings=settings, claims=claims
    )
    ref = _ticket_ref(claims, payload.ticket_id)
    await gateway.transition(ref, to_state=payload.to_state)
    session.add(
        AuditLog(
            workspace_id=claims.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="routine_callback.transition",
            target_kind="ticket",
            target_id=ref.raw,
            payload={
                "routine_id": claims.routine_id,
                "pattern": claims.pattern,
                "agent_id": claims.agent_id,
                "provider": provider,
                "to_state": payload.to_state,
            },
        )
    )
    await session.flush()
    return CallbackOut(ok=True, provider=provider)


@router.post("/inbox-item", response_model=CallbackOut)
async def post_inbox_item(
    payload: InboxItemIn,
    claims: RoutineRunClaims = Depends(_claims),
    session: AsyncSession = Depends(get_session),
) -> CallbackOut:
    """Drop a free-form item into the workspace inbox.

    Used by context-free routines (daily digest, learning capture)
    that don't transition tickets — they leave their output as an
    operator-facing inbox row.
    """
    item = InboxItem(
        workspace_id=claims.workspace_id,
        repo_id=claims.repo_id,
        type=payload.type,
        title=payload.title[:300],
        summary=(payload.summary or "")[:2000] or None,
        payload={
            **payload.payload,
            "routine_id": claims.routine_id,
            "pattern": claims.pattern,
            "agent_id": claims.agent_id,
            "produced_at": datetime.now(timezone.utc).isoformat(),
        },
        status="new",
        intake_handle=None,
        intake_reason=f"routine:{claims.routine_id}",
    )
    session.add(item)
    session.add(
        AuditLog(
            workspace_id=claims.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="routine_callback.inbox_item",
            target_kind="inbox_item",
            target_id=None,
            payload={
                "routine_id": claims.routine_id,
                "pattern": claims.pattern,
                "agent_id": claims.agent_id,
                "type": payload.type,
                "title": payload.title[:300],
            },
        )
    )
    await session.flush()
    return CallbackOut(ok=True, note=f"inbox item created (type={payload.type})")
