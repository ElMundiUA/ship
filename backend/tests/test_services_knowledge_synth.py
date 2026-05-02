"""Tests for KB-3 synthesiser (ELS-37)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import Improvement
from backend.app.services.agent.embedding import EMBED_DIM
from backend.app.services.knowledge_harvest import NOTE_KIND
from backend.app.services.knowledge_synth import synthesise_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubLLMClient:
    vendor = "stub"

    def __init__(self, responses):
        if isinstance(responses, str):
            responses = [responses]
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def acomplete(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), **kwargs})
        if not self._responses:
            raise RuntimeError("stub LLM ran out of queued responses")
        return self._responses.pop(0)

    async def astream(self, messages, tools=(), **kwargs):  # pragma: no cover
        raise NotImplementedError


@pytest_asyncio.fixture
async def workspace_with_routed_notes(db_session, seed_workspace, monkeypatch):
    """Workspace + one bucket + two routed notes pinned to that bucket."""
    _, _, workspace = seed_workspace

    bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
        slug="architecture-decisions",
        name="Architecture Decisions",
        description="ADRs and architectural choices.",
    )
    db_session.add(bucket)
    await db_session.flush()

    notes = [
        Improvement(
            workspace_id=workspace.id,
            kind=NOTE_KIND,
            title=f"atom {i}",
            body=f"body of atom {i}",
            context={
                "source_kind": "clarification",
                "source_id": str(uuid.uuid4()),
                "ticket_ref": f"ELS-{20+i}",
                "routed_bucket_id": str(bucket.id),
                "route_confidence": 0.9,
                "synthesised_into_article_id": None,
                "atom_idx": i,
                "extractor": "llm_v1",
                "harvested_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        for i in range(2)
    ]
    for n in notes:
        db_session.add(n)
    await db_session.flush()

    # Stub embed_text so the synthesiser doesn't hit OpenAI.
    async def fake_embed(text, settings=None):
        v = [0.0] * EMBED_DIM
        v[0] = 0.42
        return v

    monkeypatch.setattr(
        "backend.app.services.knowledge_synth.embed_text", fake_embed
    )

    return workspace, bucket, notes


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synth_creates_new_draft_article(
    db_session, workspace_with_routed_notes
):
    """LLM action='new' → new BucketArticle status='draft' v1, notes consumed."""
    workspace, bucket, notes = workspace_with_routed_notes

    stub = _StubLLMClient(json.dumps({
        "action": "new",
        "slug": "branchless-exit-protocol",
        "title": "ADR: Branchless agent exit protocol",
        "body_md": "## Decision\n\nAgents post to /agent-runs/finish.\n\n## Why\n...",
        "supersedes_slug": None,
    }))

    report = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.drafts_created == 1
    assert report.notes_consumed == 2

    art = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.bucket_id == bucket.id)
        )
    ).scalar_one()
    assert art.status == BucketArticleStatus.DRAFT
    assert art.slug == "branchless-exit-protocol"
    assert art.version == 1
    assert art.supersedes_id is None
    assert art.embedding is not None  # acceptance contract
    assert art.provenance["kind"] == "auto_routed_notes"
    assert sorted(art.provenance["source_note_ids"]) == sorted(
        [str(n.id) for n in notes]
    )

    # Bucket.updated_at moved.
    refreshed_bucket = await db_session.get(KnowledgeBucket, bucket.id)
    assert refreshed_bucket.updated_at >= datetime.now(timezone.utc).replace(
        microsecond=0
    ) - (datetime.now(timezone.utc) - datetime.now(timezone.utc))

    # Notes marked consumed.
    for n in notes:
        await db_session.refresh(n)
        assert n.context["synthesised_into_article_id"] == str(art.id)


@pytest.mark.asyncio
async def test_synth_update_increments_version_and_links_supersedes(
    db_session, workspace_with_routed_notes
):
    """LLM action='update' on an existing slug → version bump + supersedes_id."""
    workspace, bucket, _notes = workspace_with_routed_notes

    prev = BucketArticle(
        bucket_id=bucket.id,
        slug="branchless-exit-protocol",
        title="ADR: Branchless agent exit protocol",
        body_md="## Decision\n\nv1 body",
        content_sha="a" * 64,
        version=1,
        status=BucketArticleStatus.PUBLISHED,
    )
    db_session.add(prev)
    await db_session.flush()

    stub = _StubLLMClient(json.dumps({
        "action": "update",
        "slug": "branchless-exit-protocol",
        "title": "ADR: Branchless agent exit protocol",
        "body_md": "## Decision (v2)\n\nUpdated body with no-work-not-blocker rule.",
        "supersedes_slug": "branchless-exit-protocol",
    }))

    report = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.drafts_created == 1

    drafts = (
        await db_session.execute(
            select(BucketArticle)
            .where(BucketArticle.bucket_id == bucket.id)
            .where(BucketArticle.status == BucketArticleStatus.DRAFT)
        )
    ).scalars().all()
    assert len(drafts) == 1
    new_draft = drafts[0]
    assert new_draft.version == 2
    assert new_draft.supersedes_id == prev.id

    # Prior published row stays published — KB-4 flips it on approval.
    refreshed_prev = await db_session.get(BucketArticle, prev.id)
    assert refreshed_prev.status == BucketArticleStatus.PUBLISHED


@pytest.mark.asyncio
async def test_synth_skips_when_no_pending_notes(
    db_session, seed_workspace, monkeypatch
):
    """Bucket exists but no routed notes → no draft, no LLM call."""
    _, _, workspace = seed_workspace
    bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
        slug="architecture-decisions",
        name="Architecture Decisions",
        description=None,
    )
    db_session.add(bucket)
    await db_session.flush()

    async def fake_embed(text, settings=None):
        return [0.0] * EMBED_DIM

    monkeypatch.setattr(
        "backend.app.services.knowledge_synth.embed_text", fake_embed
    )

    stub = _StubLLMClient([])
    report = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.drafts_created == 0
    assert report.drafts_skipped_no_notes == 1
    assert stub.calls == []


@pytest.mark.asyncio
async def test_synth_skips_when_no_llm_client(
    db_session, workspace_with_routed_notes
):
    """LLM client missing → notes stay pending, counted as skipped_no_llm."""
    workspace, bucket, notes = workspace_with_routed_notes

    report = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=None
    )
    assert report.drafts_created == 0
    assert report.drafts_skipped_no_llm == 1

    # Notes still pending.
    for n in notes:
        await db_session.refresh(n)
        assert n.context.get("synthesised_into_article_id") is None


@pytest.mark.asyncio
async def test_synth_skip_outcome_creates_no_draft(
    db_session, workspace_with_routed_notes
):
    """LLM returns action='skip' → no article, notes stay pending."""
    workspace, _bucket, notes = workspace_with_routed_notes

    stub = _StubLLMClient(json.dumps({"action": "skip"}))
    report = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.drafts_created == 0
    for n in notes:
        await db_session.refresh(n)
        assert n.context.get("synthesised_into_article_id") is None


@pytest.mark.asyncio
async def test_synth_idempotent_on_dup_content(
    db_session, workspace_with_routed_notes
):
    """A second tick with the same body+slug doesn't create a second draft."""
    workspace, bucket, _notes = workspace_with_routed_notes

    body = "## Decision\n\nstable body — same content_sha both ticks"
    stub_payload = json.dumps({
        "action": "new",
        "slug": "stable-decision",
        "title": "Stable decision",
        "body_md": body,
        "supersedes_slug": None,
    })
    stub = _StubLLMClient([stub_payload, stub_payload])

    first = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert first.drafts_created == 1

    # Reset the consumed flag so the second tick sees the same notes
    # again — simulates a redelivery / lock-race.
    pending = (
        await db_session.execute(
            select(Improvement).where(Improvement.workspace_id == workspace.id)
        )
    ).scalars().all()
    for n in pending:
        ctx = dict(n.context)
        ctx["synthesised_into_article_id"] = None
        n.context = ctx
    await db_session.flush()

    second = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert second.drafts_created == 0
    assert second.drafts_skipped_dup_content == 1

    arts = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.bucket_id == bucket.id)
        )
    ).scalars().all()
    assert len(arts) == 1


