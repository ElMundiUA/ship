"""Per-repo ``intel`` snapshots harvested by the onboarding wizard.

The wizard (P5-06) dispatches one harvest per newly activated repo and
persists the result here so future agent runs can read a structured
snapshot of the project (languages, frameworks, entry points, commit
style, etc.) without re-scanning the tree on every invocation.

Layout decisions
----------------

- **Versioned, append-only.** Every harvest inserts a fresh row with
  ``version = prev.version + 1`` and flips the previous current row to
  ``is_current=FALSE``. This keeps history navigable when the heuristic
  changes or the repo's shape drifts.
- **Single live row per repo.** The partial unique index in the
  migration on ``(repo_id) WHERE is_current=TRUE`` enforces the
  invariant — at most one ``is_current=TRUE`` per repo.
- **Self-contained payload.** All harvested signals live in JSONB
  columns rather than separate child tables. The shape is consumed
  by agents reading from the bucket, not by SQL queries that need to
  filter on individual fields, so the storage cost of a JSONB column
  beats the schema-churn cost of broadening this into normalised
  tables every time we add a heuristic.
- **Lifecycle stamps.** ``harvested_by`` records which surface kicked
  the harvest off (wizard / manual_refresh / scheduled), and
  ``harvest_error`` is the diagnostic that survives a failed attempt
  so operators can tell "we tried and bounced" apart from "we never
  ran".

See :mod:`backend.app.services.repo_intel` for the harvest pipeline
that fills these rows in, and migration ``0034_repo_intel`` for the
DDL + indexes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import _pk  # noqa: PLC2701


class RepoIntelTriggeredBy:
    """Allowed values for :attr:`RepoIntel.harvested_by`.

    These are advisory tags (no DB-level CHECK) — operators may add new
    surfaces (e.g. a CLI ``ship intel refresh``) without a migration.
    The set below is the closed list the API + worker call sites use
    today; widening it is a one-line change here.
    """

    WIZARD = "wizard"
    MANUAL_REFRESH = "manual_refresh"
    SCHEDULED = "scheduled"

    ALL: tuple[str, ...] = (WIZARD, MANUAL_REFRESH, SCHEDULED)


class RepoIntel(Base):
    """One harvested snapshot of a repo's intel.

    Rows are versioned per repo: every harvest inserts a new row and
    flips the previous current row to ``is_current=FALSE``. The ``id``
    of the newly-inserted current row is what callers persist when
    they want to point at "the version we used to plan this run".

    The JSONB payload columns are intentionally permissive — each one
    documents a *shape* (see the harvest service for the canonical
    schemas) rather than a strict contract, so heuristics can evolve
    without a migration as long as readers are defensive about new
    keys.
    """

    __tablename__ = "repo_intel"
    __table_args__ = (
        # ``(repo_id, version)`` is the natural identity tuple — two
        # harvests of the same repo can never share a version. The
        # partial unique index that enforces "at most one current row
        # per repo" lives in the migration (Alembic-side) because
        # SQLAlchemy can't express ``WHERE is_current = TRUE``
        # portably.
        UniqueConstraint(
            "repo_id", "version", name="uq_repo_intel_repo_version"
        ),
        Index("ix_repo_intel_workspace_id", "workspace_id"),
        Index("ix_repo_intel_repo_id", "repo_id"),
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

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    # --- Harvested data (structured) -------------------------------------
    # ``{"typescript": 0.62, "python": 0.31, ...}``. Floats sum to ~1.
    languages: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # ``["next.js", "fastapi", "tailwindcss"]``. Lowercased canonical
    # framework names.
    frameworks: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # ``["npm", "pip", "uv"]``. Detected from manifest files.
    package_managers: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # ``[{"path":"console/src/app/page.tsx","kind":"page"}, ...]``.
    entry_points: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # ``{"top_level_dirs": [...], "depth_p50": 3, "depth_p95": 4,
    # "file_count": 1234}``.
    structure: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # ``{"convention":"conventional", "common_types":["feat","fix"],
    # "avg_subject_len":52, "samples":200}``.
    commit_style: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # ``{"primary_color":"#0ea5e9","font_family":"Inter", ...}``.
    # Best-effort, frontend-only. Empty dict ≠ failure (just nothing
    # to find).
    visual_tokens: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- Lifecycle -------------------------------------------------------
    harvested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    harvested_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    harvest_duration_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    harvest_error: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["RepoIntel", "RepoIntelTriggeredBy"]
