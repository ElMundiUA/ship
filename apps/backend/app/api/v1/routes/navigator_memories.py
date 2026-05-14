"""E17/ELS-130 — REST surface powering the Console ``/memory`` page.

Strict personal-scope: every read/delete is gated on
``owner_user_id == auth.user.id``. A workspace admin canNOT see or
delete another user's facts — the Console queries this endpoint
with the operator's own session, and the service layer's
``WHERE owner_user_id = ...`` clause is the authoritative gate.

Three endpoints:

- ``GET /v1/workspaces/{ws}/navigator-memories`` — paginated list
  with optional ``project_native_id`` filter. The Console renders
  facts grouped by project; the filter handles the "show only this
  project" click.
- ``DELETE /v1/workspaces/{ws}/navigator-memories/{id}`` — hard
  delete + audit row carrying the original fact text. Uses
  ``memory.delete`` which already enforces the actor-is-owner check.
- ``POST /v1/workspaces/{ws}/navigator-memories/forget``  —
  bulk-delete by age window ("forget the last N days"). The
  ``days`` payload caps at 90 to prevent an accidental
  ``forget all=true`` button on the UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.navigator_memory import NavigatorMemory
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.db.session import get_session
from backend.app.services.agent import memory as navigator_memory


router = APIRouter(prefix="/workspaces/{workspace_id}")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NavigatorMemoryOut(BaseModel):
    """One fact as the Console sees it.

    Provenance pointers are nullable because a fact's source thread
    or message can be deleted independently (FK SET NULL); the fact
    itself outlives that linkage.
    """

    id: uuid.UUID
    fact_text: str
    project_native_id: str | None
    intent_at_capture: str | None
    source_thread_id: uuid.UUID | None
    source_message_id: uuid.UUID | None
    source_message_position: int | None
    confidence: float
    created_at: datetime
    updated_at: datetime


class NavigatorMemoriesListOut(BaseModel):
    items: list[NavigatorMemoryOut]
    total: int


class BulkForgetIn(BaseModel):
    """Body for the "forget last N days" bulk delete."""

    days: int = Field(
        ..., ge=1, le=90,
        description="Forget facts captured in the last N days (1-90).",
    )


class BulkForgetOut(BaseModel):
    deleted: int


class MemoryHealthOut(BaseModel):
    """Snapshot powering the Console "Memory health" tile.

    Fields are scoped to the calling user — workspace admins can't
    use this to peek at someone else's add-failure rate.
    """

    facts_count: int
    adds_24h: int
    add_failures_24h: int
    searches_24h: int
    search_failures_24h: int
    # Fraction of refetches in the last 24h that returned 0 hits.
    # When the user hasn't built up much memory yet, this is high
    # (most searches find nothing); after a healthy backfill it
    # should trend toward 10-30%. A sudden spike from 20% → 80% is
    # the backfill-gap signal the ELS-131 ticket called out.
    zero_hit_rate_24h: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/navigator-memories",
    response_model=NavigatorMemoriesListOut,
)
async def list_navigator_memories(
    workspace_id: uuid.UUID,
    project_native_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> NavigatorMemoriesListOut:
    """Paginated list of the caller's facts.

    ``project_native_id`` narrows to one project. Pass ``"untagged"``
    to get only general-purpose facts (no project tag). Omit to get
    everything (grouped client-side).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    if project_native_id == "untagged":
        rows = await navigator_memory.list_for_user(
            session,
            workspace_id=workspace_id,
            owner_user_id=auth.user.id,
            project_native_id=None,
            limit=limit,
            offset=offset,
        )
        # The helper treats ``project_native_id=None`` as "no filter",
        # so re-filter here.
        rows = [r for r in rows if r.project_native_id is None]
        total = len(rows)
    elif project_native_id:
        rows = await navigator_memory.list_for_user(
            session,
            workspace_id=workspace_id,
            owner_user_id=auth.user.id,
            project_native_id=project_native_id,
            limit=limit,
            offset=offset,
        )
        total = len(rows)
    else:
        rows = await navigator_memory.list_for_user(
            session,
            workspace_id=workspace_id,
            owner_user_id=auth.user.id,
            project_native_id=None,
            limit=limit,
            offset=offset,
        )
        total = len(rows)

    return NavigatorMemoriesListOut(
        items=[_to_out(r) for r in rows],
        total=total,
    )


