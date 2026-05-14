"""Navigator memory service (E17/ELS-126).

Thin wrapper around the ``mem0`` SDK that:

- Owns the mem0 client singleton (one ``Memory`` instance per
  process, lazy-built on first call). mem0's client holds a Postgres
  connection pool, so reusing one instance keeps the connection
  budget bounded.
- Mirrors every ``add`` / ``delete`` into our own
  ``navigator_memories`` table so access control + Console UI
  queries don't have to round-trip through mem0's surface.
- Enforces the personal-scope contract: every read goes through
  ``WHERE owner_user_id = current_user.id`` AND ``workspace_id =
  current_workspace.id``. mem0's own ``user_id`` is set to a
  composite ``"<workspace_id>:<user_id>"`` string so the same human
  in two workspaces gets isolated fact namespaces.

The wrapper is intentionally minimal — :class:`Memory` (mem0) is
the system of record for the vector + fact text; the mirror exists
so SQL queries can rank, filter, paginate, and join through Ship's
existing models without leaving the database.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.navigator_memory import NavigatorMemory
from backend.app.db.models.tenancy import AuditLog


log = logging.getLogger(__name__)


# Cap on how much of the raw user message we ship into mem0's
# extractor LLM. mem0 already does its own truncation but a hard
# upstream cap keeps per-call token cost predictable.
_MAX_MESSAGE_CHARS = 4000

# mem0 collection name. Single global collection — separation is
# done via the composite ``user_id`` namespace + metadata filters,
# not by collection.
_MEM0_COLLECTION = "ship_navigator_memories"

# Lazy singleton.
_MEMORY_CLIENT_LOCK = threading.Lock()
_MEMORY_CLIENT: Any | None = None


def _build_mem0_client(settings: Settings) -> Any:
    """Construct mem0's ``Memory`` against Ship's existing Postgres.

    Imported lazily because mem0 pulls in qdrant-client + others at
    module import; we don't want that on every cold start of the
    backend image when Navigator may not be exercised.
    """
    from urllib.parse import urlparse

    from mem0 import Memory

    # mem0's pgvector adapter wants discrete dsn pieces. Parse from
    # the async URL the rest of the app uses; mem0 itself talks via
    # ``psycopg`` so the ``+asyncpg`` driver hint is stripped.
    raw = settings.database_url
    raw = raw.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )
    parsed = urlparse(raw)

    # Detect Neon / DO Managed PG / anything that requires SSL.
    sslmode = None
    if "neon.tech" in (parsed.hostname or "") or "ondigitalocean" in (parsed.hostname or ""):
        sslmode = "require"

    config = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "dbname": (parsed.path or "/postgres").lstrip("/"),
                "collection_name": _MEM0_COLLECTION,
                "embedding_model_dims": 1536,
                "user": parsed.username or "postgres",
                "password": parsed.password or "",
                "host": parsed.hostname or "localhost",
                "port": parsed.port or 5432,
                "diskann": False,
                "hnsw": False,
                "sslmode": sslmode,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gpt-4o-mini",
                "api_key": settings.openai_api_key,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "text-embedding-3-small",
                "api_key": settings.openai_api_key,
            },
        },
    }
    return Memory.from_config(config)


def _get_memory_client(settings: Settings) -> Any:
    """Lazy singleton accessor — built once per process."""
    global _MEMORY_CLIENT
    if _MEMORY_CLIENT is not None:
        return _MEMORY_CLIENT
    with _MEMORY_CLIENT_LOCK:
        if _MEMORY_CLIENT is None:
            _MEMORY_CLIENT = _build_mem0_client(settings)
    return _MEMORY_CLIENT


def _namespace(workspace_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Composite mem0 ``user_id`` so the same human in two
    workspaces gets isolated fact spaces. Single field because mem0's
    SDK only takes one of (user_id, agent_id, run_id) as the scope
    key; we encode the second axis as a prefix.
    """
    return f"ws:{workspace_id}:u:{user_id}"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AddedFact:
    """Result of a successful ``add`` call.

    Carries the mem0 id alongside the mirror row id so callers can
    chain into ``delete`` / ``recall_context`` without a re-query.
    """

    id: uuid.UUID
    mem0_id: str
    fact_text: str


