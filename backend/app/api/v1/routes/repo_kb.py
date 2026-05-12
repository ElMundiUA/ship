"""On-demand ``.ship/knowledge`` indexing surface (ELS-62).

Three endpoints under ``/v1/workspaces/{ws}/repos/{repo_id}/kb``:

- ``POST /reindex`` — enqueue a new :class:`KbIndexingRun` and schedule
  the indexer to run as a FastAPI background task. Returns the run id
  immediately so the agent (or a human operator) can poll the run with
  ``GET /runs/{id}``. The Navigator's ``trigger_repo_kb_indexing`` tool
  is a thin wrapper over this endpoint.
- ``GET /runs/{run_id}`` — single-run state read-out: ``status``,
  ``trigger``, timestamps, the :class:`IndexReport` counters persisted
  into ``stats``, plus the workspace-level ``kb_chunk_count`` /
  ``kb_last_indexed_at`` aggregates so one probe answers "is it
  running, and is the result fresh?".
- ``GET /runs?limit=N`` — recent runs for the repo, newest first.
  ``limit`` clamps to ``[1, 50]``.

Authorization is plain workspace membership: per the BA decision in
the ELS-62 ticket, manual reindex is read-mostly (it costs OpenAI
tokens but doesn't mutate tenant code), so it sits at the same band as
``search_repo_kb`` rather than the admin band of e.g. seed-bundle
mutations. Audit log records the trigger as ``kb_indexing.trigger``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import KbChunk, KbIndexingRun
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos/{repo_id}/kb",
    tags=["repo-kb"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class KbReindexIn(BaseModel):
    """Optional trigger discriminator. Accepts ``agent`` / ``manual``.

    ``push`` is reserved for the webhook handler — the HTTP surface
    refuses it so the audit trail can distinguish "an agent (or human
    with a PAT) hit the trigger endpoint" from "a GitHub push fired".
    """

    trigger: str | None = Field(default=None, pattern=r"^(agent|manual)$")


class KbReindexOut(BaseModel):
    run_id: uuid.UUID
    repo_id: uuid.UUID
    status: str
    trigger: str


class KbRunStats(BaseModel):
    files_discovered: int = 0
    files_indexed: int = 0
    files_skipped_unchanged: int = 0
    files_skipped_too_big: int = 0
    files_skipped_binary: int = 0
    chunks_deleted: int = 0
    chunks_written: int = 0


class KbRunOut(BaseModel):
    """Single-run detail with workspace-level aggregates folded in.

    ``kb_chunk_count`` / ``kb_last_indexed_at`` reflect the *current*
    state of the repo's ``kb_chunks`` table — i.e. the last successful
    run, even when this row is still ``running``. That's deliberate:
    AC #4 wants the probe to answer "is the KB current?" in a single
    call without requiring the operator to chain another lookup.
    """

    run_id: uuid.UUID
    repo_id: uuid.UUID
    status: str
    trigger: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    stats: KbRunStats
    error: str | None
    kb_chunk_count: int
    kb_last_indexed_at: datetime | None


class KbRunListItem(BaseModel):
    """Row shape for ``GET /runs`` — same as the detail block minus the
    workspace aggregates (those are run-independent and would re-fetch
    the same number for every row)."""

    run_id: uuid.UUID
    repo_id: uuid.UUID
    status: str
    trigger: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    stats: KbRunStats
    error: str | None


class KbRunListOut(BaseModel):
    runs: list[KbRunListItem]


_LIST_DEFAULT = 10
_LIST_MAX = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_repo(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> WorkspaceRepo:
    """Tenancy fence: a repo from another workspace must look indistinguishable
    from "no such repo" — never leak existence (AC #3).
    """
    row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="repo_not_found_in_workspace",
        )
    return row


def _stats_from_row(run: KbIndexingRun) -> KbRunStats:
    raw = run.stats or {}
    return KbRunStats(
        files_discovered=int(raw.get("files_discovered", 0)),
        files_indexed=int(raw.get("files_indexed", 0)),
        files_skipped_unchanged=int(raw.get("files_skipped_unchanged", 0)),
        files_skipped_too_big=int(raw.get("files_skipped_too_big", 0)),
        files_skipped_binary=int(raw.get("files_skipped_binary", 0)),
        chunks_deleted=int(raw.get("chunks_deleted", 0)),
        chunks_written=int(raw.get("chunks_written", 0)),
    )


async def _workspace_kb_aggregates(
    session: AsyncSession, *, repo_id: uuid.UUID
) -> tuple[int, datetime | None]:
    row = (
        await session.execute(
            select(
                func.count(KbChunk.id),
                func.max(KbChunk.indexed_at),
            ).where(KbChunk.repo_id == repo_id)
        )
    ).one()
    return int(row[0] or 0), row[1]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/reindex", response_model=KbReindexOut)
async def trigger_reindex(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: KbReindexIn | None = None,
    *,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KbReindexOut:
    """Enqueue a fresh indexer run for one repo's ``.ship/knowledge``.

    Returns 200 with ``run_id`` even on logical issues like
    ``OPENAI_API_KEY missing`` (AC #8) — those land on the run row as
    ``status='error'`` so a probe call can read the message. 404 is
    reserved for "this repo isn't in your workspace" (AC #3).
    """
    from backend.app.services.agent.kb_indexer import (
        create_kb_indexing_run,
        run_kb_indexing_background,
    )

    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    repo = await _require_repo(
        session, workspace_id=workspace_id, repo_id=repo_id
    )

    trigger = (payload.trigger if payload is not None else None) or "agent"
    run = await create_kb_indexing_run(
        session,
        workspace_id=workspace_id,
        repo_id=repo.id,
        trigger=trigger,
        created_by_user_id=auth.user.id,
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="kb_indexing.trigger",
            target_kind="workspace_repo",
            target_id=str(repo.id),
            payload={
                "repo_id": str(repo.id),
                "run_id": str(run.id),
                "trigger": trigger,
            },
        )
    )
    # Persist the pending row + audit entry now so the background task
    # observes them on its own session, and the response can carry a
    # ``run_id`` callers can probe immediately.
    await session.commit()

    background_tasks.add_task(run_kb_indexing_background, run.id)

    return KbReindexOut(
        run_id=run.id,
        repo_id=repo.id,
        status=run.status,
        trigger=run.trigger,
    )


@router.get("/runs", response_model=KbRunListOut)
async def list_runs(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    limit: int = Query(default=_LIST_DEFAULT, ge=1, le=_LIST_MAX),
    *,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KbRunListOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _require_repo(
        session, workspace_id=workspace_id, repo_id=repo_id
    )
    rows = (
        await session.execute(
            select(KbIndexingRun)
            .where(
                KbIndexingRun.workspace_id == workspace_id,
                KbIndexingRun.repo_id == repo_id,
            )
            .order_by(desc(KbIndexingRun.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return KbRunListOut(
        runs=[
            KbRunListItem(
                run_id=r.id,
                repo_id=r.repo_id,
                status=r.status,
                trigger=r.trigger,
                started_at=r.started_at,
                finished_at=r.finished_at,
                created_at=r.created_at,
                stats=_stats_from_row(r),
                error=r.error,
            )
            for r in rows
        ]
    )


@router.get("/runs/{run_id}", response_model=KbRunOut)
async def get_run(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KbRunOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _require_repo(
        session, workspace_id=workspace_id, repo_id=repo_id
    )
    # Scope the WHERE on ``(workspace_id, repo_id, run_id)`` so a run id
    # leaked from another workspace's audit log can't be probed (AC #6).
    run = (
        await session.execute(
            select(KbIndexingRun).where(
                KbIndexingRun.id == run_id,
                KbIndexingRun.workspace_id == workspace_id,
                KbIndexingRun.repo_id == repo_id,
            )
        )
    ).scalars().first()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run_not_found",
        )
    chunk_count, last_indexed_at = await _workspace_kb_aggregates(
        session, repo_id=repo_id
    )
    return KbRunOut(
        run_id=run.id,
        repo_id=run.repo_id,
        status=run.status,
        trigger=run.trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        stats=_stats_from_row(run),
        error=run.error,
        kb_chunk_count=chunk_count,
        kb_last_indexed_at=last_indexed_at,
    )


async def latest_run_for_repo(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> KbIndexingRun | None:
    """Single-row helper used by the agent's ``probe_repo_kb_indexing``
    tool. Returns the most recent run for the repo regardless of status
    (the probe shape uses ``status`` to distinguish pending / running /
    done / error)."""
    return (
        await session.execute(
            select(KbIndexingRun)
            .where(
                KbIndexingRun.workspace_id == workspace_id,
                KbIndexingRun.repo_id == repo_id,
            )
            .order_by(desc(KbIndexingRun.created_at))
            .limit(1)
        )
    ).scalars().first()


def stats_dict_from_row(run: KbIndexingRun) -> dict[str, int]:
    """Re-export the projection used by the agent tool layer."""
    s = _stats_from_row(run)
    return {
        "files_discovered": s.files_discovered,
        "files_indexed": s.files_indexed,
        "files_skipped_unchanged": s.files_skipped_unchanged,
        "files_skipped_too_big": s.files_skipped_too_big,
        "files_skipped_binary": s.files_skipped_binary,
        "chunks_deleted": s.chunks_deleted,
        "chunks_written": s.chunks_written,
    }


async def workspace_kb_aggregates_for(
    session: AsyncSession, *, repo_id: uuid.UUID
) -> tuple[int, datetime | None]:
    """Public alias so the agent-tool layer can share the SELECT shape."""
    return await _workspace_kb_aggregates(session, repo_id=repo_id)


__all__ = [
    "KbReindexIn",
    "KbReindexOut",
    "KbRunOut",
    "KbRunListOut",
    "KbRunListItem",
    "KbRunStats",
    "latest_run_for_repo",
    "router",
    "stats_dict_from_row",
    "workspace_kb_aggregates_for",
]
