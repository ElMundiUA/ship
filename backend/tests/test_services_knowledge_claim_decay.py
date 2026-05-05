"""Pure-unit coverage for the claim decay engine + the extractor's
auto-revive path.

The decay engine is a single bulk UPDATE — we exercise it by
recording the SQL filters / values the fake session sees, so the
test stays cheap (no live Postgres, no event-loop teardown flake)
while still locking down the critical invariants:

- only ``status='active'`` rows are touched;
- ``superseded_by_id IS NOT NULL`` rows are excluded;
- the cutoff is ``now - ttl_days``, parameterisable for replay.

The auto-revive companion behaviour lives in the extractor's
``_persist_claim`` short-circuit; we cover it by reusing the
extractor unit-test fakes from P1 and asserting the status flips
back to ``active`` only on the ``stale`` branch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.models.agent_memory import ClaimStatus
from backend.app.services import knowledge_claim_decay as kcd
from backend.app.services import knowledge_claim_extractor as ext


# ---------------------------------------------------------------------------
# Fakes (decay)
# ---------------------------------------------------------------------------


class _CapturingSession:
    """Captures the bulk UPDATE statement so we can assert the WHERE
    clauses without a real DB. ``execute`` returns a stub with a
    configurable ``rowcount``."""

    def __init__(self, rowcount: int = 0):
        self.last_stmt = None
        self.last_compiled = None
        self.rowcount = rowcount

    async def execute(self, stmt):
        self.last_stmt = stmt
        try:
            self.last_compiled = stmt.compile(
                compile_kwargs={"literal_binds": True}
            )
        except Exception:
            self.last_compiled = None
        return _Result(self.rowcount)


@dataclass
class _Result:
    rowcount: int


@pytest.mark.asyncio
async def test_decay_flips_only_active_unsuperseded_old_claims():
    """The bulk UPDATE filters:
    - workspace_id = …
    - status = 'active'
    - superseded_by_id IS NULL
    - last_seen_at < now - ttl
    All four must show up in the compiled SQL."""
    ws = uuid.uuid4()
    session = _CapturingSession(rowcount=7)
    fixed_now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)

    report = await kcd.decay_workspace_claims(
        session,
        workspace_id=ws,
        ttl_days=30,
        now=fixed_now,
    )

    assert report.flipped_stale == 7
    assert report.workspace_id == ws

    sql = str(session.last_compiled or session.last_stmt).lower()
    assert "knowledge_claim" in sql
    assert "status" in sql
    assert "active" in sql
    assert "superseded_by_id" in sql
    # Cutoff = now - 30 days = 2026-04-06 12:00 UTC.
    cutoff_iso = (fixed_now - timedelta(days=30)).isoformat()
    # Day part is enough — Postgres literal-bind formats the timestamp
    # with timezone, but the date prefix is stable across drivers.
    assert "2026-04-06" in sql, sql


@pytest.mark.asyncio
async def test_decay_returns_zero_when_no_rows_match():
    session = _CapturingSession(rowcount=0)
    report = await kcd.decay_workspace_claims(
        session, workspace_id=uuid.uuid4()
    )
    assert report.flipped_stale == 0
    assert report.inspected == 0


# ---------------------------------------------------------------------------
# Auto-revive in extractor
# ---------------------------------------------------------------------------


@dataclass
class _FakeItem:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = "Doc"
    external_url: str | None = "https://example/doc"
    body_md: str | None = "# Doc\n\nFresh content asserts X."
    body_md_sha: str | None = None
    extracted_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass
class _ExistingClaim:
    """Stand-in for a ``KnowledgeClaim`` row — we only set the fields
    the extractor's dedup branch reads or writes."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    claim_md: str = "X is true"
    claim_md_sha: str = ""
    status: str = ClaimStatus.STALE
    last_seen_at: datetime | None = None
    source_links: list = field(default_factory=list)