@dataclass(frozen=True, slots=True)
class MemorySearchHit:
    """One ranked hit from ``search``.

    ``score`` is mem0's cosine similarity (higher is better). The
    full ``NavigatorMemory`` row sits in ``row`` for callers that
    need provenance pointers.
    """

    row: NavigatorMemory
    score: float


async def add(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    message: str,
    source_thread_id: uuid.UUID | None = None,
    source_message_id: uuid.UUID | None = None,
    source_message_position: int | None = None,
    project_native_id: str | None = None,
    intent_at_capture: str | None = None,
    settings: Settings | None = None,
) -> list[AddedFact]:
    """Extract facts from ``message`` via mem0 and mirror them.

    Returns the list of facts mem0 produced — empty list when mem0
    decided nothing was worth storing (e.g. acknowledgement-only
    messages, duplicates of existing facts). Never raises on the
    happy path; mem0 / DB errors are logged + an audit row drops
    so the operator dashboard can see add failure rates.

    mem0 runs its own LLM call to extract facts, so this is **not**
    cheap — the caller should fire-and-forget (``asyncio.create_task``)
    from the chat hot path rather than awaiting inline.
    """
    settings = settings or get_settings()
    text_to_send = (message or "").strip()
    if not text_to_send:
        return []
    if len(text_to_send) > _MAX_MESSAGE_CHARS:
        text_to_send = text_to_send[:_MAX_MESSAGE_CHARS]

    client = _get_memory_client(settings)
    namespace = _namespace(workspace_id, owner_user_id)
    metadata = {
        "workspace_id": str(workspace_id),
        "owner_user_id": str(owner_user_id),
        "source_thread_id": str(source_thread_id) if source_thread_id else None,
        "source_message_id": str(source_message_id) if source_message_id else None,
        "source_message_position": source_message_position,
        "project_native_id": project_native_id,
        "intent_at_capture": intent_at_capture,
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}

    try:
        # mem0's SDK is sync; off-load to a thread so we don't block
        # the asyncio event loop for the LLM round-trip (5-20s under
        # load).
        result = await asyncio.to_thread(
            client.add,
            text_to_send,
            user_id=namespace,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("mem0.add failed for user=%s ws=%s", owner_user_id, workspace_id)
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=owner_user_id,
                action="navigator.memory.add_failed",
                target_kind="chat_message",
                target_id=str(source_message_id) if source_message_id else None,
                payload={
                    "error": str(exc)[:512],
                    "thread_id": str(source_thread_id) if source_thread_id else None,
                },
            )
        )
        return []

    # mem0 returns a dict with ``results`` — a list of {memory, id, event}
    # entries. ``event`` is one of ``ADD`` / ``UPDATE`` / ``DELETE`` /
    # ``NONE`` — we mirror everything except ``NONE`` (no-op).
    out: list[AddedFact] = []
    for entry in (result or {}).get("results") or []:
        event = entry.get("event") or "ADD"
        if event == "NONE":
            continue
        mem0_id = str(entry.get("id") or "")
        if not mem0_id:
            continue
        fact_text = entry.get("memory") or entry.get("text") or ""
        if event == "DELETE":
            await _delete_mirror_by_mem0_id(session, mem0_id)
            continue
        row = await _upsert_mirror_row(
            session,
            mem0_id=mem0_id,
            fact_text=fact_text,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            source_thread_id=source_thread_id,
            source_message_id=source_message_id,
            source_message_position=source_message_position,
            project_native_id=project_native_id,
            intent_at_capture=intent_at_capture,
        )
        out.append(AddedFact(id=row.id, mem0_id=mem0_id, fact_text=fact_text))
    return out


