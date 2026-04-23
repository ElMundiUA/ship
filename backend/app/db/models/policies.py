"""Workspace prose-rule policies (Workspace policy injection).

A *workspace policy* is a free-text standing rule a workspace admin
declares once and Ship enforces by **injecting it into the agent's
system prompt** every time work is dispatched in this workspace.
Examples: "Always work via PR", "Never commit secrets", "Run lint
before opening a PR". The injection happens in two places:

1. Navigator chat — :func:`TopicService.assemble_messages` adds a
   system message immediately after the base agent prompt with the
   rendered policy preamble.
2. ``shipctl run`` — fetches the rendered preamble and prepends it
   to the pattern markdown emitted on stdout, so the GitHub-Actions
   agent step consumes it as part of its instructions.

This is the **new** ``WorkspacePolicy`` model. The previous concept
(mirror-lane rules) was renamed to :class:`FleetLane` in revision
``0029`` so this name could be repurposed for the prose-rule
feature without breaking the API surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701 — shared column helper.
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


class WorkspacePolicy(Base):
    """One free-text standing rule for a workspace.

    The combination of ``enabled`` + ``sort_order`` lets admins
    re-order rules in the Console without renumbering and toggle
    individual rules off without losing the body. The renderer
    iterates ``enabled=true`` rows ordered by ``(sort_order,
    created_at)`` so the preamble layout is stable across requests.
    """

    __tablename__ = "workspace_policies"
    __table_args__ = (
        Index(
            "ix_workspace_policies_workspace_enabled_sort",
            "workspace_id",
            "enabled",
            "sort_order",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = ["WorkspacePolicy"]
