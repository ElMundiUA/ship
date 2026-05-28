"""Backfill — tickets that should already be `blocked`-labeled per the
Phase 1 FSM rearchitecture (2026-05-28).

A ticket counts as stuck if:

- it has ≥2 ``agent_run.finish`` with outcome in {blocked,
  needs_clarification} in the last 14 days (real, non-synthetic);
- there is NO ``ready_next_step`` finish for the same ticket AFTER the
  most recent blocked/needs_clarification one.

For each such ticket the script calls Linear's `add_signal_label`
with key ``blocked`` (falling back to ``needs_clarification`` if the
team's provisioner predates the new SIGNAL_LABELS entry). The label is
already in OVERLAY_FREEZE_LABEL_PREFIXES, so the picker will refuse
the ticket on every subsequent dispatch until a human clears the label
in Linear.

Idempotent — Linear's GraphQL ``addedLabelIds`` is a set union, so
re-running adds nothing new.

Run with:

    DATABASE_URL=... \
    python -m backend.app.scripts.backfill_blocked_label_2026_05_28 [--dry-run]

The script reuses the production tracker resolver, so it picks up the
same OAuth tokens the server uses at runtime — no separate config.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import Workspace
from backend.app.db.session import session_factory
from backend.app.services.tracker_resolver import resolve_tracker


log = logging.getLogger("backfill.blocked")


_CANDIDATES_SQL = text(
    """
    with all_finishes as (
      select al.workspace_id as ws_id,
             al.payload->>'ticket_ref' as ticket_ref,
             al.created_at as ts,
             al.payload->>'outcome' as outcome
        from audit_log al
       where al.action = 'agent_run.finish'
         and (al.payload->>'synthetic') is distinct from 'true'
         and al.created_at > now() - interval '14 day'
         and al.payload->>'ticket_ref' is not null
         and al.payload->>'ticket_ref' like '%-%'
    ),
    last_progress as (
      select ws_id, ticket_ref, max(ts) as t
        from all_finishes
       where outcome = 'ready_next_step'
       group by ws_id, ticket_ref
    ),
    stuck as (
      select f.ws_id, f.ticket_ref,
             count(*) filter (where f.outcome = 'blocked') as n_blocked,
             count(*) filter (where f.outcome = 'needs_clarification') as n_clar
        from all_finishes f
        left join last_progress p
          on p.ws_id = f.ws_id and p.ticket_ref = f.ticket_ref
       where f.outcome in ('blocked', 'needs_clarification')
         and (p.t is null or f.ts > p.t)
       group by f.ws_id, f.ticket_ref
    )
    select ws_id, ticket_ref, n_blocked, n_clar
      from stuck
     where n_blocked + n_clar >= 2
     order by n_blocked + n_clar desc
    """
)


async def _add_blocked_label(
    session: AsyncSession, *, workspace: Workspace, ticket_ref: str
) -> str:
    """Resolve the workspace's tracker, then add the ``blocked`` signal
    label. Returns the action taken: ``added`` / ``fallback_clar`` /
    ``no_adapter`` / ``no_tracker`` / ``error:<msg>``.
    """
    try:
        resolved = await resolve_tracker(session, workspace_id=workspace.id)
    except Exception as exc:  # noqa: BLE001
        return f"error:resolve:{exc}"
    if resolved is None:
        return "no_tracker"
    add_signal = getattr(resolved.gateway, "add_signal_label", None)
    if add_signal is None:
        return "no_adapter"
    from backend.app.services.tracker_resolver import TicketRef

    ref = TicketRef(kind=resolved.kind, id=ticket_ref)
    try:
        await add_signal(ref, key="blocked")
        return "added"
    except ValueError:
        try:
            await add_signal(ref, key="needs_clarification")
            return "fallback_clar"
        except Exception as exc:  # noqa: BLE001
            return f"error:fallback:{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"error:{exc}"


async def run(dry_run: bool) -> int:
    async with session_factory() as session:
        rows = (await session.execute(_CANDIDATES_SQL)).all()
        if not rows:
            print("no stuck tickets — nothing to backfill")
            return 0
        print(f"found {len(rows)} stuck tickets")
        # Pre-fetch workspaces by id so the per-row log line shows the
        # human-readable slug.
        ws_ids = list({row.ws_id for row in rows})
        slugs = {
            w.id: (w.slug or w.name or str(w.id))
            for w in (
                await session.execute(
                    select(Workspace).where(Workspace.id.in_(ws_ids))
                )
            ).scalars().all()
        }
        for row in rows:
            slug = slugs.get(row.ws_id, str(row.ws_id))
            total = row.n_blocked + row.n_clar
            if dry_run:
                print(f"  DRY  {slug:<32} {row.ticket_ref:<14} stuck={total}")
                continue
            workspace = (
                await session.execute(
                    select(Workspace).where(Workspace.id == row.ws_id)
                )
            ).scalar_one()
            outcome = await _add_blocked_label(
                session, workspace=workspace, ticket_ref=row.ticket_ref
            )
            print(f"  {outcome:<14} {slug:<32} {row.ticket_ref:<14} stuck={total}")
        if not dry_run:
            # Backfill is read-only at the SQL level — all writes
            # happen via the Linear adapter — but commit anyway in
            # case the resolver lazy-loaded any integration rows.
            await session.commit()
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    sys.exit(asyncio.run(run(dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
