from __future__ import annotations

import httpx
import pytest

from backend.app.integrations.notion.tracker_adapter import NotionTracker


@pytest.mark.asyncio
async def test_create_ticket_uses_data_source_title_property_with_custom_name() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/v1/data_sources/ds_123":
            return httpx.Response(
                200,
                json={
                    "object": "data_source",
                    "id": "ds_123",
                    "properties": {
                        "Название задачи": {"id": "title", "type": "title"},
                        "Status": {"id": "status", "type": "status"},
                    },
                },
            )
        if request.method == "POST" and request.url.path == "/v1/pages":
            payload = request.read()
            assert b'"type":"data_source_id"' in payload
            assert b'"data_source_id":"ds_123"' in payload
            assert "Название задачи".encode() in payload
            return httpx.Response(
                200,
                json={
                    "id": "page_123",
                    "url": "https://notion.so/page_123",
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tracker = NotionTracker("secret_test", client=client)
        created = await tracker.create_ticket(
            title="Test backend",
            body="Cover Visitor backend.",
            project_hint="ds_123",
        )

    assert created.ref.workspace_hint == "ds_123"
    assert created.ref.id == "page_123"
    assert [request.url.path for request in requests] == [
        "/v1/data_sources/ds_123",
        "/v1/pages",
    ]


@pytest.mark.asyncio
async def test_create_ticket_resolves_database_container_to_single_data_source() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/v1/data_sources/db_123":
            return httpx.Response(404, json={"message": "not found"})
        if request.method == "GET" and request.url.path == "/v1/databases/db_123":
            return httpx.Response(
                200,
                json={
                    "object": "database",
                    "id": "db_123",
                    "data_sources": [{"id": "ds_456", "name": "Tasks"}],
                },
            )
        if request.method == "GET" and request.url.path == "/v1/data_sources/ds_456":
            return httpx.Response(
                200,
                json={
                    "object": "data_source",
                    "id": "ds_456",
                    "properties": {"Name": {"id": "title", "type": "title"}},
                },
            )
        if request.method == "POST" and request.url.path == "/v1/pages":
            payload = request.read()
            assert b'"data_source_id":"ds_456"' in payload
            return httpx.Response(
                200,
                json={"id": "page_456", "url": "https://notion.so/page_456"},
            )
        return httpx.Response(404, json={"message": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tracker = NotionTracker("secret_test", client=client)
        created = await tracker.create_ticket(
            title="Test backend",
            body="Cover Visitor backend.",
            project_hint="db_123",
        )

    assert created.ref.workspace_hint == "ds_456"
    assert created.ref.id == "page_456"
    assert [request.url.path for request in requests] == [
        "/v1/data_sources/db_123",
        "/v1/databases/db_123",
        "/v1/data_sources/ds_456",
        "/v1/pages",
    ]
