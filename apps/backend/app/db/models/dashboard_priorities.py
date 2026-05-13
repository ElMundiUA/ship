"""Workspace-level project prioritisation table (Dashboard v2 — PR-1).

The home dashboard's prioritizer is the operator's main lever for
telling the bot which Linear/Jira project queue to drain first. Order
is workspace-scoped (not per-user) — a workspace has a single canonical
priority list that the agent consults when picking the next ticket.

One row per (workspace, tracker project). ``project_native_id`` is the
tracker's own id (Linear UUID, Jira project key, …). ``ordinal`` is a
plain int — 0 is highest priority, ascending. We persist the order
explicitly rather than ``created_at``-sort so reordering is cheap and
unambiguous (one row update per moved item, idempotent bulk replace
on save).

The table is best-effort: a project that disappears from the tracker
just stops being rendered; we don't proactively prune. Likewise,
unprioritised projects (no row here) sort *after* prioritised ones in
the UI, in tracker-update order.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


class WorkspaceProjectPriority(Base):
    __tablename__ = "workspace_project_priorities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "project_native_id",
            name="uq_workspace_project_priorities_ws_native",
        ),
        Index(
            "ix_workspace_project_priorities_ws_ord",
            "workspace_id",
            "ordinal",
        ),
        CheckConstraint(
            "state IN ('active', 'planning', 'parked')",
            name="ck_workspace_project_priorities_state",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Tracker-native project id (Linear project UUID / Jira project key).
    # Stored as text so vendors with non-UUID identifiers (Jira keys like
    # "ELS") slot in without a schema change.
    project_native_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # Bucket on the prioritizer:
    # ``active`` (agent may pick) | ``planning`` (operator is shaping —
    # UI label "Drafts") | ``parked`` (explicitly hold). Only
    # ``active`` is visible to the agent's project picker.
    #
    # Default is ``planning`` (ELS-82): newly created projects need to
    # go through the dashboard's drafting + decomposition flow before
    # the agent picks them up. Decomposition completion flips the row
    # to ``parked`` (ELS-81), and the PO promotes ``parked`` → ``active``
    # manually when ready to ship. The previous default of ``active``
    # let any new project bypass the gate.
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'planning'"),
    )
    # Backref to the Navigator thread this project was drafted in.
    # Powers the "Continue shaping" link on Drafts-bucket rows: clicking
    # re-opens the originating conversation rather than spawning a new
    # one (the agent then prefers ``append_project_description`` over
    # creating a duplicate). Null for projects that predate the drafting
    # flow or were created outside Navigator (e.g. directly in Linear).
    originating_thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_threads.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = ["WorkspaceProjectPriority"]
