from __future__ import annotations

import json

import httpx
import pytest

from backend.app.integrations.notion.tracker_adapter import (
    NotionTracker,
    _resolve_type_select,
)


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


# ---------------------------------------------------------------------------
# Type select-column rendering (ELS-69)
# ---------------------------------------------------------------------------


def _ds_with_type_select(options: list[dict[str, str]]) -> dict:
    return {
        "object": "data_source",
        "id": "ds_typed",
        "properties": {
            "Name": {"id": "title", "type": "title"},
            "Type": {"type": "select", "select": {"options": options}},
        },
    }


async def _create_with_ds_schema(ds_response: dict, ticket_type):
    """Spin up the adapter, fire ``create_ticket`` with the requested
    ``ticket_type``, return the decoded ``pages.create`` request body."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.method == "GET"
            and request.url.path == f"/v1/data_sources/{ds_response['id']}"
        ):
            return httpx.Response(200, json=ds_response)
        if request.method == "POST" and request.url.path == "/v1/pages":
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "page_typed",
                    "url": "https://notion.so/page_typed",
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        tracker = NotionTracker("secret_test", client=client)
        await tracker.create_ticket(
            title="t",
            body="b",
            project_hint=ds_response["id"],
            ticket_type=ticket_type,
        )
    return captured["body"]


@pytest.mark.asyncio
async def test_create_ticket_sets_type_select_when_option_matches() -> None:
    ds = _ds_with_type_select(
        [{"name": "Bug"}, {"name": "Feature"}, {"name": "Task"}]
    )
    body = await _create_with_ds_schema(ds, "bug")
    assert body["properties"]["Type"] == {"select": {"name": "Bug"}}


@pytest.mark.asyncio
async def test_create_ticket_skips_type_when_option_missing() -> None:
    """Database has a ``Type`` select but the agent asked for a value
    that isn't on the option list (``feature`` against a bug-only
    database) — no-op rather than raise; the page still lands."""
    ds = _ds_with_type_select([{"name": "Bug"}])
    body = await _create_with_ds_schema(ds, "feature")
    assert "Type" not in body["properties"]


@pytest.mark.asyncio
async def test_create_ticket_default_unchanged_when_no_type_column() -> None:
    """Database without a ``Type`` column + no ``ticket_type`` arg —
    the create payload is byte-for-byte today's golden (only the
    title property, no extra select keys)."""
    ds = {
        "object": "data_source",
        "id": "ds_typed",
        "properties": {"Name": {"id": "title", "type": "title"}},
    }
    body = await _create_with_ds_schema(ds, None)
    assert list(body["properties"].keys()) == ["Name"]


def test_resolve_type_select_indexes_canonical_options() -> None:
    """Schema discovery: a ``Type`` ``select`` column with the three
    canonical option names returns a lowercase → exact-case lookup."""
    name, options = _resolve_type_select(
        {
            "Name": {"type": "title"},
            "Type": {
                "type": "select",
                "select": {
                    "options": [
                        {"name": "Bug"},
                        {"name": "Feature"},
                        {"name": "Task"},
                    ]
                },
            },
        }
    )
    assert name == "Type"
    assert options == {"bug": "Bug", "feature": "Feature", "task": "Task"}


def test_resolve_type_select_handles_mixed_casing_and_extras() -> None:
    """Case-insensitive name match (``type`` vs ``Type``), and extra
    options (``Other``) are dropped from the index. Mixed-case option
    names are preserved verbatim so the write payload matches what
    Notion stored."""
    name, options = _resolve_type_select(
        {
            "type": {
                "type": "select",
                "select": {
                    "options": [
                        {"name": "BUG"},
                        {"name": "feature"},
                        {"name": "Other"},
                    ]
                },
            }
        }
    )
    assert name == "type"
    assert options == {"bug": "BUG", "feature": "feature"}
