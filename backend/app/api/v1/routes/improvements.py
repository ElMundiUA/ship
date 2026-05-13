"""Improvements surface (C8).

Agent-proposed changes awaiting a yes / no / later human call. The
shape mirrors :mod:`.clarifications` on purpose: session-auth admin
routes under ``/workspaces/{ws}/improvements`` for the ``/improvements``
page to render + mutate, and a second router at
``/improvements/pipeline`` authenticated by the dispatched workflow's
``run_token`` so a pipeline can bulk-publish proposals.

Decision transitions are explicit in the PATCH body:

- ``accepted`` — user clicked yes. ``decision_reason`` is optional
  here but encouraged (e.g. "keep, matches architecture doc"); we
  also accept a ``next_action_url`` the agent will have filled in
  (pointing at the PR we opened on the tenant's behalf).
- ``declined`` — user clicked no. ``decision_reason`` is *required*
  because downstream agents learn from it.
- ``deferred`` — user clicked later. Pairs with a ``defer_until``
  timestamp in the future (pilot: just re-surface after 7 days).
- ``pending`` — user clicked the revert arrow (undoes a decision).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.runs import (
    RunTokenContext,
    get_run_token_context,
)
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_surface import Improvement
from backend.app.db.models.lanes import RoutineRun
from backend.app.db.models.tenancy import AuditLog, User
from backend.app.db.session import get_session
from backend.app.services.inbox.dual_write import (
    mirror_improvement_create,
    mirror_improvement_resolve,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/improvements",
    tags=["improvements"],
)


VALID_DECISIONS = frozenset({"pending", "accepted", "declined", "deferred"})


class ImprovementOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    repo_id: uuid.UUID | None
    routine_run_id: uuid.UUID | None
    kind: str
    title: str
    body: str
    impact: str | None
    effort: str | None
    context: dict
    decision: str
    decision_reason: str | None
    decided_by_email: str | None
    decided_at: datetime | None
    next_action_url: str | None
    created_at: datetime
    updated_at: datetime


class ImprovementCreateIn(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=20_000)
    impact: str | None = None
    effort: str | None = None
    repo_id: uuid.UUID | None = None
    routine_run_id: uuid.UUID | None = None
    context: dict = Field(default_factory=dict)


class ImprovementPatchIn(BaseModel):
    decision: Literal["pending", "accepted", "declined", "deferred"] | None = None
    decision_reason: str | None = None
    next_action_url: str | None = None


def _to_out(row: Improvement, decided_by_email: str | None) -> ImprovementOut:
    return ImprovementOut(
        id=row.id,
        workspace_id=row.workspace_id,
        repo_id=row.repo_id,
        routine_run_id=row.routine_run_id,
        kind=row.kind,
        title=row.title,
        body=row.body,
        impact=row.impact,
        effort=row.effort,
        context=row.context,
        decision=row.decision,
        decision_reason=row.decision_reason,
        decided_by_email=decided_by_email,
        decided_at=row.decided_at,
        next_action_url=row.next_action_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _enrich(
    session: AsyncSession, rows: list[Improvement]
) -> list[ImprovementOut]:
    user_ids = [r.decided_by_user_id for r in rows if r.decided_by_user_id]
    emails: dict[uuid.UUID, str] = {}
    if user_ids:
        users = (
            await session.execute(
                select(User).where(User.id.in_({*user_ids}))
            )
        ).scalars().all()
        emails = {u.id: u.email for u in users}
    return [
        _to_out(
            r, emails.get(r.decided_by_user_id) if r.decided_by_user_id else None
        )
        for r in rows
    ]


@router.get("", response_model=list[ImprovementOut])
async def list_improvements(
    workspace_id: uuid.UUID,
    decision_filter: str | None = Query(default=None, alias="decision"),
    repo_id: uuid.UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ImprovementOut]:
    """List improvements, optionally narrowed to a decision bucket.

    ``repo_id`` (query) narrows to a single activated repo — the
    repo-mode console (``/r/<owner>/<repo>/improvements``) passes it
    so the page doesn't have to fetch the entire workspace backlog
    just to filter client-side.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(Improvement)
        .where(Improvement.workspace_id == workspace_id)
        .order_by(Improvement.created_at.desc())
    )
    if decision_filter:
        if decision_filter not in VALID_DECISIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unknown decision '{decision_filter}'. Expected:"
                    f" {', '.join(sorted(VALID_DECISIONS))}."
                ),
            )
        stmt = stmt.where(Improvement.decision == decision_filter)
    if repo_id is not None:
        stmt = stmt.where(Improvement.repo_id == repo_id)
    rows = (await session.execute(stmt)).scalars().all()
    return await _enrich(session, list(rows))


