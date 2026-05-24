"""workspace_repo_routing — explicit project→repo dispatch routing.

Replaces the brittle name-heuristic + oldest-activated fallback in
``dispatcher._pick_dispatch_repo`` with an explicit binding. Each row maps
a Linear project (``project_native_id``) to the repo its tickets dispatch
into; a single row with ``project_native_id IS NULL`` is the workspace
default (where unbound / projectless tickets land). No binding + no
default → the dispatcher files a clarification instead of dumping work on
a random repo. Caught on askslayer/visitor-web 2026-05-24: projectless
infra tickets (PAC-32..36) fell back to the oldest-activated repo, which
lacked CURSOR_API_KEY, so every run died and froze the whole workspace.

Two partial-unique indexes enforce the shape (a plain UNIQUE would let
multiple NULL-project rows coexist):
  - one default row per workspace (project_native_id IS NULL),
  - one binding per (workspace, project) (project_native_id IS NOT NULL).

Revision ID: 0079_workspace_repo_routing
Revises: 0078_project_done_state
Create Date: 2026-05-24

Note: revision id kept <=32 chars (``alembic_version.version_num`` is
``varchar(32)``).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0079_workspace_repo_routing"
down_revision: Union[str, None] = "0078_project_done_state"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None

_TABLE = "workspace_repo_routing"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NULL == workspace default (catch-all for unbound/projectless
        # tickets). Non-null == per-project binding.
        sa.Column("project_native_id", sa.Text(), nullable=True),
        sa.Column(
            "repo_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_workspace_repo_routing_ws", _TABLE, ["workspace_id"]
    )
    # One default per workspace.
    op.create_index(
        "uq_workspace_repo_routing_default",
        _TABLE,
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("project_native_id IS NULL"),
    )
    # One binding per (workspace, project).
    op.create_index(
        "uq_workspace_repo_routing_project",
        _TABLE,
        ["workspace_id", "project_native_id"],
        unique=True,
        postgresql_where=sa.text("project_native_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_workspace_repo_routing_project", table_name=_TABLE)
    op.drop_index("uq_workspace_repo_routing_default", table_name=_TABLE)
    op.drop_index("ix_workspace_repo_routing_ws", table_name=_TABLE)
    op.drop_table(_TABLE)
