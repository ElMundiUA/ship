"""Unit tests for the topic-view renderer.

Stays pure-unit by stubbing the LLM client, the active-claims query,
and the existing-view lookup so the tests don't need a live
Postgres. The behaviours we want to lock down:

- Below the density threshold the renderer skips without touching
  the LLM or writing rows.
- A cache hit (same ``claim_set_sha`` as the existing row) is a
  no-op.
- A cache miss with no LLM client falls back to the deterministic
  bullet body and stamps ``rendered_by_model='deterministic'``.
- A cache miss with an LLM client uses the LLM body and stamps the
  vendor.
- An LLM failure inside the call falls back to the deterministic
  body — no retry storm, the operator sees structured fallback.
- ``claim_set_sha`` is order-invariant on the underlying claim ids
  (so claims fetched in different orders by Postgres still hit the
  cache).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from backend.app.services import knowledge_topic_renderer as ktr


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeClaim:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    claim_md: str = "claim text"
    kind: str = "fact"


@dataclass
class _FakeView:
    workspace_id: uuid.UUID
    topic_tag: str
    title: str = ""
    body_md: str = ""
    claim_set_sha: str = ""
    claim_count: int = 0
    rendered_by_model: str | None = None
    last_rendered_at: datetime | None = None


class _FakeSession:
    """Minimal session that returns canned (claims, view) for the two
    queries the renderer issues — claims-by-tag and existing-view-
    by-(ws, tag)."""

    def __init__(self, *, claims=None, existing=None):
        self.claims_by_tag = list(claims or [])
        self.existing = existing
        self.added: list = []
        self.flush_count = 0

    async def execute(self, stmt):
        body = str(stmt)
        if "knowledge_topic_view" in body:
            return _FakeResult(self.existing)
        return _FakeResult(self.claims_by_tag, scalars=True)

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self):
        self.flush_count += 1


class _FakeResult:
    def __init__(self, val, *, scalars: bool = False):
        self._val = val
        self._scalars = scalars

    def scalar_one_or_none(self):
        return self._val

    def scalars(self):
        return self

    def all(self):
        return list(self._val) if isinstance(self._val, list) else []


@dataclass
class _FakeLLM:
    response: str
    raises: bool = False
    calls: int = 0
    vendor: str = "fakevendor"

    async def acomplete(self, messages, **kwargs) -> str:
        self.calls += 1
        if self.raises:
            raise RuntimeError("simulated render failure")
        return self.response

    async def astream(self, *args, **kwargs):  # pragma: no cover
        yield None


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_claim_set_sha_is_order_invariant():
    a = uuid.uuid4()
    b = uuid.uuid4()
    c = uuid.uuid4()
    assert ktr._claim_set_sha([a, b, c]) == ktr._claim_set_sha([c, a, b])


def test_claim_set_sha_distinguishes_membership():
    a = uuid.uuid4()
    b = uuid.uuid4()
    assert ktr._claim_set_sha([a, b]) != ktr._claim_set_sha([a])


# ---------------------------------------------------------------------------
# Density threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_topic_skips_when_under_threshold():
    """A single-claim topic isn't worth rendering — the LLM would
    produce a half-sentence article. The renderer skips and the
    cache stays untouched."""
    ws = uuid.uuid4()
    session = _FakeSession(
        claims=[_FakeClaim(workspace_id=ws)],  # only 1, threshold=3
        existing=None,
    )
    llm = _FakeLLM(response="should not be called")

    report = await ktr.render_topic(
        session, workspace_id=ws, topic_tag="tiny", llm_client=llm
    )

    assert report.skipped_low_density is True
    assert report.rendered is False
    assert llm.calls == 0
    assert session.added == []


# ---------------------------------------------------------------------------
# Cache hit / miss
# ---------------------------------------------------------------------------


def _three_claims(ws: uuid.UUID) -> list[_FakeClaim]:
    return [
        _FakeClaim(workspace_id=ws, claim_md="alpha"),
        _FakeClaim(workspace_id=ws, claim_md="beta", kind="rule"),
        _FakeClaim(workspace_id=ws, claim_md="gamma", kind="decision"),
    ]


@pytest.mark.asyncio
async def test_cache_hit_skips_llm_and_persistence():
    """Same claim set as last render → no LLM call, no DB writes."""
    ws = uuid.uuid4()
    claims = _three_claims(ws)
    sha = ktr._claim_set_sha([c.id for c in claims])
    existing = _FakeView(
        workspace_id=ws,
        topic_tag="t",
        body_md="cached body",
        claim_set_sha=sha,
        claim_count=3,
        rendered_by_model="vendorA",
    )
    session = _FakeSession(claims=claims, existing=existing)
    llm = _FakeLLM(response="should not be called")

    report = await ktr.render_topic(
        session, workspace_id=ws, topic_tag="t", llm_client=llm
    )

    assert report.skipped_unchanged is True
    assert report.rendered is False
    assert llm.calls == 0
    assert session.added == []
    # No mutation of the existing view.
    assert existing.body_md == "cached body"
    assert existing.rendered_by_model == "vendorA"


@pytest.mark.asyncio
async def test_cache_miss_with_llm_renders_and_inserts():
    """First render of a topic creates a row, stamps the LLM vendor."""
    ws = uuid.uuid4()
    claims = _three_claims(ws)
    session = _FakeSession(claims=claims, existing=None)
    llm = _FakeLLM(response="# Linear FSM\n\nLinear FSM uses 7 states. …")

    report = await ktr.render_topic(
        session, workspace_id=ws, topic_tag="linear-fsm", llm_client=llm
    )

    assert report.rendered is True
    assert report.used_llm is True
    assert llm.calls == 1
    assert len(session.added) == 1
    row = session.added[0]
    assert row.workspace_id == ws
    assert row.topic_tag == "linear-fsm"
    assert "Linear FSM uses 7 states." in row.body_md
    assert row.title == "Linear FSM"
    assert row.claim_count == 3
    assert row.rendered_by_model == "fakevendor"
    assert row.claim_set_sha == ktr._claim_set_sha([c.id for c in claims])


@pytest.mark.asyncio
async def test_cache_miss_without_llm_falls_back_to_deterministic():
    """No LLM client → bullet-list body, model stamp = 'deterministic'.
    Cache invariant still holds so the next tick with an LLM upgrades
    the same row in place."""
    ws = uuid.uuid4()
    claims = _three_claims(ws)
    session = _FakeSession(claims=claims, existing=None)

    report = await ktr.render_topic(
        session, workspace_id=ws, topic_tag="example-tag", llm_client=None
    )

    assert report.rendered is True
    assert report.used_llm is False
    row = session.added[0]
    assert row.rendered_by_model == "deterministic"
    assert "# Example Tag" in row.body_md
    # Bullet-list output starts with the title then a blank line, then
    # the kind-sorted claims.
    assert "- alpha" in row.body_md
    assert "_decision_: gamma" in row.body_md
    assert "_rule_: beta" in row.body_md


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_deterministic():
    """An LLM call that raises mid-render must not surface — we still
    get a structured fallback body and a row written, so search has
    something to index."""
    ws = uuid.uuid4()
    claims = _three_claims(ws)
    session = _FakeSession(claims=claims, existing=None)
    llm = _FakeLLM(response="", raises=True)

    report = await ktr.render_topic(
        session, workspace_id=ws, topic_tag="boom", llm_client=llm
    )

    assert report.rendered is True
    assert report.used_llm is False
    row = session.added[0]
    assert row.rendered_by_model == "deterministic"


@pytest.mark.asyncio
async def test_cache_miss_updates_existing_row_in_place():
    """A drifted claim set updates the existing row, not inserts a new
    one — uniqueness on (workspace, topic_tag) means we mutate."""
    ws = uuid.uuid4()
    claims = _three_claims(ws)
    existing = _FakeView(
        workspace_id=ws,
        topic_tag="evolving",
        body_md="old body",
        claim_set_sha="STALE_SHA",
        claim_count=2,
        rendered_by_model="oldvendor",
    )
    session = _FakeSession(claims=claims, existing=existing)
    llm = _FakeLLM(response="# Evolving\n\nFresh body.")

    report = await ktr.render_topic(
        session, workspace_id=ws, topic_tag="evolving", llm_client=llm
    )

    assert report.rendered is True
    # No new row added — we mutated the existing one.
    assert session.added == []
    assert "Fresh body." in existing.body_md
    assert existing.claim_set_sha != "STALE_SHA"
    assert existing.claim_count == 3
    assert existing.rendered_by_model == "fakevendor"