# ---------------------------------------------------------------------------
# Archive action — pin the new fourth verdict shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synth_archive_emits_inbox_proposal(
    db_session, workspace_with_routed_notes
):
    """LLM action='archive' on an existing slug → InboxItem with
    payload.kind='auto_routed_archive_proposal' targeting the named
    article. No new BucketArticle is written — the proposal is the
    operator-review surface, not a draft."""
    from backend.app.db.models.inbox import InboxItem
    from backend.app.db.models.tenancy import AuditLog

    workspace, bucket, notes = workspace_with_routed_notes

    # Seed the article the LLM will vote to archive.
    target = BucketArticle(
        bucket_id=bucket.id,
        slug="workspace-equals-project-tracker-scope",
        title="ADR: tracker is workspace-scoped",
        body_md="## Decision\n\nOne tracker per workspace, no per-repo override.",
        content_sha="b" * 64,
        version=1,
        status=BucketArticleStatus.PUBLISHED,
    )
    db_session.add(target)
    await db_session.flush()

    stub = _StubLLMClient(
        json.dumps(
            {
                "action": "archive",
                "archive_slug": "workspace-equals-project-tracker-scope",
                "archive_reason": (
                    "Integration.repo_id was added in commit cf9f983 — "
                    "per-repo trackers now exist."
                ),
            }
        )
    )

    report = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.archive_proposals_emitted == 1
    assert report.drafts_created == 0
    assert report.notes_consumed == 2

    # Article is NOT yet archived — that's the operator's call.
    fresh_target = await db_session.get(BucketArticle, target.id)
    assert fresh_target.archived_at is None
    assert fresh_target.status == BucketArticleStatus.PUBLISHED

    # InboxItem with the new payload kind exists.
    items = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert len(items) == 1
    item = items[0]
    assert item.payload["kind"] == "auto_routed_archive_proposal"
    assert item.payload["article_id"] == str(target.id)
    assert item.payload["bucket_slug"] == bucket.slug
    assert "Integration.repo_id" in item.payload["reason"]
    assert item.source_table == "bucket_articles"
    assert item.source_id == target.id
    assert item.intake_reason == "knowledge_archive_review"

    # Audit row mirrors the proposal so a forensic sweep can find it.
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "knowledge.article.archive_proposed",
                AuditLog.target_id == str(target.id),
            )
        )
    ).scalars().all()
    assert len(audit) == 1

    # Notes consumed against the existing target id.
    for n in notes:
        await db_session.refresh(n)
        assert n.context["synthesised_into_article_id"] == str(target.id)


