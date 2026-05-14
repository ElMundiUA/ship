"""Chat surface for the C12 single-window agent.

This module owns three distinct surfaces, co-located because they're
the public face of the same conceptual object (the chat window):

1. **Live conversation** —
   ``GET  /workspaces/{ws}/chat/active``
       Return the user's current active thread (the freshest one by
       ``last_user_activity_at``), or create an empty one on the fly.
   ``POST /workspaces/{ws}/chat/active/new``
       Explicitly start a fresh thread. Optional ``pack_into_bucket``
       argument packs the outgoing thread into a bucket first.
   ``POST /workspaces/{ws}/chat/stream``
       SSE endpoint. The request body carries the user's next
       message; the response streams :class:`AgentEvent`-shaped JSON
       chunks until the model stops. Runs the tool-use loop
       server-side so the client only ever sees deltas + tool
       results, never vendor-specific shapes.
   ``POST /workspaces/{ws}/chat/threads/{id}/pack``
       Pack a thread into a bucket (explicit user action or
       accepted topic-shift banner).

2. **Knowledge buckets** —
   CRUD + listing under ``/workspaces/{ws}/buckets``. Buckets
   live in :class:`KnowledgeBucket`; their summaries live in
   :class:`BucketSummary`. The "continue from bucket" affordance
   is a client-side operation — it just calls ``/active/new``
   with the bucket's summaries pre-injected into the running
   summary.

3. **Artifact feedback memory** —
   :meth:`create_artifact_feedback` feeds into the feedback table for
   agent use. The old console HTTP surface has been retired.

We deliberately do not keep the old C10 "scope a ticket" surface
around — the single-window model replaces it. Migrations keep
older ``ChatThread`` rows around for audit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_MEMBER,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    ArtifactFeedback,
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    BucketSummary,
    KnowledgeBucket,
    KnowledgeSource,
)
from backend.app.db.models.agent_surface import (
    ChatMessage as ChatMessageRow,
    ChatThread,
)
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.services.bucket_visibility import visible_to_user_clause
from backend.app.services.distiller_sources import ensure_user_memory_bucket
from backend.app.services.agent.client import (
    AgentClient,
    ChatMessage,
    End,
    TextDelta,
    ToolCall,
    ToolResult,
    pick_default_client,
)
from backend.app.services.agent.tools import ToolBox, ToolInvocationError
from backend.app.services.agent.topic import TopicService

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["chat-v2"],
)


# ---------------------------------------------------------------------------
# Schemas
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
    title: str
    status: str
    topic_summary: str | None
    packed_into_bucket_id: uuid.UUID | None
    last_user_activity_at: datetime | None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int
    messages: list[ChatMessageOut]
    # Conversation purpose tag (``shape_project`` for drafting mode,
    # null otherwise). Surfaced so the client can render mode-specific
    # UI hints (e.g. a subtle "Drafting a project" banner).
    intent: str | None = None


class ChatThreadSummaryOut(BaseModel):
    """Trimmed thread row for the archive list view.

    The archive list does not load the full message history — only
    enough to render a row (title, archived-at, packed-into pointer).
    The dedicated ``GET /chat/threads/{id}`` route is still the way
    to fetch a thread's transcript on demand.
    """

    id: uuid.UUID
    title: str
    status: str
    topic_summary: str | None
    packed_into_bucket_id: uuid.UUID | None
    last_user_activity_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChatActiveNewIn(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    pack_into_bucket_slug: str | None = Field(default=None, max_length=120)
    pack_into_bucket_name: str | None = Field(default=None, max_length=255)
    # Conversation purpose. ``shape_project`` flips Navigator into
    # drafting mode (system prompt biases toward shaping a brief and
    # waiting for explicit confirmation before calling create_project).
    # NULL = default chat. The dashboard's "+ New project" CTA passes
    # ``shape_project``; everything else leaves it null.
    intent: Literal["shape_project"] | None = None


# ``ChatStreamIn`` retired in phase 3b — ``POST /chat/stream`` now
# takes multipart/form-data so attachments and the text body arrive
# in one request. Form fields: ``body`` (text), ``classify_shift``
# (bool, default True), ``thread_id`` (optional UUID), ``files[]``
# (UploadFile, optional). See route signature below.


class PackThreadIn(BaseModel):
    bucket_slug: str | None = Field(default=None, max_length=120)
    bucket_id: uuid.UUID | None = None
    bucket_name: str | None = Field(default=None, max_length=255)


class BucketOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    summary_count: int
    # Consolidation surface (Phase 1) — echoed so UI + CLI can
    # distinguish workspace/agent-memory (the historical shape) from
    # repo/project/user and non-agent-memory sources introduced by the
    # later phases. Defaults line up with the DB server_defaults, so
    # responses for old rows stay stable for existing consumers.
    scope_kind: str = "workspace"
    source_kind: str = "agent_memory"
    source_ref: dict[str, Any] | None = None
    project_id: uuid.UUID | None = None
    repo_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


class KnowledgeSourceOut(BaseModel):
    id: uuid.UUID
    bucket_id: uuid.UUID
    kind: str
    config: dict[str, Any]
    status: str
    cursor: dict[str, Any] | None = None
    content_fingerprint: str | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class BucketSummaryOut(BaseModel):
    id: uuid.UUID
    bucket_id: uuid.UUID
    thread_id: uuid.UUID | None
    title: str
    summary: str
    created_at: datetime


class BucketArticleOut(BaseModel):
    """Phase 5d: canonical article shape the new UI reads.

    Exposed by ``GET /v1/workspaces/{ws}/buckets/{slug}/articles``.
    Mirrors the :class:`BucketArticle` row but trims internals
    (``content_sha``, ``supersedes_id``) that aren't useful on the
    wire. ``version`` + ``status`` are kept so a future read-detail
    view can show "v3, published" without a second call.
    """

    id: uuid.UUID
    bucket_id: uuid.UUID
    slug: str
    title: str
    body_md: str
    version: int
    status: str
    provenance: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None




class ArtifactFeedbackIn(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20_000)
    context: dict = Field(default_factory=dict)


class ArtifactFeedbackUpdateIn(BaseModel):
    status: Literal["open", "triaged", "merged", "closed"] | None = None
    linked_pr_url: str | None = Field(default=None, max_length=1024)


class ArtifactFeedbackOut(BaseModel):
    id: uuid.UUID
    artifact_id: str
    body: str
    status: str
    linked_pr_url: str | None
    context: dict
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg_to_out(row: ChatMessageRow) -> ChatMessageOut:
    return ChatMessageOut(
        id=row.id,
        thread_id=row.thread_id,
        role=row.role,
        body=row.body,
        meta=row.meta or {},
        created_at=row.created_at,
    )


async def _thread_messages(
    session: AsyncSession, thread_id: uuid.UUID
) -> list[ChatMessageRow]:
    rows = (
        await session.execute(
            select(ChatMessageRow)
            .where(ChatMessageRow.thread_id == thread_id)
            .order_by(ChatMessageRow.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


def _thread_to_out(
    thread: ChatThread, messages: list[ChatMessageRow]
) -> ChatThreadOut:
    return ChatThreadOut(
        id=thread.id,
        title=thread.title,
        status=thread.status,
        topic_summary=thread.topic_summary,
        packed_into_bucket_id=thread.packed_into_bucket_id,
        last_user_activity_at=thread.last_user_activity_at,
        archived_at=thread.archived_at,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        message_count=len(messages),
        messages=[_msg_to_out(m) for m in messages],
        intent=thread.intent,
    )


def _thread_to_summary(thread: ChatThread) -> ChatThreadSummaryOut:
    return ChatThreadSummaryOut(
        id=thread.id,
        title=thread.title,
        status=thread.status,
        topic_summary=thread.topic_summary,
        packed_into_bucket_id=thread.packed_into_bucket_id,
        last_user_activity_at=thread.last_user_activity_at,
        archived_at=thread.archived_at,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _infer_project_hint(thread: ChatThread) -> str | None:
    """Best-effort guess at the project this chat is "about".

    Today we have one signal: ``intent='shape_project'`` indicates a
    drafting session whose project doesn't yet exist (the
    ``create_project`` tool will mint it later — ELS-129 wires the
    ``project_native_id`` back on success). Returning ``None`` for
    everything else is fine; memory facts get a global tag and the
    retrieval boost in ELS-128 falls back gracefully.
    """
    # Placeholder for future heuristics — repo pin, project picker
    # in the UI, ``project_native_id`` baked on the thread row, etc.
    # For now there's nothing better than None to return.
    _ = thread
    return None


async def _find_or_create_active_thread(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str | None = None,
) -> ChatThread:
    """Resolve the user's single active thread (create if none).

    "Active" = status==active AND not yet packed into a bucket,
    ordered by ``last_user_activity_at`` DESC. We pick the freshest
    one so a user with several legacy threads lands on the most
    recent activity.
    """
    row = (
        await session.execute(
            select(ChatThread)
            .where(
                ChatThread.workspace_id == workspace_id,
                ChatThread.created_by_user_id == user_id,
                ChatThread.status == "active",
                ChatThread.packed_into_bucket_id.is_(None),
            )
            .order_by(desc(ChatThread.last_user_activity_at))
            .limit(1)
        )
    ).scalars().first()
    if row is not None:
        return row
    row = ChatThread(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        title=title or "New conversation",
        status="active",
        last_user_activity_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    # Pull server-generated timestamps into the instance so callers
    # can serialise them without triggering lazy IO.
    await session.refresh(row)
    return row


def _get_agent_client(settings: Settings) -> AgentClient:
    """Factory with a crisp 412 when no LLM key is configured."""
    try:
        return pick_default_client(settings)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# Active thread — GET / new
# ---------------------------------------------------------------------------


@router.get("/chat/active", response_model=ChatThreadOut)
async def get_active_thread(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ChatThreadOut:
    """Return (or create) the caller's single active chat thread.

    The single-window UX never shows a thread list — the user always
    sees exactly one conversation. This endpoint is how the UI
    bootstraps on page load.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    thread = await _find_or_create_active_thread(
        session, workspace_id=workspace_id, user_id=auth.user.id
    )
    messages = await _thread_messages(session, thread.id)
    return _thread_to_out(thread, messages)


