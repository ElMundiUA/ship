"""Unit tests for the Confluence connector fetcher."""

from __future__ import annotations

import uuid
from typing import Callable

import httpx
import pytest

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import encrypt
from backend.app.services.connectors import ConnectorConfigError, fetch_connector_pages


@pytest.fixture
def integration() -> Integration:
    return Integration(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="confluence",
        config={
            "site_url": "https://acme.atlassian.net",
            "email": "ops@example.com",
        },
        status="ok",
        secret_ciphertext=encrypt("atlassian-token"),
    )


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_confluence_fetcher_renders_storage_html(integration) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wiki/api/v2/pages/12345"
        assert request.url.params["body-format"] == "storage"
        return httpx.Response(
            200,
            json={
                "id": "12345",
                "title": "Deploy Runbook",
                "body": {
                    "storage": {
                        "value": "<h1>Deploy</h1><p>Ship it carefully.</p><ul><li>Check CI</li></ul>"
                    }
                },
                "_links": {"webui": "/wiki/spaces/ENG/pages/12345"},
            },
        )

    async with _make_client(handler) as client:
        pages = await fetch_connector_pages(
            integration, {"page_id": "12345"}, http_client=client
        )

    assert len(pages) == 1
    page = pages[0]
    assert page.slug == "confluence-12345"
    assert page.title == "Deploy Runbook"
    assert page.page_ref["page_id"] == "12345"
    assert "# Deploy Runbook" in page.body_md
    assert "## Deploy" in page.body_md
    assert "Ship it carefully." in page.body_md
    assert "- Check CI" in page.body_md


@pytest.mark.asyncio
async def test_confluence_fetcher_requires_page_id(integration) -> None:
    assert await fetch_connector_pages(integration, {}) == []


@pytest.mark.asyncio
async def test_confluence_fetcher_requires_secret() -> None:
    integration = Integration(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="confluence",
        config={
            "site_url": "https://acme.atlassian.net",
            "email": "ops@example.com",
        },
        status="ok",
        secret_ciphertext=None,
    )
    with pytest.raises(ConnectorConfigError, match="no API token"):
        await fetch_connector_pages(integration, {"page_id": "12345"})


@pytest.mark.asyncio
async def test_confluence_section_fetches_root_plus_descendants(integration) -> None:
    """resource_ref={root_page_id} pulls the root + every descendant.

    Each descendant becomes its own ConnectorPage so the ingestion
    pipeline fingerprints/skips per-page on subsequent syncs.
    """
    fetched_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched_paths.append(request.url.path)
        if request.url.path == "/wiki/api/v2/pages/100":
            return httpx.Response(
                200,
                json={
                    "id": "100",
                    "title": "Onboarding handbook",
                    "spaceId": "S1",
                    "body": {"storage": {"value": "<p>Welcome.</p>"}},
                    "_links": {"webui": "/wiki/spaces/ENG/pages/100"},
                },
            )
        if request.url.path == "/wiki/api/v2/pages/100/descendants":
            return httpx.Response(
                200,
                json={
                    "results": [{"id": "101"}, {"id": "102"}],
                    "_links": {},
                },
            )
        if request.url.path == "/wiki/api/v2/pages/101":
            return httpx.Response(
                200,
                json={
                    "id": "101",
                    "title": "Day 1",
                    "spaceId": "S1",
                    "body": {"storage": {"value": "<p>Read this first.</p>"}},
                    "_links": {"webui": "/wiki/spaces/ENG/pages/101"},
                },
            )
        if request.url.path == "/wiki/api/v2/pages/102":
            return httpx.Response(
                200,
                json={
                    "id": "102",
                    "title": "Day 2",
                    "spaceId": "S1",
                    "body": {"storage": {"value": "<p>Then this.</p>"}},
                    "_links": {"webui": "/wiki/spaces/ENG/pages/102"},
                },
            )
        return httpx.Response(404)

    async with _make_client(handler) as client:
        pages = await fetch_connector_pages(
            integration,
            {"root_page_id": "100", "space_id": "S1"},
            http_client=client,
        )

    assert [p.title for p in pages] == ["Onboarding handbook", "Day 1", "Day 2"]
    assert all(p.page_ref["space_id"] == "S1" for p in pages)
    # Root + descendants list + 2 descendant body fetches = 4 calls.
    assert "/wiki/api/v2/pages/100" in fetched_paths
    assert "/wiki/api/v2/pages/100/descendants" in fetched_paths
    assert fetched_paths.count("/wiki/api/v2/pages/101") == 1
    assert fetched_paths.count("/wiki/api/v2/pages/102") == 1


@pytest.mark.asyncio
async def test_confluence_unknown_shape_falls_back(integration) -> None:
    """A ref without page_id or root_page_id is unsupported (silent stub)."""
    pages = await fetch_connector_pages(integration, {"space_id": "S1"})
    assert pages == []
