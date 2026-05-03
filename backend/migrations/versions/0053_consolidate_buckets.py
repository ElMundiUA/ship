"""archive retired starter buckets — 10 → 6 consolidation

The recommended-buckets list is collapsing to six (architecture-decisions,
engineering-standards, runbooks-operations, product-knowledge,
integration-playbooks, security-access). The four retired slugs lose
their reason to exist:

- ``project-map`` / ``source-intelligence`` — replaced by runtime
  code-exploration tools (Step 7); static articles for them go stale
  the moment the repo moves.
- ``generated-assets`` — sweep bucket nobody could describe; routing
  into it stopped being meaningful.
- ``data-domain-glossary`` — vocabulary is a section of
  ``product-knowledge``, not a bucket of its own.

This migration:

1. Archives any workspace bucket whose slug is in the retired set
   (``archived_at = now()`` if not already archived).
2. Archives any non-archived ``BucketArticle`` under those buckets so
   ``GET /buckets/{slug}/articles`` stops returning them by default.
3. Cancels any unrouted ``Improvement(kind='knowledge_note')`` rows
   that the harvester pinned to a retired bucket — sets
   ``context.routed_bucket_id = NULL`` so the next router tick can
   re-route them into a live bucket.

The rows are preserved (no DELETE) — operators can resurrect content
manually if a piece turns out to be load-bearing. Down-migration
clears ``archived_at`` for both buckets and articles. The unrouted
notes carry a ``rerouted_from_retired`` flag so the down-migration
can put them back; we don't try to perfectly invert the route flip
because the router will pick a fresh target on the next tick anyway.

Revision ID: 0053_consolidate_buckets
Revises: 0052_drop_pipeline_c
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0053_consolidate_buckets"
down_revision: Union[str, None] = "0052_drop_pipeline_c"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


RETIRED_SLUGS = (
    "project-map",
    "source-intelligence",
    "generated-assets",
    "data-domain-glossary",
)


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            UPDATE bucket_articles ba
               SET status = 'archived',
                   archived_at = COALESCE(ba.archived_at, now())
              FROM knowledge_buckets kb
             WHERE ba.bucket_id = kb.id
               AND kb.slug = ANY(:slugs)
               AND ba.archived_at IS NULL
            """
        ),
        {"slugs": list(RETIRED_SLUGS)},
    )

    bind.execute(
        sa.text(
            """
            UPDATE knowledge_buckets
               SET archived_at = now()
             WHERE slug = ANY(:slugs)
               AND archived_at IS NULL
            """
        ),
        {"slugs": list(RETIRED_SLUGS)},
    )

    # Recycle unrouted knowledge-notes that the router previously pinned
    # to a now-retired bucket. JSONB update sets ``routed_bucket_id`` =
    # null and adds a marker so the down-migration can find them.
    bind.execute(
        sa.text(
            """
            UPDATE improvements i
               SET context = jsonb_set(
                       jsonb_set(
                           i.context,
                           '{routed_bucket_id}',
                           'null'::jsonb,
                           true
                       ),
                       '{rerouted_from_retired}',
                       to_jsonb(kb.slug),
                       true
                   )
              FROM knowledge_buckets kb
             WHERE i.kind = 'knowledge_note'
               AND kb.slug = ANY(:slugs)
               AND (i.context->>'routed_bucket_id')::uuid = kb.id
            """
        ),
        {"slugs": list(RETIRED_SLUGS)},
    )


def downgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            UPDATE knowledge_buckets
               SET archived_at = NULL
             WHERE slug = ANY(:slugs)
            """
        ),
        {"slugs": list(RETIRED_SLUGS)},
    )

    bind.execute(
        sa.text(
            """
            UPDATE bucket_articles ba
               SET status = 'published',
                   archived_at = NULL
              FROM knowledge_buckets kb
             WHERE ba.bucket_id = kb.id
               AND kb.slug = ANY(:slugs)
            """
        ),
        {"slugs": list(RETIRED_SLUGS)},
    )

    bind.execute(
        sa.text(
            """
            UPDATE improvements i
               SET context = jsonb_set(
                       i.context - 'rerouted_from_retired',
                       '{routed_bucket_id}',
                       to_jsonb(kb.id::text),
                       true
                   )
              FROM knowledge_buckets kb
             WHERE i.kind = 'knowledge_note'
               AND i.context ? 'rerouted_from_retired'
               AND i.context->>'rerouted_from_retired' = kb.slug
            """
        )
    )
