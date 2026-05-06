"""Unit tests for the source-document claim extractor.

These tests stay pure-unit by stubbing the LLM client and the
embedding helper so they don't depend on a live Postgres or any
network. The stages we want to lock down:

- the LLM JSON envelope (and the two common wrapping mistakes —
  markdown fences, top-level array) is parsed defensively;
- each accepted claim is persisted with the right status, kind,
  topic_tags + a source_link entry pointing back at the item;
- an exact-text duplicate doesn't insert a second row (the
  ``(workspace_id, claim_md_sha)`` uniqueness contract);
- bodies that haven't changed since the last extract are skipped
  (idempotency of the cron tick);
- LLM-call exceptions are absorbed: row is stamped extracted, no
  claims persisted, batch keeps going.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from backend.app.services import knowledge_claim_extractor as ext
from backend.app.db.models.agent_memory import (
    ClaimKind,
    ClaimStatus,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeItem:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = "Doc"
    external_url: str | None = "https://example/doc"
    body_md: str | None = "# Doc\n\nContent."
    body_md_sha: str | None = None
    extracted_at: datetime | None = None
    deleted_at: datetime | None = None


class _FakeSession:
    """Records added rows + simulates the (ws, sha) uniqueness check.

    The extractor only uses ``select`` to look up an existing claim
    by sha, ``add`` to persist new ones, and ``flush`` (no-op here).
    """

    def __init__(self):
        self.added: list = []
        self._by_sha: dict[tuple[uuid.UUID, str], object] = {}

    async def execute(self, stmt):
        # The extractor's only query is "select existing claim by
        # (workspace_id, claim_md_sha)". We extract the literal values
        # from the compiled where clause to look up our in-memory dict.
        # Production sessions don't need this contortion — this is just
        # for unit tests.
        try:
            compiled = stmt.compile(compile_kwargs={"literal_binds": False})
            params = compiled.params
            ws = params.get("workspace_id_1")
            sha = params.get("claim_md_sha_1")
        except Exception:
            ws = None
            sha = None
        existing = self._by_sha.get((ws, sha)) if (ws and sha) else None
        return _FakeResult(existing)

    def add(self, row) -> None:
        self.added.append(row)
        self._by_sha[(row.workspace_id, row.claim_md_sha)] = row

    async def flush(self) -> None:
        return None


class _FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar_one_or_none(self):
        return self._val


@dataclass
class _FakeLLM:
    response: str
    raises: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)
    vendor: str = "fake"

    async def acomplete(self, messages, **kwargs) -> str:
        if self.raises:
            raise RuntimeError("simulated llm failure")
        self.calls.append(tuple(m.content for m in messages))
        return self.response

    async def astream(self, *args, **kwargs):  # pragma: no cover
        yield None


@pytest.fixture(autouse=True)
def _stub_embed(monkeypatch):
    """Stub embed_text so tests don't try to reach OpenAI / Anthropic."""

    async def _fake(text, settings=None):
        return [0.0] * 16

    monkeypatch.setattr(ext, "embed_text", _fake)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_accepts_canonical_envelope():
    out = ext._parse_extractor_json(
        '{"claims":[{"text":"X","kind":"fact","topic_tags":["a","b"]}]}'
    )
    assert len(out) == 1
    assert out[0].text == "X"
    assert out[0].kind == "fact"
    assert out[0].topic_tags == ("a", "b")


def test_parser_strips_markdown_fences():
    out = ext._parse_extractor_json(
        '```json\n{"claims":[{"text":"X","kind":"rule","topic_tags":[]}]}\n```'
    )
    assert len(out) == 1
    assert out[0].kind == "rule"


def test_parser_accepts_bare_array():
    out = ext._parse_extractor_json(
        '[{"text":"X","kind":"fact","topic_tags":["a"]}]'
    )
    assert len(out) == 1


def test_parser_drops_invalid_kind_to_other():
    out = ext._parse_extractor_json(
        '{"claims":[{"text":"X","kind":"made-up","topic_tags":[]}]}'
    )
    assert out[0].kind == ClaimKind.OTHER


def test_parser_returns_empty_on_garbage():
    assert ext._parse_extractor_json("not json at all") == []
    assert ext._parse_extractor_json("") == []
    assert (
        ext._parse_extractor_json('{"unrelated": "shape"}') == []
    )


def test_parser_salvages_json_from_anthropic_preamble():
    """Anthropic ignores ``response_format`` and frequently wraps the
    JSON object in chatty preamble. The salvage step pulls the
    ``{...}`` substring out so prod doesn't get zero claims for
    every doc the way it did between P1 deploy and this fix."""
    raw = (
        "Sure, here are the atomic claims I extracted:\n\n"
        '{"claims":[{"text":"X is Y","kind":"fact","topic_tags":["a"]}]}'
        "\n\nLet me know if you want me to refine any of these."
    )
    out = ext._parse_extractor_json(raw)
    assert len(out) == 1
    assert out[0].text == "X is Y"


