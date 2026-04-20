"""Chat window (C10) — scope tickets with the Ship agent.

Shape:

- ``POST /workspaces/{ws}/chat/threads`` — open a thread with a seed
  prompt (becomes the first user message) and optional repo /
  workflow binding. Returns the thread + initial messages.
- ``GET  /workspaces/{ws}/chat/threads`` — list threads (paginated
  in the future; pilot returns everything).
- ``GET  /workspaces/{ws}/chat/threads/{id}`` — thread + ordered
  messages.
- ``POST /workspaces/{ws}/chat/threads/{id}/messages`` — append a
  user message; server inserts a stub assistant reply. The reply is
  an echo-ish heuristic until we wire a real model.
- ``POST /workspaces/{ws}/chat/threads/{id}/resolve`` — mark the
  thread as resolved with a ticket ref; optionally materialise an
  :class:`Improvement` row from the last assistant message so the
  decision loop picks it up.

We keep the agent "stub" very simple on purpose — the whole point
of this surface is to get the UX right first and swap the model
behind :func:`_agent_reply` when we wire Claude/GPT in Phase-2.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_surface import (
    ChatMessage,
    ChatThread,
    Improvement,
)
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/chat", tags=["chat"]
)


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    role: str
    body: str
    meta: dict
    created_at: datetime


class ChatThreadOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    repo_id: uuid.UUID | None
    workflow_id: str | None
    title: str
    status: str
    resolved_ticket_ref: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int


class ChatThreadDetailOut(ChatThreadOut):
    messages: list[ChatMessageOut]


# ---------------------------------------------------------------------------
# Agent stub
# ---------------------------------------------------------------------------


def _agent_reply(user_message: str, thread: ChatThread) -> str:
    """Produce a placeholder assistant response.

    The stub is intentionally boring so nobody confuses it for a
    real model while the product work finishes. It echoes the user's
    prompt, restates the thread's binding (repo / workflow) so we
    can verify that wiring end-to-end, and suggests a next-step
    affordance. When we swap in a real model, this function becomes
    the only touchpoint that changes.
    """
    lines = [
        "Thanks — logging that.",
    ]
    if thread.repo_id:
        lines.append(
            f"I'll scope this against the repo bound to this thread "
            f"({thread.repo_id})."
        )
    if thread.workflow_id:
        lines.append(
            f"Lane context: {thread.workflow_id}."
        )
    lines.append(
        "Reply *resolve: TICKET-REF* when you want me to materialise a "
        "ticket, or *cancel* to archive this thread."
    )
    summary = user_message.strip().splitlines()[0][:200]
    if summary:
        lines.append(f"You said: “{summary}”")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class ChatThreadCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    initial_message: str = Field(min_length=1, max_length=20_000)
    repo_id: uuid.UUID | None = None
    workflow_id: str | None = Field(default=None, max_length=120)


class ChatMessageAppendIn(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)


class ChatResolveIn(BaseModel):
    ticket_ref: str = Field(min_length=1, max_length=255)
    create_improvement: bool = False
    action: Literal["resolved", "archived"] = "resolved"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg_to_out(row: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=row.id,
        thread_id=row.thread_id,
        role=row.role,
        body=row.body,
        meta=row.meta,
        created_at=row.created_at,
    )


def _thread_to_out(
    row: ChatThread, message_count: int
) -> ChatThreadOut:
    return ChatThreadOut(
        id=row.id,
        workspace_id=row.workspace_id,
        repo_id=row.repo_id,
        workflow_id=row.workflow_id,
        title=row.title,
        status=row.status,
        resolved_ticket_ref=row.resolved_ticket_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
        message_count=message_count,
    )


async def _load_thread(
    session: AsyncSession, workspace_id: uuid.UUID, thread_id: uuid.UUID
) -> ChatThread:
    row = (
        await session.execute(
            select(ChatThread).where(
                ChatThread.id == thread_id,
                ChatThread.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found.",
        )
    return row


async def _thread_messages(
    session: AsyncSession, thread_id: uuid.UUID
) -> list[ChatMessage]:
    rows = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/threads",
    response_model=ChatThreadDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    workspace_id: uuid.UUID,
    payload: ChatThreadCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ChatThreadDetailOut:
    """Open a new thread with a seed user message and a stub reply."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    thread = ChatThread(
        workspace_id=workspace_id,
        repo_id=payload.repo_id,
        created_by_user_id=auth.user.id,
        title=payload.title,
        workflow_id=payload.workflow_id,
    )
    session.add(thread)
    await session.flush()
    await session.refresh(thread)

    user_msg = ChatMessage(
        thread_id=thread.id,
        role="user",
        author_user_id=auth.user.id,
        body=payload.initial_message,
    )
    assistant_msg = ChatMessage(
        thread_id=thread.id,
        role="assistant",
        body=_agent_reply(payload.initial_message, thread),
        meta={"stub": True},
    )
    session.add_all([user_msg, assistant_msg])
    await session.flush()
    messages = await _thread_messages(session, thread.id)

    await session.refresh(thread)
    out = ChatThreadDetailOut(
        **_thread_to_out(thread, len(messages)).model_dump(),
        messages=[_msg_to_out(m) for m in messages],
    )
    return out


