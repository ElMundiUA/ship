"""Workspace-scoped agent role prompts (Phase 2.4 Step A).

An *agent role* is the specialist prompt body that ``shipctl run``
loads when a routine fires. Ship ships a small read-only set of
defaults under ``backend/app/resources/agent_roles/`` (``system``,
``developer``, ``ba``, ``intake``, …). This table holds the
workspace-side rows that:

* **Override** a Ship default (``slug`` matches a default file).
  The runtime resolver returns the workspace prompt instead of the
  file body for the owning workspace.
* **Clone** a Ship default into a new specialist (``slug`` is unique
  within the workspace, ``base_role_slug`` records which file the
  clone was seeded from).

Ship defaults themselves are never persisted in this table — they
stay file-backed so a release of Ship can refresh them without
touching workspace data. ``DELETE`` of an override therefore reverts
the workspace to the file-backed default; ``DELETE`` of a clone
removes the row outright (callers warn the operator if the slug is
referenced from a routine).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701 — shared column helper.
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


class AgentRole(Base):
    """One workspace-scoped specialist prompt row."""

    __tablename__ = "agent_roles"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "slug", name="uq_agent_roles_workspace_slug"
        ),
        Index("ix_agent_roles_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Informational pointer to the Ship default the row was cloned
    # from. ``NULL`` means: same-slug override of a Ship default, or a
    # brand-new role with no Ship parent.
    base_role_slug: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = ["AgentRole"]
