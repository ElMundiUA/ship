"""HTTP tests for workspace knowledge search (K8 — Lighthouse only).

The internal vector/keyword index is retired: the route now delegates
to the per-workspace Lighthouse engine and returns whatever it holds.
These tests monkeypatch the Lighthouse client so the suite stays
offline — engine retrieval quality is covered in whafr, not here.
"""

from __future__ import annotations

import uuid

import pytest

import backend.app.services.knowledge_search as ks


class _FakeClient:
    def __init__(self, *, hits=None):
        self._hits = hits or []
        self.calls: list[tuple] = []

    async def search(self, *, workspace_id, query, top_k):
        self.calls.append((str(workspace_id), query, top_k))
        return self._hits


def _use_client(monkeypatch, client) -> None:
    monkeypatch.setattr(ks, "build_lighthouse_client", lambda settings: client)


@pytest.mark.asyncio
async def test_search_returns_lighthouse_hits(
    v1_client, seed_workspace, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    node = str(uuid.uuid4())
    client = _FakeClient(
        hits=[{"node_id": node, "summary": "# Auth\nUse PKCE for SPAs."}]
    )
    _use_client(monkeypatch, client)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/search",
        headers=headers,
        json={"query": "auth", "limit": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "auth"
    assert [hit["source"] for hit in body["hits"]] == ["lighthouse"]
    assert body["hits"][0]["title"] == "Auth"
    # Scoped by workspace, top_k forwarded.
    assert client.calls == [(str(workspace.id), "auth", 10)]


@pytest.mark.asyncio
async def test_search_empty_when_engine_unconfigured(
    v1_client, seed_workspace, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    _use_client(monkeypatch, None)  # build_lighthouse_client → None

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/search",
        headers=headers,
        json={"query": "auth"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hits"] == []