@router.get("/threads", response_model=list[ChatThreadOut])
async def list_threads(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ChatThreadOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    rows = (
        await session.execute(
            select(ChatThread)
            .where(ChatThread.workspace_id == workspace_id)
            .order_by(ChatThread.updated_at.desc())
        )
    ).scalars().all()
    # One SELECT count per thread is fine in pilot volumes; if this
    # becomes hot we flip to a materialised view.
    out: list[ChatThreadOut] = []
    for row in rows:
        count = len(await _thread_messages(session, row.id))
        out.append(_thread_to_out(row, count))
    return out


@router.get(
    "/threads/{thread_id}", response_model=ChatThreadDetailOut
)
async def get_thread(
    workspace_id: uuid.UUID,
    thread_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ChatThreadDetailOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    thread = await _load_thread(session, workspace_id, thread_id)
    messages = await _thread_messages(session, thread.id)
    return ChatThreadDetailOut(
        **_thread_to_out(thread, len(messages)).model_dump(),
        messages=[_msg_to_out(m) for m in messages],
    )


@router.post(
    "/threads/{thread_id}/messages",
    response_model=ChatThreadDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def append_message(
    workspace_id: uuid.UUID,
    thread_id: uuid.UUID,
    payload: ChatMessageAppendIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ChatThreadDetailOut:
    """Append a user message and the stub assistant reply.

    Behaviour matters for the UX contract:

    - Refuses to append to ``resolved`` / ``archived`` threads (422);
      the UI should have hidden the composer in those cases. We
      still check because API clients can't be trusted.
    - Bumps ``updated_at`` so the thread list sorts recent ones up.
    - Keeps the stub reply inline so the round-trip is single-shot;
      when we add the real model we keep the contract (client sends
      one message, gets the updated thread back).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    thread = await _load_thread(session, workspace_id, thread_id)
    if thread.status != "active":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Thread is {thread.status}; can't append.",
        )

    user_msg = ChatMessage(
        thread_id=thread.id,
        role="user",
        author_user_id=auth.user.id,
        body=payload.body,
    )
    assistant_msg = ChatMessage(
        thread_id=thread.id,
        role="assistant",
        body=_agent_reply(payload.body, thread),
        meta={"stub": True},
    )
    session.add_all([user_msg, assistant_msg])
    thread.updated_at = datetime.now(timezone.utc)
    await session.flush()
    messages = await _thread_messages(session, thread.id)
    await session.refresh(thread)
    return ChatThreadDetailOut(
        **_thread_to_out(thread, len(messages)).model_dump(),
        messages=[_msg_to_out(m) for m in messages],
    )


@router.post(
    "/threads/{thread_id}/resolve", response_model=ChatThreadDetailOut
)
async def resolve_thread(
    workspace_id: uuid.UUID,
    thread_id: uuid.UUID,
    payload: ChatResolveIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ChatThreadDetailOut:
    """Mark the thread terminal and optionally spawn an Improvement.

    The typical flow: user chats, converges on a proposal, clicks
    "create ticket" → we stamp ``resolved_ticket_ref`` (the caller
    passes the tracker ref) and, when ``create_improvement=True``,
    materialise an :class:`Improvement` row so the C8 page shows
    the accept / decline buttons on it.

    ``action="archived"`` is the "close without a ticket" path —
    the thread ends but no Improvement is created.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    thread = await _load_thread(session, workspace_id, thread_id)
    if thread.status != "active":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Thread is already {thread.status}.",
        )
    thread.status = payload.action
    thread.resolved_ticket_ref = payload.ticket_ref if payload.action == "resolved" else None

    if payload.action == "resolved" and payload.create_improvement:
        # Grab the latest assistant message body as the improvement
        # body — in practice this is the agent's summary of the
        # proposed change. If there isn't one (stub disabled?) we
        # fall back to the thread title.
        messages = await _thread_messages(session, thread.id)
        assistant_tail = next(
            (m.body for m in reversed(messages) if m.role == "assistant"),
            None,
        )
        improvement = Improvement(
            workspace_id=workspace_id,
            repo_id=thread.repo_id,
            kind="chat",
            title=thread.title[:512],
            body=assistant_tail or thread.title,
            context={
                "thread_id": str(thread.id),
                "ticket_ref": payload.ticket_ref,
            },
        )
        session.add(improvement)

    await session.flush()
    await session.refresh(thread)
    messages = await _thread_messages(session, thread.id)
    return ChatThreadDetailOut(
        **_thread_to_out(thread, len(messages)).model_dump(),
        messages=[_msg_to_out(m) for m in messages],
    )


__all__ = ["router"]