@pytest.mark.asyncio
async def test_synth_archive_skipped_for_unknown_slug(
    db_session, workspace_with_routed_notes
):
    """LLM hallucinated ``archive_slug`` — slug doesn't exist in the
    bucket. We still consume the notes (otherwise the next tick
    would loop) but emit no inbox row and no audit row."""
    from backend.app.db.models.inbox import InboxItem
    from backend.app.db.models.tenancy import AuditLog

    workspace, bucket, notes = workspace_with_routed_notes

    stub = _StubLLMClient(
        json.dumps(
            {
                "action": "archive",
                "archive_slug": "i-do-not-exist",
                "archive_reason": "irrelevant",
            }
        )
    )

    report = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.archive_proposals_emitted == 0
    assert report.archive_proposals_skipped_unknown_slug == 1
    assert report.notes_consumed == 2

    inbox_rows = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert inbox_rows == []
    audit_rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "knowledge.article.archive_proposed"
            )
        )
    ).scalars().all()
    assert audit_rows == []

    # Notes still marked consumed so the next tick doesn't redrive.
    for n in notes:
        await db_session.refresh(n)
        assert n.context["synthesised_into_article_id"] is not None


@pytest.mark.asyncio
async def test_synth_skip_action_writes_nothing(
    db_session, workspace_with_routed_notes
):
    """action='skip' is the LLM's escape hatch for thin / off-topic
    notes. It used to be parsed via the ``action not in (new, update)``
    fast-path; with the new ``archive`` branch we want to make sure
    skip still writes nothing and registers as ``no_llm`` in the
    report (matches pre-fix behaviour)."""
    from backend.app.db.models.inbox import InboxItem

    workspace, bucket, _notes = workspace_with_routed_notes
    stub = _StubLLMClient(json.dumps({"action": "skip"}))

    report = await synthesise_workspace(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.drafts_created == 0
    assert report.archive_proposals_emitted == 0

    arts = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.bucket_id == bucket.id)
        )
    ).scalars().all()
    assert arts == []
    inbox_rows = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert inbox_rows == []
