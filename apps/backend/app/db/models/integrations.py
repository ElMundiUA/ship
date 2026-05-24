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
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
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

    ``installation_id`` is GitHub's numeric id for the install — stable
    across webhook deliveries and API calls. Multiple Ship workspaces
    under the same GitHub org (Dev / Staging / Prod) all attach to the
    same ``installation_id``; uniqueness is enforced on the COMPOUND
    ``(workspace_id, installation_id)`` so each workspace gets at most
    one row per install but the same install can appear in N
    workspaces. Webhook fan-out (one delivery → N workspaces) reads
    all rows for the inbound ``installation_id`` and routes a copy of
    the event into each one.

    ``account_login`` / ``account_type`` are mirrored here purely for
    UI ("Connected to org @acme") — never trust them as a security
    boundary, treat them as cache that may go stale on rename.
    """

    __tablename__ = "github_installations"
    __table_args__ = (
        # Compound unique — same install allowed in multiple
        # workspaces, but never duplicated within one workspace.
        # Migration ``0076_github_installations_multi_workspace``
        # replaced the prior single-column ``UniqueConstraint`` on
        # ``installation_id``.
        UniqueConstraint(
            "workspace_id", "installation_id",
            name="uq_github_installations_workspace_installation",
        ),
        # Webhook fan-out reads by ``installation_id`` alone; index
        # keeps that path O(log n) per event.
        Index(
            "ix_github_installations_installation_id",
            "installation_id",
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

    # Phase-2 catalog integration. Pre-P5-01 this column held one of
    # Post-Phase-2.4 every write stores ``"default"`` (or ``NULL``
    # when the binding is cleared). The historical 14-preset
    # collapse helper retired in the deep-dig cleanup — Ship's
    # registry only knows one preset. Treat ``NULL`` as "no
    # explicit preset; use the default-shaped seed".
    preset: Mapped[str | None] = mapped_column(String(64), nullable=True)

    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Long-lived per-repo callback token (Wizard v2). RFC-0007 lanes
    # trigger on cron / push / PR — they can't carry a per-run JWT
    # from ``workflow_dispatch inputs``, so they need a persistent
    # secret they can read with ``secrets.SHIP_RUN_TOKEN``.
    #
    # We never persist the plaintext: minting generates it, pushes
    # it into GitHub Actions encrypted secrets via the App, and
    # stores only the sha256 hex here. Callback handlers verify a
    # presented bearer by re-hashing it and matching ``run_token_hash``.
    # ``run_token_prefix`` mirrors the first few chars of the hash so
    # operators can eyeball "is this the one I just rotated?" without
    # needing the plaintext. ``run_token_rotated_at`` is the audit
    # stamp for "when was the secret last pushed to GitHub".
    #
    # All three are nullable: pre-wizard-v2 repos come out of the
    # migration with none set; the seed step is what mints the
    # first one. A future admin-triggered rotation overwrites all
    # three atomically.
    run_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_token_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    run_token_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Snapshot of ``seed_bundle.BUNDLE_VERSION`` the last time Ship
    # successfully seeded (or re-seeded) this repo. Dashboard uses
    # the drift between this and the current constant to decide
    # whether to show the "update available" CTA on the repo card.
    # ``NULL`` means either never seeded (greenfield wizard state)
    # or seeded before this column existed — the UI surfaces that
    # as "run the wizard" rather than "upgrade".
    installed_bundle_version: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspace: Mapped[Workspace] = relationship()


class NativeIntegrationProvider:
    """Provider ids for first-party integrations owned by Ship backend.

    These are deliberately separate from the legacy ``Integration.kind``
    strings. The legacy row is still useful for shallow tracker settings;
    this surface models installable accounts with bindings, credentials,
    sync cursors, and audit trails.
    """

    GITHUB = "github"
    AZURE_DEVOPS = "azure_devops"
    ATLASSIAN = "atlassian"
    NOTION = "notion"
    LINEAR = "linear"
    GITLAB = "gitlab"

    ALL: tuple[str, ...] = (
        GITHUB,
        AZURE_DEVOPS,
        ATLASSIAN,
        NOTION,
        LINEAR,
        GITLAB,
    )


class NativeIntegrationAuthMode:
    """How Ship obtained credentials for a native provider account."""

    APP = "app"
    OAUTH = "oauth"
    PAT = "pat"

    ALL: tuple[str, ...] = (APP, OAUTH, PAT)


class NativeIntegrationStatus:
    """Lifecycle state shared by native install/binding/sync rows."""

    PENDING = "pending"
    READY = "ready"
    ERROR = "error"
    DISABLED = "disabled"

    ALL: tuple[str, ...] = (PENDING, READY, ERROR, DISABLED)


class NativeIntegrationInstallation(Base):
    """A workspace's first-party connection to one external account.

    Examples:
    - Azure DevOps organization connected with a PAT.
    - Atlassian cloud site that powers Jira + Confluence adapters.
    - Notion workspace connected through OAuth.

    Provider-specific details stay in ``config`` so we can ship one
    durable contract before every adapter's account model is fully known.
    """

    __tablename__ = "native_integration_installations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            name="uq_native_integration_installations_account",
        ),
        Index("ix_native_integration_installations_workspace_id", "workspace_id"),
        Index(
            "ix_native_integration_installations_provider",
            "workspace_id",
            "provider",
        ),
        CheckConstraint(
            "provider IN ('github', 'azure_devops', 'atlassian', "
            "'notion', 'linear', 'gitlab')",
            name="ck_native_integration_installations_provider",
        ),
        CheckConstraint(
            "auth_mode IN ('app', 'oauth', 'pat')",
            name="ck_native_integration_installations_auth_mode",
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'error', 'disabled')",
            name="ck_native_integration_installations_status",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_account_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    external_account_url: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )

    capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    scopes: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    last_health_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspace: Mapped[Workspace] = relationship()


class NativeIntegrationCredential(Base):
    """Encrypted credential material for a native provider installation."""

    __tablename__ = "native_integration_credentials"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "kind",
            name="uq_native_integration_credentials_installation_kind",
        ),
        Index(
            "ix_native_integration_credentials_installation_id",
            "installation_id",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("native_integration_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # access_token | refresh_token | pat | app_private_key | webhook_secret
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scopes: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class NativeIntegrationBinding(Base):
    """A selected provider resource that Ship is allowed to operate on."""

    __tablename__ = "native_integration_bindings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "resource_type",
            "external_id",
            name="uq_native_integration_bindings_resource",
        ),
        Index("ix_native_integration_bindings_workspace_id", "workspace_id"),
        Index(
            "ix_native_integration_bindings_installation_id",
            "installation_id",
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'error', 'disabled')",
            name="ck_native_integration_bindings_status",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("native_integration_installations.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # repo | project | pipeline | jira_project | confluence_space |
    # notion_database | notion_page | linear_team
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    external_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class NativeIntegrationSyncState(Base):
    """Cursor and health state for provider sync loops.

    A row can be installation-wide (``binding_id`` is NULL) or scoped to
    one selected binding. The migration adds partial unique indexes for
    both shapes because SQL NULL semantics cannot express that invariant
    with one portable ``UniqueConstraint``.
    """

    __tablename__ = "native_integration_sync_states"
    __table_args__ = (
        Index(
            "ix_native_integration_sync_states_installation_id",
            "installation_id",
        ),
        Index("ix_native_integration_sync_states_binding_id", "binding_id"),
        CheckConstraint(
            "status IN ('pending', 'ready', 'error', 'disabled')",
            name="ck_native_integration_sync_states_status",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("native_integration_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("native_integration_bindings.id", ondelete="CASCADE"),
        nullable=True,
    )
    # examples: repo_discovery, pipeline_runs, jira_issues, knowledge_pages
    sync_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class NativeIntegrationAuditEvent(Base):
    """Provider action audit trail for CLI/agent-triggered operations."""

    __tablename__ = "native_integration_audit_events"
    __table_args__ = (
        Index(
            "ix_native_integration_audit_events_workspace_created",
            "workspace_id",
            "created_at",
        ),
        Index(
            "ix_native_integration_audit_events_installation_id",
            "installation_id",
        ),
        Index(
            "ix_native_integration_audit_events_actor_user_id",
            "actor_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("native_integration_installations.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = _ts_created()


class WorkspaceRepoRouting(Base):
    """Explicit project→repo dispatch routing.

    Maps a Linear project (``project_native_id``) to the
    :class:`WorkspaceRepo` its tickets dispatch into. A single row with
    ``project_native_id IS NULL`` is the **workspace default** — the
    catch-all for tickets whose project has no binding, or that have no
    project at all. ``dispatcher._pick_dispatch_repo`` resolves
    binding → default → (nothing); on nothing it files a clarification
    rather than dumping work on a random repo.

    Replaces the old name-heuristic + oldest-activated fallback that
    routed askslayer's projectless infra tickets onto a repo missing
    CURSOR_API_KEY (2026-05-24), failing every run and freezing the
    workspace. Shape enforced by two partial-unique indexes (see
    migration 0079): one NULL-project default per workspace, one binding
    per (workspace, project).
    """

    __tablename__ = "workspace_repo_routing"
    __table_args__ = (
        Index("ix_workspace_repo_routing_ws", "workspace_id"),
        Index(
            "uq_workspace_repo_routing_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("project_native_id IS NULL"),
        ),
        Index(
            "uq_workspace_repo_routing_project",
            "workspace_id",
            "project_native_id",
            unique=True,
            postgresql_where=text("project_native_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL == workspace default; non-null == per-project binding.
    project_native_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


__all__ = [
    "GitHubInstallation",
    "NativeIntegrationAuditEvent",
    "NativeIntegrationAuthMode",
    "NativeIntegrationBinding",
    "NativeIntegrationCredential",
    "NativeIntegrationInstallation",
    "NativeIntegrationProvider",
    "NativeIntegrationStatus",
    "NativeIntegrationSyncState",
    "WorkspaceRepo",
    "WorkspaceRepoRouting",
]
