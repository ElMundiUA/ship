"""Per-repo integrations + long-lived repo callback token (Wizard v2 — Iter 1).

Revision ID: 0019_repo_scoped_integrations
Revises: 0018_lanes
Create Date: 2026-04-21

Wizard-v2 (the full-loop onboarding overhaul) wants two things that
aren't expressible in today's schema:

1. **Per-repo integrations.** Up to now ``integrations`` was keyed by
   ``(workspace_id, kind)`` — i.e. one Linear connection per workspace.
   That's fine for workspace-level things (Notion knowledge store,
   OTLP sink) but blocks the "repo A → Linear team X, repo B → Jira
   project Y" story the wizard now wants.

   We could have split into a new ``repo_integrations`` table, but
   that forks every piece of integration code (OAuth callbacks,
   secret rotation, health probes, audit) right at the source. So
   instead we **extend** the existing table: add a nullable ``repo_id``
   FK and swap the single unique to **two partial uniques**:

     - ``uq_integrations_ws_kind_global`` on ``(workspace_id, kind)``
       WHERE ``repo_id IS NULL`` — workspace-scoped rows (Notion, OTLP,
       slack, s3-export, …) keep their original uniqueness.
     - ``uq_integrations_ws_repo_kind`` on
       ``(workspace_id, repo_id, kind)`` WHERE ``repo_id IS NOT NULL``
       — per-repo rows (tracker picks) can't collide with themselves
       but can coexist with a workspace-level row of the same kind
       (e.g. workspace default tracker + repo override).

   This preserves back-compat (every existing row has
   ``repo_id = NULL`` after upgrade) and unlocks per-repo rows without
   a data migration.

2. **Long-lived repo callback token.** Today's ``SHIP_RUN_TOKEN`` is a
   per-``PipelineRun`` JWT injected via ``workflow_dispatch`` inputs
   (see ``pipelines._mint_run_token``). RFC-0007 lanes trigger on
   cron / push / PR — they have no ``inputs`` channel and need a
   **persistent** secret they can read with ``secrets.SHIP_RUN_TOKEN``.

   We don't want to store the plaintext token anywhere; instead we
   mint it, push it into GitHub Actions encrypted secrets via the
   existing ``put_repo_secret`` path, and persist **only the sha256
   hash** on the repo row so callback handlers can verify a
   presented token with one lookup.

   ``run_token_hash`` is nullable so every pre-wizard-v2 repo comes
   out of the migration with no token — the wizard's seed step is
   what mints and pushes the first one. Rotation re-hashes the same
   column; the plaintext only ever lives in memory for the duration
   of the PUT to GitHub's secrets API.

Downgrade is destructive for both additions but preserves every
``integrations`` row (``repo_id`` dropped, original unique restored).
The repo rows keep their ``id`` / ``full_name`` — only the token
hash is lost.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0019_repo_scoped_integrations"
down_revision: Union[str, None] = "0018_lanes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # integrations.repo_id + partial uniques
    # ---------------------------------------------------------------
    op.add_column(
        "integrations",
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_integrations_repo_id",
        "integrations",
        ["repo_id"],
    )

    # Drop the old single-column unique; partial indexes below replace
    # it with equivalent (and then some) coverage.
    op.drop_constraint(
        "uq_integrations_workspace_id_kind",
        "integrations",
        type_="unique",
    )

    # Workspace-scoped row: at most one (workspace, kind) without a
    # repo binding. Preserves the legacy semantics for Notion / OTLP /
    # slack / s3-export etc.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_integrations_ws_kind_global
          ON integrations (workspace_id, kind)
          WHERE repo_id IS NULL
        """
    )
    # Per-repo row: one (workspace, repo, kind) when a repo is bound.
    # Lets "repo A → Linear, repo B → Jira" (or repo A + workspace
    # default) coexist. Partial form means the NULL side isn't forced
    # to carry a dummy value for every pre-wizard-v2 row.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_integrations_ws_repo_kind
          ON integrations (workspace_id, repo_id, kind)
          WHERE repo_id IS NOT NULL
        """
    )

    # ---------------------------------------------------------------
    # workspace_repos.run_token_hash
    # ---------------------------------------------------------------
    op.add_column(
        "workspace_repos",
        sa.Column(
            # sha256 hex → 64 chars. Stored as CHAR(64) would save a
            # few bytes but String keeps us flexible for a future
            # scheme bump (argon2 prefix, per-repo salt, …).
            "run_token_hash",
            sa.String(128),
            nullable=True,
        ),
    )
    op.add_column(
        "workspace_repos",
        sa.Column(
            # Short prefix (first 8 chars) of the token's hash so the
            # dashboard can show "SHIP_RUN_TOKEN rotated 2h ago ·
            # a1b2c3d4" without needing access to plaintext. Also
            # helps support debug "does the secret in the repo match
            # what Ship thinks it should be?" queries without
            # disclosing the hash.
            "run_token_prefix",
            sa.String(16),
            nullable=True,
        ),
    )
    op.add_column(
        "workspace_repos",
        sa.Column(
            "run_token_rotated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_repos", "run_token_rotated_at")
    op.drop_column("workspace_repos", "run_token_prefix")
    op.drop_column("workspace_repos", "run_token_hash")

    op.execute("DROP INDEX IF EXISTS uq_integrations_ws_repo_kind")
    op.execute("DROP INDEX IF EXISTS uq_integrations_ws_kind_global")

    # Re-adding the original unique only works if there is at most
    # one row per (workspace_id, kind) with ``repo_id IS NULL``. The
    # partial indexes above enforced exactly that, so the downgrade
    # is safe as long as no caller populated ``repo_id`` on any row.
    op.create_unique_constraint(
        "uq_integrations_workspace_id_kind",
        "integrations",
        ["workspace_id", "kind"],
    )

    op.drop_index("ix_integrations_repo_id", table_name="integrations")
    op.drop_column("integrations", "repo_id")
