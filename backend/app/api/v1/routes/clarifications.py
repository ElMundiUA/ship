"""Clarifications inbox (C9).

Surface:

- ``GET  /v1/workspaces/{ws}/clarifications?status=open``
- ``POST /v1/workspaces/{ws}/clarifications`` — create (admin-only)
- ``PATCH /v1/workspaces/{ws}/clarifications/{id}`` — answer / skip

Two call patterns share the create endpoint:

- **Human-authored** (PAT / session) — test fixtures and the "add a
  manual question" affordance in the console.
- **Pipeline-authored** — a dispatched GitHub Action posts here with
  its ``run_token`` bearer; the token's ``run_id`` is captured in
  ``pipeline_run_id`` automatically so the UI can deep-link back to
  the run that raised the question.

Both paths share the same RBAC for reads (any workspace member) and
different RBAC for writes (see inline docstrings).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.pipelines import (
    RunTokenContext,
    get_run_token_context,
)
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_surface import Clarification
from backend.app.db.models.pipelines import PipelineRun
from backend.app.db.models.tenancy import AuditLog, User
from backend.app.db.session import get_session
from backend.app.services import clarifications_sync


router = APIRouter(
    prefix="/workspaces/{workspace_id}/clarifications",
    tags=["clarifications"],
)


VALID_STATUSES = frozenset({"open", "answered", "skipped", "stale"})


class ClarificationOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    repo_id: uuid.UUID | None
    pipeline_run_id: uuid.UUID | None
    ticket_ref: str | None
    question: str
    answer: str | None
    status: str
    context: dict
    # D13 projection fields — surfaced so the console can render a
    # "from tracker" badge + deep link without re-resolving the
    # tracker on every page view.
    source: str
    tracker_provider: str | None
    tracker_issue_key: str | None
    tracker_issue_url: str | None
    answered_by_email: str | None
    answered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ClarificationCreateIn(BaseModel):
    """Payload accepted by both session-auth admins and run-token pipelines.

    When the caller is a pipeline, ``pipeline_run_id`` is inferred
    from the run token and any client-supplied value is ignored.
    """

    question: str = Field(min_length=1, max_length=10_000)
    ticket_ref: str | None = None
    repo_id: uuid.UUID | None = None
    pipeline_run_id: uuid.UUID | None = None
    context: dict = Field(default_factory=dict)


class ClarificationPatchIn(BaseModel):
    """Partial update — answer, skip, or re-open."""

    answer: str | None = None
    status: Literal["open", "answered", "skipped"] | None = None


def _to_out(row: Clarification, answered_by_email: str | None) -> ClarificationOut:
    return ClarificationOut(
        id=row.id,
        workspace_id=row.workspace_id,
        repo_id=row.repo_id,
        pipeline_run_id=row.pipeline_run_id,
        ticket_ref=row.ticket_ref,
        question=row.question,
        answer=row.answer,
        status=row.status,
        context=row.context,
        source=row.source,
        tracker_provider=row.tracker_provider,
        tracker_issue_key=row.tracker_issue_key,
        tracker_issue_url=row.tracker_issue_url,
        answered_by_email=answered_by_email,
        answered_at=row.answered_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _enrich(
    session: AsyncSession, rows: list[Clarification]
) -> list[ClarificationOut]:
    user_ids = [r.answered_by_user_id for r in rows if r.answered_by_user_id]
    emails: dict[uuid.UUID, str] = {}
    if user_ids:
        users = (
            await session.execute(
                select(User).where(User.id.in_({*user_ids}))
            )
        ).scalars().all()
        emails = {u.id: u.email for u in users}
    return [
        _to_out(r, emails.get(r.answered_by_user_id) if r.answered_by_user_id else None)
        for r in rows
    ]


@router.get("", response_model=list[ClarificationOut])
async def list_clarifications(
    workspace_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    repo_id: uuid.UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ClarificationOut]:
    """List clarifications for the workspace.

    ``status`` (query) narrows to a single bucket (``open`` /
    ``answered`` / ``skipped`` / ``stale``) — default returns every
    row so the ``/clarifications`` page can render tab counts
    without extra calls. Sorted by ``created_at`` desc so the newest
    questions sit on top.

    ``repo_id`` (query) narrows to a single activated repo — the
    repo-mode console (``/r/<owner>/<repo>/clarifications``) passes
    it so the page doesn't have to fetch the entire workspace inbox
    just to filter client-side. Omitting it preserves legacy
    workspace-wide behaviour.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(Clarification)
        .where(Clarification.workspace_id == workspace_id)
        .order_by(Clarification.created_at.desc())
    )
    if status_filter:
        if status_filter not in VALID_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unknown status '{status_filter}'. Expected one of:"
                    f" {', '.join(sorted(VALID_STATUSES))}."
                ),
            )
        stmt = stmt.where(Clarification.status == status_filter)
    if repo_id is not None:
        stmt = stmt.where(Clarification.repo_id == repo_id)
    rows = (await session.execute(stmt)).scalars().all()
    return await _enrich(session, list(rows))


