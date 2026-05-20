"""Pull-request cache reconciler.

The ``pull_requests`` table is a webhook-fed cache mirroring open PRs
on every workspace's activated repos. When GitHub merges or closes a
PR, a ``pull_request`` webhook event updates the cached row; the
dashboard's :func:`_mirror_stuck_prs_to_inbox` then dismisses any
``stuck_pr`` inbox letter pointing at that PR.

When the webhook misses an event (install paused, replay queue
backed up, hand-merge via GH UI before the webhook fires reliably,
operator deleted then reinstalled the App on the repo) the cache
drifts: the row stays at ``state=open``, the PR is merged-or-closed
on GitHub, and every dashboard render regenerates a ``stuck_pr``
inbox letter the operator can't dismiss (the next render brings it
back). Caught on askslayer/visitor 2026-05-19: 5 stale-open rows on
PRs that were merged 7-12 days ago, all racking up "Stuck work" inbox
spam.

This reconciler walks every workspace's ``state=open`` rows whose
``updated_at_external`` is more than ``STALE_THRESHOLD`` old, queries
GitHub for the live state, and updates the cache when the live state
disagrees. Idempotent: rows whose cache already matches GitHub are
no-ops.

Bounded scope:

- Only ``state=open`` rows — closed/merged rows are terminal.
- Only rows >3d stale — fresh churn goes through the webhook path
  uninterrupted.
- Workspace + install must still be reachable; rows on suspended
  installs are logged and skipped (operator surfaces those via the
  re-install flow).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Final

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import PullRequest
from backend.app.db.session import get_sessionmaker
from backend.app.integrations.github.code_host_adapter import (
    GitHubCodeHost,
    PullRequestRef,
    RepoRef,
)


log = logging.getLogger(__name__)


# Don't touch rows fresher than this — the webhook path is the
# primary signal and we don't want to step on its toes.
STALE_THRESHOLD = timedelta(days=3)

# Cap per-tick work so a backlog doesn't burn the GH API budget.
# Conservative; reconciler runs every 30min, so 50/tick = 2400/day —
# more than enough headroom for typical workspaces.
MAX_ROWS_PER_TICK: Final[int] = 50


async def reconcile_stale_pull_requests() -> int:
    """Walk every workspace's ``state=open`` PRs >3d stale and
    refresh the cache from GitHub. Returns the number of rows updated.

    Designed for the cron at :data:`cron.CronLockId.PR_CACHE_RECONCILE`
    — single-leader, idempotent, bounded-budget. Failures on individual
    rows log + continue; the tick never raises.
    """
    sessionmaker = get_sessionmaker()
    settings = get_settings()
    updated = 0

    async with sessionmaker() as session:
        cutoff = datetime.now(timezone.utc) - STALE_THRESHOLD
        rows = (
            await session.execute(
                select(PullRequest)
                .where(
                    PullRequest.state == "open",
                    PullRequest.updated_at_external < cutoff,
                )
                .order_by(PullRequest.updated_at_external.asc())
                .limit(MAX_ROWS_PER_TICK)
            )
        ).scalars().all()
        if not rows:
            return 0

        # Group by workspace so we can resolve the install once per
        # workspace instead of per-PR. Most rows on a tick belong to
        # the same workspace anyway (one tenant let the cache drift,
        # not all of them at once).
        by_ws: dict = {}
        for pr in rows:
            by_ws.setdefault(pr.workspace_id, []).append(pr)

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            for ws_id, prs in by_ws.items():
                install = (
                    await session.execute(
                        select(GitHubInstallation).where(
                            GitHubInstallation.workspace_id == ws_id,
                            GitHubInstallation.suspended_at.is_(None),
                        ).limit(1)
                    )
                ).scalar_one_or_none()
                if install is None:
                    log.info(
                        "pr_cache_reconciler: no active install for ws=%s "
                        "skipping %d stale PR rows",
                        ws_id, len(prs),
                    )
                    continue
                gateway = GitHubCodeHost(
                    install.installation_id, settings=settings, client=client
                )
                for pr in prs:
                    if not pr.repo_full_name or "/" not in pr.repo_full_name:
                        continue
                    owner, name = pr.repo_full_name.split("/", 1)
                    ref = PullRequestRef(
                        repo=RepoRef(kind="github", owner=owner, repo=name),
                        number=pr.number,
                    )
                    try:
                        live = await gateway.get_pull_request(ref)
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "pr_cache_reconciler: get_pull_request "
                            "failed ws=%s repo=%s pr=%s err=%s",
                            ws_id, pr.repo_full_name, pr.number, exc,
                        )
                        continue
                    if not isinstance(live, dict):
                        continue
                    new_state = live.get("state") or pr.state
                    merged = bool(live.get("merged"))
                    merged_at_raw = live.get("merged_at")
                    closed_at_raw = live.get("closed_at")
                    if new_state == pr.state and merged == bool(pr.merged):
                        # Cache already current; record the touch via
                        # updated_at_external so we don't keep re-
                        # checking the same row every tick.
                        pr.updated_at_external = datetime.now(timezone.utc)
                        continue
                    # State changed — apply the live payload to the row.
                    pr.state = "merged" if merged else new_state
                    pr.merged = merged
                    if merged_at_raw:
                        try:
                            pr.merged_at = _parse_iso(merged_at_raw)
                        except Exception:  # noqa: BLE001
                            pass
                    if closed_at_raw:
                        try:
                            pr.closed_at = _parse_iso(closed_at_raw)
                        except Exception:  # noqa: BLE001
                            pass
                    pr.updated_at_external = datetime.now(timezone.utc)
                    updated += 1
                    log.info(
                        "pr_cache_reconciler: reconciled ws=%s repo=%s "
                        "pr=%s open->%s merged=%s",
                        ws_id, pr.repo_full_name, pr.number,
                        pr.state, merged,
                    )
        await session.commit()
    return updated


def _parse_iso(value: str) -> datetime:
    """Parse a GitHub-flavoured ISO-8601 timestamp into a tz-aware
    ``datetime``. GitHub emits ``...Z``; Python's ``fromisoformat`` only
    accepts ``+00:00`` pre-3.11. Normalise the suffix before parsing."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


__all__ = ["reconcile_stale_pull_requests", "STALE_THRESHOLD"]
