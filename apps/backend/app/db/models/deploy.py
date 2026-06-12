"""Deployment model — one deploy attempt for a repo to a cloud provider.

Lifecycle
---------
``pending``    → just created, plan not yet run
``planning``   → LLM is producing the DeployPlan
``deploying``  → app spec submitted to provider, waiting for ACTIVE
``active``     → deployment live, health check passed
``failed``     → provider returned ERROR/CANCELED or health check failed
``cancelled``  → user cancelled
``deleted``    → provider app was intentionally deleted; row kept for audit

The ``provider_ref`` JSONB stores whatever the provider adapter returned
(DO app_id, deployment_id, region, spec_name) so the status-poller knows
exactly what to query. ``plan`` stores the full ``DeployPlan`` JSON for
auditability. ``status_detail`` stores the last provider phase string.
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
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import _pk, _ts_created, _ts_updated


class DeploymentStatus:
    """Deployment lifecycle states stored in ``Deployment.status``."""

    PENDING = "pending"
    PLANNING = "planning"
    DEPLOYING = "deploying"
    ACTIVE = "active"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELETED = "deleted"

    ALL: tuple[str, ...] = (
        PENDING, PLANNING, DEPLOYING, ACTIVE, FAILED, CANCELLED, DELETED
    )
    TERMINAL: tuple[str, ...] = (ACTIVE, FAILED, CANCELLED, DELETED)


class DeploymentEventKind:
    """Human-readable lifecycle events shown in the app's Activity feed."""

    DEPLOYED = "deployed"            # a version went live
    DEPLOY_FAILED = "deploy_failed"  # a deploy attempt failed
    DELETED = "deleted"             # user tore the app down
    REMOVED_EXTERNALLY = "removed_externally"  # gone from the provider (drift)
    HEALTH_LOST = "health_lost"
    HEALTH_RESTORED = "health_restored"


class Deployment(Base):
    """One deploy attempt: repo → provider."""

    __tablename__ = "deployments"
    __table_args__ = (
        Index("ix_deployments_workspace_id", "workspace_id"),
        Index("ix_deployments_repo_id", "repo_id"),
        Index("ix_deployments_workspace_repo", "workspace_id", "repo_id"),
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

    # Provider identifier, e.g. "digitalocean".
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    # Lifecycle state (see DeploymentStatus constants).
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )

    # Last raw phase string from the provider (e.g. "BUILDING", "ACTIVE").
    status_detail: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Full DeployPlan JSON for audit / replay.
    plan: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Provider-specific handle: {"app_id": "...", "deployment_id": "...", ...}
    provider_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Live URL once deployment is ACTIVE.
    live_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Health check result (True = passing, False = failing, None = not yet checked).
    healthy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Human-readable error when status=failed.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When the deploy was kicked off.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When it reached a terminal state.
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class DeploymentEvent(Base):
    """One human-readable activity entry for an app (repo + provider).

    Keyed by the **app** (workspace + repo + provider), not a single
    deployment, so the card's Activity feed shows the whole story across
    redeploys: "v1 deployed", "removed on DigitalOcean", "deleted", etc.
    ``deployment_id`` links to the specific deployment when relevant (no FK
    cascade — events outlive individual deployment rows).
    """

    __tablename__ = "deployment_events"
    __table_args__ = (
        Index(
            "ix_deployment_events_app",
            "workspace_id",
            "repo_id",
            "provider",
            "created_at",
        ),
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
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts_created()


__all__ = [
    "Deployment",
    "DeploymentStatus",
    "DeploymentEvent",
    "DeploymentEventKind",
]
