#!/usr/bin/env python3
"""Backfill ``workspace_repo_routing`` from dispatch history.

Variant A replaces the name-heuristic + oldest-activated fallback in
``dispatcher._pick_dispatch_repo`` with explicit project→repo bindings.
This one-off seeds those bindings from what actually happened — the
``agent_run.dispatch`` audit trail — so existing routing is preserved
when the strict path goes live. DB-only (no tracker calls):

- **Per-project binding**: for each project (``payload.project_id`` not
  null), bind it to the repo it dispatched to most often.
- **Workspace default** (``project_native_id IS NULL``): the repo that
  the most project bindings point at — the de-facto catch-all. This
  deliberately ignores projectless churn (e.g. askslayer's infra
  tickets hammering visitor-web), so the default lands on the repo that
  does real work (visitor-back), not the key-less dump.

Idempotent: never overwrites an existing routing row.

    .venv/bin/python tools/scripts/backfill_repo_routing.py            # dry-run
    .venv/bin/python tools/scripts/backfill_repo_routing.py --execute
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402

from backend.app.db.models.integrations import (  # noqa: E402
    WorkspaceRepo,
    WorkspaceRepoRouting,
)
from backend.app.db.session import dispose_engine, get_sessionmaker  # noqa: E402


async def _backfill(execute: bool) -> None:
    SM = get_sessionmaker()
    async with SM() as s:
        ws_ids = (
            await s.execute(
                select(WorkspaceRepo.workspace_id)
                .where(WorkspaceRepo.activated_at.is_not(None))
                .distinct()
            )
        ).scalars().all()

        total_bindings = 0
        total_defaults = 0
        for ws in ws_ids:
            repos = {
                r.full_name: r.id
                for r in (
                    await s.execute(
                        select(WorkspaceRepo).where(
                            WorkspaceRepo.workspace_id == ws,
                            WorkspaceRepo.activated_at.is_not(None),
                        )
                    )
                ).scalars().all()
            }
            if not repos:
                continue

            existing = {
                row[0]
                for row in (
                    await s.execute(
                        select(WorkspaceRepoRouting.project_native_id).where(
                            WorkspaceRepoRouting.workspace_id == ws
                        )
                    )
                ).all()
            }  # contains None if a default already exists

            # project_id -> {repo_full_name: dispatch_count}
            rows = (
                await s.execute(
                    text(
                        "SELECT payload->>'project_id' AS pid, "
                        "payload->>'repo' AS repo, count(*) AS n "
                        "FROM audit_log WHERE workspace_id=:w "
                        "AND action='agent_run.dispatch' "
                        "AND payload->>'project_id' IS NOT NULL "
                        "AND payload->>'repo' IS NOT NULL "
                        "GROUP BY 1, 2"
                    ),
                    {"w": str(ws)},
                )
            ).all()
            per_project: dict[str, Counter] = defaultdict(Counter)
            for pid, repo, n in rows:
                per_project[pid][repo] += int(n)

            binding_targets: Counter = Counter()
            for pid, repo_counts in per_project.items():
                if pid in existing:
                    continue
                best_repo = repo_counts.most_common(1)[0][0]
                repo_id = repos.get(best_repo)
                if repo_id is None:
                    continue  # dispatched repo no longer activated
                binding_targets[repo_id] += 1
                print(f"  bind ws={ws} project={pid} -> {best_repo}")
                total_bindings += 1
                if execute:
                    s.add(
                        WorkspaceRepoRouting(
                            workspace_id=ws,
                            project_native_id=pid,
                            repo_id=repo_id,
                        )
                    )

            # Default = repo most project-bindings point at.
            if None not in existing and binding_targets:
                default_repo_id = binding_targets.most_common(1)[0][0]
                default_name = next(
                    (n for n, i in repos.items() if i == default_repo_id), "?"
                )
                print(f"  default ws={ws} -> {default_name}")
                total_defaults += 1
                if execute:
                    s.add(
                        WorkspaceRepoRouting(
                            workspace_id=ws,
                            project_native_id=None,
                            repo_id=default_repo_id,
                        )
                    )

        if execute:
            await s.commit()
        print(
            f"\n{'APPLIED' if execute else 'DRY-RUN'}: "
            f"{total_bindings} bindings, {total_defaults} defaults across "
            f"{len(ws_ids)} workspaces."
        )
    await dispose_engine()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Write rows (default: dry-run, print only).",
    )
    args = ap.parse_args()
    asyncio.run(_backfill(args.execute))


if __name__ == "__main__":
    main()
