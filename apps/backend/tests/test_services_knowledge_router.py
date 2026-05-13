"""Tests for KB-2 routing (ELS-36).

The router consumes pending ``Improvement(kind='knowledge_note')`` rows
and pins each to a workspace bucket via centroid → hint → LLM →
no_fit. Tests stub both the embedding API and the LLM client so we
don't need OPENAI_API_KEY in CI.
"""

from __future__ import annotations

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
from backend.app.services.knowledge_router import (
    AUTO_PIN_THRESHOLD,
    HINT_CONFIDENCE,
    route_pending_notes,
)


def _vec(seed: float) -> list[float]:
    """Deterministic vector. Two seeds with the same first dimension
    produce vectors with high cosine similarity; different first
    dimensions → low similarity. Lets us steer routing decisions
    without leaving the test."""
    base = [0.0] * EMBED_DIM
    base[0] = seed
    base[1] = 1.0
    return base


def _orthogonal_vec(seed: float) -> list[float]:
    """Vector with weight on a different dimension — low cosine vs
    ``_vec`` family."""
    base = [0.0] * EMBED_DIM
    base[10] = seed
    return base


@pytest_asyncio.fixture
async def workspace_with_buckets(db_session, seed_workspace):
    """One workspace, two buckets, each with a single embedded article."""
    _, _, workspace = seed_workspace

    arch = KnowledgeBucket(
        workspace_id=workspace.id,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
        slug="architecture-decisions",
        name="Architecture Decisions",
        description="ADRs and architectural choices.",
    )
    eng = KnowledgeBucket(
        workspace_id=workspace.id,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
        slug="engineering-standards",
        name="Engineering Standards",
        description="Conventions and standards we hold ourselves to.",
    )
    db_session.add_all([arch, eng])
    await db_session.flush()

    db_session.add_all(
        [
            BucketArticle(
                bucket_id=arch.id,
                slug="seed-arch",
                title="Seed ADR",
                body_md="seed body",
                content_sha="x" * 64,
                version=1,
                status=BucketArticleStatus.PUBLISHED,
                embedding=_vec(1.0),
            ),
            BucketArticle(
                bucket_id=eng.id,
                slug="seed-eng",
                title="Seed standard",
                body_md="seed body",
                content_sha="y" * 64,
                version=1,
                status=BucketArticleStatus.PUBLISHED,
                embedding=_orthogonal_vec(1.0),
            ),
        ]
    )
    await db_session.flush()
    return workspace, arch, eng


def _make_note(workspace_id, *, body="note body", bucket_hint=None) -> Improvement:
    return Improvement(
        workspace_id=workspace_id,
        repo_id=None,
        routine_run_id=None,
        kind=NOTE_KIND,
        title="atom title",
        body=body,
        impact=None,
        effort=None,
        context={
            "source_kind": "clarification",
            "source_id": str(uuid.uuid4()),
            "routed_bucket_id": None,
            "route_confidence": None,
            "bucket_hint": bucket_hint,
            "atom_idx": 0,
            "extractor": "llm_v1",
            "harvested_at": datetime.now(timezone.utc).isoformat(),
        },
    )


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