async def search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    query: str,
    limit: int = 10,
    project_native_id: str | None = None,
    settings: Settings | None = None,
) -> list[MemorySearchHit]:
    """Top-N facts ranked by semantic similarity to ``query``.

    Access control: every hit is re-loaded from the mirror with the
    ``(owner_user_id, workspace_id)`` filter, so a mem0-side leak
    (cross-namespace collision, bug in our composite ``user_id``)
    can't surface another user's fact. mem0's score is preserved
    for caller-side re-ranking.
    """
    import time

    settings = settings or get_settings()
    if not (query or "").strip():
        return []

    client = _get_memory_client(settings)
    namespace = _namespace(workspace_id, owner_user_id)
    started_at = time.monotonic()
    try:
        result = await asyncio.to_thread(
            client.search,
            query,
            user_id=namespace,
            limit=max(1, min(limit, 50)),
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "mem0.search failed for user=%s ws=%s",
            owner_user_id,
            workspace_id,
        )
        # Audit the failure so the health endpoint can surface
        # search-error rates separately from "no hits".
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=owner_user_id,
                action="navigator.memory.search_failed",
                target_kind="user",
                target_id=str(owner_user_id),
                payload={
                    "query_chars": len(query),
                    "latency_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
        )
        return []

    raw_hits = (result or {}).get("results") or []
    mem0_ids = [str(h.get("id")) for h in raw_hits if h.get("id")]
    rows = (
        (
            await session.execute(
                select(NavigatorMemory).where(
                    NavigatorMemory.mem0_id.in_(mem0_ids),
                    NavigatorMemory.owner_user_id == owner_user_id,
                    NavigatorMemory.workspace_id == workspace_id,
                )
            )
        )
        .scalars()
        .all()
    ) if mem0_ids else []
    rows_by_mem0 = {r.mem0_id: r for r in rows}

    out: list[MemorySearchHit] = []
    for hit in raw_hits:
        mid = str(hit.get("id"))
        row = rows_by_mem0.get(mid)
        if row is None:
            # Mirror missed this entry — either a stale mem0 row from
            # before the mirror landed, or a cross-namespace leak the
            # filter just stopped. Skip silently; the operator-facing
            # error surface is the empty-result audit row in search.
            continue
        if project_native_id and row.project_native_id != project_native_id:
            # Caller asked for a project-scoped boost; keep only
            # facts tagged with this project. Untagged facts fall
            # through unchanged (they're general-purpose).
            if row.project_native_id is not None:
                continue
        score = float(hit.get("score") or 0.0)
        out.append(MemorySearchHit(row=row, score=score))
    out = out[:limit]

    # Audit row drives the health endpoint: hit_count + top_similarity
    # let the dashboard surface "0-hit refetches" as a backfill-gap
    # signal; latency_ms makes mem0 latency visible without scraping
    # logs. We deliberately do NOT log the raw query text — facts
    # already leave audit fingerprints, the query is the operator's
    # in-flight thought and shouldn't get a second persistence venue.
    top_similarity = round(out[0].score, 4) if out else 0.0
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=owner_user_id,
            action="navigator.memory.search",
            target_kind="user",
            target_id=str(owner_user_id),
            payload={
                "query_chars": len(query),
                "hit_count": len(out),
                "top_similarity": top_similarity,
                "latency_ms": int((time.monotonic() - started_at) * 1000),
                "project_native_id": project_native_id,
            },
        )
    )
    return out


