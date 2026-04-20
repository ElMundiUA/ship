"""Agent surface tables (C8 — improvements, C9 — clarifications, C10 — chat).

Three tables that make up the "human-in-the-loop" side of Ship:

- :class:`Clarification` — questions the agent raises about a specific
  tracker ticket / repo file / PR. The user can answer inline or skip
  ("not relevant") without opening the tracker UI. Populated by the
  agent pipelines; answered from ``/clarifications`` in the console.

- :class:`Improvement` — proposed changes (refactors, lint fixes,
  doc gaps, architecture nudges) the agent surfaces for a yes / no /
  later decision. Thin wrapper around a JSONB ``body`` so different
  agent pipelines can attach whatever context they need (diff link,
  cost/impact, etc.) without schema churn.

- :class:`ChatThread` + :class:`ChatMessage` — conversational
  interface for scoping new tickets with the agent before they land
  in the tracker. Messages alternate user ↔ assistant; threads carry
  an optional ``repo_id`` + ``workflow_id`` so the agent knows
  *which* catalog workflow (or none) is driving the conversation.

All three are workspace-scoped and cascade on workspace / repo
deletion — B6's disconnect story wipes them automatically.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


# ---------------------------------------------------------------------------
# C9 — Clarifications
# ---------------------------------------------------------------------------


class Clarification(Base):
    """One question the agent asked a human, plus the eventual answer.

    ``ticket_ref`` is a free-form string (e.g. ``LINEAR-123`` / GitHub
    issue URL) because we support multiple trackers and the pilot
    can't guarantee a single canonical ticket model. ``context`` is
    a JSONB bag so an agent pipeline can attach a snippet, file path,
    commit SHA, whatever it needs for the UI to render the full
    "why is this being asked" story.

    Status progression:

    - ``open`` (initial, answer is NULL)
    - ``answered`` (user filled in ``answer``)
    - ``skipped`` (user said "not relevant", answer stays NULL)
    - ``stale`` (pipeline re-ran and superseded the question; reserved
      for a future sweeper, not used in the pilot)
    """

    __tablename__ = "clarifications"
    __table_args__ = (
        Index(
            "ix_clarifications_workspace_status",
            "workspace_id",
            "status",
        ),
        Index(
            "ix_clarifications_repo_id", "repo_id"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=True,
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    ticket_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # open | answered | skipped | stale
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'")
    )
    context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    answered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


# ---------------------------------------------------------------------------
# C8 — Improvements
# ---------------------------------------------------------------------------


class Improvement(Base):
    """One agent-proposed improvement awaiting a yes / no / later call.

    ``kind`` is a coarse bucket so the UI can group them — ``refactor``
    / ``doc`` / ``test`` / ``dep`` / ``arch`` are the obvious buckets
    but we keep it as a free-form string to avoid a schema migration
    every time an agent invents a new category.

    Decision progression:

    - ``pending`` (initial)
    - ``accepted`` (user clicked yes; optional ``next_action_url``
      points at the PR we opened on the tenant's behalf, if any)
    - ``declined`` (user clicked no — we surface the reason in
      future improvement proposals to avoid re-suggesting)
    - ``deferred`` (user clicked later — re-surfaced after TTL)
    """

    __tablename__ = "improvements"
    __table_args__ = (
        Index(
            "ix_improvements_workspace_decision",
            "workspace_id",
            "decision",
        ),
        Index("ix_improvements_repo_id", "repo_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=True,
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # pending | accepted | declined | deferred
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_action_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


# ---------------------------------------------------------------------------
# C10 — Chat
# ---------------------------------------------------------------------------


class ChatThread(Base):
    """A conversation between a user and the Ship agent.

    Threads are repo-scoped (the happy path) but ``repo_id`` is
    optional for workspace-wide chats. ``workflow_id`` optionally
    pins the thread to a catalog workflow (e.g. ``tech-debt-scan``)
    so the agent knows which lane's context to load.

    ``status`` tracks where the thread is in its life:

    - ``active`` (default — accepting messages)
    - ``resolved`` (user pressed "create ticket" and the thread
      spawned an :class:`Improvement` / tracker entry)
    - ``archived`` (user closed without resolving)
    """

    __tablename__ = "chat_threads"
    __table_args__ = (
        Index("ix_chat_threads_workspace_id", "workspace_id"),
        Index("ix_chat_threads_repo_id", "repo_id"),
        Index(
            "ix_chat_threads_last_user_activity",
            "workspace_id",
            "created_by_user_id",
            "last_user_activity_at",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # active | resolved | archived
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'")
    )
    resolved_ticket_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- C12 single-window UX ---
    # Natural-language recap rendered as a sentinel "Packed → bucket X"
    # message in the chat UI when the thread is packed into a bucket.
    topic_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    packed_into_bucket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_buckets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Bumped on every user message; the "active thread" resolver picks
    # the thread with the freshest value per (workspace, user) tuple.
    last_user_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class ChatMessage(Base):
    """One message in a :class:`ChatThread`.

    ``role`` is one of ``user`` / ``assistant`` / ``system``. We
    avoid the OpenAI ``tool`` role in the pilot because we don't
    ship tool-calling end-to-end yet — a stub ``system`` message can
    carry "pipeline ran, here is its summary" payloads until we do.

    Messages are append-only. The UI renders the full thread on each
    request; for long conversations this is fine because a single
    thread is bounded by its life window (minutes, not months).
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index(
            "ix_chat_messages_thread_created",
            "thread_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = _ts_created()
