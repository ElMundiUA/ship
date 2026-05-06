"""Unit tests for the claim-store reconciliation engine.

Stays pure-unit by stubbing the LLM judge and the nearest-neighbour
SQL query so the tests don't depend on a live Postgres or pgvector
index. The behaviours we want to lock down:

- the cosine fast-path (sim ≥ 0.95) folds without spending an LLM
  call;
- LLM-judge band returns ``duplicate`` / ``refines`` / ``contradicts``
  / ``unrelated`` with the right state mutations on each;
- a missing or failed LLM client in the judge band leaves the new
  claim alone (treated as ``unrelated``);
- a claim with no embedding gets reconciled-stamped without a
  neighbour query;
- ``reconciled_at`` is always stamped, including failure paths, so
  the cron tick's ``WHERE reconciled_at IS NULL`` filter never
  loops the same broken row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from backend.app.db.models.agent_memory import ClaimStatus
from backend.app.services import knowledge_reconciler as recon


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeClaim:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    claim_md: str = "claim"
    embedding: list[float] | None = field(default_factory=lambda: [0.1] * 8)
    status: str = ClaimStatus.ACTIVE
    superseded_by_id: uuid.UUID | None = None
    source_links: list[dict] = field(default_factory=list)
    last_seen_at: datetime | None = None
    reconciled_at: datetime | None = None


class _FakeSession:
    """Minimal session that:
    - returns a canned nearest-neighbour list when the reconciler
      executes the raw SQL,
    - resolves ``session.get(KnowledgeClaim, claim_id)`` to entries
      in a dict the test seeds.
    """

    def __init__(self, *, neighbours=None, claim_by_id=None):
        self.neighbours = list(neighbours or [])
        self.claim_by_id = dict(claim_by_id or {})
        self.flushed = False

    async def execute(self, stmt, params=None):
        # Return canned nearest-neighbour rows. The reconciler maps
        # them via ``row[0], row[1], row[2]`` so we mimic SQLAlchemy
        # Row tuples.
        return _FakeAllResult(
            [(n.claim_id, n.claim_md, n.similarity) for n in self.neighbours]
        )

    async def get(self, model, key):
        return self.claim_by_id.get(key)

    async def flush(self):
        self.flushed = True


class _FakeAllResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@dataclass
class _FakeLLM:
    decision: str
    reason: str = "synthetic"
    raises: bool = False
    calls: int = 0
    vendor: str = "fake"

    async def acomplete(self, messages, **kwargs) -> str:
        self.calls += 1
        if self.raises:
            raise RuntimeError("simulated judge failure")
        return f'{{"decision":"{self.decision}","reason":"{self.reason}"}}'

    async def astream(self, *args, **kwargs):  # pragma: no cover
        yield None


def _seed_claim(
    *,
    workspace_id: uuid.UUID,
    text: str = "existing claim",
    source_links: list[dict] | None = None,
) -> _FakeClaim:
    return _FakeClaim(
        workspace_id=workspace_id,
        claim_md=text,
        source_links=source_links or [],
    )


def _patch_nearest(monkeypatch, neighbours: list[recon._NearestRow]):
    async def _fake(*args, **kwargs):
        return list(neighbours)

    monkeypatch.setattr(recon, "_nearest_active_claims", _fake)


# ---------------------------------------------------------------------------
# Fast-path: cosine ≥ 0.95
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_duplicate_fast_path_does_not_call_llm(monkeypatch):
    """sim ≥ 0.95 short-circuits to duplicate without spending an LLM call."""
    ws = uuid.uuid4()
    new = _FakeClaim(workspace_id=ws, claim_md="new wording", source_links=[
        {"source_item_id": "doc-2", "extracted_at": "t2"}
    ])
    existing = _seed_claim(workspace_id=ws, source_links=[
        {"source_item_id": "doc-1", "extracted_at": "t1"}
    ])
    _patch_nearest(
        monkeypatch,
        [recon._NearestRow(claim_id=existing.id, similarity=0.97, claim_md=existing.claim_md)],
    )
    session = _FakeSession(claim_by_id={existing.id: existing})
    llm = _FakeLLM(decision="duplicate")  # would tick if called

    report = await recon.reconcile_claim(
        session, claim=new, llm_client=llm
    )

    assert report.decision == "duplicate"
    assert report.used_llm is False
    assert llm.calls == 0
    assert new.status == ClaimStatus.SUPERSEDED
    assert new.superseded_by_id == existing.id
    # source_links got merged into the canon row, not the superseded one
    sl_keys = {(e["source_item_id"], e["extracted_at"]) for e in existing.source_links}
    assert ("doc-1", "t1") in sl_keys
    assert ("doc-2", "t2") in sl_keys
    assert new.reconciled_at is not None


# ---------------------------------------------------------------------------
# LLM-judge band: 0.85 ≤ sim < 0.95
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_judge_duplicate_supersedes_new(monkeypatch):
    ws = uuid.uuid4()
    existing = _seed_claim(workspace_id=ws)
    new = _FakeClaim(workspace_id=ws)
    _patch_nearest(
        monkeypatch,
        [recon._NearestRow(claim_id=existing.id, similarity=0.88, claim_md=existing.claim_md)],
    )
    session = _FakeSession(claim_by_id={existing.id: existing})
    llm = _FakeLLM(decision="duplicate")

    report = await recon.reconcile_claim(session, claim=new, llm_client=llm)

    assert report.decision == "duplicate"
    assert report.used_llm is True
    assert new.status == ClaimStatus.SUPERSEDED
    assert new.superseded_by_id == existing.id
    assert existing.status == ClaimStatus.ACTIVE  # canon survives


@pytest.mark.asyncio
async def test_llm_judge_refines_supersedes_existing(monkeypatch):
    ws = uuid.uuid4()
    existing = _seed_claim(workspace_id=ws, text="old way")
    new = _FakeClaim(workspace_id=ws, claim_md="new way")
    _patch_nearest(
        monkeypatch,
        [recon._NearestRow(claim_id=existing.id, similarity=0.9, claim_md=existing.claim_md)],
    )
    session = _FakeSession(claim_by_id={existing.id: existing})
    llm = _FakeLLM(decision="refines")

    report = await recon.reconcile_claim(session, claim=new, llm_client=llm)

    assert report.decision == "refines"
    # Direction inverts vs duplicate: existing is now superseded, new is canon.
    assert existing.status == ClaimStatus.SUPERSEDED
    assert existing.superseded_by_id == new.id
    assert new.status == ClaimStatus.ACTIVE


@pytest.mark.asyncio
async def test_llm_judge_contradicts_marks_both_disputed(monkeypatch):
    ws = uuid.uuid4()
    existing = _seed_claim(workspace_id=ws, text="A is true")
    new = _FakeClaim(workspace_id=ws, claim_md="A is false")
    _patch_nearest(
        monkeypatch,
        [recon._NearestRow(claim_id=existing.id, similarity=0.9, claim_md=existing.claim_md)],
    )
    session = _FakeSession(claim_by_id={existing.id: existing})
    llm = _FakeLLM(decision="contradicts")

    report = await recon.reconcile_claim(session, claim=new, llm_client=llm)

    assert report.decision == "contradicts"
    assert existing.status == ClaimStatus.DISPUTED
    assert new.status == ClaimStatus.DISPUTED
    # Neither is a "winner" — no supersedes link gets written.
    assert existing.superseded_by_id is None
    assert new.superseded_by_id is None


@pytest.mark.asyncio
async def test_llm_judge_unrelated_no_op(monkeypatch):
    ws = uuid.uuid4()
    existing = _seed_claim(workspace_id=ws, text="A")
    new = _FakeClaim(workspace_id=ws, claim_md="B")
    _patch_nearest(
        monkeypatch,
        [recon._NearestRow(claim_id=existing.id, similarity=0.9, claim_md="A")],
    )
    session = _FakeSession(claim_by_id={existing.id: existing})
    llm = _FakeLLM(decision="unrelated")

    report = await recon.reconcile_claim(session, claim=new, llm_client=llm)

    assert report.decision == "unrelated"
    assert new.status == ClaimStatus.ACTIVE
    assert existing.status == ClaimStatus.ACTIVE
    assert new.reconciled_at is not None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_embedding_marks_no_match_without_query(monkeypatch):
    new = _FakeClaim(embedding=None)

    async def _should_not_query(*args, **kwargs):
        raise AssertionError("nearest query must not run for embeddingless claim")

    monkeypatch.setattr(recon, "_nearest_active_claims", _should_not_query)
    session = _FakeSession()

    report = await recon.reconcile_claim(session, claim=new, llm_client=None)

    assert report.decision == "no_match"
    assert new.reconciled_at is not None


@pytest.mark.asyncio
async def test_numpy_array_embedding_does_not_short_circuit(monkeypatch):
    """pgvector returns the column as a ``numpy.ndarray``; ``not arr``
    raises ``ValueError: ambiguous truth value`` on multi-element
    arrays. The reconciler must use ``is None`` instead of truthiness
    so prod claims (real ndarray) reach the nearest-neighbour path
    instead of crashing the whole batch.

    Prod incident 2026-05-06: every reconciler tick after the first
    extractor flush silently rolled back on the first claim because
    the truthiness check tripped this; 488 claims sat unreconciled
    for an hour. Test guards against the regression.
    """
    import numpy as np

    new = _FakeClaim(embedding=np.array([0.1] * 8))
    ws = new.workspace_id
    existing = _seed_claim(workspace_id=ws)
    _patch_nearest(
        monkeypatch,
        [
            recon._NearestRow(
                claim_id=existing.id,
                similarity=0.97,
                claim_md=existing.claim_md,
            )
        ],
    )
    session = _FakeSession(claim_by_id={existing.id: existing})

    # If the truthiness check still fires, this raises ValueError
    # before we get to the assertion.
    report = await recon.reconcile_claim(
        session, claim=new, llm_client=None
    )

    assert report.decision == "duplicate"
    assert new.reconciled_at is not None


@pytest.mark.asyncio
async def test_below_judge_threshold_is_no_match(monkeypatch):
    """sim < 0.85 means no near-match — leave the claim alone."""
    ws = uuid.uuid4()
    new = _FakeClaim(workspace_id=ws)
    _patch_nearest(
        monkeypatch,
        [recon._NearestRow(claim_id=uuid.uuid4(), similarity=0.6, claim_md="X")],
    )
    session = _FakeSession()
    llm = _FakeLLM(decision="duplicate")  # would fire if used

    report = await recon.reconcile_claim(session, claim=new, llm_client=llm)

    assert report.decision == "no_match"
    assert llm.calls == 0
    assert new.status == ClaimStatus.ACTIVE
    assert new.reconciled_at is not None


@pytest.mark.asyncio
async def test_judge_band_without_llm_falls_back_to_unrelated(monkeypatch):
    """If the LLM client is unavailable in the judge band, treat as unrelated
    rather than guessing — preserves the canon's correctness."""
    ws = uuid.uuid4()
    existing = _seed_claim(workspace_id=ws)
    new = _FakeClaim(workspace_id=ws)
    _patch_nearest(
        monkeypatch,
        [recon._NearestRow(claim_id=existing.id, similarity=0.88, claim_md=existing.claim_md)],
    )
    session = _FakeSession(claim_by_id={existing.id: existing})

    report = await recon.reconcile_claim(session, claim=new, llm_client=None)

    assert report.decision == "unrelated"
    assert new.status == ClaimStatus.ACTIVE
    assert existing.status == ClaimStatus.ACTIVE


@pytest.mark.asyncio
async def test_judge_failure_falls_back_to_unrelated(monkeypatch):
    """LLM raising mid-judge: treat as unrelated, stamp reconciled_at,
    don't mutate either row."""
    ws = uuid.uuid4()
    existing = _seed_claim(workspace_id=ws)
    new = _FakeClaim(workspace_id=ws)
    _patch_nearest(
        monkeypatch,
        [recon._NearestRow(claim_id=existing.id, similarity=0.88, claim_md=existing.claim_md)],
    )
    session = _FakeSession(claim_by_id={existing.id: existing})
    llm = _FakeLLM(decision="duplicate", raises=True)

    report = await recon.reconcile_claim(session, claim=new, llm_client=llm)

    assert report.decision == "unrelated"
    assert new.status == ClaimStatus.ACTIVE
    assert existing.status == ClaimStatus.ACTIVE
    assert new.reconciled_at is not None