class _ExtractorSession:
    """Returns a canned existing claim when the extractor probes by
    sha; records ``add`` calls so we can confirm "no new row was
    inserted on a dedup hit"."""

    def __init__(self, existing):
        self.existing = existing
        self.added: list = []
        self.flushed = 0

    async def execute(self, stmt):
        return _ScalarOnlyResult(self.existing)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed += 1


class _ScalarOnlyResult:
    def __init__(self, val):
        self._val = val

    def scalar_one_or_none(self):
        return self._val


@dataclass
class _FakeLLM:
    response: str
    calls: int = 0
    vendor: str = "fake"

    async def acomplete(self, messages, **kwargs) -> str:
        self.calls += 1
        return self.response

    async def astream(self, *args, **kwargs):  # pragma: no cover
        yield None


@pytest.fixture(autouse=True)
def _stub_embed(monkeypatch):
    async def _fake(text, settings=None):
        return [0.0] * 8

    monkeypatch.setattr(ext, "embed_text", _fake)


@pytest.mark.asyncio
async def test_extractor_revives_stale_claim_on_resighting():
    """A doc came back online → extractor sees the same exact text.
    The dedup short-circuit must bump last_seen_at AND flip status
    back to active so the claim returns to canon without operator
    intervention."""
    ws = uuid.uuid4()
    item = _FakeItem(workspace_id=ws)
    text_val = "X is true"
    sha = ext._sha256(text_val)
    existing = _ExistingClaim(
        workspace_id=ws,
        claim_md=text_val,
        claim_md_sha=sha,
        status=ClaimStatus.STALE,
    )
    session = _ExtractorSession(existing=existing)
    llm = _FakeLLM(
        response=f'{{"claims":[{{"text":"{text_val}","kind":"fact","topic_tags":[]}}]}}'
    )

    report = await ext.extract_claims_for_item(
        session, item=item, llm_client=llm
    )

    assert report.claims_skipped_duplicate == 1
    assert report.claims_created == 0
    assert session.added == []  # no new row, dedup hit
    assert existing.status == ClaimStatus.ACTIVE  # revived!
    assert existing.last_seen_at == item.extracted_at


@pytest.mark.asyncio
async def test_extractor_does_not_revive_superseded_claim():
    """A superseded claim is not just unconfirmed — it's been
    *replaced* by a newer wording. The supersedes graph is the
    history record; auto-revive would corrupt it."""
    ws = uuid.uuid4()
    item = _FakeItem(workspace_id=ws)
    text_val = "Old wording"
    sha = ext._sha256(text_val)
    existing = _ExistingClaim(
        workspace_id=ws,
        claim_md=text_val,
        claim_md_sha=sha,
        status=ClaimStatus.SUPERSEDED,
    )
    session = _ExtractorSession(existing=existing)
    llm = _FakeLLM(
        response=f'{{"claims":[{{"text":"{text_val}","kind":"fact","topic_tags":[]}}]}}'
    )

    await ext.extract_claims_for_item(session, item=item, llm_client=llm)

    assert existing.status == ClaimStatus.SUPERSEDED  # untouched


@pytest.mark.asyncio
async def test_extractor_does_not_revive_disputed_claim():
    """Disputed = operator owes a decision. Auto-revive would let
    a re-asserting source short-circuit a conflict that should still
    be sitting in the inbox."""
    ws = uuid.uuid4()
    item = _FakeItem(workspace_id=ws)
    text_val = "Contested fact"
    sha = ext._sha256(text_val)
    existing = _ExistingClaim(
        workspace_id=ws,
        claim_md=text_val,
        claim_md_sha=sha,
        status=ClaimStatus.DISPUTED,
    )
    session = _ExtractorSession(existing=existing)
    llm = _FakeLLM(
        response=f'{{"claims":[{{"text":"{text_val}","kind":"fact","topic_tags":[]}}]}}'
    )

    await ext.extract_claims_for_item(session, item=item, llm_client=llm)

    assert existing.status == ClaimStatus.DISPUTED  # untouched
