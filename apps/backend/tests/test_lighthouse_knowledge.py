"""K5 — Lighthouse read cutover.

Covers the dual-read wrapper (Lighthouse-first, internal fallback), the
hit mapper, and the HTTP client's workspace scoping. All offline: the
dual-read tests monkeypatch the client + internal path so no DB is
needed, and the client test uses an httpx MockTransport.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

import backend.app.services.knowledge_search as ks
from backend.app.integrations.lighthouse import provisioning as lh_prov
from backend.app.integrations.lighthouse.client import LighthouseClient
from backend.app.services.knowledge_search import (
    KnowledgeSearchHit,
    _map_lighthouse_hit,
)

_INTERNAL_SENTINEL = KnowledgeSearchHit(
    id=uuid.uuid4(),
    source="internal-sentinel",
    scope_kind="workspace",
    score=0.0,
    rank_bucket="workspace",
    snippet="from internal index",
)


class _FakeClient:
    def __init__(self, *, hits=None, exc=None):
        self._hits = hits or []
        self._exc = exc
        self.calls: list[tuple] = []

    async def search(self, *, workspace_id, query, top_k):
        self.calls.append((str(workspace_id), query, top_k))
        if self._exc is not None:
            raise self._exc
        return self._hits


@pytest.fixture
def _patch_internal(monkeypatch):
    """Replace the internal index path with a sentinel so fallback is
    observable without seeding Postgres."""

    async def _fake_internal(session, **kwargs):
        return [_INTERNAL_SENTINEL]

    monkeypatch.setattr(ks, "_search_internal", _fake_internal)


def _use_client(monkeypatch, client) -> None:
    monkeypatch.setattr(ks, "build_lighthouse_client", lambda settings: client)


# ---- mapper -----------------------------------------------------------


def test_map_lighthouse_hit_splits_title_and_snippet() -> None:
    node = str(uuid.uuid4())
    hit = _map_lighthouse_hit(
        {"node_id": node, "summary": "# OAuth flow\nUse PKCE for SPAs.",
         "episode_ids": [node]},
        rank=0,
        total=1,
    )
    assert hit.id == uuid.UUID(node)
    assert hit.title == "OAuth flow"
    assert hit.snippet == "Use PKCE for SPAs."
    assert hit.source == "lighthouse"
    assert hit.score == 1.0


def test_map_lighthouse_hit_handles_plain_summary() -> None:
    hit = _map_lighthouse_hit(
        {"node_id": "not-a-uuid", "summary": "just a snippet"},
        rank=1,
        total=2,
    )
    assert hit.title is None
    assert hit.snippet == "just a snippet"
    # Non-uuid node_id is mapped to a stable derived uuid (no crash).
    assert isinstance(hit.id, uuid.UUID)


# ---- dual-read --------------------------------------------------------


@pytest.mark.asyncio
async def test_dual_read_returns_mapped_lighthouse_hits(monkeypatch, _patch_internal):
    ws = uuid.uuid4()
    node = str(uuid.uuid4())
    client = _FakeClient(hits=[{"node_id": node, "summary": "# T\nbody"}])
    _use_client(monkeypatch, client)

    hits = await ks.search_workspace_knowledge(
        None, workspace_id=ws, query="auth", limit=5, settings=object()
    )
    assert [h.source for h in hits] == ["lighthouse"]
    assert client.calls == [(str(ws), "auth", 5)]


@pytest.mark.asyncio
async def test_dual_read_falls_back_when_lighthouse_empty(monkeypatch, _patch_internal):
    _use_client(monkeypatch, _FakeClient(hits=[]))
    hits = await ks.search_workspace_knowledge(
        None, workspace_id=uuid.uuid4(), query="auth", settings=object()
    )
    assert hits == [_INTERNAL_SENTINEL]


@pytest.mark.asyncio
async def test_dual_read_falls_back_on_error(monkeypatch, _patch_internal):
    _use_client(monkeypatch, _FakeClient(exc=httpx.ConnectError("down")))
    hits = await ks.search_workspace_knowledge(
        None, workspace_id=uuid.uuid4(), query="auth", settings=object()
    )
    assert hits == [_INTERNAL_SENTINEL]


@pytest.mark.asyncio
async def test_dual_read_uses_internal_when_lighthouse_disabled(
    monkeypatch, _patch_internal
):
    _use_client(monkeypatch, None)  # build_lighthouse_client → None
    hits = await ks.search_workspace_knowledge(
        None, workspace_id=uuid.uuid4(), query="auth", settings=object()
    )
    assert hits == [_INTERNAL_SENTINEL]


@pytest.mark.asyncio
async def test_dual_read_skips_lighthouse_when_bucket_slug_pinned(
    monkeypatch, _patch_internal
):
    client = _FakeClient(hits=[{"node_id": str(uuid.uuid4()), "summary": "x"}])
    _use_client(monkeypatch, client)
    hits = await ks.search_workspace_knowledge(
        None,
        workspace_id=uuid.uuid4(),
        query="auth",
        bucket_slug="runbooks",
        settings=object(),
    )
    # A pinned bucket only the internal index can honour → Lighthouse skipped.
    assert hits == [_INTERNAL_SENTINEL]
    assert client.calls == []


@pytest.mark.asyncio
async def test_dual_read_blank_query_returns_empty(monkeypatch, _patch_internal):
    _use_client(monkeypatch, _FakeClient(hits=[{"node_id": "x", "summary": "y"}]))
    hits = await ks.search_workspace_knowledge(
        None, workspace_id=uuid.uuid4(), query="   ", settings=object()
    )
    assert hits == []


# ---- client -----------------------------------------------------------


@pytest.mark.asyncio
async def test_client_search_scopes_by_workspace_header(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["q"] = request.url.params.get("q")
        captured["ws"] = request.headers.get("x-workspace")
        return httpx.Response(
            200,
            json={"query": "auth", "hits": [{"node_id": "n1", "summary": "# A\nb"}]},
        )

    orig = httpx.AsyncClient

    def _factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return orig(**kwargs)

    monkeypatch.setattr(
        "backend.app.integrations.lighthouse.client.httpx.AsyncClient", _factory
    )

    client = LighthouseClient(base_url="https://lh.example")
    ws = uuid.uuid4()
    hits = await client.search(workspace_id=ws, query="auth", top_k=7)

    assert captured["path"] == "/v1/search"
    assert captured["q"] == "auth"
    assert captured["ws"] == str(ws)
    assert hits == [{"node_id": "n1", "summary": "# A\nb"}]


# ---- corpus (K7) ------------------------------------------------------


@pytest.mark.asyncio
async def test_client_corpus_stats_and_sources(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/v1/corpus/stats":
            assert request.headers.get("x-workspace") == "ws-1"
            return httpx.Response(
                200, json={"total_chunks": 7, "total_sources": 2, "last_ingest_at": "2026-05-26T00:00:00Z"}
            )
        return httpx.Response(
            200,
            json=[{"source": "repo-intel", "chunk_count": 5, "recipes": ["workspace-s3"], "last_ingest_at": None}],
        )

    orig = httpx.AsyncClient
    monkeypatch.setattr(
        "backend.app.integrations.lighthouse.client.httpx.AsyncClient",
        lambda **kw: orig(transport=httpx.MockTransport(handler), **kw),
    )
    client = LighthouseClient(base_url="https://lh.example")
    stats = await client.corpus_stats(workspace_id="ws-1")
    sources = await client.corpus_sources(workspace_id="ws-1")
    assert stats["total_chunks"] == 7
    assert sources[0]["source"] == "repo-intel"
    assert seen == ["/v1/corpus/stats", "/v1/corpus/sources"]


# ---- provisioning (best-effort) ---------------------------------------


class _FakeProvClient:
    def __init__(self, *, exc=None):
        self._exc = exc
        self.calls: list[str] = []

    async def provision_s3_importer(self, *, workspace_id):
        self.calls.append(str(workspace_id))
        if self._exc is not None:
            raise self._exc
        return {"id": "imp-1", "workspace_id": str(workspace_id)}


@pytest.mark.asyncio
async def test_provision_noop_when_lighthouse_disabled(monkeypatch):
    monkeypatch.setattr(lh_prov, "build_lighthouse_client", lambda settings: None)
    ok = await lh_prov.provision_workspace_knowledge(
        uuid.uuid4(), settings=object()
    )
    assert ok is False


@pytest.mark.asyncio
async def test_provision_calls_client_when_configured(monkeypatch):
    fake = _FakeProvClient()
    monkeypatch.setattr(lh_prov, "build_lighthouse_client", lambda settings: fake)
    ws = uuid.uuid4()
    ok = await lh_prov.provision_workspace_knowledge(ws, settings=object())
    assert ok is True
    assert fake.calls == [str(ws)]


@pytest.mark.asyncio
async def test_provision_swallows_errors(monkeypatch):
    monkeypatch.setattr(
        lh_prov,
        "build_lighthouse_client",
        lambda settings: _FakeProvClient(exc=httpx.ConnectError("down")),
    )
    # Never raises — workspace setup must not fail on a Lighthouse outage.
    ok = await lh_prov.provision_workspace_knowledge(
        uuid.uuid4(), settings=object()
    )
    assert ok is False
