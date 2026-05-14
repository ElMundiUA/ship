"""ORM models for the laptop-offline Memory adapters (E19).

Three workspace-scoped families:

- ``memory_tracker_*`` — projects + tickets + comments. Backs
  :class:`backend.app.integrations.local.tracker.MemoryTracker`.
- ``memory_git_*`` — repos + file snapshots + pull requests. Backs
  :class:`backend.app.integrations.local.code_host.MemoryCodeHost`.
- ``memory_ci_runs`` — workflow runs with scheduled state
  transitions. Backs :class:`backend.app.integrations.local.ci.MemoryCi`.

Each table is workspace-scoped and self-contained — none of the
existing tracker / repo / audit columns reference these rows. The
adapters speak the canonical gateway protocols, so callers can't
tell whether they got a memory adapter or a real Linear/Jira/etc.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class MemoryTrackerProject(Base):
    __tablename__ = "memory_tracker_projects"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "slug", name="uq_memory_tracker_projects_ws_slug"
        ),
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
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MemoryTrackerTicket(Base):
    __tablename__ = "memory_tracker_tickets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "display_id",
            name="uq_memory_tracker_tickets_ws_display",
        ),
        UniqueConstraint(
            "workspace_id", "serial",
            name="uq_memory_tracker_tickets_ws_serial",
        ),
        Index(
            "ix_memory_tracker_tickets_ws_state",
            "workspace_id", "state",
        ),
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
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_tracker_projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_id: Mapped[str] = mapped_column(String(32), nullable=False)
    serial: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    state: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'Todo'")
    )
    ticket_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    labels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        insert_default=list,
    )
    assignee_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MemoryTrackerComment(Base):
    __tablename__ = "memory_tracker_comments"
    __table_args__ = (
        Index(
            "ix_memory_tracker_comments_ticket_created",
            "ticket_id", "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_tracker_tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ---------------------------------------------------------------------------
# Code host
# ---------------------------------------------------------------------------


class MemoryGitRepo(Base):
    __tablename__ = "memory_git_repos"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "owner", "name",
            name="uq_memory_git_repos_ws_owner_name",
        ),
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
    owner: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    default_branch: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=text("'main'")
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MemoryGitFile(Base):
    __tablename__ = "memory_git_files"
    __table_args__ = (
        UniqueConstraint(
            "repo_id", "ref", "path",
            name="uq_memory_git_files_repo_ref_path",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_git_repos.id", ondelete="CASCADE"),
        nullable=False,
    )
    ref: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=text("'main'")
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MemoryGitPullRequest(Base):
    __tablename__ = "memory_git_prs"
    __table_args__ = (
        UniqueConstraint(
            "repo_id", "number", name="uq_memory_git_prs_repo_number"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_git_repos.id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    head: Mapped[str] = mapped_column(String(255), nullable=False)
    base: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'")
    )
    draft: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    merged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------


class MemoryCiRun(Base):
    __tablename__ = "memory_ci_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_git_repos.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'queued'")
    )
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logs: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    transition_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
