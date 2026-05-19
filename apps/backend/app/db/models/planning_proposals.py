"""``planning_proposals`` — draft project + epics + deps awaiting
operator review before commit to Linear (ELS-170)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
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


class PlanningProposal(Base):
    """One mass-planning intake draft.

    Created by the Navigator agent's ``propose_mass_plan`` tool
    after running the extractor on an uploaded PDF; edited via the
    Console preview pane; committed via
    ``POST /planning/mass-import`` which writes Linear and stamps
    ``committed_at`` + the resulting ticket refs.

    ``payload`` mirrors :class:`MassPlanProposal` from
    ``backend.app.services.planning.requirements_extraction`` —
    kept loose-typed (JSONB) so prompt-side schema tweaks don't
    force a migration.
    """

    __tablename__ = "planning_proposals"
    __table_args__ = (
        Index("ix_planning_proposals_workspace", "workspace_id"),
        Index(
            "ix_planning_proposals_thread",
            "thread_id",
            postgresql_where=text("thread_id IS NOT NULL"),
        ),
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
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_kind: Mapped[str] = mapped_column(
        String(length=32),
        nullable=False,
        server_default=text("'pdf'"),
    )
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    committed_ticket_refs: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )


__all__ = ["PlanningProposal"]