@router.post(
    "", response_model=ClarificationOut, status_code=status.HTTP_201_CREATED
)
async def create_clarification(
    workspace_id: uuid.UUID,
    payload: ClarificationCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ClarificationOut:
    """Admin-authored clarification.

    The pipeline path uses ``POST /v1/clarifications/pipeline`` with
    the run-token bearer (see :func:`create_from_pipeline`). Keeping
    the two split keeps the admin route free of run-token branching
    and makes the RBAC story easy to audit.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = Clarification(
        workspace_id=workspace_id,
        repo_id=payload.repo_id,
        pipeline_run_id=payload.pipeline_run_id,
        ticket_ref=payload.ticket_ref,
        question=payload.question,
        context=payload.context,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _to_out(row, None)


@router.patch("/{clarification_id}", response_model=ClarificationOut)
async def update_clarification(
    workspace_id: uuid.UUID,
    clarification_id: uuid.UUID,
    payload: ClarificationPatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ClarificationOut:
    """Answer, skip, or re-open a clarification.

    The matrix:

    - ``status='answered'`` + ``answer`` present → ``answered``,
      records answerer + timestamp.
    - ``status='skipped'`` → ``skipped``, ``answer`` stays ``NULL``
      (you can't "skip with an answer").
    - ``status='open'`` → re-open; clears ``answer``, answerer,
      ``answered_at``. Useful when the agent's follow-up wasn't
      actually informative.
    - Body without ``status`` but with ``answer`` is shorthand for
      ``status='answered'``.

    When ``source='tracker'`` (D13 projection), we also write the
    answer back to the customer's tracker and strip the
    ``ship:needs-clarification`` label. The tracker is the source of
    truth; Ship's row is a cached projection. Write-back is
    synchronous: if the tracker call fails we surface a 502 and roll
    back the DB mutation (FastAPI's unit-of-work auto-commits only on
    a clean return), so the admin can retry without a split-brain
    between Ship and the tracker.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = (
        await session.execute(
            select(Clarification).where(
                Clarification.id == clarification_id,
                Clarification.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clarification not found.",
        )

    new_status = payload.status
    if new_status is None and payload.answer is not None:
        new_status = "answered"
    if new_status == "answered" and not (payload.answer or row.answer):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'answered' requires a non-empty answer body.",
        )

    now = datetime.now(timezone.utc)
    answer_for_writeback: str | None = None
    if new_status == "answered":
        row.status = "answered"
        if payload.answer is not None:
            row.answer = payload.answer
        row.answered_by_user_id = auth.user.id
        row.answered_at = now
        answer_for_writeback = row.answer
    elif new_status == "skipped":
        row.status = "skipped"
        row.answered_by_user_id = auth.user.id
        row.answered_at = now
    elif new_status == "open":
        row.status = "open"
        row.answer = None
        row.answered_by_user_id = None
        row.answered_at = None
    # ``stale`` is only set by server-side sweepers — rejected on the
    # API for now so a malicious client can't mass-mark inbox stale.

    # Write-back BEFORE flushing the DB: if the tracker is down or
    # misconfigured, we want the admin to see a 502 and not a silent
    # "Ship says answered, tracker still labelled" divergence. The
    # row is still dirty in the session at this point — the 502 path
    # below raises, which short-circuits the implicit commit in
    # ``backend.app.db.session.get_session``.
    if row.source == "tracker" and new_status == "answered" and answer_for_writeback:
        try:
            await clarifications_sync.writeback_answer(
                session=session,
                settings=settings,
                row=row,
                answer_text=answer_for_writeback,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Tracker write-back failed: {exc}. Ship row unchanged;"
                    " retry once the tracker is reachable."
                ),
            ) from exc
        row.tracker_synced_at = now

    await session.flush()
    # Refresh server-side computed fields (``updated_at``) before
    # serialising, otherwise Pydantic's model construction triggers
    # a lazy-load outside the greenlet context.
    await session.refresh(row)
    email: str | None = None
    if row.answered_by_user_id:
        user = (
            await session.execute(
                select(User).where(User.id == row.answered_by_user_id)
            )
        ).scalars().first()
        if user is not None:
            email = user.email
    return _to_out(row, email)


# ---------------------------------------------------------------------------
# Tracker projection — admin-triggered + cron-backed sync (D13)
# ---------------------------------------------------------------------------


class ClarificationSyncReportOut(BaseModel):
    """Shape returned by ``POST .../clarifications/sync``."""

    workspace_id: uuid.UUID
    ingested: int
    updated: int
    stale_marked: int
    errors: list[str]


@router.post(
    "/sync",
    response_model=ClarificationSyncReportOut,
    status_code=status.HTTP_200_OK,
)
async def sync_clarifications(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ClarificationSyncReportOut:
    """Run the tracker→Ship projection for this workspace on demand.

    The cron already runs this every few minutes; the manual hook is
    useful (a) for onboarding ("I just added the Linear integration,
    show me my questions now"), and (b) for debugging — the route
    returns a breakdown of what was ingested / updated / marked stale.

    Admin-only: a full tenant listing of tracker issues would leak
    labels outside the ``owner`` / ``admin`` trust boundary otherwise.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    report = await clarifications_sync.sync_workspace(
        session, settings=settings, workspace_id=workspace_id
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action="clarification.sync",
            target_kind="workspace",
            target_id=str(workspace_id),
            payload=report.as_dict(),
        )
    )
    await session.flush()
    return ClarificationSyncReportOut(**report.as_dict())


# ---------------------------------------------------------------------------
# Pipeline-authored ingress (run_token bearer)
# ---------------------------------------------------------------------------


pipeline_router = APIRouter(
    prefix="/clarifications", tags=["clarifications"]
)


class PipelineClarificationIn(BaseModel):
    """Payload accepted by dispatched GitHub Actions workflows.

    The run_token encodes the ``pipeline_run_id`` + ``workspace_id``
    so the workflow never has to guess either; we drop any client-
    supplied values for those two fields and always use the token's.
    """

    question: str = Field(min_length=1, max_length=10_000)
    ticket_ref: str | None = None
    context: dict = Field(default_factory=dict)


@pipeline_router.post(
    "/pipeline",
    response_model=ClarificationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_pipeline(
    payload: PipelineClarificationIn,
    ctx: RunTokenContext = Depends(get_run_token_context),
    session: AsyncSession = Depends(get_session),
) -> ClarificationOut:
    """Accept clarification rows from a dispatched workflow.

    The Action's YAML makes a ``POST`` with its ``ship_run_token``
    bearer. We already validated the token in
    :func:`get_run_token_context` and resolved the matching
    :class:`PipelineRun`, so at this point we trust ``ctx.run_id``
    and ``ctx.workspace_id`` unconditionally.
    """
    pipeline_run = (
        await session.execute(
            select(PipelineRun).where(PipelineRun.id == ctx.run_id)
        )
    ).scalars().first()
    repo_id = pipeline_run.payload.get("repo_id") if pipeline_run else None
    if repo_id is not None:
        try:
            repo_id = uuid.UUID(str(repo_id))
        except (TypeError, ValueError):
            repo_id = None

    row = Clarification(
        workspace_id=ctx.workspace_id,
        repo_id=repo_id,
        pipeline_run_id=ctx.run_id,
        ticket_ref=payload.ticket_ref,
        question=payload.question,
        context=payload.context,
        # D13: distinguish runtime-authored rows so the PATCH
        # writeback path (which targets a tracker) stays a no-op
        # for pipeline-authored ones. Legacy workflows that haven't
        # moved to the tracker-comment model still land here.
        source="pipeline",
    )
    session.add(row)
    session.add(
        AuditLog(
            workspace_id=ctx.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="clarification.create.pipeline",
            target_kind="clarification",
            target_id=None,
            payload={
                "run_id": str(ctx.run_id),
                "ticket_ref": payload.ticket_ref,
            },
        )
    )
    await session.flush()
    await session.refresh(row)
    return _to_out(row, None)


__all__ = [
    "router",
    "pipeline_router",
]
