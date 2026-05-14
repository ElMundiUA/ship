"""E17/ELS-130 — one-shot: backfill ``my-memory`` bucket articles into mem0.

Walks every ``BucketArticle`` whose parent bucket is a
``scope=user`` / ``source_kind=agent_memory`` bucket (the
``my-memory`` slug for each user) and feeds the article body
through ``memory.add`` so the same content lives in mem0 going
forward. The bucket articles themselves stay on disk (the
``KnowledgeBucket`` model isn't going anywhere — other source kinds
still write there); they just stop being the system of record for
chat memory.

Idempotency: mem0's add path dedups internally on near-identical
text, so a re-run won't double-store. The local mirror table also
has a unique index on ``mem0_id`` — if the SAME mem0-side fact is
ever returned (e.g. mem0 collapses two near-duplicate articles), we
upsert the mirror in place.

Usage:

    # Dry-run — counts only, no mem0 calls, no OpenAI tokens
    python tools/scripts/migrate_bucket_memory_to_mem0.py --dry-run

    # Single workspace
    python tools/scripts/migrate_bucket_memory_to_mem0.py \\
        --workspace-slug denys-99938640

    # Whole prod
    python tools/scripts/migrate_bucket_memory_to_mem0.py --yes

Knobs:

    --sleep-between-calls 0.3     mem0 rate-limit cushion
    --max-articles-per-bucket 500 stop iterating after N articles
                                  per bucket (safety against runaways)

Env: DATABASE_URL, OPENAI_API_KEY, ENCRYPTION_KEY — same as the
backend container.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
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
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    KnowledgeBucket,
)
from backend.app.db.models.tenancy import Workspace
from backend.app.services.agent import memory as navigator_memory


log = logging.getLogger("ship.scripts.migrate_bucket_memory_to_mem0")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


# ``KnowledgeBucket.scope_kind`` value for user-private buckets.
_USER_SCOPE = "user"
# ``KnowledgeBucket.source_kind`` value for the chat-memory write path.
_AGENT_MEMORY_SOURCE = "agent_memory"


def _dsn() -> tuple[str, dict]:
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


async def _backfill_bucket(
    session: AsyncSession,
    *,
    bucket: KnowledgeBucket,
    settings,
    sleep_between_calls: float,
    max_articles: int,
    dry_run: bool,
) -> tuple[int, int]:
    """Returns ``(articles_processed, facts_added)``."""
    if bucket.user_id is None:
        log.info("  skip bucket %s — scope=user but user_id NULL", bucket.id)
        return 0, 0

    articles = (
        (
            await session.execute(
                select(BucketArticle)
                .where(
                    BucketArticle.bucket_id == bucket.id,
                    BucketArticle.status == BucketArticleStatus.PUBLISHED,
                    BucketArticle.archived_at.is_(None),
                )
                .order_by(BucketArticle.created_at.asc())
                .limit(max_articles)
            )
        )
        .scalars()
        .all()
    )
    if not articles:
        return 0, 0

    processed = 0
    facts_added = 0
    for a in articles:
        body = (a.body_md or "").strip()
        if not body:
            continue
        processed += 1
        if dry_run:
            continue
        try:
            # Each bucket article was a thread-level summary, not a
            # single-message extract, so we don't have ``source_message_id``.
            # mem0 still gets ``project_native_id=None``,
            # ``intent_at_capture=None`` — these are pre-E17 artefacts.
            added = await navigator_memory.add(
                session,
                workspace_id=bucket.workspace_id,
                owner_user_id=bucket.user_id,
                # Feed the title + body so mem0's extractor has the
                # topic banner alongside the summary. mem0 dedups
                # internally so a re-run is safe.
                message=f"{a.title}\n\n{body}",
                source_thread_id=None,
                source_message_id=None,
                source_message_position=None,
                project_native_id=None,
                intent_at_capture=None,
                settings=settings,
            )
            facts_added += len(added)
        except Exception:  # noqa: BLE001
            log.exception(
                "bucket-memory migrate add failed bucket=%s article=%s",
                bucket.id,
                a.id,
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
    max_articles: int,
    dry_run: bool,
) -> dict[str, int]:
    log.info(
        "workspace %s (%s) — starting bucket→mem0 migration",
        workspace.slug,
        workspace.id,
    )
    buckets = (
        (
            await session.execute(
                select(KnowledgeBucket).where(
                    KnowledgeBucket.workspace_id == workspace.id,
                    KnowledgeBucket.scope_kind == _USER_SCOPE,
                    KnowledgeBucket.source_kind == _AGENT_MEMORY_SOURCE,
                    KnowledgeBucket.archived_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    summary = {
        "buckets": len(buckets),
        "articles_processed": 0,
        "facts_added": 0,
    }
    for b in buckets:
        proc, added = await _backfill_bucket(
            session,
            bucket=b,
            settings=settings,
            sleep_between_calls=sleep_between_calls,
            max_articles=max_articles,
            dry_run=dry_run,
        )
        summary["articles_processed"] += proc
        summary["facts_added"] += added
        if not dry_run:
            # Commit per-bucket so a crash mid-run doesn't lose
            # everything completed before the failure.
            await session.commit()
    log.info("workspace %s — %s", workspace.slug, summary)
    return summary


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-slug", default=None)
    parser.add_argument("--max-articles-per-bucket", type=int, default=500)
    parser.add_argument("--sleep-between-calls", type=float, default=0.3)
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
                f"\nMigrating bucket-memory → mem0 across {len(workspaces)} "
                "workspace(s). Burns OpenAI tokens. Continue?"
            )
            ans = input("Type 'yes' to proceed: ").strip().lower()
            if ans != "yes":
                print("aborted")
                return 0

        totals = {
            "workspaces": 0,
            "buckets": 0,
            "articles_processed": 0,
            "facts_added": 0,
        }
        for w in workspaces:
            try:
                s = await _backfill_workspace(
                    session,
                    workspace=w,
                    settings=settings,
                    sleep_between_calls=args.sleep_between_calls,
                    max_articles=args.max_articles_per_bucket,
                    dry_run=args.dry_run,
                )
                totals["workspaces"] += 1
                for k in ("buckets", "articles_processed", "facts_added"):
                    totals[k] += s[k]
            except Exception:  # noqa: BLE001
                log.exception("workspace %s failed; continuing", w.slug)

        print()
        print(f"DONE — dry_run={args.dry_run} totals={totals}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
