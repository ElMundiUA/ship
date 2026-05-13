"""Wizard preview — `/v1/.../knowledge/sources/website/preview`."""

from __future__ import annotations

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_website_preview_returns_firecrawl_urls(
    v1_client, seed_workspace, monkeypatch
) -> None:
    """The preview proxies Firecrawl /map and returns the discovered URLs."""
    _, raw, workspace = seed_workspace
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc_test_key")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        captured = {}

        async def _fake_map(root_url, *, config, settings):
            captured["root_url"] = root_url
            captured["limit"] = config["limit"]
            return [
                "https://docs.example.com/intro",
                "https://docs.example.com/setup",
                "https://docs.example.com/cli",
            ]

        monkeypatch.setattr(
            "backend.app.services.knowledge_ingestion._firecrawl_map", _fake_map
        )

        response = await v1_client.get(
            f"/v1/workspaces/{workspace.id}/knowledge/sources/website/preview",
            headers=_auth(raw),
            params={"url": "https://docs.example.com", "limit": 25},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["urls"] == [
            "https://docs.example.com/intro",
            "https://docs.example.com/setup",
            "https://docs.example.com/cli",
        ]
        assert body["truncated"] is False
        assert captured["root_url"] == "https://docs.example.com"
        assert captured["limit"] == 25
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_website_preview_503_when_firecrawl_unconfigured(
    v1_client, seed_workspace, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        response = await v1_client.get(
            f"/v1/workspaces/{workspace.id}/knowledge/sources/website/preview",
            headers=_auth(raw),
            params={"url": "https://docs.example.com"},
        )
        assert response.status_code == 503
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_website_preview_truncated_when_limit_hit(
    v1_client, seed_workspace, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc_test_key")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        async def _fake_map(root_url, *, config, settings):
            # Return exactly the limit so caller knows there may be more.
            return [f"https://docs.example.com/page-{i}" for i in range(config["limit"])]

        monkeypatch.setattr(
            "backend.app.services.knowledge_ingestion._firecrawl_map", _fake_map
        )

        response = await v1_client.get(
            f"/v1/workspaces/{workspace.id}/knowledge/sources/website/preview",
            headers=_auth(raw),
            params={"url": "https://docs.example.com", "limit": 5},
        )
        assert response.status_code == 200
        assert response.json()["truncated"] is True
    finally:
        get_settings.cache_clear()
