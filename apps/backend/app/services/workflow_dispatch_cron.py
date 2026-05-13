"""Ship-side scheduler for the customer trigger workflow.

The customer's ``ship-trigger-schedule.yml`` carries a
``schedule: */30 * * * *`` block, but GitHub heavily throttles
scheduled events under load — production observed 4-8 hour gaps
between ticks on busy days. The result is operators see "cron runs
every 30 min" in the YAML but real cadence is "between 30 min and
8 hours, whatever GitHub feels like". A single ticket needed 6+
hours to advance one SDLC stage; a 5-stage pipeline took 30+ hours
end-to-end.

This module replaces (or, in transition, supplements) the GHA
schedule with a backend cron that calls ``workflow_dispatch`` on
each active customer repo at a deterministic cadence. Backend
cadence is set by :data:`Settings.workflow_dispatch_cron_expr`
(default ``0 * * * *`` — every hour at :00). Each dispatch fires
the workflow with NO inputs, so the workflow's shell falls into
the normal cron path (``shipctl trigger`` → drain due routines)
exactly as a schedule-event would have.

Why hourly + multi-action-per-tick is the right shape:

* The runner's wall-clock budget is 30 minutes per job. The
  customer template (since v0.32) drains up to 5 due routines per
  tick, ~4 min each = 20 min. Hourly dispatch comfortably fits.
* GHA scheduled-event throttling never matters because we're
  dispatching, not scheduling. Dispatch latency is sub-second.
* Skipping a tick is cheap: ``shipctl trigger`` returns no due
  routines → workflow exits in ~30 sec without spawning an agent.

What this cron does NOT do:

* It does not push code. ``contents:write`` is unused. The only
  GitHub permission needed is ``actions:write`` on the repo, which
  the App already has from install time.
* It does not pick routines itself. The picker lives in
  ``shipctl trigger`` (drain-first by ``pipeline_priority``); this
  cron only triggers the workflow.
* It does not gate on workspace-level state (e.g. paused, frozen).
  A separate ``activated_at IS NOT NULL`` check is enough for
  closed beta; richer gating belongs on the workspace row once we
  need it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog
from backend.app.integrations.github.workflows import (
    WorkflowDispatchError,
    dispatch_workflow,
)


log = logging.getLogger(__name__)


# Hard-coded path is fine: every customer install ships the same
# filename via the starter-workflow bundle. Bumping the bundle
# version that renames it would require updating this constant in
# the same PR — easier to spot a rename than to debug a silent
# 404 dispatch.
_WORKFLOW_FILE: str = "ship-trigger-schedule.yml"


@dataclass(slots=True)
class DispatchTickResult:
    """Counters returned from one ``dispatch_due_repos`` invocation.

    Surfaced via the cron-job log so an operator can grep one line
    per tick to see "did the dispatch fan-out actually fire". The
    failures list carries enough context to drill into a stuck
    workspace from the dashboard without re-running the cron.
    """

    dispatched: int = 0
    skipped_no_install: int = 0
    failed: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


async def dispatch_due_repos(
    session: AsyncSession,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> DispatchTickResult:
    """Dispatch the trigger workflow on every activated repo.

    The query joins ``workspace_repos`` to ``github_installations``
    so a repo whose install row was revoked silently skips instead
    of crashing the tick. Closed-beta scale (<50 active repos)
    makes the per-repo sequential dispatch fine; if we hit hundreds
    we can fan out via ``asyncio.gather`` with a Semaphore — the
    bottleneck would be GitHub API rate limits, not DB roundtrips.
    """
    result = DispatchTickResult()

    rows = (
        await session.execute(
            select(WorkspaceRepo, GitHubInstallation)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == WorkspaceRepo.installation_id,
            )
            .where(WorkspaceRepo.activated_at.is_not(None))
        )
    ).all()

    if not rows:
        log.info("workflow_dispatch tick: no activated repos")
        return result

    for repo, install in rows:
        if install is None:
            # Defensive — the INNER JOIN above already filters this
            # out, but keep the early-out so future refactors that
            # widen the query don't crash on a missing install row.
            result.skipped_no_install += 1
            continue
        try:
            await dispatch_workflow(
                repo,
                install,
                _WORKFLOW_FILE,
                inputs={},
                settings=settings,
                client=client,
            )
        except WorkflowDispatchError as exc:
            # 404 on the workflow file means the customer hasn't
            # merged the wizard-seed PR yet (or merged it on a
            # non-default branch). Logged but not raised so the
            # cron tick keeps dispatching to the rest of the fleet.
            log.warning(
                "workflow_dispatch failed: repo=%s status=%s msg=%s",
                repo.full_name,
                exc.status_code,
                exc.message[:200],
            )
            result.failed += 1
            result.failures.append(
                {
                    "repo": repo.full_name,
                    "status": str(exc.status_code),
                    "error": exc.message[:200],
                }
            )
            session.add(
                AuditLog(
                    workspace_id=repo.workspace_id,
                    action="workflow_dispatch.failed",
                    target_kind="workspace_repo",
                    target_id=str(repo.id),
                    payload={
                        "repo_full_name": repo.full_name,
                        "workflow_file": _WORKFLOW_FILE,
                        "upstream_status": exc.status_code,
                        "error": exc.message[:512],
                    },
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 — never break the loop
            log.exception(
                "workflow_dispatch unexpected error: repo=%s", repo.full_name
            )
            result.failed += 1
            result.failures.append(
                {
                    "repo": repo.full_name,
                    "status": "exception",
                    "error": str(exc)[:200],
                }
            )
            continue

        result.dispatched += 1
        session.add(
            AuditLog(
                workspace_id=repo.workspace_id,
                action="workflow_dispatch.tick",
                target_kind="workspace_repo",
                target_id=str(repo.id),
                payload={
                    "repo_full_name": repo.full_name,
                    "workflow_file": _WORKFLOW_FILE,
                },
            )
        )

    log.info(
        "workflow_dispatch tick: dispatched=%d failed=%d",
        result.dispatched,
        result.failed,
    )
    return result


__all__ = ["DispatchTickResult", "dispatch_due_repos"]
