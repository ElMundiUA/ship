"""Synthetic Routine sync (Wave 8b P5-07).

Bridges the wizard seed PR composer and the post-merge
:func:`backend.app.services.lanes_sync.sync_lanes_for_repo`. Today the
``lanes`` table is populated only after the seed PR merges and a
``workflow_run`` webhook drives the real syncer. That makes the new
Inbox / Coverage / Automations surfaces show empty for a freshly
activated repo until the operator clicks Merge — bad first impression.

This module fixes that: the wizard route calls
:func:`synthetic_lane_sync` IMMEDIATELY after
:func:`commit_bundle_pr` succeeds (so we don't insert Lanes for a
seed PR that never opened) and the database lights up with the
canonical bundle's lanes. Each row is stamped with
``origin='wizard_seed_synthetic'`` so the post-merge syncer
(:func:`reconcile_synthetic_lanes`) knows which rows are placeholders
vs. operator-edited truth.

Idempotency
-----------

The wizard re-run path is the design target — operators retry the
button when CI is flaky. ``synthetic_lane_sync`` therefore:

- Skips ``(repo_id, lane_id)`` pairs that already exist (regardless
  of their ``origin`` — even ``'merged'`` ones, because real merged
  data trumps the synthetic placeholder).
- Reports the count of *newly inserted* rows so the route can audit
  log "wrote 5 synthetic lanes" or "0 (re-run; nothing new)".

Reconciliation
--------------

When the seed PR eventually merges and
:func:`backend.app.services.lanes_sync.sync_lanes_for_repo` fires:

- A synthetic row whose ``(lane_id, kind)`` still matches the merged
  config gets its ``origin`` flipped to ``'merged'`` in place — no
  row replacement, so ``last_run_at`` / ``last_run_status`` survive
  the transition (a ``once`` lane that fired between the seed and
  the first scheduled tick keeps its history).
- A synthetic row that diverges (operator edited the lane in the
  PR — different ``kind``, missing from the merged config, etc.)
  falls back to the real syncer's existing add/update/remove path.
  In particular, a synthetic row for a now-absent lane gets
  hard-deleted exactly like any other stale row.

The reconciliation helper is exposed on this module instead of
patching ``lanes_sync.py`` so the lanes_sync surface stays focused
on "config-on-disk → DB" while the synthetic-origin transition
logic lives next to its producer.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.lanes import Routine
from backend.app.services import catalog as catalog_service


logger = logging.getLogger(__name__)


ORIGIN_MERGED: str = "merged"
ORIGIN_SYNTHETIC: str = "wizard_seed_synthetic"


async def reconcile_synthetic_lanes(
    *,
    session: AsyncSession,
    repo_id: uuid.UUID,
    merged_lane_specs: Iterable[tuple[str, str]],
) -> tuple[int, int]:
    """Promote synthetic rows whose ``(lane_id, kind)`` matches the merged config.

    Called from inside the post-merge ``sync_lanes_for_repo`` pass
    (after the YAML has been parsed and per-lane shape decided)
    BEFORE the existing add/update/remove walk so the in-place
    promotion happens without churning the row's
    ``last_run_at`` / ``last_run_status`` columns.

    ``merged_lane_specs`` is the post-merge set of
    ``(lane_id, kind)`` tuples derived from ``.ship/config.yml``. A
    synthetic row whose pair appears in the set is promoted to
    ``origin='merged'``; everything else (different kind, lane no
    longer in the merged config) is left to the syncer's existing
    diff/delete logic.

    Returns ``(promoted, divergent)`` so the caller can include both
    in its sync report — ``divergent`` is the count of synthetic
    rows still on the table after the promote pass that *don't*
    match the merged config. The post-merge syncer's existing
    update / delete walk reconciles those.
    """
    spec_index: dict[str, str] = {
        lane_id: kind for lane_id, kind in merged_lane_specs
    }

    rows = (
        await session.execute(
            select(Routine).where(
                Routine.repo_id == repo_id,
                Routine.origin == ORIGIN_SYNTHETIC,
            )
        )
    ).scalars().all()

    promoted = 0
    divergent = 0
    for row in rows:
        merged_kind = spec_index.get(row.lane_id)
        if merged_kind is not None and merged_kind == row.kind:
            row.origin = ORIGIN_MERGED
            promoted += 1
            continue
        # Either the merged config doesn't reference this lane any
        # more, or the operator changed its trigger kind in the PR.
        # Leave it for the syncer's diff/delete pass to handle —
        # this helper deliberately doesn't update or delete divergent
        # rows so the existing logic stays the single source of
        # truth for the divergent path.
        divergent += 1

    if promoted:
        await session.flush()
    return promoted, divergent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _existing_lane_ids(
    *, session: AsyncSession, repo_id: uuid.UUID
) -> set[str]:
    """Return ``{lane_id}`` for every existing Routine on ``repo_id``.

    One indexed query keeps the synthetic-sync linear in (#bundle
    lanes) regardless of how many lanes the repo carries pre-call.
    """
    rows = (
        await session.execute(
            select(Routine.lane_id).where(Routine.repo_id == repo_id)
        )
    ).scalars().all()
    return set(rows)


def _shape_lane_entry(
    *,
    lane_id: str,
    trigger: dict[str, Any],
) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    """Project one ``bundle_lane_entries`` value into the Routine row columns.

    The ``trigger`` shape is whatever
    :func:`backend.app.services.catalog.bundle_lane_entries`
    produced — a flat dict with one of ``event`` / ``schedule`` /
    ``once`` plus a ``pattern`` key. We mirror the post-merge
    syncer's normalisation (``lanes_sync._parse_lane_entry``) so the
    synthetic row's ``config_blob`` and ``kind`` are byte-identical
    to what would land on a real merge — important so the post-merge
    reconciler's "promote in place" path stays a no-op diff.
    """
    kind: str | None = None
    for candidate in ("once", "event", "schedule"):
        if candidate in trigger:
            kind = candidate
            break
    if kind is None:
        return None, None, None, {}

    cron_value: str | None = None
    if kind == "schedule":
        raw_cron = trigger.get("schedule")
        if isinstance(raw_cron, str):
            cron_value = raw_cron[:128]

    pattern_value = trigger.get("pattern")
    pattern_str = (
        str(pattern_value)[:255] if pattern_value is not None else None
    )

    # Mirror lanes_sync's flat blob shape so a real-merge sync that
    # finds the same lane writes the same blob — keeps the row's
    # ``config_blob`` byte-stable across the synthetic→merged flip.
    config_blob: dict[str, Any] = {
        "lane_id": lane_id,
        "kind": kind,
        "trigger": trigger.get(kind),
        "pattern": pattern_str,
        "patterns": [pattern_str] if pattern_str else [],
        "fanout": "matrix",
        "cron": cron_value,
        "idempotency_key": None,
        "raw": dict(trigger),
    }
    return kind, cron_value, pattern_str, config_blob


__all__ = [
    "ORIGIN_MERGED",
    "ORIGIN_SYNTHETIC",
    "reconcile_synthetic_lanes",
]
