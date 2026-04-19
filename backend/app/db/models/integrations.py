"""Per-vendor integration tables that don't fit the generic ``integrations`` row.

The generic :class:`backend.app.db.models.tenancy.Integration` row stores
*one secret + JSON config* per ``(workspace, kind)``. A GitHub App
installation needs more shape than that:

- it has a stable ``installation_id`` from GitHub that we must persist to
  mint per-installation tokens later,
- the same App can be installed against several GitHub *accounts* (a user +
  several orgs), so we may end up with multiple rows per workspace once we
  let users wire more than one GH account,
- webhook deliveries arrive *before* a user is logged in, so we need to
  look an installation up by ``installation_id`` alone.

Hence its own table, modelled after :class:`Integration` for style
consistency (UUID PK, ``workspace_id`` FK + cascade, ``created_at`` /
``updated_at`` server defaults).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    Workspace,
    _pk,  # noqa: PLC2701  — shared column helper, intra-package.
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


class GitHubInstallation(Base):
    """A workspace's link to a "Ship" GitHub App installation.

    ``installation_id`` is the only stable identifier GitHub gives us across
    webhook deliveries and API calls, so it's the one we look up by and
    enforce uniqueness on. ``account_login`` / ``account_type`` are mirrored
    here purely for UI ("Connected to org @acme") — never trust them as a
    security boundary, treat them as cache that may go stale on rename.
    """

    __tablename__ = "github_installations"
    __table_args__ = (
        # Same App installation can only be linked to one workspace at a
        # time. If the user reinstalls into a different workspace we will
        # update the existing row rather than insert a duplicate (see
        # callback handler).
        UniqueConstraint(
            "installation_id", name="uq_github_installations_installation_id"
        ),
        Index(
            "ix_github_installations_workspace_id",
            "workspace_id",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    # GitHub's numeric installation id. Stored as BigInteger because the
    # public space already exceeds 2^31 in 2024.
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Numeric id of the user/org the App is installed on (also exceeds
    # 2^31). NULL is permitted only for legacy backfill rows; new installs
    # always populate it.
    account_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    account_login: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # ``User`` for personal accounts, ``Organization`` for orgs. Used to
    # decide whether to show org-only UI controls.
    account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Repository selection chosen at install-time: ``all`` or ``selected``.
    # ``selected_repositories`` (an array of repo full-names) is mirrored
    # under ``settings`` for UI; the source of truth is always GitHub's
    # installation API at request time.
    repository_selection: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )

    # Free-form JSON for things we don't want to add columns for yet
    # (selected repo full-names, suspended_at, target webhook url override).
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    installed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspace: Mapped[Workspace] = relationship()


class WorkspaceRepo(Base):
    """A repository the workspace has *activated* for Ship pipelines.

    Day-2 of the pilot adds this table so the wizard can persist the
    user's repo picks across sessions and so default-pipeline creation
    (Day 3) has something concrete to key off.

    The row is intentionally a *snapshot*: ``full_name`` /
    ``default_branch`` / ``private`` are mirrored from the vendor at
    activate time and refreshed on subsequent picker visits. The vendor
    is always the source of truth — we never resolve permissions or
    visibility off this row alone. ``external_id`` (the vendor's numeric
    id) is what we de-dupe on, so a rename on the vendor side won't
    accidentally create a duplicate activation.

    The ``installation_id`` FK points at our internal
    :class:`GitHubInstallation` row (UUID), not at GitHub's numeric
    installation_id, so cascading deletes work the moment the user
    uninstalls the App.
    """

    __tablename__ = "workspace_repos"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "external_id",
            name="uq_workspace_repos_external",
        ),
        Index("ix_workspace_repos_workspace_id", "workspace_id"),
        Index("ix_workspace_repos_installation_id", "installation_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Nullable to leave room for non-GitHub providers (paste-URL, GitLab
    # via PAT in the legacy flow) — but for the pilot every WorkspaceRepo
    # currently has an installation backing it.
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=True,
    )

    # ``github`` for the pilot. Future: ``gitlab``, ``ado``.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Vendor's numeric repo id. Opaque to us; canonical de-dupe key.
    external_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("'main'")
    )
    private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspace: Mapped[Workspace] = relationship()


__all__ = ["GitHubInstallation", "WorkspaceRepo"]
