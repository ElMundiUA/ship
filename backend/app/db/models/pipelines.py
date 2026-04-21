"""Pipelines, pipeline runs, and live PR/workflow-run cache (pilot Day 3).

The pilot's "default pipelines" idea: when the user activates their first
repo, we materialise five rows in :class:`Pipeline` keyed off our own
workflow catalog (``pr-and-ci-gate``, ``scheduled-sdlc-lane``, etc.).
The dashboard reads that table; toggling a card flips ``enabled``;
pressing "Run now" inserts a :class:`PipelineRun` and (synchronously)
executes a tiny stub that writes a ``status=succeeded`` row when the
demo path completes. Background-job execution lands in package #2.

:class:`PullRequest` and :class:`WorkflowRun` are write-only caches
populated by the GitHub App webhook handler. The dashboard reads them
to render "last 10 PRs" / "last 10 workflow runs" without round-trip-
ing GitHub on every page load. They are *not* a replacement for the
GitHub API as the source of truth — we never write back through them
and we never reconcile drift in the pilot.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    Workspace,
    _pk,  # noqa: PLC2701  — shared column helper, intra-package.
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


class Pipeline(Base):
    """One configured automation lane for a workspace.

    Each row is a thin instance of a starter GitHub Actions workflow
    (served from :mod:`backend.app.services.starter_workflows` — see
    RFC-0007 Phase 6, which retired the ``artifact_kind=workflow``
    catalog layer). ``workflow_id`` is the starter slug (e.g.
    ``pr-and-ci-gate``); ``kind`` is the broad bucket the dashboard
    groups by (``pr_review`` / ``daily_standup`` / ``code_map`` /
    ``tech_debt`` / ``self_heal``). Keeping both lets us later support
    multiple pipelines of the same kind without losing the link back
    to the starter.

    ``enabled`` is the UI toggle. ``config`` is a free-form JSONB bag
    for vendor-specific knobs (e.g. ``{"branch": "main"}``) — the
    schema is intentionally loose because each kind reads its own
    fields.
    """

    __tablename__ = "pipelines"
    __table_args__ = (
        # Auto-create logic seeds at most one pipeline per (workspace,
        # kind) on first repo activation; a unique constraint here
        # documents that intent and rules out accidental duplicates.
        UniqueConstraint(
            "workspace_id", "kind", name="uq_pipelines_workspace_kind"
        ),
        Index("ix_pipelines_workspace_id", "workspace_id"),
        Index("ix_pipelines_repo_id", "repo_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional binding to a specific activated repo. The honest
    # dispatcher (Day-4 Phase-1) resolves the install + workflow file
    # through this FK, which is set when the pipeline gets seeded on
    # repo activation. Nullable because legacy rows from before Day-4
    # have no binding, and the API surfaces a ``not_bound`` 412 in
    # that case.
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspace: Mapped[Workspace] = relationship()


class PipelineRun(Base):
    """One execution of a :class:`Pipeline`.

    Rows are inserted *immediately* (status ``running``) when the user
    presses "Run now" or a webhook trigger fires, and updated to
    ``succeeded`` / ``failed`` when the synchronous demo handler
    returns. The pilot's runner is a stub — it just records the
    transition. Real execution lands when the worker comes back in
    package #2.
    """

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_pipeline_id_started", "pipeline_id", "started_at"),
        Index("ix_pipeline_runs_workspace_id", "workspace_id"),
        Index("ix_pipeline_runs_lane_id", "lane_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalised for cheaper "all runs for workspace" queries; the
    # FK above is enough for correctness.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ``manual`` (button), ``webhook`` (PR / workflow_run), ``cron``
    # (when the worker comes back), ``onboarding`` (auto-seed run).
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # SHA-256 of the short-lived JWT we hand to the dispatched workflow
    # via ``inputs.ship_run_token``. The result-callback endpoint
    # rejects anything that doesn't match so a stolen ``run_id`` alone
    # can't fake a "succeeded" report. NULL on legacy pre-Day-4 stub
    # rows where the run never had an outbound dispatch.
    run_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # RFC-0007 Phase 7: optional back-link to the :class:`Lane` a run
    # was triggered against. ``NULL`` for legacy starter-pipeline runs
    # (seeded ``Pipeline`` rows) and for any run that doesn't originate
    # from a ``.ship/config.yml`` lane. Populated by future "Trigger
    # lane now" paths.
    lane_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lanes.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class PullRequest(Base):
    """Cached snapshot of a GitHub PR for the dashboard list.

    Updated by the ``pull_request`` webhook handler. We never reconcile
    drift — if the user mutes webhooks the row goes stale, period. The
    canonical source is always GitHub; this table only exists so the
    dashboard can render last-10-PRs without a per-render API hop.
    """

    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "external_id",
            name="uq_pull_requests_workspace_external",
        ),
        Index("ix_pull_requests_workspace_updated", "workspace_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    # GitHub's numeric PR id (not the per-repo ``number``). Globally
    # unique across all PRs on github.com, so it's a stable de-dupe key.
    external_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    merged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    draft: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at_external: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class WorkflowRun(Base):
    """Cached snapshot of a GitHub Actions ``workflow_run`` event.

    Same caveat as :class:`PullRequest`: this is a write-through cache
    populated by the webhook, not the source of truth. The dashboard
    uses it for the live runs panel and the self-heal pipeline reads
    failed rows in Day-3+ work.
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "external_id",
            name="uq_workflow_runs_workspace_external",
        ),
        Index("ix_workflow_runs_workspace_updated", "workspace_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    event: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # ``success`` / ``failure`` / ``cancelled`` / etc. NULL while the
    # run is still in progress.
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    head_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    html_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = ["Pipeline", "PipelineRun", "PullRequest", "WorkflowRun"]
