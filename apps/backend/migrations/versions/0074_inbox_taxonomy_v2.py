"""Inbox taxonomy v2 — category columns + historical blocker backfill (ELS-143).

Adds v2 taxonomy columns on ``inbox_items`` (``category``, ``priority``,
``auto_resolvable``, ``stale_after``, ``headline``), maps every legacy
``type`` to a CHECK-constrained category, auto-closes historical
``agent_run_blocked`` blocker noise via ``audit_log``, and extends
``run_escalations`` with resolution columns so runtime
``_close_run_escalations`` can persist real UPDATEs.

Revision ID: 0074_inbox_taxonomy_v2
Revises: 0073_local_memory_adapters
Create Date: 2026-05-18

Downgrade drops the new columns and CHECK/indexes. Status backfill
(``auto_recovered`` / ``stale``) is **not** reversed — matching
``0032_inbox_backfill_legacy`` honesty.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0074_inbox_taxonomy_v2"
down_revision: Union[str, None] = "0073_local_memory_adapters"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None

_INBOX_CATEGORY_CHECK = "ck_inbox_items_category"
_INBOX_TYPES = (
    "clarification",
    "approval",
    "improvement",
    "exception",
    "stuck",
    "failure",
    "blocker",
    "report",
)


def upgrade() -> None:
    # --- inbox_items: taxonomy columns --------------------------------------
    op.add_column(
        "inbox_items",
        sa.Column(
            "category",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'attention'"),
        ),
    )
    op.add_column(
        "inbox_items",
        sa.Column(
            "priority",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("50"),
        ),
    )
    op.add_column(
        "inbox_items",
        sa.Column(
            "auto_resolvable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "inbox_items",
        sa.Column("stale_after", sa.Interval(), nullable=True),
    )
    op.add_column(
        "inbox_items",
        sa.Column("headline", sa.String(length=80), nullable=True),
    )
    op.create_check_constraint(
        _INBOX_CATEGORY_CHECK,
        "inbox_items",
        "category IN ("
        "'decision_needed', 'attention', 'failure', 'dismiss_silently'"
        ")",
    )
    op.create_index(
        "ix_inbox_workspace_category_status",
        "inbox_items",
        ["workspace_id", "category", "status"],
    )

    # --- type → category / priority (explicit map; no silent default) -------
    op.execute(
        sa.text(
            """
            UPDATE inbox_items AS i
               SET category = m.category,
                   priority = m.priority,
                   auto_resolvable = m.auto_resolvable,
                   headline = LEFT(i.title, 80)
              FROM (
                VALUES
                  ('clarification', 'decision_needed', 20::smallint, false),
                  ('approval',      'decision_needed', 20::smallint, false),
                  ('improvement',   'attention',         50::smallint, false),
                  ('exception',     'attention',         50::smallint, false),
                  ('stuck',         'attention',         50::smallint, false),
                  ('failure',       'failure',           30::smallint, false),
                  ('blocker',       'failure',           30::smallint, false),
                  ('report',        'dismiss_silently',  90::smallint, false)
              ) AS m(type, category, priority, auto_resolvable)
             WHERE i.type = m.type
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            DO $$
            DECLARE
              bad_type text;
            BEGIN
              SELECT i.type INTO bad_type
                FROM inbox_items i
               WHERE i.type NOT IN ({", ".join(repr(t) for t in _INBOX_TYPES)})
               LIMIT 1;
              IF bad_type IS NOT NULL THEN
                RAISE EXCEPTION
                  'inbox_taxonomy_v2: unmapped inbox type %', bad_type;
              END IF;
            END $$;
            """
        )
    )

    # --- auto-recovered agent_run_blocked blockers (audit_log join) ----------
    op.execute(
        sa.text(
            """
            WITH recovery AS (
                SELECT
                    i.id AS item_id,
                    MIN(a.created_at) AS recovered_at
                  FROM inbox_items i
                  JOIN audit_log a
                    ON a.workspace_id = i.workspace_id
                   AND a.action = 'agent_run.finish'
                   AND a.payload->>'outcome' = 'ready_next_step'
                   AND (
                         a.target_id = i.payload->>'ticket_ref'
                      OR a.payload->>'ticket_ref' = i.payload->>'ticket_ref'
                       )
                   AND a.payload->>'fsm_stage' = i.payload->>'fsm_stage'
                   AND a.created_at > i.created_at
                 WHERE i.type = 'blocker'
                   AND i.intake_reason = 'agent_run_blocked'
                   AND i.status = 'new'
                   AND i.payload ? 'ticket_ref'
                   AND i.payload ? 'fsm_stage'
                 GROUP BY i.id
            )
            UPDATE inbox_items i
               SET status = 'resolved',
                   resolution = 'auto_recovered',
                   resolved_at = r.recovered_at,
                   auto_resolvable = true,
                   category = 'failure'
              FROM recovery r
             WHERE i.id = r.item_id
               AND i.status = 'new'
            """
        )
    )

    # --- stale blockers (>24h, no recovery, still new) ------------------------
    op.execute(
        sa.text(
            """
            UPDATE inbox_items
               SET status = 'dismissed',
                   resolution = 'stale'
             WHERE type = 'blocker'
               AND intake_reason = 'agent_run_blocked'
               AND status = 'new'
               AND created_at < now() - interval '24 hours'
            """
        )
    )

    # --- run_escalations: resolution columns --------------------------------
    op.add_column(
        "run_escalations",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "run_escalations",
        sa.Column("resolution", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "run_escalations",
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE run_escalations re
               SET resolved_at = i.resolved_at,
                   resolution = i.resolution,
                   resolved_by_user_id = i.resolved_by_user_id
              FROM inbox_items i
             WHERE re.inbox_item_id = i.id
               AND i.status IN ('resolved', 'dismissed')
               AND i.resolution IN ('auto_recovered', 'stale')
               AND re.resolved_at IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("run_escalations", "resolved_by_user_id")
    op.drop_column("run_escalations", "resolution")
    op.drop_column("run_escalations", "resolved_at")

    op.drop_index("ix_inbox_workspace_category_status", table_name="inbox_items")
    op.drop_constraint(_INBOX_CATEGORY_CHECK, "inbox_items", type_="check")
    op.drop_column("inbox_items", "headline")
    op.drop_column("inbox_items", "stale_after")
    op.drop_column("inbox_items", "auto_resolvable")
    op.drop_column("inbox_items", "priority")
    op.drop_column("inbox_items", "category")
