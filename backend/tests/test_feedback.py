from __future__ import annotations

import json

import httpx
import pytest


def _patch_github(monkeypatch, handler):
    """Install a MockTransport for the feedback endpoint's httpx client."""
    from backend.app import main as backend_main

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=10.0)

    monkeypatch.setattr(backend_main, "_FEEDBACK_HTTP_CLIENT_FACTORY", factory)


def test_feedback_with_artifact_payload(client, github_env, monkeypatch):
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/issues"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path.endswith("/issues"):
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                201,
                json={
                    "html_url": "https://github.com/ElMundiUA/ship/issues/1",
                    "number": 1,
                    "labels": body.get("labels"),
                },
            )
        return httpx.Response(404, json={"error": "unexpected"})

    _patch_github(monkeypatch, handler)

    resp = client.post(
        "/feedback",
        json={
            "title": "Test artifact feedback",
            "summary": "The pattern misses a step about mobile previews.",
            "recommendations": ["add mobile preview step"],
            "artifact": {
                "kind": "pattern",
                "id": "cloud-developer",
                "version": "1.0.0",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["issue_number"] == 1
    assert body["deduplicated"] is False
    assert "artifact:pattern:cloud-developer" in body["labels"]
    assert "version:1.0.0" in body["labels"]

    # Verify the machine-readable footer was included in body posted to GitHub
    posts = [r for r in seen_requests if r.method == "POST"]
    assert len(posts) == 1
    payload = json.loads(posts[0].content.decode("utf-8"))
    assert "ship-feedback-meta" in payload["body"]
    assert "cloud-developer" in payload["body"]


def test_feedback_dedup_attaches_comment(client, github_env, monkeypatch):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/issues"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 42,
                        "html_url": "https://github.com/ElMundiUA/ship/issues/42",
                        "labels": [
                            {"name": "feedback"},
                            {"name": "artifact:pattern:cloud-developer"},
                            {"name": "version:1.0.0"},
                        ],
                    }
                ],
            )
        if request.method == "POST" and "/issues/42/comments" in request.url.path:
            return httpx.Response(201, json={"id": 101})
        return httpx.Response(500, json={"error": "should not hit create"})

    _patch_github(monkeypatch, handler)

    resp = client.post(
        "/feedback",
        json={
            "title": "Dedup path should attach a comment",
            "summary": "Same artifact feedback — should be added as a comment.",
            "artifact": {
                "kind": "pattern",
                "id": "cloud-developer",
                "version": "1.0.0",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deduplicated"] is True
    assert body["issue_number"] == 42
    assert body["issue_url"].endswith("/issues/42")

    # Confirm we made a comment POST and never a create POST
    methods_paths = [(m, p) for m, p in seen]
    assert any(m == "POST" and p.endswith("/issues/42/comments") for m, p in methods_paths)
    assert not any(m == "POST" and p.endswith("/issues") for m, p in methods_paths)
