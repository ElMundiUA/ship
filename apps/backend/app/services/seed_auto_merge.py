"""Auto-merge stuck wizard-seed PRs and stamp the bundle version.

The wizard auto-merges the seed PR inline (see
``repos.wizard_seed``), but the merge can be deferred — branch
protection, a required check still pending, a transient GitHub 5xx.
When that happens the PR stays open and ``installed_bundle_version``
stays unstamped (correct: the new bundle isn't live yet).

This reconciler closes the gap. Once per tick it finds repos whose
recorded bundle version is behind the current ``BUNDLE_VERSION`` but
which already have a **current-version** seed PR, merges it via the
App's installation token, and stamps the version on a confirmed
merge.

Deliberately conservative:

* It never composes a fresh seed (that needs the wizard's tracker /
  agent context). A repo on drift with no current-version seed PR is
  left for the operator's next wizard click, which now works.
* It never merges a **stale** seed PR (an older bundle version) —
  doing so would roll the repo backwards. Matching is by the
  ``repo.wizard_seed`` audit row's recorded ``bundle_version``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import PullRequest
from backend.app.db.models.tenancy import AuditLog
from backend.app.integrations.github.workflows import (
    WorkflowDispatchError,
    merge_pull_request,
)
from backend.app.services.seed_bundle import BUNDLE_VERSION

log = logging.getLogger(__name__)

_SEED_AUDIT_ACTION = "repo.wizard_seed"
# GitHub returns 405 from the merge endpoint when the PR is already
# merged — treat that as success (stamp the version) rather than an error.
_ALREADY_MERGED_STATUS = 405


@dataclass(slots=True)
class SeedAutoMergeReport:
    repos_scanned: int = 0
    merged: int = 0
    stamped_already_merged: int = 0
    deferred: int = 0


async def reconcile_seed_merges(
    session: AsyncSession, *, settings: Settings
) -> SeedAutoMergeReport:
    """Merge stuck current-version seed PRs across drift repos.

    Caller owns the transaction — commit after this returns.
    """
    report = SeedAutoMergeReport()

    drift_repos = (
        await session.execute(
            select(WorkspaceRepo).where(
                or_(
                    WorkspaceRepo.installed_bundle_version.is_(None),
                    WorkspaceRepo.installed_bundle_version != BUNDLE_VERSION,
                )
            )
        )
    ).scalars().all()

    for repo in drift_repos:
        report.repos_scanned += 1

        # Most recent wizard-seed audit rows for this repo; pick the
        # newest one that recorded the CURRENT bundle version + a PR.
        audits = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.action == _SEED_AUDIT_ACTION,
                    AuditLog.target_id == str(repo.id),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(8)
            )
        ).scalars().all()

        seed_pr_number: int | None = None
        for audit in audits:
            payload = audit.payload or {}
            if payload.get("bundle_version") != BUNDLE_VERSION:
                continue
            raw = payload.get("pr_number")
            if raw is None:
                continue
            try:
                seed_pr_number = int(raw)
            except (TypeError, ValueError):
                seed_pr_number = None
            break

        if seed_pr_number is None:
            # Drift with no current-version seed PR — needs a fresh seed
            # (operator wizard click). Out of scope for this backstop.
            continue

        pr = (
            await session.execute(
                select(PullRequest).where(
                    PullRequest.repo_id == repo.id,
                    PullRequest.number == seed_pr_number,
                )
            )
        ).scalars().first()

        # Cache says already merged → just stamp (covers PRs merged by a
        # human or by the inline path before this row synced).
        if pr is not None and pr.merged:
            repo.installed_bundle_version = BUNDLE_VERSION
            report.stamped_already_merged += 1
            continue

        # Cache says closed-but-not-merged → abandoned; don't reopen.
        if pr is not None and pr.state not in ("open", None):
            continue

        install = (
            await session.execute(
                select(GitHubInstallation).where(
                    GitHubInstallation.id == repo.installation_id
                )
            )
        ).scalars().first()
        if install is None:
            log.warning(
                "seed_auto_merge: repo=%s has no installation row; skipping",
                repo.full_name,
            )
            continue

        try:
            await merge_pull_request(
                repo,
                install,
                pr_number=seed_pr_number,
                settings=settings,
                commit_title=f"Ship: wizard seed (#{seed_pr_number})",
                merge_method="squash",
            )
            repo.installed_bundle_version = BUNDLE_VERSION
            report.merged += 1
        except WorkflowDispatchError as exc:
            if exc.status_code == _ALREADY_MERGED_STATUS:
                repo.installed_bundle_version = BUNDLE_VERSION
                report.stamped_already_merged += 1
            else:
                report.deferred += 1
                log.info(
                    "seed_auto_merge: repo=%s pr=#%s merge deferred (%s): %s",
                    repo.full_name,
                    seed_pr_number,
                    exc.status_code,
                    exc.message[:160],
                )

    return report
