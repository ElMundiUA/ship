"""Agent dispatch locks — working state for the E16 event-driven dispatcher.

One row per active claim. The dispatcher creates a row before firing
a ``workflow_dispatch`` and deletes it (or lets it expire) when the
agent reports finish via the sidecar. Two readers care about this
table:

- ``dispatcher.maybe_dispatch`` — tries to claim ``(workspace_id,
  key)`` via INSERT ... ON CONFLICT DO NOTHING; success means
  "nobody else is working on this key", failure means "another agent
  is in flight (or recently was)".
- ``dispatcher.sweep_expired_locks`` — deletes rows whose ``expires_at`` is
  in the past, freeing the slot. Run on the agent-dispatch lock sweep
  cron (5-minute cadence) alongside the ``project:*`` heuristic sweeper.

Keys are opaque strings — the dispatcher writes ``ticket:<ticket_ref>``
today; future namespaces (``daily:<routine_id>``, ``project:<anchor>``)
slot in without a schema change.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class AgentDispatchLock(Base):
    __tablename__ = "agent_dispatch_locks"
    __table_args__ = (
        Index(
            "uq_agent_dispatch_locks_workspace_key",
            "workspace_id",
            "key",
            unique=True,
        ),
        Index("ix_agent_dispatch_locks_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(256), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    # Self-release deadline. Default = claim time + 60 min. Bundles
    # that legitimately run longer should override the column at
    # insert time, not extend it after-the-fact.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now() + interval '60 minutes'"),
    )
    # Audit-log row id of the dispatch that owns this lock. NULL is
    # allowed because daily-tick locks are claimed before the dispatch
    # row is written. Not a FK — audit_log has its own lifecycle and
    # we don't want a cascade dragging locks into archive deletes.
    # Type is bigint to match ``audit_log.id``; the lock sweeper joins
    # ``agent_dispatch_locks.run_id = audit_log.id`` to recover the
    # owning ticket_ref.
    run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
