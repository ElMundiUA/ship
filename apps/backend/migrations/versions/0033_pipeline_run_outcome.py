"""pipeline_runs.outcome JSONB (RFC-0010 §RunSummary contract — P3-01).

Adds a single ``outcome JSONB NOT NULL DEFAULT '{}'::jsonb`` column to
``pipeline_runs`` so RFC-0010's RunSummary contract has a structured
home that's separate from the loose ``payload`` JSONB and the legacy
``summary`` string.

Why a new column rather than reusing ``payload``:

- ``payload`` is the existing free-form bag the run-callback tucks
  arbitrary client metrics into (``payload['metrics'][...]``); we
  want the structured RunSummary shape to live somewhere with a
  documented contract so the FE list / detail surfaces and the
  Inbox intake pipeline can rely on it without defensive lookups.
- The Pydantic schema (``backend.app.api.v1.routes.pipelines.RunSummary``)
  uses ``extra='forbid'``, so co-locating it with ``payload`` would
  force an awkward "validate this nested key, ignore everything
  else" carve-out. A dedicated column keeps both sides honest.

Backfill: none required. The column defaults to ``{}::jsonb`` for
both pre-existing and future-inserted rows that don't carry an
explicit outcome (callers using the pre-P3-01 callback shape stay
happy).

Revision ID: 0033_pipeline_run_outcome
Revises: 0032_inbox_backfill_legacy
Create Date: 2026-04-24

Downgrade drops the column outright. Because the field is purely
additive and never written from the legacy callers, no data needs
preserving on downgrade.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0033_pipeline_run_outcome"
down_revision: Union[str, None] = "0032_inbox_backfill_legacy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "outcome",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "outcome")
