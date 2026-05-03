"""workspace_policies.applies_to_roles: per-role policy scoping.

Revision ID: 0049_policy_role_scope
Revises: 0048_telegram_link
Create Date: 2026-05-03

Adds an optional role-scope column so a workspace policy can target a
specific set of specialist role slugs instead of every agent run in the
workspace. Without this column the prose-rule injection has been
all-or-nothing: a rule meant only for the developer role still showed
up in BA / intake / QA preambles. With it, the seeded defaults
extracted from ``artifacts/patterns/role-*/ARTIFACT.md`` can land at
the right roles only.

Semantics: ``applies_to_roles IS NULL`` (the default) keeps the legacy
behaviour — the rule renders globally for every role and for the
Navigator chat. A non-empty array scopes the rule to the listed slugs
(e.g. ``{developer}``). An empty array is treated as global too,
so the renderer's filter handles both NULL and ``cardinality=0`` as
"render for everyone" — cheaper than a CHECK constraint and forgiving
of admin edits that empty the array.

No data backfill: every existing ``workspace_policies`` row is admin-
authored and was already global, which is what NULL preserves. Seed
defaults that need a non-NULL scope land separately in step 5
(``policies_seed.py``).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0049_policy_role_scope"
down_revision: Union[str, None] = "0048_telegram_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_policies",
        sa.Column(
            "applies_to_roles",
            postgresql.ARRAY(sa.String(length=64)),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_policies", "applies_to_roles")
