"""Workspace-private catalog patterns (RFC-0008 §H — PR-6).

Baked-in patterns ship as YAML files in ``artifacts/patterns/`` and
are read-only from the operator's point of view — they change when
Ship itself ships. This model backs *workspace-owned* patterns:
things an operator (or Navigator with LLM help) authors at runtime
because the baked-in catalog is missing something they need.

The row shape mirrors the subset of a pattern's frontmatter the
Console ever reads (:class:`CatalogArtifact.to_summary`). A tiny
adapter in :mod:`backend.app.services.catalog` promotes these rows
to in-memory :class:`CatalogArtifact` objects so every picker that
already calls ``list_patterns()`` gets workspace-private entries
"for free" once the service-level merge is wired in.

Collision policy: ``pattern_id`` is unique per workspace. Colliding
with a baked-in id is fine — the merge function resolves it by
preferring the workspace-private row, so operators can shadow a
baked-in pattern without forking Ship.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


class CustomPattern(Base):
    """A workspace-owned catalog pattern.

    ``body`` is the markdown prompt the agent renders; ``spec`` is
    the free-form frontmatter JSON that mirrors a baked-in pattern's
    ``spec:`` block (``default_trigger``, ``include``, ``lane_workflow``,
    ``enabled_on_install`` etc.). The structured columns ``modes``
    and ``inputs`` are pulled out of ``spec`` so the catalog adapter
    can type them without re-parsing.
    """

    __tablename__ = "custom_patterns"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "pattern_id",
            name="uq_custom_patterns_pattern_id",
        ),
        Index("ix_custom_patterns_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    pattern_id: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # ``["lane"]``, ``["request"]``, or ``["lane", "request"]``. Stored as
    # JSONB so the service-layer adapter can pass it straight through
    # to ``CatalogArtifact.modes``.
    modes: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    inputs: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    spec: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    body: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = ["CustomPattern"]
