"""Deterministic workflow primitive — durable run/step state (W8.7).

Thesis 8: the workflow engine is a BOUNDED, imperative fan-out you
INVOKE for one job — distinct from the FSM (reactive, per-ticket,
never completes) and from /process (the state-machine *definition*
editor). These two tables are the engine's only state:

- :class:`AgentWorkflowRun` — one invocation of a named spec, fired by one
  of the three triggers (chat / gate / cron). Bounded: it reaches a
  terminal status when the DAG drains or a budget trips.
- :class:`AgentWorkflowStepRun` — one attempt of one step. The UNIQUE
  ``(workflow_run_id, step_id, attempt)`` constraint is the durable
  idempotency key the dispatch gate (W8.2) relies on: a retried
  reconcile tick upserts into the same attempt row instead of
  double-spawning; re-running a failed step REQUIRES a new attempt.

``lock_key`` ties a step to its ``workflow:<run>:<step>`` row in
``agent_dispatch_locks`` (countable + sweepable like ``ticket:*``);
``run_id`` correlates a coding leaf to the CI agent run that reports
back via ``/agent-runs/finish``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701 — shared column helper, intra-package.
    _ts_created,  # noqa: PLC2701
)


# Closed status sets — kept as plain tuples (not DB enums) so adding a
# member is a code change, not a migration.
WORKFLOW_RUN_STATUSES = ("queued", "running", "completed", "blocked", "failed")
WORKFLOW_STEP_STATUSES = (
    "pending",
    "dispatched",
    "running",
    "completed",
    "blocked",
    "failed",
    "skipped",
)


class AgentWorkflowRun(Base):
    __tablename__ = "agent_workflow_runs"
    __table_args__ = (
        Index("ix_agent_workflow_runs_ws_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    spec_name: Mapped[str] = mapped_column(String(120), nullable=False)
    spec_version: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'1'")
    )
    inputs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # chat | gate | cron — the three triggers (thesis 3).
    trigger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'queued'")
    )
    # Final synthesized output (when the spec's last step emits one).
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Audit linkage: who/what fired it (user id for chat, ticket ref
    # for gate, cron lock id for cron) — free-form, for the audit
    # trail, never control flow.
    triggered_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _ts_created()
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentWorkflowStepRun(Base):
    __tablename__ = "agent_workflow_step_runs"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "step_id",
            "attempt",
            name="uq_agent_workflow_step_attempt",
        ),
        Index("ix_agent_workflow_step_runs_run", "workflow_run_id"),
        Index("ix_agent_workflow_step_runs_ci_run", "run_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_id: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    # Step kind from the spec (parallel/pipeline/loop/barrier/
    # synthesize/judge/verify) — denormalized for dashboards.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Leaf executor (claude/codex/cursor/ship for coding leaves,
    # 'reasoning' for the in-process subagent loop).
    agent_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    # Validated against the step's output_schema before persisting;
    # nullable until the step completes.
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # ``workflow:<run>:<step>`` — the agent_dispatch_locks key the
    # gate acquired for this attempt.
    lock_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Correlates a coding leaf to its CI agent run (the run_id the
    # /agent-runs/finish webhook reports with).
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Why the gate refused (cap_exceeded/cascade_blocked/lock_held)
    # when status='blocked'/'skipped'.
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _ts_created()
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
