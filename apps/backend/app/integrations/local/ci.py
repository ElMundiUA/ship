"""Memory CI adapter (E19 step 4b).

Implements :class:`CIGateway` over ``memory_ci_runs`` (migration 0073)
so the laptop-offline orchestrator can spawn + observe workflow runs
without a real GHA / GitLab CI / etc. The simulator uses a tiny
self-driving state machine: each run carries a ``transition_at``
deadline; the adapter walks any "ripe" runs forward on every read,
so callers see a deterministic ``queued → in_progress → completed``
cycle without a separate scheduler.

Cycle (defaults, override per-run via :meth:`dispatch`):

  queued ──+5s──► in_progress ──+5s──► completed (success)

The deterministic deadline beats spawning a background tick — tests
can advance time by reading once per phase, and a `make dev-up`
session that idles for a minute still sees runs complete on the
next dashboard refresh. For long-running scenarios the seed +
local-tracker UI can set ``transition_at`` further out.

Like the other memory adapters, this is *behavioural* parity with
GitHub Actions, not wire-shape parity — we return the dict fields
the existing consumers key on (``id``, ``name``, ``status``,
``conclusion``, ``url``, ``created_at``) but skip the GH-only ones.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.memory_adapters import (
    MemoryCiRun,
    MemoryGitRepo,
)
from backend.app.integrations.gateway.code_host import RepoRef


# Default phase length — short enough that a developer doesn't sit
# staring at the dashboard, long enough that a click-around does see
# the queued → in_progress transition rather than a snap-to-complete.
_DEFAULT_PHASE_SECONDS = 5


class MemoryCi:
    """In-Postgres CIGateway. One workspace, many repos, many runs."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        console_origin: str = "http://localhost:3001",
    ) -> None:
        self._session = session
        self._workspace_id = workspace_id
        self._origin = console_origin.rstrip("/")

    # ------------------------------------------------------------------
    # CIGateway protocol
    # ------------------------------------------------------------------

    async def list_runs(
        self, repo: RepoRef, *, limit: int = 25
    ) -> list[dict[str, Any]]:
        repo_row = await self._fetch_repo(repo)
        if repo_row is None:
            return []
        # Walk any ripe runs forward before serialising so the caller
        # sees current state.
        await self._tick(repo_row.id)
        rows = (
            (
                await self._session.execute(
                    select(MemoryCiRun)
                    .where(MemoryCiRun.repo_id == repo_row.id)
                    .order_by(MemoryCiRun.created_at.desc())
                    .limit(max(1, min(limit, 100)))
                )
            )
            .scalars()
            .all()
        )
        return [self._row_to_dict(repo_row, r) for r in rows]

    async def rerun(self, repo: RepoRef, *, run_id: str | int) -> None:
        repo_row = await self._fetch_repo(repo)
        if repo_row is None:
            return
        row = await self._fetch_run(repo_row.id, run_id)
        if row is None:
            return
        row.status = "queued"
        row.conclusion = None
        row.logs = ""
        row.transition_at = _utcnow() + timedelta(seconds=_DEFAULT_PHASE_SECONDS)
        row.updated_at = _utcnow()

    async def get_logs(
        self, repo: RepoRef, *, run_id: str | int
    ) -> str:
        repo_row = await self._fetch_repo(repo)
        if repo_row is None:
            return ""
        row = await self._fetch_run(repo_row.id, run_id)
        return row.logs if row else ""

    # ------------------------------------------------------------------
    # Helpers — used by seeders + the local-tracker UI to spawn runs.
    # Not on the gateway protocol; real CIs are triggered by the code
    # host (push / PR open), the memory adapter exposes the trigger
    # directly for fixture wiring.
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        repo: MemoryGitRepo,
        *,
        workflow_name: str,
        branch: str | None = None,
        commit_sha: str | None = None,
        phase_seconds: int = _DEFAULT_PHASE_SECONDS,
        outcome: str = "success",
        logs: str | None = None,
    ) -> MemoryCiRun:
        """Spawn a new run that walks queued → in_progress → completed.

        ``outcome`` is one of ``success`` / ``failure`` / ``cancelled``;
        applied at the final transition. ``logs`` overrides the default
        scripted log body.
        """
        now = _utcnow()
        row = MemoryCiRun(
            repo_id=repo.id,
            workflow_name=workflow_name,
            status="queued",
            branch=branch or repo.default_branch,
            commit_sha=commit_sha,
            logs="",
            transition_at=now + timedelta(seconds=phase_seconds),
        )
        # Stash the desired outcome + log payload on the row so
        # ``_tick`` can apply them when the deadline lapses.
        row.logs = _pending_logs(logs or _default_logs(workflow_name))
        # Encode the desired outcome in a sentinel prefix the tick
        # parser strips before publishing. Cheap stand-in for a real
        # "queued payload" column.
        row.logs = _OUTCOME_PREFIX + outcome + "\n" + row.logs
        self._session.add(row)
        await self._session.flush()
        return row

    # ------------------------------------------------------------------
    # State-machine tick
    # ------------------------------------------------------------------

    async def _tick(self, repo_id: uuid.UUID) -> None:
        """Walk any runs whose transition_at has lapsed."""
        now = _utcnow()
        rows = (
            (
                await self._session.execute(
                    select(MemoryCiRun).where(
                        MemoryCiRun.repo_id == repo_id,
                        MemoryCiRun.status != "completed",
                        MemoryCiRun.transition_at.is_not(None),
                        MemoryCiRun.transition_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.status == "queued":
                row.status = "in_progress"
                row.transition_at = now + timedelta(seconds=_DEFAULT_PHASE_SECONDS)
            elif row.status == "in_progress":
                outcome, body = _split_outcome(row.logs)
                row.status = "completed"
                row.conclusion = outcome
                row.logs = body
                row.transition_at = None
            row.updated_at = now

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_repo(self, repo: RepoRef) -> MemoryGitRepo | None:
        return (
            await self._session.execute(
                select(MemoryGitRepo).where(
                    MemoryGitRepo.workspace_id == self._workspace_id,
                    MemoryGitRepo.owner == repo.owner,
                    MemoryGitRepo.name == repo.repo,
                )
            )
        ).scalar_one_or_none()

    async def _fetch_run(
        self, repo_id: uuid.UUID, run_id: str | int
    ) -> MemoryCiRun | None:
        run_uuid = _safe_uuid(run_id)
        if run_uuid is None:
            return None
        return (
            await self._session.execute(
                select(MemoryCiRun).where(
                    MemoryCiRun.repo_id == repo_id,
                    MemoryCiRun.id == run_uuid,
                )
            )
        ).scalar_one_or_none()

    def _row_to_dict(
        self,
        repo: MemoryGitRepo,
        row: MemoryCiRun,
    ) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "name": row.workflow_name,
            "status": row.status,
            "conclusion": row.conclusion,
            "url": f"{self._origin}/local-tracker/repos/{repo.owner}/{repo.name}/runs/{row.id}",
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "branch": row.branch,
            "commit_sha": row.commit_sha,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Sentinel for the pending outcome stored on the row while queued/in
# progress; ``_split_outcome`` strips it when the run completes.
_OUTCOME_PREFIX = "__pending_outcome:"


def _pending_logs(body: str) -> str:
    return body


def _default_logs(workflow_name: str) -> str:
    return (
        f"[memory-ci] starting {workflow_name}\n"
        "[memory-ci] running step 1/3 — install\n"
        "[memory-ci] running step 2/3 — test\n"
        "[memory-ci] running step 3/3 — build\n"
        "[memory-ci] done\n"
    )


def _split_outcome(logs: str) -> tuple[str, str]:
    """Return ``(conclusion, log_body)`` for the row after a tick.

    The dispatch path stuffs ``__pending_outcome:<outcome>\\n`` at the
    front of ``logs``; this helper strips it back out so the user-
    facing log body is clean and the conclusion is preserved.
    """
    if not logs.startswith(_OUTCOME_PREFIX):
        return "success", logs
    head, _, rest = logs.partition("\n")
    outcome = head[len(_OUTCOME_PREFIX) :].strip() or "success"
    return outcome, rest


def _safe_uuid(raw: str | int | uuid.UUID) -> uuid.UUID | None:
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
