"""Tenancy and identity tables (RFC-0006, v1 schema).

The hierarchy is ``Org → Workspace → Project``; everything tenant-scoped from
later RFCs (artifacts, documents, telemetry, audit) carries a
``workspace_id`` foreign key from this module.
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
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def _ts_created() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


def _ts_updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Local-mode password hash; null for users who only authenticate via OAuth.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # OIDC subject (e.g. Auth0 `sub`: "auth0|abc123", "google-oauth2|xyz").
    # Populated lazily on first JIT login when SHIP_AUTH_MODE=auth0; lets us
    # reattach an existing email-based row to a new IdP identity without
    # resetting workspace memberships.
    external_subject: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    org_memberships: Mapped[list["OrgMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    workspace_memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    api_tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Org / Workspace / Project
# ---------------------------------------------------------------------------


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'free'"))
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )
    members: Mapped[list["OrgMember"]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )


class OrgMember(Base):
    __tablename__ = "org_members"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_members_org_id_user_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # org_owner | org_admin | billing
    role: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = _ts_created()

    org: Mapped[Org] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="org_memberships")


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_workspaces_org_id_slug"),
    )

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # `{ "global": true, "workspace": true, "project": true }` — toggled per
    # workspace; air-gapped enterprises disable `global`.
    catalog_sources: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            "'{\"global\": true, \"workspace\": true, \"project\": true}'::jsonb"
        ),
    )
    # Workspace-level default for per-state ``default_agent_profile`` —
    # NULL means the operator hasn't picked one yet; the /process editor
    # gates all edits on a non-NULL value here. Validated against the
    # frozenset in ``backend.app.api.v1.routes.repos._PROCESS_AGENT_PROFILES``.
    default_agent_profile: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    org: Mapped[Org] = relationship(back_populates="workspaces")
    members: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    artifact_repos: Mapped[list["ArtifactRepo"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    integrations: Mapped[list["Integration"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_members_workspace_id_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # owner | admin | maintainer | member | viewer
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # Specialist queues (BA, QA, …) this member may answer; ["*"] = all.
    answer_specialist_slugs: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        insert_default=list,
    )

    created_at: Mapped[datetime] = _ts_created()

    workspace: Mapped[Workspace] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="workspace_memberships")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "slug", name="uq_projects_workspace_id_slug"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # linear | jira | github | spreadsheet | custom
    tracker_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspace: Mapped[Workspace] = relationship(back_populates="projects")


# ---------------------------------------------------------------------------
# Artifact source repos (one workspace can pull from many)
# ---------------------------------------------------------------------------


class ArtifactRepo(Base):
    __tablename__ = "artifact_repos"

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # workspace | project | global-mirror
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    default_branch: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("'main'")
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspace: Mapped[Workspace] = relationship(back_populates="artifact_repos")


# ---------------------------------------------------------------------------
# Auth tokens & integrations
# ---------------------------------------------------------------------------


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        Index("ix_api_tokens_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # If non-null, the token is scoped to a single workspace; otherwise it can
    # see every workspace the user is a member of.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # SHA-256 of the secret; the plaintext is shown to the user exactly once
    # at mint time and never stored.
    hashed_secret: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # Optional short prefix for UI display ("ship_pat_…").
    prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()

    user: Mapped[User] = relationship(back_populates="api_tokens")


class Integration(Base):
    """A workspace- or repo-scoped connection to a third-party service.

    ``repo_id`` is ``NULL`` for workspace-level integrations (Notion
    knowledge store, OTLP sink, slack/teams notifications — anything
    that's global to the tenant). It is set for per-repo integrations,
    today only tracker kinds (``linear`` / ``jira`` / ``github``)
    where the wizard asks "where do tickets for this repo live".

    Uniqueness is enforced by two partial indexes created in
    :mod:`backend.migrations.versions.0019_repo_scoped_integrations`:

    - ``uq_integrations_ws_kind_global`` — ``(workspace_id, kind)``
      WHERE ``repo_id IS NULL``;
    - ``uq_integrations_ws_repo_kind`` — ``(workspace_id, repo_id, kind)``
      WHERE ``repo_id IS NOT NULL``.

    This lets a workspace default (``repo_id = NULL``) coexist with a
    per-repo override (``repo_id = <uuid>``) for the same ``kind``
    — callers that need "what tracker does repo X use" should look
    up the per-repo row first and fall back to the workspace-level
    row when absent.
    """

    __tablename__ = "integrations"
    # Uniqueness comes from two partial unique indexes — see the
    # migration. SQLAlchemy doesn't need to know about them for ORM
    # operations (we don't UPSERT by constraint name), and modelling
    # them here would require ``postgresql_where=`` Index entries
    # that every non-Postgres backend (unit tests etc.) would have
    # to special-case.
    __table_args__ = (
        Index("ix_integrations_repo_id", "repo_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL → workspace-scoped. UUID → per-repo (today: tracker only).
    # ON DELETE CASCADE so uninstalling / deleting a repo doesn't
    # leave orphaned tracker rows pointing at a dead id.
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=True,
    )
    # linear | jira | github | slack | otlp | webhook | s3-export | …
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    secret_ciphertext: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    last_health_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    workspace: Mapped[Workspace] = relationship(back_populates="integrations")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_workspace_id_created_at", "workspace_id", "created_at"),
        Index("ix_audit_log_actor_user_id", "actor_user_id"),
    )

    # Use BigInteger autoincrement so audit insertion never blocks on uuid
    # generation, and the natural order is by `id` even when clocks skew.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = _ts_created()


# ---------------------------------------------------------------------------
# Team invites (B7)
# ---------------------------------------------------------------------------


class WorkspaceInvite(Base):
    """Pending (or accepted / revoked) invitation into a workspace.

    The WOW onboarding promise is "invite the whole team with one
    bulk submit" — in practice that's one admin pasting a list of
    emails and forwarding the resulting accept-URLs out-of-band
    (email, Slack, etc.). We don't ship an email sender in the
    pilot; the UI surfaces the accept-URL exactly once so the
    admin can copy it.

    Lifecycle:

    - ``created_at`` → ``revoked_at`` — admin clicked revoke.
    - ``created_at`` → ``accepted_at`` — invitee logged in and
      POSTed ``/v1/invites/{token}/accept``. A
      :class:`WorkspaceMember` row is created in the same
      transaction.
    - ``expires_at`` is a hard wall — past it, the accept endpoint
      returns 410 Gone.

    Tokens are persisted as a **SHA-256 hash** only; the plaintext
    value is returned exactly once to the caller that created the
    invite. Matches the :class:`AccessToken` pattern so a DB leak
    doesn't turn into an "anyone can join any workspace" incident.
    """

    __tablename__ = "workspace_invites"
    __table_args__ = (
        Index("ix_workspace_invites_workspace_id", "workspace_id"),
        Index("ix_workspace_invites_email", "email"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, unique=True
    )
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts_created()


# ---------------------------------------------------------------------------
# Platform invites (E08)
# ---------------------------------------------------------------------------


class PlatformInvite(Base):
    """Platform-wide invite token for closed-beta onboarding.

    Each invite is a one-time-use token that creates a new ``User`` row
    when accepted. Tokens are persisted as a **SHA-256 hash** only; the
    plaintext value is returned exactly once to the caller that created
    the invite.

    Lifecycle:

    - ``created_at`` → ``revoked_at`` — platform admin clicked revoke.
    - ``created_at`` → ``accepted_at`` — invitee logged in and POSTed
      ``/v1/platform/invites/{token}/accept``. A new :class:`User`
      row is created in the same transaction, and this record is linked
      via ``accepted_by_user_id``.
    - ``expires_at`` is a hard wall — past it, the accept endpoint
      returns 410 Gone.

    Non-unique email index allows the same email to be re-invited after
    expiry or revocation.
    """

    __tablename__ = "platform_invites"
    __table_args__ = (
        Index("ix_platform_invites_email", "email"),
    )

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_created()
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String, nullable=True)


class WaitlistSubmission(Base):
    """Public waitlist submission from landing site (E08 T05).

    Each row represents a user who filled out the "Request closed-beta access"
    form on the landing site. Used to build an initial list of potential
    early-access customers; periodic admin reviews convert promising
    submissions into ``PlatformInvite`` rows.

    Columns:

    - ``id`` — UUID primary key
    - ``email`` — invitee email (required)
    - ``role`` — user's role (nullable: "Product owner", "Engineering manager",
      "Tech lead", "Other")
    - ``tracker`` — current tracker in use (nullable: "Linear", "GitHub Issues",
      "Jira", "Notion", "None/Other")
    - ``agent`` — current code agent/IDE plugin (nullable: "Cursor", "Claude Code",
      "Codex", "Copilot", "Other", "None")
    - ``note`` — free-text answer to "what would Ship help with?" (nullable,
      max 500 chars enforced at API layer)
    - ``created_at`` — submission timestamp
    """

    __tablename__ = "waitlist_submissions"
    __table_args__ = (
        Index("ix_waitlist_submissions_email", "email"),
    )

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    tracker: Mapped[str | None] = mapped_column(String, nullable=True)
    agent: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = _ts_created()
