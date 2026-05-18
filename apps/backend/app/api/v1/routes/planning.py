"""Mass-planning intake endpoints (ELS-170 / M3).

CRUD over :class:`PlanningProposal` rows — the drafts Navigator emits
after running the requirements extractor (M1) on a PDF. Operator
edits the proposal in the Console preview pane; M2's
``/planning/mass-import`` endpoint reads the draft and writes
Linear.

Routes
======
- ``POST /v1/workspaces/{ws}/planning/proposals`` — create draft
- ``GET  /v1/workspaces/{ws}/planning/proposals/{id}`` — read
- ``PATCH /v1/workspaces/{ws}/planning/proposals/{id}`` — edit
- ``DELETE /v1/workspaces/{ws}/planning/proposals/{id}`` — discard

All routes are workspace-scoped + membership-gated.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.planning_proposals import PlanningProposal
from backend.app.db.session import get_session
from backend.app.services.planning.requirements_extraction import (
    MassPlanProposal,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/planning", tags=["planning"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanningProposalIn(BaseModel):
    """Create payload. ``source_kind`` defaults to ``pdf`` (Navigator
    intake); future surfaces (manual UI seed, Confluence import) set
    their own."""

    source_kind: str = Field(default="pdf", max_length=32)
    source_ref: str | None = Field(default=None, max_length=2_000)
    thread_id: uuid.UUID | None = None
    payload: dict[str, Any]


class PlanningProposalPatch(BaseModel):
    """Operator edits. Either supply a fully-replacement ``payload``
    (when the Console serialises the whole tree on save), or
    individual field replacements — ``payload`` wins when present.
    """

    payload: dict[str, Any] | None = None


class PlanningProposalOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    thread_id: uuid.UUID | None
    source_kind: str
    source_ref: str | None
    payload: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    committed_at: datetime | None
    committed_ticket_refs: list[str] | None


def _to_out(row: PlanningProposal) -> PlanningProposalOut:
    return PlanningProposalOut(
        id=row.id,
        workspace_id=row.workspace_id,
        thread_id=row.thread_id,
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        payload=row.payload,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        committed_at=row.committed_at,
        committed_ticket_refs=row.committed_ticket_refs,
    )


def _validate_payload_shape(payload: dict[str, Any]) -> None:
    """Run the MassPlanProposal schema validators (cycle, unknown deps,
    key shape, size). Raises 422 on first violation."""
    try:
        MassPlanProposal.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_proposal_payload",
                "errors": exc.errors()[:10],
            },
        ) from exc


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


@router.post(
    "/proposals",
    response_model=PlanningProposalOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_proposal(
    workspace_id: uuid.UUID,
    body: PlanningProposalIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PlanningProposalOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    _validate_payload_shape(body.payload)

    row = PlanningProposal(
        workspace_id=workspace_id,
        thread_id=body.thread_id,
        source_kind=body.source_kind,
        source_ref=body.source_ref,
        payload=body.payload,
        created_by=auth.user.id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _to_out(row)


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------


async def _load_or_404(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
) -> PlanningProposal:
    row = (
        await session.execute(
            select(PlanningProposal).where(
                PlanningProposal.id == proposal_id,
                PlanningProposal.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found"},
        )
    return row


@router.get("/proposals/{proposal_id}", response_model=PlanningProposalOut)
async def get_proposal(
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PlanningProposalOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    return _to_out(await _load_or_404(session, workspace_id, proposal_id))


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


@router.patch(
    "/proposals/{proposal_id}", response_model=PlanningProposalOut
)
async def patch_proposal(
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: PlanningProposalPatch,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PlanningProposalOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    row = await _load_or_404(session, workspace_id, proposal_id)
    if row.committed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "proposal_already_committed",
                "committed_at": row.committed_at.isoformat(),
            },
        )
    if body.payload is None:
        # Nothing to change — return current row. Operator may have hit
        # save with no edits; treat as idempotent.
        return _to_out(row)
    _validate_payload_shape(body.payload)
    row.payload = body.payload
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(row)
    return _to_out(row)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


@router.delete(
    "/proposals/{proposal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_proposal(
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    row = await _load_or_404(session, workspace_id, proposal_id)
    if row.committed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "proposal_already_committed",
                "committed_at": row.committed_at.isoformat(),
            },
        )
    await session.delete(row)
    await session.flush()


__all__ = ["router"]
