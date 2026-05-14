"""E19/Local-dev — Memory tracker / code-host / CI adapters.

Backs the laptop-offline profile with workspace-scoped tables that
the ``backend.app.integrations.local.*`` adapters read and write.
Mirrors what Linear / GitHub Issues / GitHub Actions would store
externally — but in our own Postgres so a developer can:

- bring up `make dev-up` and immediately have demo tickets to play with,
- run the whole orchestrator (picker → dispatcher → finish hooks)
  without an external account,
- run e2e specs against a deterministic local fixture instead of
  paying for fresh Linear / GitHub OAuth dances.

The tables are workspace-scoped and self-contained — no FKs to the
real tracker columns on ``projects`` / ``audit_log`` / etc. The
adapters speak the existing gateway protocols (TrackerGateway /
CodeHostGateway / CIGateway), so nothing else in the codebase has
to know whether it's talking to "real Linear" or "memory tracker".

Revision ID: 0073_local_memory_adapters
Revises: 0072_chat_threads_last_retrieved
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0073_local_memory_adapters"
down_revision: Union[str, None] = "0072_chat_threads_last_retrieved"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # Tracker — projects (epics) + tickets + comments
    # -----------------------------------------------------------------
    op.create_table(
        "memory_tracker_projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        # active | completed | backlog — same vocabulary the Linear
        # adapter exposes upstream.
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "slug", name="uq_memory_tracker_projects_ws_slug"
        ),
    )

    op.create_table(
        "memory_tracker_tickets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "memory_tracker_projects.id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        # Human-readable id minted per workspace: MEM-1, MEM-2, …
        # Auto-assigned by the adapter on insert (MAX(serial)+1 within
        # the workspace).
        sa.Column("display_id", sa.String(32), nullable=False),
        sa.Column("serial", sa.Integer, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        # Vendor-style state name (Todo / In Progress / Done) — the
        # picker filters by ``state`` + ``stage:*`` label, same as
        # Linear.
        sa.Column(
            "state",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'Todo'"),
        ),
        sa.Column("ticket_type", sa.String(32), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("assignee_email", sa.String(320), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "display_id", name="uq_memory_tracker_tickets_ws_display"
        ),
        sa.UniqueConstraint(
            "workspace_id", "serial", name="uq_memory_tracker_tickets_ws_serial"
        ),
        sa.Index(
            "ix_memory_tracker_tickets_ws_state",
            "workspace_id",
            "state",
        ),
    )

    op.create_table(
        "memory_tracker_comments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "ticket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "memory_tracker_tickets.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Index(
            "ix_memory_tracker_comments_ticket_created",
            "ticket_id",
            "created_at",
        ),
    )

    # -----------------------------------------------------------------
    # Code host — repos, files snapshots, pull requests
    # -----------------------------------------------------------------
    op.create_table(
        "memory_git_repos",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "default_branch",
            sa.String(120),
            nullable=False,
            server_default=sa.text("'main'"),
        ),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "private",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "owner", "name",
            name="uq_memory_git_repos_ws_owner_name",
        ),
    )

    op.create_table(
        "memory_git_files",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_git_repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ref",
            sa.String(120),
            nullable=False,
            server_default=sa.text("'main'"),
        ),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "repo_id", "ref", "path",
            name="uq_memory_git_files_repo_ref_path",
        ),
    )

    op.create_table(
        "memory_git_prs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_git_repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("head", sa.String(255), nullable=False),
        sa.Column("base", sa.String(255), nullable=False),
        sa.Column(
            "state",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "draft",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "merged",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "repo_id", "number", name="uq_memory_git_prs_repo_number"
        ),
    )

    # -----------------------------------------------------------------
    # CI — workflow runs (mirrors GitHub Actions run row shape)
    # -----------------------------------------------------------------
    op.create_table(
        "memory_ci_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_git_repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_name", sa.String(200), nullable=False),
        # queued | in_progress | completed
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        # success | failure | cancelled | skipped — null while in_progress
        sa.Column("conclusion", sa.String(32), nullable=True),
        sa.Column("branch", sa.String(255), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("logs", sa.Text(), nullable=False, server_default=sa.text("''")),
        # When the run should auto-transition queued → in_progress →
        # completed. The local scheduler reads this column on a tick
        # to walk a run through its lifecycle without an external CI
        # poking back.
        sa.Column("transition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Index(
            "ix_memory_ci_runs_repo_created",
            "repo_id",
            sa.text("created_at DESC"),
        ),
    )


def downgrade() -> None:
    op.drop_table("memory_ci_runs")
    op.drop_table("memory_git_prs")
    op.drop_table("memory_git_files")
    op.drop_table("memory_git_repos")
    op.drop_table("memory_tracker_comments")
    op.drop_table("memory_tracker_tickets")
    op.drop_table("memory_tracker_projects")
