"""E17/ELS-127 — one-shot backfill from existing chat threads into mem0.

Walks every chat thread the workspace has on disk, iterates its
user-side messages oldest-first, and feeds them through the same
``memory.add`` path the live chat-stream handler uses. Each
message becomes (potentially) one or more facts mem0 owns, mirrored
into ``navigator_memories``.

Designed to run at night (token cost dominates) with rate limiting
+ per-workspace token budget logging so an operator can stop
mid-run if a single tenant's chat archive turns out to be larger
than expected.

Usage:

    # Dry-run — counts only, no mem0 calls
    python tools/scripts/extract_navigator_memories_backfill.py --dry-run

    # Single workspace
    python tools/scripts/extract_navigator_memories_backfill.py \\
        --workspace-slug denys-99938640

    # Whole prod (asks for confirmation when ``--yes`` isn't passed)
    python tools/scripts/extract_navigator_memories_backfill.py --yes

    # Tunable knobs
    --max-messages-per-thread 200   # stop iterating after N user messages
    --sleep-between-calls 0.5       # mem0 rate-limit cushion
    --since-days 90                 # only threads with activity in last N days

Environment:

    DATABASE_URL, OPENAI_API_KEY, ENCRYPTION_KEY (for any secret
    decryption the underlying services need) — same as the backend
    container.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.core.config import get_settings
from backend.app.db.models.agent_surface import ChatMessage, ChatThread
from backend.app.db.models.tenancy import Workspace
from backend.app.services.agent import memory as navigator_memory


log = logging.getLogger("ship.scripts.extract_navigator_memories_backfill")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not raw:
        print("ERROR: DATABASE_URL / DB_URL not set", file=sys.stderr)
        sys.exit(2)
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(raw)
    qs = dict(parse_qsl(parts.query))
    sslmode = qs.pop("sslmode", None)
    qs.pop("channel_binding", None)
    raw = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )
    return raw, ({"ssl": True} if sslmode and sslmode != "disable" else {})


async def _backfill_thread(
    session: AsyncSession,
    *,
    thread: ChatThread,
    settings,
    sleep_between_calls: float,
    max_messages: int,
    dry_run: bool,
) -> tuple[int, int]:
    """Returns ``(messages_processed, facts_added)`` for the thread."""
    if thread.created_by_user_id is None:
        log.info("  skip thread %s — no owner", thread.id)
        return 0, 0

    msgs = (
        (
            await session.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.thread_id == thread.id,
                    ChatMessage.role == "user",
                )
                .order_by(ChatMessage.created_at.asc())
                .limit(max_messages)
            )
        )
        .scalars()
        .all()
    )
    if not msgs:
        return 0, 0

    processed = 0
    facts_added = 0
    for idx, m in enumerate(msgs):
        if not navigator_memory.should_extract_memory(
            thread.memory_enabled, m.body or ""
        ):
            continue
        processed += 1
        if dry_run:
            continue
        try:
            added = await navigator_memory.add(
                session,
                workspace_id=thread.workspace_id,
                owner_user_id=thread.created_by_user_id,
                message=m.body or "",
                source_thread_id=thread.id,
                source_message_id=m.id,
                source_message_position=idx,
                project_native_id=None,
                intent_at_capture=thread.intent,
                settings=settings,
            )
            facts_added += len(added)
        except Exception:  # noqa: BLE001
            log.exception(
                "backfill add failed thread=%s msg=%s", thread.id, m.id
            )
        if sleep_between_calls > 0:
            await asyncio.sleep(sleep_between_calls)
    return processed, facts_added


async def _backfill_workspace(
    session: AsyncSession,
    *,
    workspace: Workspace,
    settings,
    sleep_between_calls: float,
    max_messages: int,
    since_days: int | None,
    dry_run: bool,
) -> dict[str, int]:
    """Per-workspace summary so the operator can budget at the tenant level."""
    log.info(
        "workspace %s (%s) — starting backfill",
        workspace.slug,
        workspace.id,
    )
    stmt = select(ChatThread).where(ChatThread.workspace_id == workspace.id)
    if since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        stmt = stmt.where(ChatThread.last_user_activity_at > cutoff)

    threads = (
        (await session.execute(stmt.order_by(ChatThread.created_at.asc())))
        .scalars()
        .all()
    )
    summary = {
        "threads": len(threads),
        "messages_processed": 0,
        "facts_added": 0,
    }
    for t in threads:
        proc, added = await _backfill_thread(
            session,
            thread=t,
            settings=settings,
            sleep_between_calls=sleep_between_calls,
            max_messages=max_messages,
            dry_run=dry_run,
        )
        summary["messages_processed"] += proc
        summary["facts_added"] += added
        if not dry_run:
            # Commit per-thread so a crash mid-workspace doesn't lose
            # all the work the run completed before the failure.
            await session.commit()

    log.info("workspace %s — %s", workspace.slug, summary)
    return summary


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-slug", default=None)
    parser.add_argument("--max-messages-per-thread", type=int, default=200)
    parser.add_argument("--sleep-between-calls", type=float, default=0.3)
    parser.add_argument("--since-days", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive confirmation",
    )
    args = parser.parse_args()

    db_url, connect_args = _dsn()
    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    settings = get_settings()

    async with Session() as session:
        stmt = select(Workspace)
        if args.workspace_slug:
            stmt = stmt.where(Workspace.slug == args.workspace_slug)
        workspaces = (
            (await session.execute(stmt.order_by(Workspace.created_at.asc())))
            .scalars()
            .all()
        )
        if not workspaces:
            log.warning("no workspaces matched")
            return 0

        if not args.yes and not args.dry_run:
            print(
                f"\nAbout to back-fill mem0 across {len(workspaces)} "
                "workspace(s). This will burn OpenAI tokens. Continue?"
            )
            ans = input("Type 'yes' to proceed: ").strip().lower()
            if ans != "yes":
                print("aborted")
                return 0

        totals = {"workspaces": 0, "threads": 0, "messages_processed": 0, "facts_added": 0}
        for w in workspaces:
            try:
                s = await _backfill_workspace(
                    session,
                    workspace=w,
                    settings=settings,
                    sleep_between_calls=args.sleep_between_calls,
                    max_messages=args.max_messages_per_thread,
                    since_days=args.since_days,
                    dry_run=args.dry_run,
                )
                totals["workspaces"] += 1
                for k in ("threads", "messages_processed", "facts_added"):
                    totals[k] += s[k]
            except Exception:  # noqa: BLE001
                log.exception("workspace %s failed; continuing", w.slug)

        print()
        print(f"DONE — dry_run={args.dry_run} totals={totals}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
