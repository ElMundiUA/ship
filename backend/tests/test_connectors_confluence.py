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
