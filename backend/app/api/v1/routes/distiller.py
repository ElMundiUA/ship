"""Distiller v1 surface — Phase 6 ingest + run history.

Mounted under ``/v1/workspaces/{ws}`` to match the rest of the
knowledge surface (``/buckets/...`` routes live in ``chat.py``).
Two endpoints:

- ``POST /buckets/{slug}/distill`` — ingest one blob. Uses
  :func:`backend.app.services.distiller.run_distiller` under the
  hood; maintains the full transaction inside FastAPI's session
  dep so a hook failure rolls everything back. Returns the run
  row so the client can branch on ``decision``.
- ``GET /buckets/{slug}/distill/runs`` — paginated history.
  Useful for the console's "what did the Distiller do against this
  bucket?" panel and for operator debugging.

RBAC:
  - POST requires ``ROLES_MAINTAIN`` — writing to knowledge is a
    maintainer-level action, matching the existing bucket CRUD
    routes in ``chat.py``.
  - GET requires ``ROLES_READ`` — history is workspace-wide info.

We lean on the schemas from ``chat.py`` for the bucket lookup
(``_load_bucket``) so slug resolution stays in one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.chat import _load_bucket
from backend.app.api.v1.routes.workspaces import (
    ROLES_MAINTAIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import (
    BucketSource,
    DistillerRun,
)
from backend.app.db.session import get_session
from backend.app.services.distiller import (
    DistillerInput,
    run_distiller,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["distiller"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DistillIn(BaseModel):
    """Shape of one Distiller invocation.

    ``source_kind`` must be one of
    :class:`backend.app.db.models.agent_memory.BucketSource` so the
    article provenance stays consistent with the bucket categorisation.
    Defaults to ``external_static`` which matches the most common
    console-triggered ingest ("paste/upload a chunk of notes").
    """

    body_md: str = Field(min_length=0, max_length=500_000)
    source_kind: str = Field(default=BucketSource.EXTERNAL_STATIC)
    title_hint: str | None = Field(default=None, max_length=512)
    slug_hint: str | None = Field(default=None, max_length=120)
    provenance: dict[str, Any] = Field(default_factory=dict)
    input_ref: dict[str, Any] = Field(default_factory=dict)


class DistillerRunOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    bucket_id: uuid.UUID
    source_kind: str
    status: str
    decision: str | None
    input_ref: dict[str, Any]
    output_refs: dict[str, Any]
    error: str | None
    created_by_user_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DistillOut(BaseModel):
    """Response from :func:`distill_bucket`.

    Surface the ``DistillerRun`` shape plus a light ``article_ids``
    promotion so callers don't have to dig into ``output_refs`` to
    find what landed. Matches the public contract documented in
    ``backend/docs/knowledge-consolidation.md``.
    """

    run: DistillerRunOut
    decision: str
    article_ids: list[uuid.UUID]
    reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_to_out(row: DistillerRun) -> DistillerRunOut:
    return DistillerRunOut(
        id=row.id,
        workspace_id=row.workspace_id,
        bucket_id=row.bucket_id,
        source_kind=row.source_kind,
        status=row.status,
        decision=row.decision,
        input_ref=row.input_ref or {},
        output_refs=row.output_refs or {},
        error=row.error,
        created_by_user_id=row.created_by_user_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/buckets/{slug}/distill",
    response_model=DistillOut,
    status_code=status.HTTP_200_OK,
)
async def distill_bucket(
    workspace_id: uuid.UUID,
    slug: str,
    payload: DistillIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DistillOut:
    """Phase 6a stub ingest.

    Validates the bucket exists under the workspace, runs the
    Distiller (stub classifier + article writer), and returns the
    stored run row along with any ids the call produced.

    4xx codes:
      * ``400`` — ``source_kind`` not in
        :class:`backend.app.db.models.agent_memory.BucketSource`.
      * ``403`` — caller is not a workspace maintainer.
      * ``404`` — bucket slug not found under the workspace.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_MAINTAIN
    )
    bucket = await _load_bucket(session, workspace_id, slug)

    if payload.source_kind not in BucketSource.ALL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"source_kind must be one of {BucketSource.ALL}",
        )

    outcome = await run_distiller(
        session,
        workspace_id=workspace_id,
        bucket=bucket,
        actor_user_id=auth.user.id,
        inp=DistillerInput(
            body_md=payload.body_md,
            source_kind=payload.source_kind,
            title_hint=payload.title_hint,
            slug_hint=payload.slug_hint,
            provenance=payload.provenance or None,
            input_ref=payload.input_ref or None,
        ),
    )

    run = (
        await session.execute(
            select(DistillerRun).where(DistillerRun.id == outcome.run_id)
        )
    ).scalars().one()

    return DistillOut(
        run=_run_to_out(run),
        decision=outcome.decision,
        article_ids=outcome.article_ids,
        reason=outcome.reason,
    )


@router.get(
    "/buckets/{slug}/distill/runs",
    response_model=list[DistillerRunOut],
)
async def list_distiller_runs(
    workspace_id: uuid.UUID,
    slug: str,
    limit: int = 50,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[DistillerRunOut]:
    """Recent runs for the bucket, newest first.

    ``limit`` is clamped to [1, 200]; the console's history panel
    only needs the last ~20 rows in practice. An older-style cursor
    pagination can layer in later without touching this shape.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    bucket = await _load_bucket(session, workspace_id, slug)

    clamped = max(1, min(int(limit), 200))
    rows = (
        await session.execute(
            select(DistillerRun)
            .where(
                DistillerRun.workspace_id == workspace_id,
                DistillerRun.bucket_id == bucket.id,
            )
            .order_by(desc(DistillerRun.created_at))
            .limit(clamped)
        )
    ).scalars().all()
    return [_run_to_out(r) for r in rows]