@router.delete(
    "/navigator-memories/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_navigator_memory(
    workspace_id: uuid.UUID,
    memory_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    """Hard-delete one fact. 404 when the caller doesn't own it."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    ok = await navigator_memory.delete(
        session,
        memory_id=memory_id,
        actor_user_id=auth.user.id,
        workspace_id=workspace_id,
        settings=settings,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.flush()


@router.post(
    "/navigator-memories/forget",
    response_model=BulkForgetOut,
)
async def bulk_forget_navigator_memories(
    workspace_id: uuid.UUID,
    payload: BulkForgetIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BulkForgetOut:
    """Bulk-delete facts captured in the last ``days`` window.

    Hard delete + per-row audit log entries. Capped at 90 days so
    a misclick can't wipe a user's entire memory in one call.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    cutoff = datetime.now(timezone.utc) - timedelta(days=payload.days)

    rows = (
        (
            await session.execute(
                select(NavigatorMemory).where(
                    NavigatorMemory.workspace_id == workspace_id,
                    NavigatorMemory.owner_user_id == auth.user.id,
                    NavigatorMemory.created_at >= cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    deleted = 0
    for r in rows:
        ok = await navigator_memory.delete(
            session,
            memory_id=r.id,
            actor_user_id=auth.user.id,
            workspace_id=workspace_id,
            settings=settings,
        )
        if ok:
            deleted += 1

    # Single roll-up audit row for the operator (per-fact audits land
    # via ``memory.delete`` already; this one is the "user pressed
    # the big bulk button" signal).
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            action="navigator.memory.bulk_forget",
            target_kind="user",
            target_id=str(auth.user.id),
            payload={"days": payload.days, "deleted": deleted},
        )
    )
    await session.flush()
    return BulkForgetOut(deleted=deleted)


@router.get(
    "/navigator-memories/health",
    response_model=MemoryHealthOut,
)
async def get_navigator_memory_health(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> MemoryHealthOut:
    """Per-user memory health snapshot for the last 24h.

    All counters scoped to ``actor_user_id == auth.user.id`` so a
    workspace admin can't peek at another user's memory hygiene.
    Reads from ``audit_log`` directly — no separate metrics store.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    # Facts the user currently owns. ``COUNT(*)`` is the cheap
    # signal — for a paginated drill-down the operator uses the
    # ``GET /navigator-memories`` endpoint.
    facts_count = int(
        (
            await session.execute(
                select(func.count(NavigatorMemory.id)).where(
                    NavigatorMemory.workspace_id == workspace_id,
                    NavigatorMemory.owner_user_id == auth.user.id,
                )
            )
        ).scalar_one()
    )

    # 24h activity counters. Group everything in a single audit_log
    # roundtrip so the endpoint doesn't fan out to 4 separate
    # SELECTs on every render.
    rows = (
        await session.execute(
            select(AuditLog.action, AuditLog.payload).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.actor_user_id == auth.user.id,
                AuditLog.created_at >= cutoff,
                AuditLog.action.in_(
                    (
                        "navigator.memory.add_failed",
                        "navigator.memory.search",
                        "navigator.memory.search_failed",
                    )
                ),
            )
        )
    ).all()

    add_failures = 0
    searches = 0
    search_failures = 0
    zero_hits = 0
    for action, payload in rows:
        if action == "navigator.memory.add_failed":
            add_failures += 1
        elif action == "navigator.memory.search":
            searches += 1
            if int((payload or {}).get("hit_count", 0)) == 0:
                zero_hits += 1
        elif action == "navigator.memory.search_failed":
            search_failures += 1

    # adds_24h: every new ``navigator_memories.id`` whose
    # ``created_at`` falls in the window. mem0's own ADD events
    # mirror to the table, so a row count is the cheapest signal.
    adds_24h = int(
        (
            await session.execute(
                select(func.count(NavigatorMemory.id)).where(
                    NavigatorMemory.workspace_id == workspace_id,
                    NavigatorMemory.owner_user_id == auth.user.id,
                    NavigatorMemory.created_at >= cutoff,
                )
            )
        ).scalar_one()
    )

    zero_hit_rate = (zero_hits / searches) if searches else 0.0

    return MemoryHealthOut(
        facts_count=facts_count,
        adds_24h=adds_24h,
        add_failures_24h=add_failures,
        searches_24h=searches,
        search_failures_24h=search_failures,
        zero_hit_rate_24h=round(zero_hit_rate, 3),
    )


# ---------------------------------------------------------------------------
# Internal — serialisation
# ---------------------------------------------------------------------------


def _to_out(row: NavigatorMemory) -> NavigatorMemoryOut:
    return NavigatorMemoryOut(
        id=row.id,
        fact_text=row.fact_text,
        project_native_id=row.project_native_id,
        intent_at_capture=row.intent_at_capture,
        source_thread_id=row.source_thread_id,
        source_message_id=row.source_message_id,
        source_message_position=row.source_message_position,
        confidence=row.confidence,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# E2E sandbox — direct mirror write for the Playwright suite.
# ---------------------------------------------------------------------------
#
# Why this lives in the prod router (instead of behind a feature flag):
# the e2e workspace is the *only* legitimate caller, and the gate is the
# workspace's slug (must start with ``e2e-``). Any other workspace gets a
# plain 404 — same as if the route didn't exist — so prod surface area
# stays clean. The endpoint mirrors what the LLM extractor would have
# written, minus the embedding (left NULL because we don't want test seed
# rows polluting the ivfflat index). All other reads (list / health /
# delete / bulk-forget) keep filtering by ``owner_user_id`` so a seed
# row can't leak to a different user.


class SandboxSeedIn(BaseModel):
    """Body for the e2e sandbox seed.

    Mirrors the small shape of a real LLM-extracted fact. We don't
    accept an ``embedding`` here — leaving it NULL is fine for the
    contract / UI assertions the suite exercises (the search-ranking
    code-paths are covered in unit tests).
    """

    fact_text: str = Field(..., min_length=1, max_length=2048)
    project_native_id: str | None = None
    intent_at_capture: str | None = None
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class SandboxSeedOut(BaseModel):
    id: uuid.UUID


@router.post(
    "/navigator-memories/_test_seed",
    response_model=SandboxSeedOut,
    status_code=status.HTTP_201_CREATED,
)
async def seed_navigator_memory_for_tests(
    workspace_id: uuid.UUID,
    payload: SandboxSeedIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SandboxSeedOut:
    """E2E-only: store a fact in mem0 + mirror it into ``navigator_memories``.

    Returns 404 unless the workspace slug starts with ``e2e-`` so the
    endpoint is invisible on any production workspace. The auth /
    membership gate is unchanged — only members of the e2e workspace
    can call this, and the fact is bound to the caller's
    ``owner_user_id``.

    Pushes the fact through mem0 with ``infer=False`` so mem0 stores
    the raw text verbatim (no LLM extraction) but **does** compute an
    embedding — the seeded row is therefore retrievable via
    ``mem0.search`` just like a fact extracted from a real chat turn.
    When mem0 is unavailable (laptop without OPENAI_API_KEY, prod
    pool cooldown) we still write the mirror row so non-retrieval
    tests (list / delete / forget / health) work without the LLM.
    """
    ws = await session.get(Workspace, workspace_id)
    if ws is None or not ws.slug.startswith("e2e-"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    # Try mem0 first so the resulting row carries mem0's id +
    # an embedding (retrievable by search). On any failure we
    # fall through to a mirror-only insert so non-retrieval tests
    # don't depend on mem0 being healthy.
    mem0_id: str | None = None
    try:
        added = await navigator_memory.add(
            session,
            workspace_id=workspace_id,
            owner_user_id=auth.user.id,
            message=payload.fact_text,
            project_native_id=payload.project_native_id,
            intent_at_capture=payload.intent_at_capture,
            infer=False,
        )
        if added:
            return SandboxSeedOut(id=added[0].id)
    except Exception:  # noqa: BLE001
        # mem0 may be cooling down (Neon timeout) or unavailable
        # entirely. Fall through to mirror-only so tests that don't
        # exercise retrieval still work.
        pass

    row = NavigatorMemory(
        workspace_id=workspace_id,
        owner_user_id=auth.user.id,
        # ``mem0_id`` is normally the id mem0 assigns when it stores
        # the fact. For seed rows we fabricate a stable-enough id so
        # the column's UNIQUE constraint is satisfied; nothing in mem0
        # corresponds to this row, but the e2e suite only exercises
        # the mirror table, never the vector store.
        mem0_id=f"e2e-seed-{uuid.uuid4().hex}",
        fact_text=payload.fact_text,
        project_native_id=payload.project_native_id,
        intent_at_capture=payload.intent_at_capture,
        confidence=payload.confidence,
    )
    session.add(row)
    await session.flush()
    return SandboxSeedOut(id=row.id)


__all__ = ["router"]