async def delete(
    session: AsyncSession,
    *,
    memory_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    settings: Settings | None = None,
) -> bool:
    """Hard-delete a fact + write an audit row carrying the original.

    The audit row keeps the full ``fact_text`` so the operator can
    still investigate after the fact (literally) — without leaving
    a recoverable copy in the live table.

    Returns ``True`` when the row existed and was deleted, ``False``
    when no row matched the (id, workspace_id, actor=owner) tuple.
    """
    settings = settings or get_settings()
    row = (
        await session.execute(
            select(NavigatorMemory).where(
                NavigatorMemory.id == memory_id,
                NavigatorMemory.workspace_id == workspace_id,
                NavigatorMemory.owner_user_id == actor_user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False

    # Audit BEFORE delete so the row is captured even if the mem0
    # client raises mid-call.
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="navigator.memory.deleted",
            target_kind="navigator_memory",
            target_id=str(memory_id),
            payload={
                "mem0_id": row.mem0_id,
                "fact_text": row.fact_text,
                "source_thread_id": str(row.source_thread_id)
                if row.source_thread_id
                else None,
                "source_message_id": str(row.source_message_id)
                if row.source_message_id
                else None,
                "project_native_id": row.project_native_id,
            },
        )
    )

    client = _get_memory_client(settings)
    try:
        await asyncio.to_thread(client.delete, row.mem0_id)
    except Exception:  # noqa: BLE001
        # mem0's delete failure is non-fatal — the mirror is the
        # access-control source of truth, and stale mem0 entries
        # become invisible once we drop the mirror row.
        log.exception("mem0.delete failed for mem0_id=%s", row.mem0_id)

    await session.delete(row)
    return True


async def list_for_user(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    project_native_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[NavigatorMemory]:
    """Paginated list of a user's facts for the Console ``/memory``
    page. SQL-only (no mem0 call) — fast + cacheable."""
    stmt = (
        select(NavigatorMemory)
        .where(
            NavigatorMemory.workspace_id == workspace_id,
            NavigatorMemory.owner_user_id == owner_user_id,
        )
        .order_by(NavigatorMemory.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .offset(max(0, offset))
    )
    if project_native_id is not None:
        stmt = stmt.where(NavigatorMemory.project_native_id == project_native_id)
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Internal — mirror writes
# ---------------------------------------------------------------------------


async def _upsert_mirror_row(
    session: AsyncSession,
    *,
    mem0_id: str,
    fact_text: str,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    source_thread_id: uuid.UUID | None,
    source_message_id: uuid.UUID | None,
    source_message_position: int | None,
    project_native_id: str | None,
    intent_at_capture: str | None,
) -> NavigatorMemory:
    """Insert or update the mirror row keyed on ``mem0_id``.

    mem0 can return an ``UPDATE`` event for an existing fact when
    the new message refined it; in that case we update ``fact_text``
    + ``updated_at`` in place but leave the provenance pointers
    pointing at the ORIGINAL source — refinement provenance lives in
    mem0's own history, and surfacing two source pointers per row
    would complicate the Console UI without obvious benefit.
    """
    existing = (
        await session.execute(
            select(NavigatorMemory).where(
                NavigatorMemory.mem0_id == mem0_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.fact_text = fact_text
        await session.execute(
            text(
                "UPDATE navigator_memories SET updated_at = now() WHERE id = :id"
            ),
            {"id": existing.id},
        )
        return existing
    row = NavigatorMemory(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        fact_text=fact_text,
        embedding=None,  # mem0 owns the vector; mirror keeps text + provenance
        source_thread_id=source_thread_id,
        source_message_id=source_message_id,
        source_message_position=source_message_position,
        project_native_id=project_native_id,
        intent_at_capture=intent_at_capture,
        mem0_id=mem0_id,
    )
    session.add(row)
    await session.flush()
    return row


async def _delete_mirror_by_mem0_id(
    session: AsyncSession, mem0_id: str
) -> None:
    """Best-effort mirror cleanup for a ``DELETE`` event from mem0."""
    row = (
        await session.execute(
            select(NavigatorMemory).where(
                NavigatorMemory.mem0_id == mem0_id
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await session.delete(row)


# ---------------------------------------------------------------------------
# Public helpers — chat-route side
# ---------------------------------------------------------------------------


# Minimum length to bother running extraction. Anything shorter than
# this is overwhelmingly likely to be ack noise — "ok", "thanks",
# "yes please", "ага", "норм" — and the LLM extractor's fixed
# overhead isn't worth it.
_MIN_EXTRACT_CHARS = 30

# Plain ack patterns we filter before pre-length even matters. Match
# is case-insensitive, allows trailing punctuation / emoji. Anything
# captured here is a single-word acknowledgement with no factual
# payload.
_ACK_TOKENS: frozenset[str] = frozenset(
    {
        "ok", "okay", "yes", "no", "sure", "thanks", "thank you",
        "got it", "noted", "great", "cool", "nice", "perfect",
        "да", "нет", "ага", "ок", "норм", "спасибо", "понятно",
    }
)


def should_refetch_memory(
    *,
    memory_enabled: bool,
    prior_message_count: int,
    last_user_activity_at: "datetime | None",
    now: "datetime | None" = None,
    gap_seconds: int = 30 * 60,
) -> bool:
    """Pure decision function for the smart-trigger retrieval.

    Refetch fires on two events:

    1. **First turn** of a session — ``prior_message_count == 0``.
    2. **Resume after idle** — current time is more than ``gap_seconds``
       past ``last_user_activity_at``. Default window is 30 minutes;
       past that the prefetched ``{{MEMORY_CONTEXT}}`` may have aged
       out of the prompt or lost mindshare with the model.

    ``memory_enabled=False`` short-circuits both — anonymous threads
    stay anonymous on the retrieval side too.

    Pure so the chat-route can lean on it AND tests can exercise the
    logic without spinning up an SSE stream.
    """
    from datetime import datetime as _dt, timezone as _tz

    if not memory_enabled:
        return False
    if prior_message_count <= 0:
        return True
    if last_user_activity_at is None:
        return True
    current = now or _dt.now(_tz.utc)
    return (current - last_user_activity_at).total_seconds() > gap_seconds


def should_extract_memory(memory_enabled: bool, message_body: str) -> bool:
    """Decide whether a user message is worth running through mem0.

    Two pre-filters: the per-thread toggle (Console "Pause memory"
    button) and a content gate that drops obvious ack-noise before
    we burn an LLM call.
    """
    if not memory_enabled:
        return False
    body = (message_body or "").strip()
    if not body:
        return False
    if len(body) < _MIN_EXTRACT_CHARS:
        # Cheap: strip punctuation, see if what's left is just an ack.
        normalised = "".join(c for c in body.lower() if c.isalnum() or c == " ").strip()
        if normalised in _ACK_TOKENS:
            return False
        # Short but not an ack token — still skip; mem0 needs more to chew on.
        return False
    return True


async def extract_in_background(
    *,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    message_body: str,
    source_thread_id: uuid.UUID | None,
    source_message_id: uuid.UUID | None,
    source_message_position: int | None,
    project_native_id: str | None,
    intent_at_capture: str | None,
    settings: Settings | None = None,
) -> None:
    """Open a fresh DB session, run ``add``, commit on success.

    Designed for ``asyncio.create_task`` from the chat-stream handler
    so the LLM round-trip (5-20s) doesn't block the response. Never
    raises — every failure path logs + (when DB is reachable) writes
    an audit row.
    """
    from backend.app.db.session import get_sessionmaker

    settings = settings or get_settings()
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            try:
                await add(
                    session,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    message=message_body,
                    source_thread_id=source_thread_id,
                    source_message_id=source_message_id,
                    source_message_position=source_message_position,
                    project_native_id=project_native_id,
                    intent_at_capture=intent_at_capture,
                    settings=settings,
                )
                await session.commit()
            except Exception:  # noqa: BLE001
                await session.rollback()
                log.exception(
                    "memory background extract failed ws=%s user=%s msg=%s",
                    workspace_id,
                    owner_user_id,
                    source_message_id,
                )
    except Exception:  # noqa: BLE001 — sessionmaker itself flaking
        log.exception("memory background extract — sessionmaker error")


__all__ = [
    "AddedFact",
    "MemorySearchHit",
    "add",
    "delete",
    "extract_in_background",
    "list_for_user",
    "search",
    "should_extract_memory",
]
