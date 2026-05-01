"""KB-6 (ELS-40) backfill validation.

Drives the harvest → route pipeline against the workspace's *existing*
published BucketArticles to confirm the router would land each one
in its actual bucket. Synthesis itself is skipped on purpose — we
already have the published copy, no need to manufacture a draft.

What the script does:

1. For every published BucketArticle in the target workspace, build
   a synthetic ``Improvement(kind='knowledge_note',
   context.provenance.kind='manual_backfill')`` row with the
   article's title + body. ``routed_bucket_id`` is left null so the
   router picks it up.
2. Run ``route_pending_notes`` against the workspace.
3. Compare each routed-bucket vs the article's actual bucket.
4. Print a per-article report and a summary count.
5. Roll back the synthetic Improvement rows so the workspace state
   is identical to before the run (this is a validation, not a
   data write).

Outcome interpretation:

* All-match → pipeline routes existing canonical content into the
  right buckets; KB-2 doesn't have a systematic bias.
* Some-divergence → the router would have placed the article
  somewhere else; investigate centroid quality / bucket descriptions
  / article overlap. Not a blocker (operator approves drafts in KB-4
  anyway), but a useful signal before turning on autopilot.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
for raw in env_path.read_text().splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from backend.app.core.config import get_settings  # noqa: E402
from backend.app.db.models.agent_memory import (  # noqa: E402
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import Improvement  # noqa: E402
from backend.app.db.session import get_sessionmaker  # noqa: E402
from backend.app.services.agent.client import pick_default_client  # noqa: E402
from backend.app.services.knowledge_harvest import NOTE_KIND  # noqa: E402
from backend.app.services.knowledge_router import route_pending_notes  # noqa: E402


WS_ID = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")  # Ship-on-Ship


async def main() -> None:
    Sm = get_sessionmaker()
    try:
        client = pick_default_client(get_settings())
    except Exception as exc:
        print(f"warning: no LLM client ({exc}); centroid+hint only")
        client = None

    async with Sm() as session:
        # 1. Pull every published article in workspace buckets.
        articles = (
            await session.execute(
                select(BucketArticle, KnowledgeBucket)
                .join(KnowledgeBucket, KnowledgeBucket.id == BucketArticle.bucket_id)
                .where(KnowledgeBucket.workspace_id == WS_ID)
                .where(KnowledgeBucket.scope_kind == BucketScope.WORKSPACE)
                .where(BucketArticle.status == BucketArticleStatus.PUBLISHED)
                .where(BucketArticle.archived_at.is_(None))
                .order_by(KnowledgeBucket.slug, BucketArticle.title)
            )
        ).all()
        if not articles:
            print("no published articles to validate")
            return

        # 2. Build synthetic notes. We capture each fake-note's id back
        # to its real bucket so the comparison step doesn't have to
        # query the DB twice.
        synthetic_notes: list[tuple[uuid.UUID, KnowledgeBucket, BucketArticle]] = []
        for article, bucket in articles:
            note = Improvement(
                workspace_id=WS_ID,
                kind=NOTE_KIND,
                title=article.title,
                body=article.body_md or "",
                context={
                    "source_kind": "manual_backfill",
                    "source_id": str(article.id),
                    "ticket_ref": None,
                    "routed_bucket_id": None,
                    "route_confidence": None,
                    "bucket_hint": None,  # don't bias the test with hints
                    "atom_idx": 0,
                    "extractor": "manual_backfill_v1",
                    "harvested_at": datetime.now(timezone.utc).isoformat(),
                    "provenance": {
                        "kind": "manual_backfill",
                        "validation_only": True,
                        "actual_bucket_id": str(bucket.id),
                        "actual_bucket_slug": bucket.slug,
                    },
                },
            )
            session.add(note)
            await session.flush()
            synthetic_notes.append((note.id, bucket, article))

        # 3. Run the real router.
        report = await route_pending_notes(
            session, workspace_id=WS_ID, llm_client=client, limit=500
        )

        # 4. Compare routes.
        routed = {
            n.id: n
            for n in (
                await session.execute(
                    select(Improvement).where(
                        Improvement.id.in_([nid for nid, _, _ in synthetic_notes])
                    )
                )
            ).scalars().all()
        }

        match = 0
        diverge = 0
        no_fit = 0
        rows: list[str] = []
        for note_id, actual_bucket, article in synthetic_notes:
            n = routed[note_id]
            ctx = n.context or {}
            chosen_id = ctx.get("routed_bucket_id")
            confidence = ctx.get("route_confidence")
            source = ctx.get("route_source")

            if chosen_id is None:
                no_fit += 1
                verdict = "NO_FIT"
                chosen_slug = "(none)"
            elif chosen_id == str(actual_bucket.id):
                match += 1
                verdict = "MATCH"
                chosen_slug = actual_bucket.slug
            else:
                diverge += 1
                # Look up the bucket the router picked.
                chosen = await session.get(
                    KnowledgeBucket, uuid.UUID(chosen_id)
                )
                chosen_slug = chosen.slug if chosen else "?"
                verdict = "DIVERGE"
            rows.append(
                f"  [{verdict:8s}] conf={(confidence if confidence is not None else 0.0):.3f}"
                f"  src={source or '?':16s}  actual={actual_bucket.slug:30s}"
                f"  → routed={chosen_slug:30s}  · {article.title[:60]}"
            )

        print(f"== KB-6 backfill validation: {len(synthetic_notes)} articles ==")
        print(
            f"  match={match}  diverge={diverge}  no_fit={no_fit}  "
            f"errors={len(report.errors)}"
        )
        for r in rows:
            print(r)
        if report.errors:
            print("\nrouter errors:")
            for e in report.errors:
                print(f"  {e}")

        # 5. Roll back. This is a validation, not a write.
        await session.rollback()
        print("\n(rolled back — workspace state unchanged)")


if __name__ == "__main__":
    asyncio.run(main())
