#!/usr/bin/env python3
"""One-shot cleanup of stale state in a single workspace.

Closes ``workflow_runs`` whose GitHub-side run finished long ago but Ship
missed the webhook (rows still ``status='in_progress' / 'queued'`` with no
conclusion 6h+ later). Drops obvious E2E-test debris buckets created by
``e2e/scripts/`` and never cleaned up. Both passes are idempotent — safe to
re-run after a fresh batch of stale rows accumulates.

Usage:

    DATABASE_URL=postgresql://... \\
      python3 scripts/cleanup_workspace_state.py \\
        --workspace d591af28-225e-477e-8448-7a4b9b06fbfc

    Add ``--dry-run`` to preview changes without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg


STALE_WORKFLOW_RUNS_AGE = "6 hour"
E2E_BUCKET_SLUG_PREFIX = "e2e-"


def _normalise_dsn(raw: str) -> str:
    raw = raw.strip().strip('"')
    return (
        raw.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


async def close_stale_workflow_runs(
    conn: asyncpg.Connection, workspace_id: str, dry_run: bool
) -> int:
    """Mark workflow_runs that GitHub almost certainly finished (but
    Ship missed the webhook for) as cancelled. We don't delete — we
    just unstick them so the dashboard counters stop reporting them
    as in-flight.
    """
    select_sql = """
      select id, name, status, created_at
      from workflow_runs
      where workspace_id = $1
        and status != 'completed'
        and created_at < now() - interval '%s'
      order by created_at
    """ % STALE_WORKFLOW_RUNS_AGE
    rows = await conn.fetch(select_sql, workspace_id)
    if not rows:
        print("  no stale workflow_runs.")
        return 0
    print(f"  {len(rows)} stale workflow_runs to close:")
    for r in rows[:5]:
        print(f"    {r['name']!r:40} status={r['status']!r:15} created={r['created_at']}")
    if len(rows) > 5:
        print(f"    ... and {len(rows) - 5} more")
    if dry_run:
        return len(rows)
    await conn.execute(
        """
        update workflow_runs
        set status = 'completed',
            conclusion = 'cancelled',
            finished_at = coalesce(finished_at, now())
        where workspace_id = $1
          and status != 'completed'
          and created_at < now() - interval '%s'
        """ % STALE_WORKFLOW_RUNS_AGE,
        workspace_id,
    )
    return len(rows)


async def delete_e2e_buckets(
    conn: asyncpg.Connection, workspace_id: str, dry_run: bool
) -> int:
    rows = await conn.fetch(
        """
        select id, slug from knowledge_buckets
        where workspace_id = $1 and slug like $2
        """,
        workspace_id,
        E2E_BUCKET_SLUG_PREFIX + "%",
    )
    if not rows:
        print("  no e2e debris buckets.")
        return 0
    bucket_ids = [r["id"] for r in rows]
    print(f"  {len(rows)} e2e buckets to delete:")
    for r in rows:
        print(f"    {r['slug']}")
    if dry_run:
        return len(rows)
    # Order matters: bucket_article_sources (by article_id) →
    # bucket_summaries (by bucket_id) → bucket_articles (by bucket_id) →
    # knowledge_buckets (by id). kb_chunks is workspace+repo+source_path
    # scoped, not per-bucket, so it stays untouched here.
    article_ids = await conn.fetch(
        "select id from bucket_articles where bucket_id = any($1::uuid[])",
        bucket_ids,
    )
    article_id_list = [r["id"] for r in article_ids]
    if article_id_list:
        await conn.execute(
            "delete from bucket_article_sources where article_id = any($1::uuid[])",
            article_id_list,
        )
    await conn.execute(
        "delete from bucket_summaries where bucket_id = any($1::uuid[])",
        bucket_ids,
    )
    await conn.execute(
        "delete from bucket_articles where bucket_id = any($1::uuid[])",
        bucket_ids,
    )
    await conn.execute(
        "delete from knowledge_buckets where id = any($1::uuid[])",
        bucket_ids,
    )
    return len(rows)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2
    dsn = _normalise_dsn(raw)

    print(f"workspace={args.workspace}  dry_run={args.dry_run}")
    conn = await asyncpg.connect(dsn=dsn, ssl="require")
    try:
        async with conn.transaction():
            print("\n== close_stale_workflow_runs ==")
            await close_stale_workflow_runs(conn, args.workspace, args.dry_run)
            print("\n== delete_e2e_buckets ==")
            await delete_e2e_buckets(conn, args.workspace, args.dry_run)
            if args.dry_run:
                # Roll back the txn even though we didn't write anything.
                raise RuntimeError("__dry_run_rollback__")
    except RuntimeError as exc:
        if str(exc) != "__dry_run_rollback__":
            raise
    finally:
        await conn.close()
    print("\ndone." + (" (dry-run; nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