def test_parser_salvages_multiline_object_with_preamble():
    raw = (
        "Here is the JSON output:\n"
        "{\n"
        '  "claims": [\n'
        '    {"text": "claim 1", "kind": "fact", "topic_tags": []},\n'
        '    {"text": "claim 2", "kind": "rule", "topic_tags": ["x"]}\n'
        "  ]\n"
        "}\n"
    )
    out = ext._parse_extractor_json(raw)
    assert [c.text for c in out] == ["claim 1", "claim 2"]
    assert out[1].kind == "rule"


def test_parser_caps_claim_count():
    items = [
        {"text": f"c{i}", "kind": "fact", "topic_tags": []}
        for i in range(100)
    ]
    out = ext._parse_extractor_json('{"claims":' + str(items).replace("'", '"') + "}")
    assert len(out) == ext._MAX_CLAIMS_PER_ITEM


# ---------------------------------------------------------------------------
# extract_claims_for_item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_persists_one_claim_per_llm_item():
    item = _FakeItem(body_md="# Doc\n\nLinear FSM has 7 states.")
    session = _FakeSession()
    llm = _FakeLLM(
        response='{"claims":['
        '{"text":"Linear FSM has 7 states.","kind":"fact",'
        '"topic_tags":["linear","fsm"]}'
        "]}"
    )

    report = await ext.extract_claims_for_item(
        session, item=item, llm_client=llm
    )

    assert report.claims_created == 1
    assert len(session.added) == 1
    row = session.added[0]
    assert row.workspace_id == item.workspace_id
    assert row.claim_md == "Linear FSM has 7 states."
    assert row.kind == "fact"
    assert row.topic_tags == ["fsm", "linear"]
    assert row.status == ClaimStatus.ACTIVE
    assert row.confidence == 1.0
    # source link points back at item
    assert row.source_links[0]["source_item_id"] == str(item.id)
    assert row.source_links[0]["external_url"] == item.external_url
    # item bookkeeping was stamped
    assert item.body_md_sha is not None
    assert item.extracted_at is not None


@pytest.mark.asyncio
async def test_extract_dedup_collapses_to_existing_row():
    """A second extraction returning the same exact text doesn't
    insert a second row — the unique-key short-circuit fires and we
    only stamp last_seen_at on the existing row."""
    item = _FakeItem()
    session = _FakeSession()
    llm = _FakeLLM(
        response='{"claims":[{"text":"Same","kind":"fact","topic_tags":[]}]}'
    )
    await ext.extract_claims_for_item(session, item=item, llm_client=llm)
    assert len(session.added) == 1
    first_seen_before = session.added[0].last_seen_at

    # Re-run on a fresh item with the same workspace_id so the
    # uniqueness check sees the prior row.
    item2 = _FakeItem(workspace_id=item.workspace_id)
    report2 = await ext.extract_claims_for_item(
        session, item=item2, llm_client=llm
    )
    assert report2.claims_created == 0
    assert report2.claims_skipped_duplicate == 1
    assert len(session.added) == 1  # no new row
    # last_seen_at on the existing row was bumped forward
    assert session.added[0].last_seen_at >= first_seen_before


@pytest.mark.asyncio
async def test_extract_skips_unchanged_body():
    """Re-extracting an item whose body_md_sha already matches the
    current body skips the LLM call — that's the idempotency
    invariant the cron tick relies on."""
    item = _FakeItem(body_md="# Doc\n\nstable text")
    sha = ext._sha256("# Doc\n\nstable text")
    item.body_md_sha = sha
    item.extracted_at = datetime.now(timezone.utc)
    session = _FakeSession()
    llm = _FakeLLM(response="{}")  # would explode if called

    report = await ext.extract_claims_for_item(
        session, item=item, llm_client=llm
    )
    assert report.skipped_unchanged is True
    assert report.claims_created == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_extract_skips_when_body_empty():
    item = _FakeItem(body_md=None)
    session = _FakeSession()
    llm = _FakeLLM(response="{}")
    report = await ext.extract_claims_for_item(
        session, item=item, llm_client=llm
    )
    assert report.skipped_no_body is True
    assert llm.calls == []


@pytest.mark.asyncio
async def test_extract_absorbs_llm_failure():
    """LLM raising must not surface to the caller — the cron has
    higher-level retry, and a single broken doc shouldn't poison the
    batch. The item's body_md_sha is still stamped so a stuck call
    doesn't repeat every 15 minutes for the same broken body."""
    item = _FakeItem(body_md="some content")
    session = _FakeSession()
    llm = _FakeLLM(response="", raises=True)

    report = await ext.extract_claims_for_item(
        session, item=item, llm_client=llm
    )
    assert report.llm_failed is True
    assert report.claims_created == 0
    assert session.added == []
    # extracted_at + body_md_sha stamped to suppress retry storm
    assert item.extracted_at is not None
    assert item.body_md_sha is not None