@router.post(
    "", response_model=ImprovementOut, status_code=status.HTTP_201_CREATED
)
async def create_improvement(
    workspace_id: uuid.UUID,
    payload: ImprovementCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ImprovementOut:
    """Admin-authored improvement (for tests / manual seeding)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = Improvement(
        workspace_id=workspace_id,
        repo_id=payload.repo_id,
        routine_run_id=payload.routine_run_id,
        kind=payload.kind,
        title=payload.title,
        body=payload.body,
        impact=payload.impact,
        effort=payload.effort,
        context=payload.context,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    await mirror_improvement_create(session, improvement=row)
    return _to_out(row, None)


@router.patch("/{improvement_id}", response_model=ImprovementOut)
async def update_improvement(
    workspace_id: uuid.UUID,
    improvement_id: uuid.UUID,
    payload: ImprovementPatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ImprovementOut:
    """Record a decision (accept / decline / defer / reset).

    - ``accepted`` stamps the decider; ``next_action_url`` can be set
      here or separately (PRs open async).
    - ``declined`` requires ``decision_reason`` — agents learn from
      it next scan.
    - ``deferred`` is a no-op pointer for now (Day-4+ sweeper will
      re-surface after TTL).
    - ``pending`` resets the row to un-decided; used for the "undo"
      affordance in the UI.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = (
        await session.execute(
            select(Improvement).where(
                Improvement.id == improvement_id,
                Improvement.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Improvement not found.",
        )

    now = datetime.now(timezone.utc)
    new_decision = payload.decision

    if new_decision == "declined" and not (
        payload.decision_reason or row.decision_reason
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'declined' requires a reason (for the agent to learn from).",
        )

    if new_decision in {"accepted", "declined", "deferred"}:
        row.decision = new_decision
        row.decided_by_user_id = auth.user.id
        row.decided_at = now
        if payload.decision_reason is not None:
            row.decision_reason = payload.decision_reason
    elif new_decision == "pending":
        row.decision = "pending"
        row.decided_by_user_id = None
        row.decided_at = None
        row.decision_reason = None

    if payload.next_action_url is not None:
        row.next_action_url = payload.next_action_url

    await session.flush()
    await session.refresh(row)
    if new_decision in ("accepted", "declined", "deferred"):
        await mirror_improvement_resolve(
            session,
            improvement=row,
            actor_user_id=auth.user.id,
            actor_kind="user",
        )
    email: str | None = None
    if row.decided_by_user_id:
        user = (
            await session.execute(
                select(User).where(User.id == row.decided_by_user_id)
            )
        ).scalars().first()
        if user is not None:
            email = user.email
    return _to_out(row, email)


# ---------------------------------------------------------------------------
# Pipeline-authored ingress (run_token bearer)
# ---------------------------------------------------------------------------


pipeline_router = APIRouter(prefix="/improvements", tags=["improvements"])


class PipelineImprovementIn(BaseModel):
    """Payload a dispatched workflow posts with its ``run_token``.

    Bulk-mode: the workflow sends one improvement at a time. If the
    agent wants to publish a batch it loops; we don't accept arrays
    on the wire to keep the audit trail linear.
    """

    kind: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=20_000)
    impact: str | None = None
    effort: str | None = None
    context: dict = Field(default_factory=dict)


@pipeline_router.post(
    "/pipeline",
    response_model=ImprovementOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_pipeline(
    payload: PipelineImprovementIn,
    ctx: RunTokenContext = Depends(get_run_token_context),
    session: AsyncSession = Depends(get_session),
) -> ImprovementOut:
    routine_run = (
        await session.execute(
            select(RoutineRun).where(RoutineRun.id == ctx.run_id)
        )
    ).scalars().first()
    repo_id = routine_run.payload.get("repo_id") if routine_run else None
    if repo_id is not None:
        try:
            repo_id = uuid.UUID(str(repo_id))
        except (TypeError, ValueError):
            repo_id = None

    row = Improvement(
        workspace_id=ctx.workspace_id,
        repo_id=repo_id,
        routine_run_id=ctx.run_id,
        kind=payload.kind,
        title=payload.title,
        body=payload.body,
        impact=payload.impact,
        effort=payload.effort,
        context=payload.context,
    )
    session.add(row)
    session.add(
        AuditLog(
            workspace_id=ctx.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="improvement.create.pipeline",
            target_kind="improvement",
            target_id=None,
            payload={"run_id": str(ctx.run_id), "kind": payload.kind},
        )
    )
    await session.flush()
    await session.refresh(row)
    await mirror_improvement_create(session, improvement=row)
    return _to_out(row, None)


__all__ = ["router", "pipeline_router"]
