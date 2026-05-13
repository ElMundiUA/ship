#!/usr/bin/env python3
"""Destructively reseed workspace knowledge buckets.

Run with the backend virtualenv:

    .venv/bin/python scripts/reseed_knowledge_buckets.py --dry-run
    .venv/bin/python scripts/reseed_knowledge_buckets.py --execute --confirm-production
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import get_settings  # noqa: E402
from backend.app.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from backend.app.services.knowledge_reseed import (  # noqa: E402
    build_backup_snapshot,
    list_workspace_ids,
    preview_reseed_counts,
    reseed_workspace_knowledge,
)


def _parse_workspace_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _is_probably_production_database() -> bool:
    settings = get_settings()
    haystack = " ".join(
        [
            settings.database_url,
            settings.console_url,
            settings.public_url,
            settings.sentry_environment,
        ]
    ).lower()
    if "localhost" in haystack or "127.0.0.1" in haystack:
        return False
    return any(
        marker in haystack
        for marker in ("production", "prod", "app.ship.elmundi.com", "neon.tech")
    )


def _backup_path(base_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return base_dir / f"knowledge-reseed-backup-{stamp}.json"


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete existing knowledge rows and recreate recommended empty workspace buckets.",
    )
    parser.add_argument(
        "--workspace-id",
        action="append",
        type=_parse_workspace_id,
        dest="workspace_ids",
        help="Restrict the reseed to a workspace id. Repeatable. Defaults to all workspaces.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the dry-run summary without mutating rows (default).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete and recreate rows. Without this, only prints a dry-run summary.",
    )
    parser.add_argument(
        "--confirm-production",
        action="store_true",
        help="Required when the target database looks like production.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "output" / "knowledge-reseed",
        help="Directory for JSON backup snapshots.",
    )
    args = parser.parse_args()

    if args.dry_run and args.execute:
        parser.error("--dry-run and --execute are mutually exclusive")

    if args.execute and _is_probably_production_database() and not args.confirm_production:
        parser.error(
            "target database looks like production; pass --confirm-production with --execute"
        )

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            workspace_ids = await list_workspace_ids(session, args.workspace_ids)
            if not workspace_ids:
                print(json.dumps({"ok": False, "error": "no workspaces matched"}))
                return 1

            counts = await preview_reseed_counts(session, workspace_ids)
            summary = {
                "mode": "execute" if args.execute else "dry-run",
                "counts": asdict(counts),
                "workspace_ids": [str(workspace_id) for workspace_id in workspace_ids],
            }

            if not args.execute:
                print(json.dumps(summary, indent=2, sort_keys=True))
                return 0

            args.backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = _backup_path(args.backup_dir)
            snapshot = await build_backup_snapshot(session, workspace_ids)
            backup_path.write_text(
                json.dumps(snapshot, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            final_counts = await reseed_workspace_knowledge(session, workspace_ids)
            await session.commit()

            print(
                json.dumps(
                    {
                        "ok": True,
                        "backup_path": str(backup_path),
                        "counts": asdict(final_counts),
                        "workspace_ids": [
                            str(workspace_id) for workspace_id in workspace_ids
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