@pytest.mark.asyncio
async def test_route_auto_pin_above_threshold(
    db_session, workspace_with_buckets, monkeypatch
):
    """Note vector close to bucket centroid → auto-pin, no LLM call."""
    workspace, arch, _eng = workspace_with_buckets

    note = _make_note(workspace.id)
    db_session.add(note)
    await db_session.flush()

    # Embed the note as a vector very close to arch's centroid.
    async def fake_embed(text, settings=None):
        return _vec(1.0)

    monkeypatch.setattr(
        "backend.app.services.knowledge_router.embed_text", fake_embed
    )

    stub = _StubLLMClient([])  # no LLM calls expected on auto-pin
    report = await route_pending_notes(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.auto_pinned == 1
    assert report.routed_via_llm == 0
    assert stub.calls == []

    refreshed = (
        await db_session.execute(
            select(Improvement).where(Improvement.id == note.id)
        )
    ).scalar_one()
    assert refreshed.context["routed_bucket_id"] == str(arch.id)
    assert refreshed.context["route_confidence"] >= AUTO_PIN_THRESHOLD
    assert refreshed.context["route_source"] == "auto_pin"


@pytest.mark.asyncio
async def test_route_falls_back_to_bucket_hint_when_centroid_ambiguous(
    db_session, workspace_with_buckets, monkeypatch
):
    """Note vector orthogonal to every centroid → use hint slug."""
    workspace, _arch, eng = workspace_with_buckets
    note = _make_note(workspace.id, bucket_hint="engineering-standards")
    db_session.add(note)
    await db_session.flush()

    # Embed orthogonal to both centroids — nothing crosses the
    # auto-pin threshold.
    async def fake_embed(text, settings=None):
        v = [0.0] * EMBED_DIM
        v[42] = 1.0  # orthogonal to both seeded centroids
        return v

    monkeypatch.setattr(
        "backend.app.services.knowledge_router.embed_text", fake_embed
    )

    stub = _StubLLMClient([])  # hint matches → no LLM call
    report = await route_pending_notes(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.routed_via_hint == 1
    assert stub.calls == []

    refreshed = (
        await db_session.execute(
            select(Improvement).where(Improvement.id == note.id)
        )
    ).scalar_one()
    assert refreshed.context["routed_bucket_id"] == str(eng.id)
    assert refreshed.context["route_confidence"] == pytest.approx(HINT_CONFIDENCE)
    assert refreshed.context["route_source"] == "bucket_hint"


@pytest.mark.asyncio
async def test_route_uses_llm_tiebreaker_when_no_hint(
    db_session, workspace_with_buckets, monkeypatch
):
    """No hint, ambiguous centroid → LLM picks slug + confidence."""
    workspace, arch, _eng = workspace_with_buckets
    note = _make_note(workspace.id)  # no bucket_hint
    db_session.add(note)
    await db_session.flush()

    async def fake_embed(text, settings=None):
        v = [0.0] * EMBED_DIM
        v[42] = 1.0
        return v

    monkeypatch.setattr(
        "backend.app.services.knowledge_router.embed_text", fake_embed
    )

    stub = _StubLLMClient(
        '{"slug": "architecture-decisions", "confidence": 0.7}'
    )
    report = await route_pending_notes(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.routed_via_llm == 1
    assert len(stub.calls) == 1

    refreshed = (
        await db_session.execute(
            select(Improvement).where(Improvement.id == note.id)
        )
    ).scalar_one()
    assert refreshed.context["routed_bucket_id"] == str(arch.id)
    assert refreshed.context["route_confidence"] == pytest.approx(0.7)
    assert refreshed.context["route_source"] == "llm_tiebreaker"


@pytest.mark.asyncio
async def test_route_no_fit_when_llm_says_null(
    db_session, workspace_with_buckets, monkeypatch
):
    """LLM returns slug=null → note exits pending pool with confidence=0."""
    workspace, *_ = workspace_with_buckets
    note = _make_note(workspace.id)
    db_session.add(note)
    await db_session.flush()

    async def fake_embed(text, settings=None):
        v = [0.0] * EMBED_DIM
        v[42] = 1.0
        return v

    monkeypatch.setattr(
        "backend.app.services.knowledge_router.embed_text", fake_embed
    )

    stub = _StubLLMClient('{"slug": null, "confidence": 0.0}')
    report = await route_pending_notes(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.no_fit == 1

    refreshed = (
        await db_session.execute(
            select(Improvement).where(Improvement.id == note.id)
        )
    ).scalar_one()
    assert refreshed.context["routed_bucket_id"] is None
    assert refreshed.context["route_confidence"] == 0.0
    assert refreshed.context["route_source"] == "no_fit"


@pytest.mark.asyncio
async def test_route_uses_description_when_buckets_are_empty(
    db_session, seed_workspace, monkeypatch
):
    """Brand-new workspace: bucket exists with a description but zero
    articles. The router must still surface it to the LLM tiebreaker
    (using the description), not silently mark every note ``no_fit``.

    Pre-fix: ``_bucket_centroids`` filtered out buckets without
    embedded articles, so the LLM caller saw ``- (no buckets)`` and
    every note routed to ``no_fit`` forever. New workspaces never
    progressed past the ingest step.
    """
    _, _, workspace = seed_workspace

    arch = KnowledgeBucket(
        workspace_id=workspace.id,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
        slug="architecture-decisions",
        name="Architecture Decisions",
        description="ADRs and architectural choices.",
    )
    eng = KnowledgeBucket(
        workspace_id=workspace.id,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
        slug="engineering-standards",
        name="Engineering Standards",
        description="Conventions and standards we hold ourselves to.",
    )
    db_session.add_all([arch, eng])
    await db_session.flush()

    note = _make_note(workspace.id, body="proposed: adopt SemVer for the gateway")
    db_session.add(note)
    await db_session.flush()

    async def fake_embed(text, settings=None):
        return _vec(1.0)

    monkeypatch.setattr(
        "backend.app.services.knowledge_router.embed_text", fake_embed
    )

    stub = _StubLLMClient(
        '{"slug": "architecture-decisions", "confidence": 0.8}'
    )
    report = await route_pending_notes(
        db_session, workspace_id=workspace.id, llm_client=stub
    )
    assert report.routed_via_llm == 1, report
    assert report.skipped_no_buckets == 0
    assert len(stub.calls) == 1

    # Crucial: the LLM saw BOTH buckets in its catalogue, including the
    # description-only ones — pre-fix it saw none.
    user_msg = stub.calls[0]["messages"][1].content
    assert "architecture-decisions" in user_msg
    assert "engineering-standards" in user_msg
    assert "ADRs and architectural choices." in user_msg

    refreshed = (
        await db_session.execute(
            select(Improvement).where(Improvement.id == note.id)
        )
    ).scalar_one()
    assert refreshed.context["routed_bucket_id"] == str(arch.id)
    assert refreshed.context["route_source"] == "llm_tiebreaker"


@pytest.mark.asyncio
async def test_route_skips_when_workspace_has_no_buckets_at_all(
    db_session, seed_workspace, monkeypatch
):
    """Workspace with zero buckets total → notes stay pending.

    Distinct from the case above: there's literally nothing to route
    into, not even description-only buckets. Caller can use the
    ``skipped_no_buckets`` counter to surface "create a bucket first".
    """
    _, _, workspace = seed_workspace
    note = _make_note(workspace.id)
    db_session.add(note)
    await db_session.flush()

    async def fake_embed(text, settings=None):
        return _vec(1.0)

    monkeypatch.setattr(
        "backend.app.services.knowledge_router.embed_text", fake_embed
    )

    report = await route_pending_notes(
        db_session, workspace_id=workspace.id, llm_client=None
    )
    assert report.skipped_no_buckets == 1
    refreshed = (
        await db_session.execute(
            select(Improvement).where(Improvement.id == note.id)
        )
    ).scalar_one()
    # Untouched — next tick (after a bucket gains an article) routes it.
    assert refreshed.context["routed_bucket_id"] is None


@pytest.mark.asyncio
async def test_route_embed_failure_leaves_pending(
    db_session, workspace_with_buckets, monkeypatch
):
    """Embed call raises → note left pending, counted as embed_failed."""
    workspace, *_ = workspace_with_buckets
    note = _make_note(workspace.id)
    db_session.add(note)
    await db_session.flush()

    async def fake_embed_boom(text, settings=None):
        raise RuntimeError("simulated embed failure")

    monkeypatch.setattr(
        "backend.app.services.knowledge_router.embed_text", fake_embed_boom
    )

    report = await route_pending_notes(
        db_session, workspace_id=workspace.id, llm_client=None
    )
    assert report.skipped_embed_failed == 1

    refreshed = (
        await db_session.execute(
            select(Improvement).where(Improvement.id == note.id)
        )
    ).scalar_one()
    assert refreshed.context["routed_bucket_id"] is None
