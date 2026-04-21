"""knowledge_buckets: scope + source awareness (Consolidation Phase 1)

Revision ID: 0014_bucket_scope_source
Revises: 0013_clarifications_tracker
Create Date: 2026-04-21

Foundation for the buckets + catalog consolidation: makes
``knowledge_buckets`` hold **any** kind of knowledge bucket, not only
the workspace-scoped agent-memory rollups it was built for.

Adds three orthogonal dimensions (see the architecture doc under
``docs/knowledge-consolidation.md`` — TBD):

- ``scope_kind``   — ``workspace`` | ``project`` | ``repo`` | ``user``.
                     Which tenancy layer the bucket belongs to. Drives
                     visibility/inheritance when the resolver returns a
                     merged list to the navigator / knowledge page.
                     (``global`` is out of Phase 1 — it needs a nullable
                     ``workspace_id`` and that change is deferred until
                     platform-scope buckets actually exist.)
- ``source_kind``  — ``agent_memory`` | ``repo_files`` | ``external_static``
                     | ``connector_proxy`` | ``audio_transcript``.
                     How the bucket's content is obtained. Existing rows
                     are the agent's packed chat memory → ``agent_memory``.
- ``source_ref``   — JSONB pointer into the physical source (e.g.
                     ``{"repo_id": "...", "path": ".ship/knowledge/ui-runbook.md"}``
                     for ``repo_files``; ``{"integration_id": "..."}`` for
                     connectors). Shape is source-kind specific; kept as
                     free-form JSONB so new sources don't require a
                     schema bump each time.

Plus three nullable FKs used by the scope resolver:
``project_id``, ``repo_id``, ``user_id``. They are populated per
scope_kind (a ``scope_kind='repo'`` row carries ``repo_id``, a
``scope_kind='user'`` row carries ``user_id``, and so on). All three
are ``ON DELETE SET NULL`` so removing a project/repo/user archives
the row instead of cascading the deletion — buckets outlive their
carriers.

Uniqueness: the old ``uq_knowledge_buckets_workspace_slug`` was too
tight — it forbids a ``code-style`` bucket from existing on two
different repos in the same workspace. Replaced by **four partial
unique indexes**, one per scope_kind. The filter clauses keep the
constraint out of rows where that combination doesn't apply.

Backfill: every existing row becomes ``scope_kind='workspace'`` +
``source_kind='agent_memory'`` with ``source_ref=NULL``. That's the
current semantics — nothing new appears, only the typing gets
explicit.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0014_bucket_scope_source"
down_revision: Union[str, None] = "0013_clarifications_tracker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Orthogonal "what is this bucket" columns.
    # -----------------------------------------------------------------
    op.add_column(
        "knowledge_buckets",
        sa.Column(
            "scope_kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'workspace'"),
        ),
    )
    op.add_column(
        "knowledge_buckets",
        sa.Column(
            "source_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'agent_memory'"),
        ),
    )
    op.add_column(
        "knowledge_buckets",
        sa.Column("source_ref", postgresql.JSONB(), nullable=True),
    )

    # -----------------------------------------------------------------
    # 2. Scope-carrier FKs (one is populated per scope_kind).
    #    ON DELETE SET NULL — removing a project/repo/user doesn't
    #    take their buckets with them; they archive instead.
    # -----------------------------------------------------------------
    op.add_column(
        "knowledge_buckets",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_buckets",
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_buckets",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # -----------------------------------------------------------------
    # 3. Replace the global unique with scope-aware partials.
    # -----------------------------------------------------------------
    op.drop_constraint(
        "uq_knowledge_buckets_workspace_slug",
        "knowledge_buckets",
        type_="unique",
    )

    op.create_index(
        "uq_knowledge_buckets_workspace_slug",
        "knowledge_buckets",
        ["workspace_id", "slug"],
        unique=True,
        postgresql_where=sa.text("scope_kind = 'workspace'"),
    )
    op.create_index(
        "uq_knowledge_buckets_project_slug",
        "knowledge_buckets",
        ["workspace_id", "project_id", "slug"],
        unique=True,
        postgresql_where=sa.text(
            "scope_kind = 'project' AND project_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_knowledge_buckets_repo_slug",
        "knowledge_buckets",
        ["workspace_id", "repo_id", "slug"],
        unique=True,
        postgresql_where=sa.text(
            "scope_kind = 'repo' AND repo_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_knowledge_buckets_user_slug",
        "knowledge_buckets",
        ["workspace_id", "user_id", "slug"],
        unique=True,
        postgresql_where=sa.text(
            "scope_kind = 'user' AND user_id IS NOT NULL"
        ),
    )

    # -----------------------------------------------------------------
    # 4. Helper indexes for the resolver. The partial uniques above
    #    already cover "one repo's buckets" queries; these give us
    #    "all repo-scoped buckets in workspace" and "all buckets from
    #    a given source" without a full table scan.
    # -----------------------------------------------------------------
    op.create_index(
        "ix_knowledge_buckets_scope_kind",
        "knowledge_buckets",
        ["workspace_id", "scope_kind"],
    )
    op.create_index(
        "ix_knowledge_buckets_source_kind",
        "knowledge_buckets",
        ["workspace_id", "source_kind"],
    )

    # -----------------------------------------------------------------
    # 5. Belt-and-braces CHECK: scope_kind-specific carrier must be set.
    #    workspace → none of project_id/repo_id/user_id
    #    project   → project_id NOT NULL
    #    repo      → repo_id NOT NULL
    #    user      → user_id NOT NULL
    #
    #    Keeps the resolver simple (never has to guess what layer a
    #    row belongs to) without a rigid per-scope table split.
    # -----------------------------------------------------------------
    op.create_check_constraint(
        "ck_knowledge_buckets_scope_carrier",
        "knowledge_buckets",
        (
            "(scope_kind = 'workspace' AND project_id IS NULL AND repo_id IS NULL AND user_id IS NULL) "
            "OR (scope_kind = 'project' AND project_id IS NOT NULL) "
            "OR (scope_kind = 'repo' AND repo_id IS NOT NULL) "
            "OR (scope_kind = 'user' AND user_id IS NOT NULL)"
        ),
    )

    # Existing rows are all agent-memory, workspace-scoped. The
    # server_defaults above covered ``scope_kind`` + ``source_kind``
    # during the ADD COLUMN; nothing to backfill explicitly.


def downgrade() -> None:
    op.drop_constraint(
        "ck_knowledge_buckets_scope_carrier",
        "knowledge_buckets",
        type_="check",
    )
    op.drop_index(
        "ix_knowledge_buckets_source_kind", table_name="knowledge_buckets"
    )
    op.drop_index(
        "ix_knowledge_buckets_scope_kind", table_name="knowledge_buckets"
    )
    op.drop_index(
        "uq_knowledge_buckets_user_slug", table_name="knowledge_buckets"
    )
    op.drop_index(
        "uq_knowledge_buckets_repo_slug", table_name="knowledge_buckets"
    )
    op.drop_index(
        "uq_knowledge_buckets_project_slug", table_name="knowledge_buckets"
    )
    op.drop_index(
        "uq_knowledge_buckets_workspace_slug", table_name="knowledge_buckets"
    )
    op.create_unique_constraint(
        "uq_knowledge_buckets_workspace_slug",
        "knowledge_buckets",
        ["workspace_id", "slug"],
    )
    op.drop_column("knowledge_buckets", "user_id")
    op.drop_column("knowledge_buckets", "repo_id")
    op.drop_column("knowledge_buckets", "project_id")
    op.drop_column("knowledge_buckets", "source_ref")
    op.drop_column("knowledge_buckets", "source_kind")
    op.drop_column("knowledge_buckets", "scope_kind")
