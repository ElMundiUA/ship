"""inbox v1: backfill from clarifications + improvements (P2-02)

Revision ID: 0032_inbox_backfill_legacy
Revises: 0031_inbox_v1
Create Date: 2026-04-24

Populate ``inbox_items`` (and a single ``inbox_item_events`` row per
backfilled item) from the two pre-Inbox-v1 sources of human work —
``clarifications`` and ``improvements``. Without this migration the
unified Inbox would only show items created *after* the cutover; any
clarification raised by an agent or improvement proposed by the
distiller before P2-01 would be invisible to the operator even though
the row still exists in its legacy table.

Design choices
--------------

- **Pure SQL.** No app imports, no async machinery, no SQLAlchemy ORM
  reflection. Mirrors ``0016_backfill_summary_articles.py`` so the
  migration is safe to run inside a maintenance window or against a
  fresh dev database with empty source tables (zero INSERTs, no error).
- **Two CTE-INSERT statements**, one per source table. Each CTE
  inserts into ``inbox_items`` with a ``RETURNING`` clause that feeds
  a follow-on ``INSERT INTO inbox_item_events`` so every backfilled
  item has a single ``actor_kind='system'`` / ``action='created'``
  audit row from the start. Doing this in one statement (rather than a
  Python loop) means we never hold uncommitted state between the two
  inserts and never need to know the new ``inbox_items.id`` values
  ahead of time.
- **Idempotent.** A ``NOT EXISTS (SELECT 1 FROM inbox_items i WHERE
  i.source_table = X AND i.source_id = c.id)`` guard inside each CTE
  ensures re-running ``alembic upgrade head`` on a partially-migrated
  database is a no-op for already-mirrored rows. Because the events
  insert only runs against the ``RETURNING`` of newly inserted items,
  audit-trail duplicates are structurally impossible.

Status / decision mapping
-------------------------

``clarifications.status`` → ``inbox_items.status``::

    open      → new
    answered  → resolved
    skipped   → dismissed
    stale     → dismissed
    (other)   → new

``clarifications.status`` → ``inbox_items.resolution``::

    answered  → 'answered'
    skipped   → 'dismissed'
    stale     → 'dismissed'
    (other)   → NULL

``improvements.decision`` → ``inbox_items.status``::

    pending   → new
    accepted  → resolved
    declined  → dismissed
    deferred  → snoozed   (snoozed_until = decided_at + interval '7 days')
    (other)   → new

``improvements.decision`` → ``inbox_items.resolution``::

    accepted  → 'accepted'
    declined  → 'dismissed'
    deferred  → NULL      (snoozed items are not resolved)
    pending   → NULL

Edge cases
----------

- **Owner.** ``owner_user_id`` is set from
  ``clarifications.answered_by_user_id`` / ``improvements.decided_by_user_id``
  verbatim. NULLs are preserved (the column allows NULL via
  ``ON DELETE SET NULL``); an accepted improvement with no
  ``decided_by_user_id`` (e.g. a system auto-accept) lands as
  ``status='resolved'`` with ``owner_user_id IS NULL``, matching the
  source row's truth.
- **resolved_at.** Only populated when the mapped status is terminal
  (``resolved`` / ``dismissed``). Snoozed and new items leave
  ``resolved_at`` NULL so the state machine in §5 stays self-consistent.
- **snoozed_until.** Only set for ``deferred`` improvements; computed
  as ``decided_at + interval '7 days'`` to mirror the implicit "look
  again next week" semantics of the legacy ``deferred`` decision. If
  ``decided_at`` is somehow NULL on a deferred row, ``snoozed_until``
  will also be NULL — the state machine will treat that as
  immediately-due, which is the right behaviour.

Phase 2 dual-write
------------------

This migration captures **pre-cutover history only**. Ticket P2-08
will wrap the legacy ``clarifications`` / ``improvements`` create
paths to also write an ``inbox_items`` row going forward, so once
P2-08 ships every new clarification / improvement gets an inbox
mirror at create time and this backfill will never need to run again.

Downgrade
---------

Destructive — deletes every ``inbox_items`` row whose
``source_table IN ('clarifications', 'improvements')``. The
``ON DELETE CASCADE`` on ``inbox_item_events.item_id`` cleans up the
audit-trail rows automatically. Any human edits made directly on a
backfilled inbox item (reassign, snooze, comments) are lost on
downgrade — this is acceptable because the source rows in
``clarifications`` / ``improvements`` are the canonical record and
re-running ``upgrade`` rebuilds the mirrors. Inbox items that did
*not* come from this backfill (native v1 items with
``source_table IS NULL``, or future P2-08 dual-writes) are untouched.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0032_inbox_backfill_legacy"
down_revision: Union[str, None] = "0031_inbox_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- clarifications → inbox_items (type='clarification') ----------------
    #
    # CTE: insert mirrors with the §4 backfill SQL, RETURNING the new
    # ids so the follow-on insert can attach a 'created' audit event
    # to every newly inserted row (and only newly inserted rows — the
    # NOT EXISTS guard means re-runs naturally produce zero events).
    op.execute(
        sa.text(
            """
            WITH inserted_clarifications AS (
                INSERT INTO inbox_items (
                    workspace_id, repo_id, type, source_table, source_id,
                    play_key, title, summary, payload, status,
                    owner_user_id, created_at, resolved_at, resolution
                )
                SELECT
                    c.workspace_id,
                    c.repo_id,
                    'clarification',
                    'clarifications',
                    c.id,
                    NULL,
                    LEFT(c.question, 250),
                    c.question,
                    jsonb_build_object(
                        'ticket_ref', c.ticket_ref,
                        'context', c.context,
                        'source', c.source,
                        'tracker_provider', c.tracker_provider,
                        'tracker_issue_key', c.tracker_issue_key,
                        'tracker_issue_url', c.tracker_issue_url
                    ),
                    CASE c.status
                        WHEN 'open'     THEN 'new'
                        WHEN 'answered' THEN 'resolved'
                        WHEN 'skipped'  THEN 'dismissed'
                        WHEN 'stale'    THEN 'dismissed'
                        ELSE 'new'
                    END,
                    c.answered_by_user_id,
                    c.created_at,
                    CASE c.status
                        WHEN 'answered' THEN c.answered_at
                        WHEN 'skipped'  THEN c.answered_at
                        WHEN 'stale'    THEN c.answered_at
                        ELSE NULL
                    END,
                    CASE c.status
                        WHEN 'answered' THEN 'answered'
                        WHEN 'skipped'  THEN 'dismissed'
                        WHEN 'stale'    THEN 'dismissed'
                        ELSE NULL
                    END
                FROM clarifications c
                WHERE NOT EXISTS (
                    SELECT 1
                      FROM inbox_items i
                     WHERE i.source_table = 'clarifications'
                       AND i.source_id = c.id
                )
                RETURNING id, created_at, source_table
            )
            INSERT INTO inbox_item_events (
                item_id, actor_user_id, actor_kind, action,
                payload, created_at
            )
            SELECT
                ic.id,
                NULL,
                'system',
                'created',
                jsonb_build_object(
                    'reason', 'legacy_backfill',
                    'source', ic.source_table
                ),
                ic.created_at
            FROM inserted_clarifications ic
            """
        )
    )

    # --- improvements → inbox_items (type='improvement') --------------------
    #
    # Decision drives both the inbox status and the resolution. The
    # ``deferred`` branch is the one place where we synthesise data
    # the legacy table didn't carry: ``snoozed_until = decided_at + 7
    # days`` mirrors the implicit "look at this again next week" intent
    # of a deferred improvement (see module docstring for the full
    # mapping table and edge-case notes).
    op.execute(
        sa.text(
            """
            WITH inserted_improvements AS (
                INSERT INTO inbox_items (
                    workspace_id, repo_id, type, source_table, source_id,
                    play_key, title, summary, payload, status,
                    owner_user_id, created_at, snoozed_until,
                    resolved_at, resolution
                )
                SELECT
                    i.workspace_id,
                    i.repo_id,
                    'improvement',
                    'improvements',
                    i.id,
                    NULL,
                    LEFT(i.title, 250),
                    i.body,
                    jsonb_build_object(
                        'kind', i.kind,
                        'impact', i.impact,
                        'effort', i.effort,
                        'decision_reason', i.decision_reason,
                        'next_action_url', i.next_action_url,
                        'context', i.context
                    ),
                    CASE i.decision
                        WHEN 'pending'  THEN 'new'
                        WHEN 'accepted' THEN 'resolved'
                        WHEN 'declined' THEN 'dismissed'
                        WHEN 'deferred' THEN 'snoozed'
                        ELSE 'new'
                    END,
                    i.decided_by_user_id,
                    i.created_at,
                    CASE i.decision
                        WHEN 'deferred' THEN
                            i.decided_at + interval '7 days'
                        ELSE NULL
                    END,
                    CASE i.decision
                        WHEN 'accepted' THEN i.decided_at
                        WHEN 'declined' THEN i.decided_at
                        ELSE NULL
                    END,
                    CASE i.decision
                        WHEN 'accepted' THEN 'accepted'
                        WHEN 'declined' THEN 'dismissed'
                        ELSE NULL
                    END
                FROM improvements i
                WHERE NOT EXISTS (
                    SELECT 1
                      FROM inbox_items ii
                     WHERE ii.source_table = 'improvements'
                       AND ii.source_id = i.id
                )
                RETURNING id, created_at, source_table
            )
            INSERT INTO inbox_item_events (
                item_id, actor_user_id, actor_kind, action,
                payload, created_at
            )
            SELECT
                ii.id,
                NULL,
                'system',
                'created',
                jsonb_build_object(
                    'reason', 'legacy_backfill',
                    'source', ii.source_table
                ),
                ii.created_at
            FROM inserted_improvements ii
            """
        )
    )


def downgrade() -> None:
    # Destructive: remove every backfilled mirror. The CASCADE on
    # ``inbox_item_events.item_id`` (see 0031) handles the audit
    # rows. Native Inbox v1 items (source_table IS NULL) and any
    # future non-legacy mirrors stay put.
    op.execute(
        sa.text(
            """
            DELETE FROM inbox_items
             WHERE source_table IN ('clarifications', 'improvements')
            """
        )
    )
