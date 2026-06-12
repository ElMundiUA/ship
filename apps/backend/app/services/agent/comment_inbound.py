"""Linear-comment → Navigator inbound (thesis 4, ELS-251).

The tracker poller hands fresh NON-AGENT comments on active tickets to
this service, which appends them as a user-role turn on the ticket's
Navigator thread and drives one agent turn so the reply lands in the
same conversation the operator reads in the Console.

CRITICAL INVARIANT (thesis 2): this module is context-only. It never
calls ``transition()``, never writes a stage-changing tracker event —
the tracker STATUS field stays the only FSM transition signal
(``tracker_fsm`` enforces it; the test suite pins it here too).

The turn loop is the same ``_run_agent_turn`` generator the
``POST /chat/stream`` route drives — imported as a function, NOT
self-called over HTTP (per the ELS-251 design note). The SSE frames
it yields are drained and discarded; the durable outcome is the
persisted assistant message on the thread.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.agent_surface import ChatMessage, ChatThread
from backend.app.db.models.tenancy import WorkspaceMember

logger = logging.getLogger(__name__)


async def _resolve_service_actor(
    session: AsyncSession, workspace_id: uuid.UUID
) -> uuid.UUID | None:
    """Pick the user identity the ingested turn runs under.

    The chat turn loop (TopicService / ToolBox) requires a real
    member id for authz + audit. Prefer the workspace owner, then
    any admin — the same set that could have typed the message into
    the Console themselves.
    """
    for roles in (("owner",), ("admin",), ("member",)):
        row = (
            await session.execute(
                select(WorkspaceMember.user_id)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.role.in_(roles),
                )
                .order_by(WorkspaceMember.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row:
            return row
    return None


async def _find_or_create_ticket_thread(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    ticket_title: str | None,
    actor_user_id: uuid.UUID,
) -> ChatThread:
    """Return the ticket's active Navigator thread, creating one when
    none exists. ``resolved_ticket_ref`` doubles as the ticket binding
    key — threads that SPAWNED the ticket already carry it, and the
    poller-created thread reuses the same column so the Console's
    existing deep-links keep working."""
    thread = (
        await session.execute(
            select(ChatThread)
            .where(
                ChatThread.workspace_id == workspace_id,
                ChatThread.resolved_ticket_ref == ticket_ref,
                ChatThread.status == "active",
            )
            .order_by(ChatThread.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if thread is not None:
        return thread
    thread = ChatThread(
        workspace_id=workspace_id,
        created_by_user_id=actor_user_id,
        title=f"{ticket_ref} — {(ticket_title or 'tracker thread')[:480]}",
        status="active",
        resolved_ticket_ref=ticket_ref,
    )
    session.add(thread)
    await session.flush()
    return thread


async def ingest_operator_comment(
    session: AsyncSession,
    *,
    settings: Settings,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    ticket_title: str | None,
    comment_id: str,
    comment_body: str,
    comment_author: str | None,
) -> bool:
    """Append one operator comment as a Navigator turn. Returns True
    when a turn ran, False when ingestion was skipped (no actor, no
    LLM, empty body). Best-effort by contract — the caller (poller)
    wraps this in try/except so chat problems never break polling."""
    body = (comment_body or "").strip()
    if not body:
        return False

    actor_user_id = await _resolve_service_actor(session, workspace_id)
    if actor_user_id is None:
        logger.warning(
            "comment_inbound: workspace %s has no members; skipping",
            workspace_id,
        )
        return False

    from backend.app.services.agent.client import pick_default_client

    try:
        agent = pick_default_client(settings)
    except RuntimeError:
        logger.warning(
            "comment_inbound: no LLM configured; skipping ws=%s ref=%s",
            workspace_id,
            ticket_ref,
        )
        return False

    thread = await _find_or_create_ticket_thread(
        session,
        workspace_id=workspace_id,
        ticket_ref=ticket_ref,
        ticket_title=ticket_title,
        actor_user_id=actor_user_id,
    )

    prefix = (
        f"[Linear comment on {ticket_ref}"
        + (f" by {comment_author}" if comment_author else "")
        + "]\n\n"
    )
    user_msg = ChatMessage(
        thread_id=thread.id,
        role="user",
        author_user_id=actor_user_id,
        body=prefix + body,
        meta={
            "source": "linear_comment",
            "comment_id": comment_id,
            "ticket_ref": ticket_ref,
            "author": comment_author,
        },
    )
    session.add(user_msg)
    thread.last_user_activity_at = datetime.now(timezone.utc)
    await session.flush()

    # Local import — the turn generator lives next to the HTTP route
    # but is a plain async generator; driving it here is the
    # service-function extraction ELS-251 asked for (no HTTP hop).
    from backend.app.api.v1.routes.chat import _run_agent_turn

    async for _frame in _run_agent_turn(
        session=session,
        settings=settings,
        agent=agent,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        thread=thread,
        user_msg=user_msg,
        user_attachments=[],
        classify_shift=False,
    ):
        pass
    return True
