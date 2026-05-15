"""Teardown helper for the Process e2e pipeline suite.

Drops every test-generated row in the ``e2e-pipeline`` workspace
while preserving the durable bootstrap state seeded by
``seed_e2e_pipeline_workspace.py``:

Deleted:
- memory_tracker_projects / _tickets / _comments
- memory_git_repos / _files / _pull_requests / memory_ci_runs
- pull_requests / workflow_runs (real-GitHub mirrors)
- routine_runs / routines
- inbox_items / inbox_item_events
- agent_dispatch_locks

Preserved:
- workspaces row + workspace_repos / github_installations
- service users + api_tokens
- workspace_members

GitHub-side cleanup (branches, PRs on ``ElMundiUA/ship-e2e-pipeline``)
is the Playwright helper's job — runs in JS so it can ``gh`` the
sandbox repo directly and avoid Python juggling a second token.

Usage:

    DATABASE_URL=... PYTHONPATH=apps .venv/bin/python \\
      tools/scripts/cleanup_e2e_pipeline.py [--dry-run]

Idempotent — re-run after every CI batch, or wedge into the e2e
afterAll hook.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg


WORKSPACE_SLUG = "e2e-pipeline"


# Order matters: child rows first, then parents. asyncpg gives us a
# single transaction so a failure mid-list rolls everything back.
DELETE_SQL: tuple[tuple[str, str], ...] = (
    # Inbox: events FK to items.
    (
        "inbox_item_events",
        "delete from inbox_item_events where item_id in "
        "(select id from inbox_items where workspace_id = $1)",
    ),
    ("inbox_items", "delete from inbox_items where workspace_id = $1"),
    # Lanes: runs FK to routines.
    ("routine_runs", "delete from routine_runs where workspace_id = $1"),
    ("routines", "delete from routines where workspace_id = $1"),
    # Pipeline mirrors (real GitHub side).
    ("workflow_runs", "delete from workflow_runs where workspace_id = $1"),
    ("pull_requests", "delete from pull_requests where workspace_id = $1"),
    # Memory adapters: child → parent.
    (
        "memory_ci_runs",
        "delete from memory_ci_runs where repo_id in "
        "(select id from memory_git_repos where workspace_id = $1)",
    ),
    (
        "memory_git_prs",
        "delete from memory_git_prs where repo_id in "
        "(select id from memory_git_repos where workspace_id = $1)",
    ),
    (
        "memory_git_files",
        "delete from memory_git_files where repo_id in "
        "(select id from memory_git_repos where workspace_id = $1)",
    ),
    ("memory_git_repos", "delete from memory_git_repos where workspace_id = $1"),
    (
        "memory_tracker_comments",
        "delete from memory_tracker_comments where ticket_id in "
        "(select id from memory_tracker_tickets where workspace_id = $1)",
    ),
    (
        "memory_tracker_tickets",
        "delete from memory_tracker_tickets where workspace_id = $1",
    ),
    (
        "memory_tracker_projects",
        "delete from memory_tracker_projects where workspace_id = $1",
    ),
    # Dispatch locks: per-ticket gate held while an agent is running.
    (
        "agent_dispatch_locks",
        "delete from agent_dispatch_locks where workspace_id = $1",
    ),
)


def _normalise_dsn(raw: str) -> str:
    raw = raw.strip().strip('"')
    return (
        raw.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


async def _resolve_workspace_id(conn: asyncpg.Connection) -> str | None:
    row = await conn.fetchrow(
        "select id from workspaces where slug = $1", WORKSPACE_SLUG
    )
    return None if row is None else str(row["id"])


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts without writing.",
    )
    args = parser.parse_args()

    raw = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not raw:
        print("ERROR: DATABASE_URL / DB_URL not set", file=sys.stderr)
        return 2
    dsn = _normalise_dsn(raw)

    ssl_arg = "require" if "localhost" not in dsn and "127.0.0.1" not in dsn else None
    conn = await asyncpg.connect(dsn=dsn, ssl=ssl_arg)
    try:
        ws_id = await _resolve_workspace_id(conn)
        if ws_id is None:
            print(
                f"workspace slug {WORKSPACE_SLUG!r} not found — nothing to clean.",
                file=sys.stderr,
            )
            return 0
        print(f"workspace: {ws_id} ({WORKSPACE_SLUG})  dry_run={args.dry_run}")
        async with conn.transaction():
            for table, sql in DELETE_SQL:
                # Count first for log clarity. The count query is a
                # simple ``select count(*) from <table>`` filtered the
                # same way as the delete — derived by stripping the
                # ``delete from`` prefix.
                count_sql = sql.replace("delete from", "select count(*) from", 1)
                count = await conn.fetchval(count_sql, ws_id)
                if count:
                    print(f"  {table:34} {count:5d} row(s)")
                if not args.dry_run and count:
                    await conn.execute(sql, ws_id)
            if args.dry_run:
                raise RuntimeError("__dry_run_rollback__")
    except RuntimeError as exc:
        if str(exc) != "__dry_run_rollback__":
            raise
    finally:
        await conn.close()
    print("done." + (" (dry-run; nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
