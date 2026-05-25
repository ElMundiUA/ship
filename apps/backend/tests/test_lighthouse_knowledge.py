"""K8 — Lighthouse-only knowledge reads.

Covers the search wrapper (Lighthouse only — the K5 internal dual-read
fallback is retired), the hit mapper, and the HTTP client's workspace
scoping. All offline: the search tests monkeypatch the client so no DB
is needed, and the client test uses an httpx MockTransport.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

import backend.app.services.knowledge_search as ks
from backend.app.integrations.lighthouse import provisioning as lh_prov
from backend.app.integrations.lighthouse.client import LighthouseClient
from backend.app.services.knowledge_search import (
    _map_lighthouse_hit,
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


# ---- search (Lighthouse only) -----------------------------------------


@pytest.mark.asyncio
async def test_search_returns_mapped_lighthouse_hits(monkeypatch):
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
async def test_search_empty_when_lighthouse_empty(monkeypatch):
    # No internal fallback (K8): empty engine → empty result.
    _use_client(monkeypatch, _FakeClient(hits=[]))
    hits = await ks.search_workspace_knowledge(
        None, workspace_id=uuid.uuid4(), query="auth", settings=object()
    )
    assert hits == []


@pytest.mark.asyncio
async def test_search_empty_on_error(monkeypatch):
    # Engine outage degrades to empty, never raises.
    _use_client(monkeypatch, _FakeClient(exc=httpx.ConnectError("down")))
    hits = await ks.search_workspace_knowledge(
        None, workspace_id=uuid.uuid4(), query="auth", settings=object()
    )
    assert hits == []


@pytest.mark.asyncio
async def test_search_empty_when_lighthouse_disabled(monkeypatch):
    _use_client(monkeypatch, None)  # build_lighthouse_client → None
    hits = await ks.search_workspace_knowledge(
        None, workspace_id=uuid.uuid4(), query="auth", settings=object()
    )
    assert hits == []


@pytest.mark.asyncio
async def test_search_ignores_bucket_slug_and_still_queries_lighthouse(monkeypatch):
    # bucket_slug is accepted for wire-compat but no longer routes to an
    # internal index — Lighthouse is still queried (flat corpus has no buckets).
    client = _FakeClient(hits=[{"node_id": str(uuid.uuid4()), "summary": "x"}])
    _use_client(monkeypatch, client)
    hits = await ks.search_workspace_knowledge(
        None,
        workspace_id=uuid.uuid4(),
        query="auth",
        bucket_slug="runbooks",
        settings=object(),
    )
    assert [h.source for h in hits] == ["lighthouse"]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_search_blank_query_returns_empty(monkeypatch):
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
