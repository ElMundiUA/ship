"""Read API for the claim-store canon (P4).

The P0–P3 phases built the write side: source items get extracted
into atomic claims, the reconciler dedupes / supersedes near-matches,
and the topic-view renderer turns the active claim set per
``topic_tag`` into a coherent markdown view. This module is the
read side — the surface that operators and agents actually query.

Endpoints (all under ``/v1/workspaces/{workspace_id}/knowledge``):

- ``GET .../topic-views`` — list rendered views ordered by canon
  depth (claim_count desc) so the most-supported topics come first.
- ``GET .../topic-views/{topic_tag}`` — one view's body_md plus the
  active claims that feed it, with their source_links so the agent
  can see where each fact comes from without a follow-up call.
- ``GET .../claims`` — filter active / superseded / disputed claims
  by topic_tag, kind, status. Includes pagination.
- ``GET .../claims/{claim_id}`` — one claim plus its supersedes
  chain (history graph) and full source_links payload.

These coexist with the legacy ``/v1/workspaces/{ws}/knowledge`` and
``/v1/workspaces/{ws}/knowledge/search`` endpoints from the
``BucketArticle`` era; the legacy surface stays wired so already-
deployed clients (CLI ≤ 0.13, the console v1) keep working while the
read clients migrate to the canon. The unified search (mixing topic
views, claims and legacy bucket articles) lives in
:mod:`backend.app.services.knowledge_search` so a single
``POST .../search`` call hits all three sources at once.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import (
    ClaimKind,
    ClaimStatus,
    KnowledgeClaim,
    KnowledgeTopicView,
)
from backend.app.db.session import get_session


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/knowledge",
    tags=["knowledge"],
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TopicViewSummary(BaseModel):
    """List-view shape — body_md is intentionally omitted to keep the
    list response small. Operators paginate this; the agent's RAG
    flow only ever wants the bodies of the topics it ranks first."""

    topic_tag: str
    title: str
    claim_count: int
    rendered_by_model: str | None
    last_rendered_at: datetime
    updated_at: datetime


class ClaimSourceLink(BaseModel):
    """One provenance entry on a claim — what doc the fact came from."""

    source_item_id: str | None = None
    external_url: str | None = None
    title: str | None = None
    excerpt: str | None = None
    extracted_at: str | None = None


class ClaimSummary(BaseModel):
    """List-view + topic-view-detail shape — text + provenance, no
    embedding round-tripping."""

    id: uuid.UUID
    claim_md: str
    kind: str
    status: str
    topic_tags: list[str]
    confidence: float
    source_links: list[ClaimSourceLink]
    supersedes_id: uuid.UUID | None = None
    superseded_by_id: uuid.UUID | None = None
    first_seen_at: datetime
    last_seen_at: datetime


class TopicViewDetail(BaseModel):
    topic_tag: str
    title: str
    body_md: str
    claim_count: int
    rendered_by_model: str | None
    last_rendered_at: datetime
    claims: list[ClaimSummary]


class ClaimList(BaseModel):
    workspace_id: uuid.UUID
    total: int
    claims: list[ClaimSummary]


class ClaimDetail(BaseModel):
    """Single-claim shape includes the supersedes chain so the agent
    can render "this fact replaced an earlier one from 2024" without
    a second round trip."""

    claim: ClaimSummary
    supersedes_chain: list[ClaimSummary]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claim_to_summary(row: KnowledgeClaim) -> ClaimSummary:
    """Map a ``KnowledgeClaim`` ORM row to the wire shape.

    ``source_links`` arrives as a list of dicts; we coerce each entry
    into the typed ``ClaimSourceLink`` so the response is
    self-validating and the front-end can rely on shape stability.
    """
    raw_links = row.source_links if isinstance(row.source_links, list) else []
    links: list[ClaimSourceLink] = []
    for entry in raw_links:
        if not isinstance(entry, dict):
            continue
        links.append(
            ClaimSourceLink(
                source_item_id=str(entry.get("source_item_id"))
                if entry.get("source_item_id")
                else None,
                external_url=entry.get("external_url"),
                title=entry.get("title"),
                excerpt=entry.get("excerpt"),
                extracted_at=entry.get("extracted_at"),
            )
        )
    return ClaimSummary(
        id=row.id,
        claim_md=row.claim_md,
        kind=row.kind,
        status=row.status,
        topic_tags=list(row.topic_tags or []),
        confidence=row.confidence,
        source_links=links,
        supersedes_id=row.supersedes_id,
        superseded_by_id=row.superseded_by_id,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
    )


async def _supersedes_chain(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    head: KnowledgeClaim,
    max_depth: int = 10,
) -> list[KnowledgeClaim]:
    """Walk ``supersedes_id`` backwards from ``head`` to root.

    The depth cap is a defensive backstop against pathological cycles
    — the reconciler doesn't write self-loops, but in 10 years of
    rewords nobody wants the read API to wedge.
    """
    chain: list[KnowledgeClaim] = []
    visited: set[uuid.UUID] = {head.id}
    cursor = head.supersedes_id
    while cursor is not None and len(chain) < max_depth:
        if cursor in visited:
            break
        visited.add(cursor)
        prev = await session.get(KnowledgeClaim, cursor)
        if prev is None or prev.workspace_id != workspace_id:
            break
        chain.append(prev)
        cursor = prev.supersedes_id
    return chain


# ---------------------------------------------------------------------------
# Topic views
# ---------------------------------------------------------------------------


@router.get("/topic-views", response_model=list[TopicViewSummary])
async def list_topic_views(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TopicViewSummary]:
    """List rendered topic views in the workspace, deepest first.

    Topics with the most active claims sit at the top — that's a
    rough but useful proxy for "what does this workspace know most
    about". Operators use this to spot under-rendered corners
    (topics with fewer than the threshold claims won't appear here
    at all because the renderer skips them).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    rows = (
        await session.execute(
            select(KnowledgeTopicView)
            .where(KnowledgeTopicView.workspace_id == workspace_id)
            .order_by(
                KnowledgeTopicView.claim_count.desc(),
                KnowledgeTopicView.topic_tag.asc(),
            )
            .limit(limit)
        )
    ).scalars().all()
    return [
        TopicViewSummary(
            topic_tag=row.topic_tag,
            title=row.title,
            claim_count=row.claim_count,
            rendered_by_model=row.rendered_by_model,
            last_rendered_at=row.last_rendered_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get("/topic-views/{topic_tag}", response_model=TopicViewDetail)
async def get_topic_view(
    workspace_id: uuid.UUID,
    topic_tag: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> TopicViewDetail:
    """Fetch one topic view's body plus the claims that feed it.

    Returning the underlying claims alongside the rendered body is
    the whole point of the canon: agents can either read the prose
    article or drill into the atomic facts to cite specific sources
    in their reasoning.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    view = (
        await session.execute(
            select(KnowledgeTopicView).where(
                KnowledgeTopicView.workspace_id == workspace_id,
                KnowledgeTopicView.topic_tag == topic_tag,
            )
        )
    ).scalar_one_or_none()
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="topic view not found",
        )
    claim_rows = (
        await session.execute(
            select(KnowledgeClaim)
            .where(KnowledgeClaim.workspace_id == workspace_id)
            .where(KnowledgeClaim.status == ClaimStatus.ACTIVE)
            .where(KnowledgeClaim.topic_tags.any(topic_tag))
            .order_by(KnowledgeClaim.created_at.asc())
        )
    ).scalars().all()
    return TopicViewDetail(
        topic_tag=view.topic_tag,
        title=view.title,
        body_md=view.body_md,
        claim_count=view.claim_count,
        rendered_by_model=view.rendered_by_model,
        last_rendered_at=view.last_rendered_at,
        claims=[_claim_to_summary(c) for c in claim_rows],
    )


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


@router.get("/claims", response_model=ClaimList)
async def list_claims(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    topic_tag: str | None = Query(default=None, max_length=128),
    kind: str | None = Query(default=None, max_length=24),
    claim_status: str = Query(
        default=ClaimStatus.ACTIVE, alias="status", max_length=16
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ClaimList:
    """Filter claims by topic / kind / status.

    Default ``status='active'`` means the response is the **current
    canon** — disputed and superseded rows ride along when an
    operator is reviewing history or running an audit, but never
    pollute the agent's default retrieval.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    if claim_status not in ClaimStatus.ALL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown status: {claim_status}",
        )
    if kind is not None and kind not in ClaimKind.ALL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown kind: {kind}",
        )

    clauses = [
        KnowledgeClaim.workspace_id == workspace_id,
        KnowledgeClaim.status == claim_status,
    ]
    if topic_tag is not None:
        clauses.append(KnowledgeClaim.topic_tags.any(topic_tag))
    if kind is not None:
        clauses.append(KnowledgeClaim.kind == kind)

    total = (
        await session.execute(
            select(func.count(KnowledgeClaim.id)).where(and_(*clauses))
        )
    ).scalar_one()

    rows = (
        await session.execute(
            select(KnowledgeClaim)
            .where(and_(*clauses))
            .order_by(KnowledgeClaim.last_seen_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    return ClaimList(
        workspace_id=workspace_id,
        total=int(total),
        claims=[_claim_to_summary(row) for row in rows],
    )


@router.get("/claims/{claim_id}", response_model=ClaimDetail)
async def get_claim(
    workspace_id: uuid.UUID,
    claim_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ClaimDetail:
    """Single-claim view including the supersedes chain.

    The chain shows "this fact replaced an earlier one which
    replaced an even earlier one". Useful for ADR-style trails
    where the operator wants to see how a decision evolved.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    claim = await session.get(KnowledgeClaim, claim_id)
    if claim is None or claim.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="claim not found"
        )
    chain = await _supersedes_chain(
        session, workspace_id=workspace_id, head=claim
    )
    return ClaimDetail(
        claim=_claim_to_summary(claim),
        supersedes_chain=[_claim_to_summary(c) for c in chain],
    )
