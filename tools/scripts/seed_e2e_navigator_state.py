"""Plant rich Navigator-driven state in the local e2e-navigator workspace.

Pairs with ``make dev-up`` + the laptop-offline profile. Drops a
representative shape that lets the LLM-ring tool-quality suite
exercise the tools that need workspace state to fire:

- 1 MemoryTracker project + 5 tickets spread across FSM stages
- 1 MemoryGit repo + 3 files + 2 PRs (one open, one merged)
- 3 MemoryCi runs (one queued-and-walking, one success, one failure)
- 3 inbox items (clarification, improvement, failure) attached to
  the workspace + assigned to the primary e2e service user

Idempotent — re-runs detect existing rows by deterministic markers
(``slug`` for project, ``owner/name`` for repo, ``intake_handle`` for
inbox items) and skip what's already there.

Usage:

    DATABASE_URL=postgresql://ship:ship@localhost:5433/ship \\
      PYTHONPATH=apps .venv/bin/python \\
      tools/scripts/seed_e2e_navigator_state.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.memory_adapters import (
    MemoryCiRun,
    MemoryGitPullRequest,
    MemoryGitRepo,
    MemoryTrackerProject,
    MemoryTrackerTicket,
)
from backend.app.db.models.tenancy import User, Workspace
from backend.app.integrations.local.ci import MemoryCi
from backend.app.integrations.local.code_host import MemoryCodeHost
from backend.app.integrations.local.tracker import MemoryTracker


PRIMARY_EMAIL = "e2e-navigator-primary@elmundi.dev"
SECONDARY_EMAIL = "e2e-navigator-secondary@elmundi.dev"
WORKSPACE_SLUG = "e2e-navigator"

PROJECT_SLUG = "memory-search-overhaul"
REPO_OWNER = "elmundi"
REPO_NAME = "ship-e2e-sandbox"


def _dsn() -> tuple[str, dict]:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif raw.startswith("postgresql+psycopg://"):
        raw = raw.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(raw)
    qs = dict(parse_qsl(parts.query))
    sslmode = qs.pop("sslmode", None)
    qs.pop("channel_binding", None)
    raw = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )
    return raw, ({"ssl": True} if sslmode and sslmode != "disable" else {})


async def _resolve_actors(session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.slug == WORKSPACE_SLUG)
        )
    ).scalar_one_or_none()
    if ws is None:
        print(
            f"ERROR: workspace {WORKSPACE_SLUG!r} missing — run "
            "setup_e2e_navigator_workspace.py first",
            file=sys.stderr,
        )
        sys.exit(3)
    primary = (
        await session.execute(
            select(User).where(User.email == PRIMARY_EMAIL)
        )
    ).scalar_one_or_none()
    secondary = (
        await session.execute(
            select(User).where(User.email == SECONDARY_EMAIL)
        )
    ).scalar_one_or_none()
    if primary is None or secondary is None:
        print(
            "ERROR: e2e service users not provisioned. Re-run "
            "setup_e2e_navigator_workspace.py.",
            file=sys.stderr,
        )
        sys.exit(4)
    return ws.id, primary.id, secondary.id


async def _seed_tracker(
    session, workspace_id: uuid.UUID
) -> uuid.UUID:
    existing = (
        await session.execute(
            select(MemoryTrackerProject).where(
                MemoryTrackerProject.workspace_id == workspace_id,
                MemoryTrackerProject.slug == PROJECT_SLUG,
            )
        )
    ).scalar_one_or_none()
    tr = MemoryTracker(session=session, workspace_id=workspace_id)
    if existing:
        print(f"  tracker: reuse project {PROJECT_SLUG}")
        return existing.id
    project = await tr.create_project(
        name="Memory & search overhaul",
        description="Demo project seeded for the LLM-ring tool-quality tests.",
        body=(
            "## Why\n\n"
            "We need to improve the Navigator memory recall accuracy + "
            "expose better search affordances in the Console.\n\n"
            "## Scope\n\n"
            "Initial pass on the search ranker. ELS-101 .. ELS-103 carry "
            "the implementation tickets.\n"
        ),
    )
    proj_uuid = uuid.UUID(project["id"])
    print(f"  tracker: created project {PROJECT_SLUG} ({proj_uuid})")

    tickets = [
        (
            "Investigate retrieval ranking gaps",
            "Mem0 sometimes returns stale facts above fresh ones.",
            "task_intake",
            "Todo",
            "task",
        ),
        (
            "BA — clarify the ranker thresholds",
            "What's the right cosine-similarity floor for retrieval hits?",
            "ba_requirements",
            "Todo",
            "task",
        ),
        (
            "Decision — pick rerank algorithm",
            "Compare BM25 + dense reranker vs straight cosine.",
            "tech_arch_plan",
            "In Progress",
            "task",
        ),
        (
            "Plumb the new ranker into chat hot path",
            "Wire ``rerank=true`` into navigator_memory.search + cache.",
            "execution",
            "In Progress",
            "feature",
        ),
        (
            "QA — soak the rerank on Ship-on-Ship corpus",
            "Run the rerank for 24h against actual memory corpus.",
            "completed",
            "Done",
            "task",
        ),
    ]
    for title, body, stage, state, kind in tickets:
        created = await tr.create_ticket(
            title=title,
            body=body,
            labels=[f"stage:{stage}"],
            project_id=str(proj_uuid),
            ticket_type=kind,  # type: ignore[arg-type]
        )
        if state != "Todo":
            row = (
                await session.execute(
                    select(MemoryTrackerTicket).where(
                        MemoryTrackerTicket.workspace_id == workspace_id,
                        MemoryTrackerTicket.display_id == created.display_id,
                    )
                )
            ).scalar_one()
            row.state = state
        print(f"    + {created.display_id} [{stage}/{state}] {title}")
    return proj_uuid


async def _seed_repo(
    session, workspace_id: uuid.UUID
) -> MemoryGitRepo:
    existing = (
        await session.execute(
            select(MemoryGitRepo).where(
                MemoryGitRepo.workspace_id == workspace_id,
                MemoryGitRepo.owner == REPO_OWNER,
                MemoryGitRepo.name == REPO_NAME,
            )
        )
    ).scalar_one_or_none()
    gw = MemoryCodeHost(session=session, workspace_id=workspace_id)
    if existing:
        print(f"  repo: reuse {REPO_OWNER}/{REPO_NAME}")
        return existing
    repo = await gw.ensure_repo(
        owner=REPO_OWNER,
        name=REPO_NAME,
        default_branch="main",
        description="Sandbox repo seeded for LLM-ring tests.",
    )
    print(f"  repo: created {repo.owner}/{repo.name}")
    await gw.upsert_file(
        repo,
        path="README.md",
        content=(
            "# ship-e2e-sandbox\n\n"
            "Demo repo for tool-quality e2e. Edit freely.\n"
        ),
    )
    await gw.upsert_file(
        repo,
        path=".ship/config.yml",
        content="preset: web-app\nagent: cursor\ntracker: memory\n",
    )
    await gw.upsert_file(
        repo,
        path="src/rank.ts",
        content=(
            "// Stub reranker — used by the seeded PRs.\n"
            "export function rerank(hits: number[]): number[] {\n"
            "  return hits.slice().sort((a, b) => b - a);\n"
            "}\n"
        ),
    )

    # 2 PRs — one open feature, one merged refactor.
    open_pr = await gw.open_pull_request(
        repo,
        title="feat: dense reranker on top of cosine",
        body=(
            "Implements ELS-103. Bumps Navigator memory hit-rate on the "
            "Ship-on-Ship corpus by ~12%.\n\n"
            "- new ``rerank=true`` flag on memory.search\n"
            "- caches reranked output per query digest\n"
        ),
        head="rerank-dense",
        base="main",
    )
    merged_pr = await gw.open_pull_request(
        repo,
        title="refactor: split rank.ts module",
        body="Pulls the rerank helper into its own module so the dense reranker can plug in cleanly.",
        head="split-rank-module",
        base="main",
    )
    await gw.mark_pr_merged(repo, number=merged_pr.number)
    print(f"  repo: PR #{open_pr.number} open, PR #{merged_pr.number} merged")
    return repo


async def _seed_ci(
    session, workspace_id: uuid.UUID, repo: MemoryGitRepo
) -> None:
    # Avoid duplicating runs on re-seed.
    existing = (
        await session.execute(
            select(MemoryCiRun).where(MemoryCiRun.repo_id == repo.id)
        )
    ).scalars().all()
    if existing:
        print(f"  ci: reuse {len(existing)} runs on {repo.owner}/{repo.name}")
        return
    ci = MemoryCi(session=session, workspace_id=workspace_id)
    await ci.dispatch(
        repo,
        workflow_name="ci.yml",
        branch="main",
        commit_sha="abc123",
        outcome="success",
        phase_seconds=2,
    )
    await ci.dispatch(
        repo,
        workflow_name="ci.yml",
        branch="rerank-dense",
        commit_sha="def456",
        outcome="failure",
        phase_seconds=2,
        logs="✗ test_rerank_caches_per_query_digest failed\nassert hit_count == 2\n",
    )
    await ci.dispatch(
        repo,
        workflow_name="deploy.yml",
        branch="main",
        commit_sha="abc123",
        outcome="success",
        phase_seconds=300,  # stays in flight long enough for tests to see "in_progress"
    )
    # Backdate first two so the queued → in_progress → completed walker
    # rolls them to terminal states on next list_runs.
    rows = (
        await session.execute(
            select(MemoryCiRun).where(MemoryCiRun.repo_id == repo.id)
        )
    ).scalars().all()
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    for r in rows[:2]:
        r.transition_at = past
    print(f"  ci: 3 runs ({rows[0].id} success / {rows[1].id} failure / {rows[2].id} in-flight)")


async def _seed_inbox(
    session,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> None:
    """Plant 3 inbox items if none exist yet."""
    existing = (
        (
            await session.execute(
                select(InboxItem).where(
                    InboxItem.workspace_id == workspace_id,
                    InboxItem.intake_handle.like("e2e-seed-%"),
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        print(f"  inbox: reuse {len(existing)} seeded items")
        return
    seeds = [
        {
            "type": "clarification",
            "title": "Pending clarification on rerank thresholds",
            "summary": "Agent needs confirmation on the cosine floor (0.20 vs 0.30) before shipping.",
            "intake_handle": "e2e-seed-clarification-1",
            "intake_reason": "agent flagged ambiguity",
            "owner_user_id": owner_user_id,
        },
        {
            "type": "improvement",
            "title": "Improvement: speed up memory.search by caching the embedder",
            "summary": "20% latency reduction observed on staging.",
            "intake_handle": "e2e-seed-improvement-1",
            "intake_reason": "qa noted",
            "owner_user_id": owner_user_id,
        },
        {
            "type": "failure",
            "title": "Failure: rerank-dense CI run failed on assertion",
            "summary": "test_rerank_caches_per_query_digest failed — likely a cache-key bug.",
            "intake_handle": "e2e-seed-failure-1",
            "intake_reason": "CI red",
            "owner_user_id": owner_user_id,
        },
    ]
    for s in seeds:
        item = InboxItem(
            workspace_id=workspace_id,
            **s,
        )
        session.add(item)
    await session.flush()
    print(f"  inbox: created {len(seeds)} items (clarification / improvement / failure)")


async def main() -> int:
    db_url, connect_args = _dsn()
    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        ws_id, primary_id, _ = await _resolve_actors(session)
        print(f"workspace: {WORKSPACE_SLUG} ({ws_id})")
        print("seeding…")
        await _seed_tracker(session, ws_id)
        repo = await _seed_repo(session, ws_id)
        await _seed_ci(session, ws_id, repo)
        await _seed_inbox(session, ws_id, primary_id)
        await session.commit()
        print("done.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
