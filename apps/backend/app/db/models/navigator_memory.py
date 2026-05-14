"""Navigator memory mirror (E17/ELS-126).

Sits between Ship's auth model and mem0's storage. mem0 owns its
own vector + history rows; we mirror each fact here so:

- Access control (owner_user_id + workspace_id filter) is enforced
  on every read in SQL, not in Python from mem0's surface.
- The Console ``/memory`` page can join through Ship's existing
  ``users`` / ``chat_threads`` / ``chat_messages`` for the
  "show source" / "edit / delete" UX.
- Backfill + migrations have a stable schema to reason about.

The one-to-one with mem0 is keyed on ``mem0_id``; the service layer
(``apps/backend/app/services/agent/memory.py``) writes both sides
in the same transaction so the mirror can't drift.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class NavigatorMemory(Base):
    __tablename__ = "navigator_memories"
    __table_args__ = (
        Index(
            "uq_navigator_memories_mem0_id", "mem0_id", unique=True
        ),
        Index(
            "ix_navigator_memories_owner_workspace",
            "owner_user_id",
            "workspace_id",
        ),
        Index(
            "ix_navigator_memories_source_thread", "source_thread_id"
        ),
        Index(
            "ix_navigator_memories_project",
            "owner_user_id",
            "workspace_id",
            "project_native_id",
        ),
        # ``ix_navigator_memories_embedding`` is created in the
        # migration via raw SQL (ivfflat with ``WITH (lists=100)``)
        # and isn't expressible as an SQLAlchemy Index — leave it
        # out of ``__table_args__`` so Alembic autogenerate doesn't
        # try to drop it.
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
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True
    )
    # Provenance — SET NULL on cascade so deleting the source thread
    # doesn't wipe the extracted facts.
    source_thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_message_position: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Free-form string — trackers spell project ids differently.
    project_native_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    # ``shape_project`` etc. — drives retrieval-boost ranking.
    intent_at_capture: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    mem0_id: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("1.0")
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
