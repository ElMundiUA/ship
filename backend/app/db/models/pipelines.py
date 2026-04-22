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
    ``pr-and-ci-gate``); ``lane_id`` is the stable lane slug the
    dashboard groups by (``pr_review`` / ``daily_standup`` /
    ``code_map`` / ``tech_debt`` / ``self_heal``) — same vocabulary
    as :class:`Lane.lane_id` for config-derived lanes. Keeping both
    lets us later support multiple pipelines per lane without losing
    the link back to the starter.

    ``enabled`` is the UI toggle. ``config`` is a free-form JSONB bag
    for vendor-specific knobs (e.g. ``{"branch": "main"}``) — the
    schema is intentionally loose because each lane reads its own
    fields.
    """

    __tablename__ = "pipelines"
    __table_args__ = (
        # Auto-create logic seeds at most one pipeline per (workspace,
        # lane_id) on first repo activation; a unique constraint here
        # documents that intent and rules out accidental duplicates.
        UniqueConstraint(
            "workspace_id", "lane_id", name="uq_pipelines_workspace_lane_id"
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
    lane_id: Mapped[str] = mapped_column(String(64), nullable=False)
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


class AgentRequest(Base):
    """Phase 3 "Requests" — one-shot agent runs dispatched via GitHub.

    Rows are created when the operator submits a ``New request`` form
    on the Console (``POST /v1/workspaces/{ws}/repos/{id}/requests``):
    Ship calls ``workflow_dispatch`` against the
    ``adhoc-agent-run.yml`` starter on the target repo and records a
    row here so the Console's ``/requests`` list can show status +
    who dispatched what.

    Unlike :class:`Pipeline` / :class:`PipelineRun`, there's no FK to
    an owning pipeline because ad-hoc runs have no lane / recipe;
    each row stands on its own. Updates come from the ``shipctl
    callback`` emitted by the workflow itself (same contract as
    scheduled lanes).
    """

    __tablename__ = "agent_requests"
    __table_args__ = (
        Index("ix_agent_requests_workspace_created", "workspace_id", "created_at"),
        Index("ix_agent_requests_repo_created", "repo_id", "created_at"),
        Index(
            "ix_agent_requests_fleet_request_id",
            "fleet_request_id",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # RFC-0008 D (Fleet Requests) — when this row was spawned as part
    # of a workspace-level fan-out, points back to the parent
    # :class:`FleetRequest`. NULL for single-repo dispatches from the
    # legacy per-repo form; the pivot view joins on this FK.
    fleet_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleet_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    # RFC-0008 C4 — pattern-backed requests. When set, the row points
    # at a catalog pattern id (e.g. ``role-ba``) and ``inputs`` holds
    # the form values collected from ``pattern.spec.inputs``. Ad-hoc
    # free-form dispatches (pre-C4 API shape) leave both ``NULL`` /
    # ``{}`` and rely on ``agent_slug`` + ``prompt`` alone.
    pattern_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    inputs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    context_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    prompt: Mapped[str] = mapped_column(String(4096), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'dispatched'")
    )
    summary: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    gh_workflow_run_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    gh_html_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    run_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class FleetRequest(Base):
    """Fan-out parent for a workspace-level agent request (RFC-0008 D).

    One row per ``POST /v1/workspaces/{ws}/fleet/requests`` — captures
    the catalog pattern + inputs selected once, then sprays N child
    :class:`AgentRequest` rows across the operator-chosen repos. The
    parent's job is twofold:

    * keep the Console's "Fleet Requests" surface O(1) in the pivot
      view (join on ``agent_requests.fleet_request_id`` instead of
      reconstructing the group by pattern + timestamp), and
    * act as the cancel / rollup anchor — a single toggle on the
      parent can short-circuit every still-running child.

    Validation of ``pattern_id`` + ``inputs`` happens *before* the
    parent lands (one decode, one error response), so the row always
    starts with a resolved payload. The per-repo fan-out is
    **best-effort** (RFC-0008 §D): repos that fail the pre-dispatch
    check (missing installation, suspended app, …) are captured on
    ``rejections`` and the parent transitions to ``partial`` instead
    of rolling back the whole batch.
    """

    __tablename__ = "fleet_requests"
    __table_args__ = (
        Index(
            "ix_fleet_requests_workspace_created",
            "workspace_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Human-readable label copied from the pattern name at dispatch
    # time (``role-ba`` etc.) — purely cosmetic; the Console list view
    # uses it as the row title. Nullable because legacy ad-hoc
    # dispatches (no pattern) fall back to ``agent_slug``.
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # RFC-0008 C4 — the catalog pattern id that all children dispatch
    # with. Free-form ad-hoc fan-outs leave this NULL and store the
    # agent slug instead.
    pattern_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Frozen inputs — the same dict we forward to every child, so the
    # pivot shows exactly what was dispatched even if the pattern's
    # catalog entry changes later.
    inputs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    context_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # ``dispatching`` (initial) → ``dispatched`` (all children created)
    # → ``partial`` (some repos rejected) → ``cancel_requested`` /
    # ``cancelled``. We don't roll up child success/failure here —
    # the Console's pivot computes that from the children live.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'dispatching'")
    )
    # How many target repos the operator picked. Stored so the pivot
    # can show "3 of 5 dispatched" even when some children were never
    # created (e.g. GitHub App missing on a few repos).
    target_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    # Pre-flight rejections (repo not found, GitHub App missing) —
    # persisted here because no child :class:`AgentRequest` row is
    # created for them, so a refresh of the detail view would
    # otherwise lose the information. Each entry is
    # ``{repo_id, repo_full_name?, code, message}``. Dispatch-time
    # failures land on the child row's ``status=dispatch_failed`` +
    # ``summary`` instead, so they round-trip via the children join.
    rejections: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = [
    "AgentRequest",
    "FleetRequest",
    "Pipeline",
    "PipelineRun",
    "PullRequest",
    "WorkflowRun",
]