@router.post("/chat/active/new", response_model=ChatThreadOut)
async def new_active_thread(
    workspace_id: uuid.UUID,
    payload: ChatActiveNewIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ChatThreadOut:
    """Archive the current active thread and open a fresh one.

    Optional ``pack_into_bucket_slug`` / ``_name`` packs the
    outgoing thread into a bucket as part of the same request —
    this is what the topic-shift banner calls when the user
    accepts the suggestion.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    current = await _find_or_create_active_thread(
        session, workspace_id=workspace_id, user_id=auth.user.id
    )
    payload = payload or ChatActiveNewIn()

    # Pack the outgoing thread if the caller asked. We only pack
    # if it has real messages — an empty bootstrapped thread is
    # not worth a bucket summary.
    messages = await _thread_messages(session, current.id)
    if (
        (payload.pack_into_bucket_slug or payload.pack_into_bucket_name)
        and messages
    ):
        agent = _get_agent_client(settings)
        topic = TopicService(
            session,
            settings=settings,
            client=agent,
            workspace_id=workspace_id,
            user_id=auth.user.id,
        )
        await topic.pack_topic(
            current,
            bucket_slug=payload.pack_into_bucket_slug,
            bucket_name=payload.pack_into_bucket_name,
        )

    current.status = "archived"
    await session.flush()

    fresh = ChatThread(
        workspace_id=workspace_id,
        created_by_user_id=auth.user.id,
        title=payload.title
        or ("Drafting a project" if payload.intent == "shape_project" else "New conversation"),
        status="active",
        last_user_activity_at=datetime.now(timezone.utc),
        intent=payload.intent,
    )
    session.add(fresh)
    await session.flush()
    await session.refresh(fresh)
    return _thread_to_out(fresh, [])


# ---------------------------------------------------------------------------
# Archived thread list (Wave C)
# ---------------------------------------------------------------------------


@router.get("/chat/threads", response_model=list[ChatThreadSummaryOut])
async def list_chat_threads(
    workspace_id: uuid.UUID,
    status: str = "archived",  # noqa: A002 — matches the query param name
    limit: int = 50,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ChatThreadSummaryOut]:
    """List the caller's chat threads in a given lifecycle bucket.

    The single-window UX deliberately hides the "list of chats"
    surface during normal use; this endpoint is the escape hatch
    so the console can render the **Archived** view (Wave C).

    Scope:
    - Always restricted to threads created by ``auth.user``; we do
      not surface other members' archived chats from this route.
    - ``status`` defaults to ``archived`` (the only currently-needed
      view) but accepts ``active`` / ``resolved`` for symmetry.
    - ``limit`` capped at 200 to bound query work; the archive view
      paginates the rest with a follow-up ``before=`` cursor when /
      if we need it.

    Ordering: archived threads are sorted by ``archived_at DESC``
    (sweeper-set value) with ``updated_at DESC`` as the fallback
    for legacy rows where ``archived_at`` is NULL.
    """
    if status not in {"active", "resolved", "archived"}:
        raise HTTPException(
            status_code=400,
            detail="status must be one of: active, resolved, archived",
        )
    capped = max(1, min(limit, 200))
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    rows = (
        await session.execute(
            select(ChatThread)
            .where(
                ChatThread.workspace_id == workspace_id,
                ChatThread.created_by_user_id == auth.user.id,
                ChatThread.status == status,
            )
            .order_by(
                desc(ChatThread.archived_at),
                desc(ChatThread.updated_at),
            )
            .limit(capped)
        )
    ).scalars().all()
    return [_thread_to_summary(r) for r in rows]


@router.get("/chat/threads/{thread_id}", response_model=ChatThreadOut)
async def get_chat_thread(
    workspace_id: uuid.UUID,
    thread_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ChatThreadOut:
    """Return one thread (active OR archived) with its full message
    history.

    Diagnostic surface — the single-window UX never lets the operator
    flip backwards through archived threads, but admins debugging
    Navigator quality (tool calls misfiring, hallucinated answers,
    wrong specialist consults) need the transcript without going
    into Postgres. Scoped to ``auth.user`` ownership for now;
    cross-user inspection is a separate admin route if it ever
    becomes a need.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    thread = (
        await session.execute(
            select(ChatThread).where(
                ChatThread.id == thread_id,
                ChatThread.workspace_id == workspace_id,
                ChatThread.created_by_user_id == auth.user.id,
            )
        )
    ).scalar_one_or_none()
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    messages = await _thread_messages(session, thread.id)
    return _thread_to_out(thread, messages)


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------


@router.post("/chat/stream")
async def chat_stream(
    workspace_id: uuid.UUID,
    body: str = Form(..., min_length=1, max_length=20_000),
    classify_shift: bool = Form(True),
    thread_id: uuid.UUID | None = Form(None),
    files: list[UploadFile] = File(default_factory=list),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream the agent's next turn as Server-Sent Events.

    Event types emitted on the wire (``data: <json>\\n\\n``):

    - ``{"type": "thread", "thread": ...}`` — sent once up-front so
      the client can render the thread id / updated title before
      any model output.
    - ``{"type": "user_message", "message": ...}`` — the persisted
      user message row.
    - ``{"type": "topic_shift", "decision": {...}}`` — optional;
      only when the classifier suggests a shift. The UI renders
      a banner the user can accept / dismiss.
    - ``{"type": "delta", "text": "..."}`` — one chunk of the
      assistant's streaming text.
    - ``{"type": "tool_call", "id": "...", "name": "...",
      "arguments": {...}}`` — the model requested a tool.
    - ``{"type": "tool_result", "id": "...", "output": "..."}`` —
      the tool's response we fed back to the model.
    - ``{"type": "assistant_message", "message": ...}`` — the
      persisted assistant message at the end of the turn.
    - ``{"type": "end", "finish_reason": "...", "usage": {...}}`` —
      terminal marker; UI unlocks the composer.
    - ``{"type": "error", "error": "..."}`` — fatal error during
      the turn; UI surfaces it and unlocks the composer.
    """
    # P6-22 — chat opened to members; admin only required for mutating
    # tools inside the turn (those self-gate via ``_require_admin_or_error``
    # in :mod:`backend.app.services.agent.tools`).
    await _require_membership(session, workspace_id, auth.user.id, ROLES_MEMBER)
    if thread_id is not None:
        thread = (
            await session.execute(
                select(ChatThread).where(
                    ChatThread.id == thread_id,
                    ChatThread.workspace_id == workspace_id,
                    ChatThread.created_by_user_id == auth.user.id,
                )
            )
        ).scalar_one_or_none()
        if thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="thread not found in this workspace",
            )
    else:
        thread = await _find_or_create_active_thread(
            session, workspace_id=workspace_id, user_id=auth.user.id
        )
    if thread.status != "active":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"thread is {thread.status}; open a new one first",
        )

    agent = _get_agent_client(settings)

    # Persist the user message before streaming so a client that
    # drops the connection mid-stream still has its message
    # recorded (and the classifier / assembler see it too).
    user_msg = ChatMessageRow(
        thread_id=thread.id,
        role="user",
        author_user_id=auth.user.id,
        body=body,
    )
    session.add(user_msg)
    thread.last_user_activity_at = datetime.now(timezone.utc)
    await session.flush()

    # E17/ELS-127 — fire mem0 extraction as a background task so the
    # LLM round-trip doesn't block the streaming response. The
    # per-thread ``memory_enabled`` toggle + the content pre-filter
    # decide together whether mem0 sees this message; the background
    # path opens its own DB session so we don't leak request-scoped
    # state into a task that outlives the connection.
    try:
        from backend.app.services.agent import memory as navigator_memory

        if navigator_memory.should_extract_memory(
            thread.memory_enabled, user_msg.body
        ):
            asyncio.create_task(
                navigator_memory.extract_in_background(
                    workspace_id=workspace_id,
                    owner_user_id=auth.user.id,
                    message_body=user_msg.body,
                    source_thread_id=thread.id,
                    source_message_id=user_msg.id,
                    # No ``position`` column on ChatMessage today —
                    # leave NULL; the Console can sort by created_at
                    # when it needs ±5 context (ELS-128 follow-up).
                    source_message_position=None,
                    project_native_id=_infer_project_hint(thread),
                    intent_at_capture=thread.intent,
                    settings=settings,
                ),
                name="ship.navigator_memory.extract",
            )
    except Exception:  # noqa: BLE001 — the chat turn must NEVER fail because of mem0
        logger.exception("memory extract scheduling failed")

    # Attachments: persist + read bytes so they survive past the
    # multipart stream's lifetime. Message-level caps (file count,
    # total bytes) checked here; per-file caps inside
    # ``persist_attachment``. Reject early so a 30 MiB upload doesn't
    # spool to disk before failing.
    in_memory_attachments: list = []
    if files:
        from backend.app.services.attachments import (
            AttachmentPolicyError,
            AttachmentPersistError,
            MAX_FILES_PER_MESSAGE,
            MAX_TOTAL_BYTES_PER_MESSAGE,
            persist_attachment,
        )
        from backend.app.services.agent.client import MessageAttachment

        if len(files) > MAX_FILES_PER_MESSAGE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"too many files (max {MAX_FILES_PER_MESSAGE})",
            )
        total_bytes = 0
        for upload in files:
            data = await upload.read()
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES_PER_MESSAGE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        f"total upload exceeds {MAX_TOTAL_BYTES_PER_MESSAGE} bytes"
                    ),
                )
            try:
                row = await persist_attachment(
                    session,
                    workspace_id=workspace_id,
                    message_id=user_msg.id,
                    filename=upload.filename or "attachment",
                    mime=upload.content_type or "application/octet-stream",
                    data=data,
                )
            except AttachmentPolicyError as exc:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail={"code": exc.code, "message": exc.message},
                ) from exc
            except AttachmentPersistError as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"code": "storage_write_failed", "message": str(exc)},
                ) from exc
            in_memory_attachments.append(
                MessageAttachment(
                    kind=row.kind,
                    mime=row.mime,
                    filename=row.filename,
                    data=data,
                    extracted_text=row.extracted_text,
                )
            )
        await session.flush()

    async def event_stream() -> AsyncIterator[bytes]:
        async for chunk in _run_agent_turn(
            session=session,
            settings=settings,
            agent=agent,
            workspace_id=workspace_id,
            user_id=auth.user.id,
            thread=thread,
            user_msg=user_msg,
            user_attachments=in_memory_attachments,
            classify_shift=classify_shift,
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class _NullSpan:
    """No-op span used when Sentry is not installed / not initialised.

    Matches the tiny subset of the ``sentry_sdk.Span`` surface we
    actually call (``set_data``) so the tracing code path can be
    branchless at the use site.
    """

    def set_data(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _NullSpanCM:
    def __enter__(self) -> _NullSpan:
        return _NullSpan()

    def __exit__(self, *_exc: Any) -> None:
        return None


def _null_span() -> _NullSpanCM:
    return _NullSpanCM()


async def _run_agent_turn(
    *,
    session: AsyncSession,
    settings: Settings,
    agent: AgentClient,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    thread: ChatThread,
    user_msg: ChatMessageRow,
    user_attachments: list,
    classify_shift: bool,
) -> AsyncIterator[bytes]:
    """Run one user→assistant turn end to end, emitting SSE frames.

    Flow:

    1. Emit the ``thread`` + ``user_message`` frames so the UI
       renders the user's message immediately.
    2. (Optional) run the topic-shift classifier — if it fires,
       emit ``topic_shift`` so the UI can render the banner while
       the model is still thinking.
    3. Assemble the LLM prompt via :class:`TopicService`.
    4. Drive the tool-use loop (``astream`` → tool calls → tool
       results → ``astream`` again) until the model stops.
    5. Persist the assistant message + emit ``end``.

    Cost guard: per-turn token budget tracked via ``End.usage`` on
    each round-trip; once exceeded we break the loop with an
    ``error`` frame.
    """
    topic_service = TopicService(
        session,
        settings=settings,
        client=agent,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    toolbox = ToolBox(
        session,
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
        # PR-7C: surface the chat's active repo to ``search_workspace_kb``
        # so the tool can promote hits from the repo the user is
        # browsing into the top rank band even on a zero-arg tool call.
        active_repo_id=thread.repo_id,
        # ELS-74 (drafting mode): pass the active thread context so
        # ``create_project`` can stamp ``originating_thread_id`` on
        # the priorities row for a future "Continue shaping" deep-link.
        thread_id=thread.id,
        thread_intent=thread.intent,
    )

    messages = await _thread_messages(session, thread.id)
    # The live row for user_msg is already in messages (we flushed
    # it above). Everything before it is history.
    prior_messages = [m for m in messages if m.id != user_msg.id]
    await session.refresh(thread)

    yield _sse({"type": "thread", "thread": _thread_to_out(thread, messages).model_dump(mode="json")})
    yield _sse({"type": "user_message", "message": _msg_to_out(user_msg).model_dump(mode="json")})

    # Topic-shift classifier (best-effort, non-blocking errors).
    if classify_shift:
        try:
            decision = await topic_service.classify_shift(
                running_summary=thread.topic_summary,
                recent_messages=prior_messages,
                new_user_message=user_msg.body,
            )
            if decision.shifted:
                yield _sse(
                    {
                        "type": "topic_shift",
                        "decision": {
                            "shifted": True,
                            "reason": decision.reason,
                            "new_title": decision.new_title,
                            "explicit_phrase": decision.explicit_phrase,
                        },
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("topic classifier errored: %s", exc)

    # Retrieve supporting context. Both are best-effort — a KB miss
    # or a bucket-retrieval error shouldn't kill the turn.
    retrieved_buckets = []
    try:
        retrieved_buckets = await topic_service.retrieve_buckets(
            query=user_msg.body, limit=3
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bucket retrieval failed: %s", exc)

    # Surface what we pulled in so the UI can render the soft
    # "Using N prior memories" disclosure. Skip when retrieval was
    # empty — the disclosure would be a visual no-op.
    if retrieved_buckets:
        yield _sse(
            {
                "type": "retrieved_context",
                "hits": [
                    {
                        "bucket_slug": h.bucket_slug,
                        "bucket_name": h.bucket_name,
                        "article_id": str(h.article_id),
                        "title": h.title,
                        "similarity": round(float(h.similarity), 3),
                    }
                    for h in retrieved_buckets
                ],
            }
        )

    assembled = await topic_service.assemble_messages(
        thread=thread,
        recent_messages=prior_messages,
        new_user_message=user_msg.body,
        new_user_attachments=user_attachments,
        retrieved_buckets=retrieved_buckets,
    )

    cost_budget = settings.agent_max_tokens_per_turn
    cost_spent = 0
    assistant_text_parts: list[str] = []
    tool_invocations: list[dict[str, Any]] = []
    tool_spec_list = toolbox.specs()
    finish_reason = "stop"

    # Tool-use loop. One iteration = one ``astream`` round-trip.
    # Break when the model answers without calling tools, or when
    # we hit the hard cap on iterations (defensive, otherwise a
    # bug in a tool could loop forever).
    _MAX_TOOL_LOOPS = 8
    # Sentry tracing is optional — only wrap when the SDK is
    # present, otherwise the chat turn runs exactly as before.
    try:
        import sentry_sdk  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover — sentry is an extra
        sentry_sdk = None  # type: ignore[assignment]

    for _loop in range(_MAX_TOOL_LOOPS):
        span_cm = (
            sentry_sdk.start_span(op="ai.chat", description="agent.astream")
            if sentry_sdk is not None
            else _null_span()
        )
        with span_cm as span:
            if span is not None:
                span.set_data("loop_index", _loop)
                span.set_data("vendor", settings.agent_vendor)
                span.set_data(
                    "model", settings.agent_model_main or "(default)"
                )
                span.set_data("tool_count", len(tool_spec_list))
                span.set_data("message_count", len(assembled))
            try:
                stream = await agent.astream(
                    assembled,
                    tools=tool_spec_list,
                    max_tokens=None,
                    temperature=0.2,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("astream failed")
                yield _sse(
                    {"type": "error", "error": f"agent stream failed: {exc}"}
                )
                return

            turn_text_parts: list[str] = []
            turn_tool_calls: list[ToolCall] = []

            try:
                async for event in stream:
                    if isinstance(event, TextDelta):
                        turn_text_parts.append(event.text)
                        yield _sse({"type": "delta", "text": event.text})
                    elif isinstance(event, ToolCall):
                        # Wave B2: emit ``tool_call`` immediately so the UI
                        # can interleave the tool card between deltas in
                        # arrival order. The post-stream loop still drives
                        # tool execution + ``tool_result`` emission, but no
                        # longer re-emits the ``tool_call`` frame (which
                        # would duplicate it on the wire).
                        turn_tool_calls.append(event)
                        yield _sse(
                            {
                                "type": "tool_call",
                                "id": event.id,
                                "name": event.name,
                                "arguments": event.arguments,
                            }
                        )
                    elif isinstance(event, ToolResult):
                        pass
                    elif isinstance(event, End):
                        finish_reason = event.finish_reason
                        cost_spent += int(event.usage.get("total_tokens", 0))
                        if span is not None:
                            for key, value in event.usage.items():
                                span.set_data(f"tokens.{key}", int(value))
                            span.set_data("finish_reason", event.finish_reason)
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent stream errored")
                yield _sse(
                    {"type": "error", "error": f"agent stream errored: {exc}"}
                )
                return

        # Record the assistant's partial text on this iteration.
        if turn_text_parts:
            assistant_text_parts.extend(turn_text_parts)

        # Cost guard — stop before we burn more than the budget.
        if cost_spent > cost_budget:
            yield _sse(
                {
                    "type": "error",
                    "error": (
                        f"agent exceeded token budget ({cost_spent} > "
                        f"{cost_budget}); ending turn"
                    ),
                }
            )
            finish_reason = "length"
            break

        if not turn_tool_calls:
            break

        # Run each requested tool and append the results back to
        # the message stack for the next astream round-trip.
        openai_style_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in turn_tool_calls
        ]
        assembled.append(
            ChatMessage(
                role="assistant",
                content="".join(turn_text_parts),
                tool_calls=openai_style_tool_calls,
            )
        )
        for tc in turn_tool_calls:
            # ``tool_call`` was already yielded inline above (Wave B2);
            # this loop only drives execution + ``tool_result`` so the
            # wire order matches the model's true emission order.
            try:
                output = await toolbox.invoke(tc.name, tc.arguments)
            except ToolInvocationError as exc:
                output = json.dumps({"error": str(exc)}, ensure_ascii=False)
            tool_invocations.append(
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments, "output": output}
            )
            assembled.append(
                ChatMessage(
                    role="tool",
                    name=tc.name,
                    tool_call_id=tc.id,
                    content=output,
                )
            )
            yield _sse(
                {
                    "type": "tool_result",
                    "id": tc.id,
                    "name": tc.name,
                    "output": output,
                }
            )
    else:
        # Exhausted the tool-loop cap — end with a best-effort
        # finish_reason so the UI surfaces the truncation.
        finish_reason = "tool_loop_exceeded"

    assistant_body = "".join(assistant_text_parts).strip() or (
        "(no response)"
    )
    meta = {
        "tool_invocations": tool_invocations,
        "tokens": cost_spent,
        "vendor": agent.vendor,
    }
    assistant_row = ChatMessageRow(
        thread_id=thread.id,
        role="assistant",
        body=assistant_body,
        meta=meta,
    )
    session.add(assistant_row)
    thread.updated_at = datetime.now(timezone.utc)
    await session.flush()

    yield _sse(
        {
            "type": "assistant_message",
            "message": _msg_to_out(assistant_row).model_dump(mode="json"),
        }
    )
    yield _sse(
        {
            "type": "end",
            "finish_reason": finish_reason,
            "usage": {"total_tokens": cost_spent},
        }
    )


# ---------------------------------------------------------------------------
# Pack thread → bucket
# ---------------------------------------------------------------------------


async def pack_thread(
    workspace_id: uuid.UUID,
    thread_id: uuid.UUID,
    payload: PackThreadIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BucketSummaryOut:
    """Pack a thread into a bucket and archive it.

    ``bucket_id`` / ``bucket_slug`` pick an existing bucket; if
    only ``bucket_name`` is provided we auto-create one (the UI
    uses this for "pack into new bucket"). The thread moves to
    ``archived`` status once packed so the caller has to open a
    fresh thread to continue.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    thread = (
        await session.execute(
            select(ChatThread).where(
                ChatThread.id == thread_id,
                ChatThread.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    agent = _get_agent_client(settings)
    topic_service = TopicService(
        session,
        settings=settings,
        client=agent,
        workspace_id=workspace_id,
        user_id=auth.user.id,
    )
    try:
        summary = await topic_service.pack_topic(
            thread,
            bucket_id=payload.bucket_id,
            bucket_slug=payload.bucket_slug,
            bucket_name=payload.bucket_name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    thread.status = "archived"
    await session.flush()

    return BucketSummaryOut(
        id=summary.id,
        bucket_id=summary.bucket_id,
        thread_id=summary.thread_id,
        title=summary.title,
        summary=summary.summary,
        created_at=summary.created_at,
    )


# ---------------------------------------------------------------------------
# Save thread → user memory bucket (Phase 8)
# ---------------------------------------------------------------------------


@router.post(
    "/chat/threads/{thread_id}/save-to-memory",
    response_model=BucketSummaryOut,
)
async def save_thread_to_memory(
    workspace_id: uuid.UUID,
    thread_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BucketSummaryOut:
    """Pack the thread into the caller's private ``my-memory`` bucket.

    Phase 8 companion to :func:`pack_thread`. Differences:

    - **Target is always the caller's** ``scope=user`` bucket — no
      ``bucket_id`` / ``bucket_slug`` inputs. The bucket is minted
      lazily the first time this endpoint fires (idempotent via
      :func:`ensure_user_memory_bucket`). Running this against a
      fresh account simply creates ``my-memory`` on the fly.
    - **No archive.** ``pack_thread`` archives the thread so the UI
      forces the user into a new one; "save to memory" is
      non-destructive — users keep chatting after saving, and can
      save again later if the thread evolves.
    - **Role is ``ROLES_READ``, not ``ROLES_ADMIN``.** Writing into
      your own ``scope=user`` bucket is a user-level action, not an
      admin one; viewers can save. The visibility helper + the
      Phase 7 resolver still guarantee other members can't read it.
    - **Explicit action = implicit consent.** Phase 8 deliberately
      does not auto-save at end of thread; the user has to hit
      "save to memory" themselves. When we later add an auto-save
      consent toggle (stored on ``users`` or ``workspace_members``),
      it reuses this same endpoint — the write path stays stable.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    thread = (
        await session.execute(
            select(ChatThread).where(
                ChatThread.id == thread_id,
                ChatThread.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    memory_bucket = await ensure_user_memory_bucket(
        session,
        workspace_id=workspace_id,
        user_id=auth.user.id,
    )

    agent = _get_agent_client(settings)
    topic_service = TopicService(
        session,
        settings=settings,
        client=agent,
        workspace_id=workspace_id,
        user_id=auth.user.id,
    )
    try:
        summary = await topic_service.pack_topic(
            thread,
            bucket_id=memory_bucket.id,
        )
    except ValueError as exc:
        # Empty thread is the only documented raise from pack_topic.
        # Surface it as 400 so the console can show a friendly
        # "nothing to save yet" hint instead of a 500.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    await session.flush()

    return BucketSummaryOut(
        id=summary.id,
        bucket_id=summary.bucket_id,
        thread_id=summary.thread_id,
        title=summary.title,
        summary=summary.summary,
        created_at=summary.created_at,
    )


# ---------------------------------------------------------------------------
# Buckets CRUD
# ---------------------------------------------------------------------------


async def _count_articles(
    session: AsyncSession, bucket_id: uuid.UUID
) -> int:
    """Count published, unarchived articles in a bucket.

    Phase 5d: ``BucketOut.summary_count`` is now backed by the
    consolidated ``bucket_articles`` table instead of the legacy
    ``bucket_summaries``. The name "summary_count" is preserved on
    the wire for backward compat (the Phase 4 frontend reads it);
    semantically the value is now "number of published articles".
    For agent_memory buckets this is 1:1 with the old meaning because
    of the Phase 5b dual-write; for repo_files buckets it reports
    the number of mirrored files, which is a strict improvement over
    the previous hard-coded 0.
    """
    rows = (
        await session.execute(
            select(BucketArticle.id)
            .where(BucketArticle.bucket_id == bucket_id)
            .where(BucketArticle.status == BucketArticleStatus.PUBLISHED)
            .where(BucketArticle.archived_at.is_(None))
        )
    ).scalars().all()
    return len(rows)


@router.get("/buckets", response_model=list[BucketOut])
async def list_buckets(
    workspace_id: uuid.UUID,
    include_archived: bool = False,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[BucketOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(KnowledgeBucket)
        .where(KnowledgeBucket.workspace_id == workspace_id)
        .where(KnowledgeBucket.scope_kind == BucketScope.WORKSPACE)
        .where(KnowledgeBucket.source_kind != BucketSource.REPO_FILES)
        # Phase 8: hide other users' user-scoped rows the same way
        # the Phase 3 resolver does. ``list_buckets`` is used by the
        # console and the `/knowledge` surface, both of which must
        # not leak another user's private memory across tenants.
        .where(visible_to_user_clause(auth.user.id))
        .order_by(KnowledgeBucket.name)
    )
    if not include_archived:
        stmt = stmt.where(KnowledgeBucket.archived_at.is_(None))
    buckets = (await session.execute(stmt)).scalars().all()

    out: list[BucketOut] = []
    for b in buckets:
        count = await _count_articles(session, b.id)
        out.append(_serialize_bucket(b, summary_count=count))
    return out


@router.get("/buckets/{slug}", response_model=BucketOut)
async def get_bucket(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> BucketOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    row = await _load_bucket(session, workspace_id, slug)
    count = await _count_articles(session, row.id)
    return _serialize_bucket(row, summary_count=count)


@router.post("/buckets/{slug}/archive", response_model=BucketOut)
async def archive_bucket(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> BucketOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _load_bucket(session, workspace_id, slug)
    if row.archived_at is None:
        row.archived_at = datetime.now(timezone.utc)
        await session.flush()
        await session.refresh(row)
    count = await _count_articles(session, row.id)
    return _serialize_bucket(row, summary_count=count)


@router.post("/buckets/{slug}/restore", response_model=BucketOut)
async def restore_bucket(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> BucketOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _load_bucket(session, workspace_id, slug)
    if row.archived_at is not None:
        row.archived_at = None
        await session.flush()
        await session.refresh(row)
    count = await _count_articles(session, row.id)
    return _serialize_bucket(row, summary_count=count)


async def list_bucket_summaries(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[BucketSummaryOut]:
    """Legacy endpoint — reads from ``bucket_summaries`` directly.

    Deprecated in favour of ``/buckets/{slug}/articles`` (Phase 5d)
    which surfaces the consolidated ``bucket_articles`` table. Kept
    as-is until the frontend migration lands so a partial Phase 4 UI
    deploy doesn't break. Removal target: Phase 9.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    bucket = await _load_bucket(session, workspace_id, slug)
    rows = (
        await session.execute(
            select(BucketSummary)
            .where(BucketSummary.bucket_id == bucket.id)
            .order_by(desc(BucketSummary.created_at))
        )
    ).scalars().all()
    return [
        BucketSummaryOut(
            id=r.id,
            bucket_id=r.bucket_id,
            thread_id=r.thread_id,
            title=r.title,
            summary=r.summary,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get(
    "/buckets/{slug}/articles", response_model=list[BucketArticleOut]
)
async def list_bucket_articles(
    workspace_id: uuid.UUID,
    slug: str,
    include_archived: bool = False,
    include_superseded: bool = False,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[BucketArticleOut]:
    """Phase 5d: canonical article listing for a bucket.

    By default returns only published, unarchived rows — the same set
    ``retrieve_buckets`` (Phase 5c) searches, and what the Phase 4
    scope-pill UI will surface. ``include_superseded`` exposes Phase
    5a's version history for a future article-timeline view, and
    ``include_archived`` lets an admin inspect files that used to be
    in ``.ship/knowledge/`` but have since been deleted from the repo.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    bucket = await _load_bucket(session, workspace_id, slug)

    stmt = (
        select(BucketArticle)
        .where(BucketArticle.bucket_id == bucket.id)
        .order_by(desc(BucketArticle.created_at), BucketArticle.slug)
    )
    if not include_superseded:
        # Default view hides supersession history to keep the UI
        # focused on "what exists now". Set ``include_superseded=true``
        # for a version timeline. ``include_archived=true`` also
        # admits ``status='archived'`` rows here — without it, the
        # archived-status filter blocks the dual-flip articles
        # produced by the operator-archive flow even after we've
        # opened up ``archived_at IS NOT NULL`` below.
        allowed = [BucketArticleStatus.PUBLISHED]
        if include_archived:
            allowed.append(BucketArticleStatus.ARCHIVED)
        stmt = stmt.where(BucketArticle.status.in_(allowed))
    if not include_archived:
        stmt = stmt.where(BucketArticle.archived_at.is_(None))
    rows = list((await session.execute(stmt)).scalars().all())

    return [
        BucketArticleOut(
            id=a.id,
            bucket_id=a.bucket_id,
            slug=a.slug,
            title=a.title,
            body_md=a.body_md,
            version=a.version,
            status=a.status,
            provenance=a.provenance or {},
            created_at=a.created_at,
            updated_at=a.updated_at,
            archived_at=a.archived_at,
        )
        for a in rows
    ]


class BucketArticleArchiveIn(BaseModel):
    """Operator-supplied reason for archiving / restoring an article.

    Required because by the time an ADR / runbook makes it to a bucket
    it's been seen by other agents — the rationale ("superseded by
    PR-Y", "decision reverted in commit cf9f983") matters more than
    the act of archiving. We persist it on the audit row so a future
    review can answer "why is this gone?" without spelunking commits.
    """

    reason: str = Field(min_length=1, max_length=2000)


@router.post(
    "/buckets/{slug}/articles/{article_id}/archive",
    response_model=BucketArticleOut,
)
async def archive_bucket_article(
    workspace_id: uuid.UUID,
    slug: str,
    article_id: uuid.UUID,
    payload: BucketArticleArchiveIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> BucketArticleOut:
    """Flip a published article into ``archived`` so readers stop
    seeing it.

    Use case: an ADR / runbook / facts article became stale (decision
    reversed, file deleted, contract redesigned) and the operator
    wants it out of the agent's warmed memory without losing the
    historical row. Archiving sets ``status='archived'`` AND
    ``archived_at=NOW()`` — the dual flip is intentional: every
    reader filter checks both, and we want defence-in-depth so a
    future bug that drops one filter doesn't silently resurface
    archived content.

    Requires workspace admin. The reason is captured on
    ``AuditLog`` (action ``knowledge.article.archive``) so the
    rationale survives outside the article itself.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_ADMIN
    )
    bucket = await _load_bucket(session, workspace_id, slug)
    article = await _load_article_in_bucket(session, bucket.id, article_id)

    if article.archived_at is not None:
        # Idempotent: already archived → return current state without
        # writing a duplicate audit row. The console retry button on
        # a flaky network shouldn't multiply the audit trail.
        return _serialize_article(article)

    now = datetime.now(timezone.utc)
    article.status = BucketArticleStatus.ARCHIVED
    article.archived_at = now
    bucket.updated_at = now

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action="knowledge.article.archive",
            target_kind="bucket_article",
            target_id=str(article.id),
            payload={
                "bucket_slug": bucket.slug,
                "article_slug": article.slug,
                "version": article.version,
                "reason": payload.reason,
            },
        )
    )
    await session.flush()
    # Re-load so the server-side ``onupdate`` on ``updated_at`` lands
    # on the instance — without this, the implicit lazy-load fires
    # later (during response serialization) and asyncpg's session is
    # no longer inside a greenlet by then.
    await session.refresh(article)
    return _serialize_article(article)


@router.post(
    "/buckets/{slug}/articles/{article_id}/restore",
    response_model=BucketArticleOut,
)
async def restore_bucket_article(
    workspace_id: uuid.UUID,
    slug: str,
    article_id: uuid.UUID,
    payload: BucketArticleArchiveIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> BucketArticleOut:
    """Undo :func:`archive_bucket_article`.

    Restoring respects the partial unique index — if a different
    article published under the same ``(bucket_id, slug)`` while
    this one was archived, the restore lands as ``draft`` instead
    of ``published`` so the operator can decide which version wins.
    Otherwise the restored row goes straight back to ``published``.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_ADMIN
    )
    bucket = await _load_bucket(session, workspace_id, slug)
    article = await _load_article_in_bucket(session, bucket.id, article_id)

    if article.archived_at is None:
        return _serialize_article(article)

    # Was a sibling article published under the same slug while this
    # one was archived? If so, demote the restore to ``draft`` so the
    # operator can compare; the partial unique index would reject a
    # second ``published`` row anyway.
    sibling = (
        await session.execute(
            select(BucketArticle.id)
            .where(BucketArticle.bucket_id == bucket.id)
            .where(BucketArticle.slug == article.slug)
            .where(BucketArticle.status == BucketArticleStatus.PUBLISHED)
            .where(BucketArticle.archived_at.is_(None))
            .limit(1)
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    article.archived_at = None
    article.status = (
        BucketArticleStatus.DRAFT
        if sibling is not None
        else BucketArticleStatus.PUBLISHED
    )
    bucket.updated_at = now

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action="knowledge.article.restore",
            target_kind="bucket_article",
            target_id=str(article.id),
            payload={
                "bucket_slug": bucket.slug,
                "article_slug": article.slug,
                "version": article.version,
                "restored_status": article.status,
                "reason": payload.reason,
            },
        )
    )
    await session.flush()
    await session.refresh(article)
    return _serialize_article(article)


async def _load_article_in_bucket(
    session: AsyncSession,
    bucket_id: uuid.UUID,
    article_id: uuid.UUID,
) -> BucketArticle:
    row = (
        await session.execute(
            select(BucketArticle)
            .where(BucketArticle.id == article_id)
            .where(BucketArticle.bucket_id == bucket_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return row


def _serialize_article(a: BucketArticle) -> BucketArticleOut:
    return BucketArticleOut(
        id=a.id,
        bucket_id=a.bucket_id,
        slug=a.slug,
        title=a.title,
        body_md=a.body_md,
        version=a.version,
        status=a.status,
        provenance=a.provenance or {},
        created_at=a.created_at,
        updated_at=a.updated_at,
        archived_at=a.archived_at,
    )


@router.get(
    "/buckets/{slug}/sources", response_model=list[KnowledgeSourceOut]
)
async def list_bucket_sources(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeSourceOut]:
    """Return durable source configs and sync state for a bucket."""

    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    bucket = await _load_bucket(session, workspace_id, slug)
    rows = (
        await session.execute(
            select(KnowledgeSource)
            .where(KnowledgeSource.bucket_id == bucket.id)
            .order_by(KnowledgeSource.created_at.asc())
        )
    ).scalars().all()
    return [_serialize_source(row) for row in rows]


async def _load_bucket(
    session: AsyncSession, workspace_id: uuid.UUID, slug: str
) -> KnowledgeBucket:
    # Phase 1 note: slugs are unique per ``(workspace, scope, carrier)``;
    # this lookup is still safe because the CRUD API only creates
    # workspace-scoped buckets today, and ``uq_knowledge_buckets_workspace_slug``
    # keeps that uniqueness. When Phase 2 starts syncing repo-scoped
    # buckets, this helper has to take a ``scope`` hint.
    row = (
        await session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace_id,
                KnowledgeBucket.slug == slug,
                KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
                KnowledgeBucket.source_kind != BucketSource.REPO_FILES,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return row


def _serialize_bucket(row: KnowledgeBucket, *, summary_count: int) -> BucketOut:
    """Unified ``KnowledgeBucket`` → ``BucketOut`` projection.

    Echoes the Phase 1 consolidation fields alongside the historical
    shape so clients can branch on ``scope_kind`` / ``source_kind``
    without hitting a separate endpoint.
    """

    return BucketOut(
        id=row.id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        summary_count=summary_count,
        scope_kind=row.scope_kind,
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        project_id=row.project_id,
        repo_id=row.repo_id,
        user_id=row.user_id,
    )


def _serialize_source(row: KnowledgeSource) -> KnowledgeSourceOut:
    return KnowledgeSourceOut(
        id=row.id,
        bucket_id=row.bucket_id,
        kind=row.kind,
        config=row.config,
        status=row.status,
        cursor=row.cursor,
        content_fingerprint=row.content_fingerprint,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Artifact feedback
# ---------------------------------------------------------------------------


async def list_artifact_feedback(
    workspace_id: uuid.UUID,
    status_filter: Literal["open", "triaged", "merged", "closed"] | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ArtifactFeedbackOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(ArtifactFeedback)
        .where(ArtifactFeedback.workspace_id == workspace_id)
        .order_by(desc(ArtifactFeedback.created_at))
    )
    if status_filter is not None:
        stmt = stmt.where(ArtifactFeedback.status == status_filter)
    rows = (await session.execute(stmt)).scalars().all()
    return [_feedback_to_out(r) for r in rows]


async def create_artifact_feedback_route(
    workspace_id: uuid.UUID,
    payload: ArtifactFeedbackIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ArtifactFeedbackOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = ArtifactFeedback(
        workspace_id=workspace_id,
        artifact_id=payload.artifact_id,
        created_by_user_id=auth.user.id,
        body=payload.body,
        status="open",
        context=payload.context,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _feedback_to_out(row)


async def update_artifact_feedback(
    workspace_id: uuid.UUID,
    feedback_id: uuid.UUID,
    payload: ArtifactFeedbackUpdateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ArtifactFeedbackOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = (
        await session.execute(
            select(ArtifactFeedback).where(
                ArtifactFeedback.workspace_id == workspace_id,
                ArtifactFeedback.id == feedback_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if payload.status is not None:
        row.status = payload.status
    if payload.linked_pr_url is not None:
        row.linked_pr_url = payload.linked_pr_url
    await session.flush()
    await session.refresh(row)
    return _feedback_to_out(row)


def _feedback_to_out(row: ArtifactFeedback) -> ArtifactFeedbackOut:
    return ArtifactFeedbackOut(
        id=row.id,
        artifact_id=row.artifact_id,
        body=row.body,
        status=row.status,
        linked_pr_url=row.linked_pr_url,
        context=row.context or {},
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


__all__ = ["router"]
