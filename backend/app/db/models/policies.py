"""Workspace-level policies (RFC-0008 §G — PR-5).

Policies are workspace-owned rules that span *all* activated repos.
The first kind we land is ``mirror_lane``: "pattern X runs as a lane
with cadence Y on every activated repo, unless explicitly excepted".
That single primitive lets a platform team set "every repo runs the
security-scan lane nightly" without editing N ``.ship/config.yml``
files by hand.

Future kinds (``required_request``, ``required_check``, …) can slot
into the same table via ``kind`` discrimination; for PR-5 only
``mirror_lane`` is validated. Repos can opt out of a policy via the
companion :class:`WorkspacePolicyException` table — the roll-up
endpoint subtracts those rows from the "missing" bucket so compliance
numbers don't panic operators over deliberate exceptions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701 — shared column helper.
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


class WorkspacePolicy(Base):
    """One workspace-level rule. See module docstring for context.

    ``lane_id`` is the string slug the materialised ``Pipeline`` row
    uses on each repo — matching this is how the compliance roll-up
    decides "is repo R covered by this policy?". ``pattern_id`` is
    the catalog pattern the lane should run; ``cadence`` is a free
    string (cron expression or ``@daily`` / ``@weekly`` sugar; the
    materialiser validates it when it actually wires the workflow).
    ``inputs`` holds default pattern inputs that all repos inherit.
    """

    __tablename__ = "workspace_policies"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "lane_id",
            name="uq_workspace_policies_lane",
        ),
        Index("ix_workspace_policies_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Discriminator. Only ``mirror_lane`` is accepted by the
    # PR-5 endpoints; other kinds can be added later without
    # schema changes.
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'mirror_lane'"),
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    pattern_id: Mapped[str] = mapped_column(String(120), nullable=False)
    lane_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cadence: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_slug: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    inputs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class WorkspacePolicyException(Base):
    """Per-repo opt-out from a policy.

    A row here means "repo R should *not* be counted as missing this
    policy's lane". It does not prevent the repo from wiring the lane
    manually later — it just excludes the repo from the compliance
    rollup's "needs attention" bucket. One row per ``(policy,
    repo)``; toggling an opt-out deletes the row.
    """

    __tablename__ = "workspace_policy_exceptions"
    __table_args__ = (
        UniqueConstraint(
            "policy_id",
            "repo_id",
            name="uq_workspace_policy_exceptions_repo",
        ),
        Index(
            "ix_workspace_policy_exceptions_policy_id",
            "policy_id",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_policies.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = ["WorkspacePolicy", "WorkspacePolicyException"]
