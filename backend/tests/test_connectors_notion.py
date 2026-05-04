"""Phase 7c — unit tests for the Notion connector fetcher.

All Notion HTTP calls are stubbed with :class:`httpx.MockTransport`,
so these tests do NOT require network, ``NOTION_CLIENT_*`` env vars,
or a live Notion workspace. They exercise:

- Happy-path page fetch + markdown rendering (headings, lists,
  to-do, quote, callout, code, divider, inline formatting).
- Unsupported ``resource_ref`` shapes (``{database_id}``, missing
  ``page_id``) raise :class:`ConnectorUnsupported` *before* the
  secret is decrypted — that's the invariant the endpoint relies
  on to fall back to the stub body when a resource shape isn't
  wired yet.
- Missing / unreadable secret for a supported shape raises
  :class:`ConnectorConfigError` (the endpoint turns that into 502).
- Notion 404 (page not shared with the integration) is surfaced as
  :class:`ConnectorConfigError` with a re-share hint.
- Pagination: ``has_more`` + ``next_cursor`` triggers a second
  call; ``_MAX_BLOCKS`` truncates very long pages.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

import httpx
import pytest

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import encrypt
from backend.app.services.connectors import (
    ConnectorConfigError,
    ConnectorUnsupported,
    fetch_connector_pages,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def integration() -> Integration:
    """A Notion Integration row with an encrypted access token."""

    return Integration(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="notion",
        config={"notion_workspace_name": "Example"},
        status="ok",
        secret_ciphertext=encrypt("secret_notion_token_abc123"),
    )


@pytest.fixture
def integration_no_secret() -> Integration:
    return Integration(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="notion",
        config={},
        status="ok",
        secret_ciphertext=None,
    )


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _page_payload(
    page_id: str, *, title: str = "Runbook", url: str = "https://notion.so/p"
) -> dict[str, Any]:
    return {
        "object": "page",
        "id": page_id,
        "url": url,
        "last_edited_time": "2026-04-20T12:00:00.000Z",
        "properties": {
            "Name": {
                "id": "title",
                "type": "title",
                "title": [
                    {"plain_text": title, "annotations": {}, "href": None},
                ],
            }
        },
    }


def _rt(text: str, **ann) -> dict[str, Any]:
    return {
        "plain_text": text,
        "annotations": {
            "bold": False,
            "italic": False,
            "strikethrough": False,
            "code": False,
            **ann,
        },
        "href": None,
    }


def _block(btype: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "block",
        "id": uuid.uuid4().hex,
        "type": btype,
        btype: payload,
    }


def _blocks_payload(
    blocks: list[dict[str, Any]],
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
) -> dict[str, Any]:
    return {
        "object": "list",
        "results": blocks,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notion_fetcher_renders_rich_page(integration) -> None:
    """A full-shape Notion page should produce idiomatic markdown.

    This is a "kitchen sink" test: one block of each supported kind
    plus inline formatting, so we'd catch regressions in any single
    renderer branch from one assertion file.
    """

    page_id = "abc123"
    blocks = [
        _block("heading_1", {"rich_text": [_rt("Runbook: DB restore")]}),
        _block(
            "paragraph",
            {
                "rich_text": [
                    _rt("Follow "),
                    _rt("these", bold=True),
                    _rt(" steps "),
                    _rt("carefully", italic=True),
                    _rt("."),
                ]
            },
        ),
        _block("heading_2", {"rich_text": [_rt("Prereqs")]}),
        _block("bulleted_list_item", {"rich_text": [_rt("pgdump access")]}),
        _block("bulleted_list_item", {"rich_text": [_rt("oncall page")]}),
        _block("numbered_list_item", {"rich_text": [_rt("Snapshot")]}),
        _block(
            "to_do",
            {"rich_text": [_rt("Verify backups")], "checked": True},
        ),
        _block("to_do", {"rich_text": [_rt("Notify ops")], "checked": False}),
        _block("quote", {"rich_text": [_rt("Backups or it didn't happen.")]}),
        _block(
            "callout",
            {
                "rich_text": [_rt("Read the runbook first.")],
                "icon": {"emoji": "⚠️"},
            },
        ),
        _block(
            "code",
            {
                "language": "bash",
                "rich_text": [_rt("pg_restore --clean db.dump")],
            },
        ),
        _block("divider", {}),
        _block(
            "paragraph",
            {"rich_text": [_rt("Inline ", code=True), _rt("code")]},
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/v1/pages/{page_id}":
            return httpx.Response(200, json=_page_payload(page_id))
        if request.url.path == f"/v1/blocks/{page_id}/children":
            return httpx.Response(200, json=_blocks_payload(blocks))
        raise AssertionError(f"unexpected call: {request.url}")

    async with _make_client(handler) as client:
        pages = await fetch_connector_pages(
            integration, {"page_id": page_id}, http_client=client
        )

    assert len(pages) == 1
    page = pages[0]
    assert page.slug == page_id
    assert page.title == "Runbook"
    assert page.page_ref["page_id"] == page_id

    body = page.body_md
    assert body.startswith("# Runbook")
    assert "## Runbook: DB restore" in body
    assert "Follow **these** steps *carefully*." in body
    assert "### Prereqs" in body
    assert "- pgdump access" in body
    assert "- oncall page" in body
    assert "1. Snapshot" in body
    assert "- [x] Verify backups" in body
    assert "- [ ] Notify ops" in body
    assert "> Backups or it didn't happen." in body
    assert "> ⚠️ Read the runbook first." in body
    assert "```bash\npg_restore --clean db.dump\n```" in body
    assert "\n\n---\n\n" in body
    assert "`Inline `code" in body


# ---------------------------------------------------------------------------
# Shape validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notion_database_id_fetches_entries(integration) -> None:
    """``{database_id}`` resolves to a data source, queries it, and
    returns one ConnectorPage per entry.

    Exercises the v2025-09-03 split: ``/data_sources/{id}`` returns 404
    when the id actually points at a database container, then
    ``/databases/{id}`` returns ``data_sources[]`` and we follow the
    first one.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/data_sources/db-xyz":
            return httpx.Response(404, json={"object": "error", "code": "object_not_found"})
        if request.url.path == "/v1/databases/db-xyz":
            return httpx.Response(
                200,
                json={
                    "object": "database",
                    "id": "db-xyz",
                    "data_sources": [{"id": "ds-1", "name": "Default"}],
                },
            )
        if request.url.path == "/v1/data_sources/ds-1/query" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"object": "page", "id": "page-A"},
                        {"object": "page", "id": "page-B"},
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
            )
        if request.url.path == "/v1/pages/page-A":
            return httpx.Response(200, json=_page_payload("page-A", title="Entry A"))
        if request.url.path == "/v1/pages/page-B":
            return httpx.Response(200, json=_page_payload("page-B", title="Entry B"))
        if request.url.path.startswith("/v1/blocks/"):
            return httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None})
        return httpx.Response(404)

    async with _make_client(handler) as client:
        pages = await fetch_connector_pages(
            integration, {"database_id": "db-xyz"}, http_client=client
        )

    assert [p.title for p in pages] == ["Entry A", "Entry B"]
    assert all(p.page_ref["page_id"] for p in pages)
    # The /data_sources/{id}/query endpoint is the queryable surface
    # under v2025-09-03; the test verifies we hit it after resolving
    # the container.
    assert "POST /v1/data_sources/ds-1/query" in calls


@pytest.mark.asyncio
async def test_notion_database_id_404_surfaces_config_error(integration) -> None:
    """Database the bot can't see → ConnectorConfigError so the wizard
    surfaces a re-share hint instead of silently 502'ing."""
    def handler(request: httpx.Request) -> httpx.Response:
        # Both data_sources and databases lookups return 404
        return httpx.Response(404, json={"object": "error", "code": "object_not_found"})

    async with _make_client(handler) as client:
        with pytest.raises(ConnectorConfigError, match="shared"):
            await fetch_connector_pages(
                integration, {"database_id": "unknown"}, http_client=client
            )


@pytest.mark.asyncio
async def test_notion_rejects_missing_page_id(integration) -> None:
    pages = await fetch_connector_pages(
        integration, {"page_id": ""}, http_client=None
    )
    assert pages == []


@pytest.mark.asyncio
async def test_notion_shape_check_runs_before_secret_decrypt(
    integration_no_secret,
) -> None:
    """No secret + unsupported shape must still resolve to stub fallback.

    This invariant protects buckets with resource_ref shapes we haven't
    wired yet — the endpoint would 502 on secret-missing even though it
    should only fall back to stub. Both ``{page_id}`` and
    ``{database_id}`` are now supported, so test with a genuinely
    unknown shape.
    """

    pages = await fetch_connector_pages(
        integration_no_secret,
        {"future_shape_id": "anything"},
        http_client=None,
    )
    assert pages == []


# ---------------------------------------------------------------------------
# Secret + upstream errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notion_missing_secret_raises_config_error(
    integration_no_secret,
) -> None:
    """Supported shape but no decryptable secret must raise ConfigError."""

    with pytest.raises(ConnectorConfigError, match="no readable access token"):
        await fetch_connector_pages(
            integration_no_secret,
            {"page_id": "abc123"},
            http_client=None,
        )


@pytest.mark.asyncio
async def test_notion_404_surfaces_share_hint(integration) -> None:
    """Notion returns 404 when the bot isn't shared with the page.

    That's the most common user error (connect the workspace, forget
    to invite the bot on the specific page). We translate it to a
    ConfigError with a re-share hint instead of a generic 500.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "object_not_found"})

    async with _make_client(handler) as client:
        with pytest.raises(ConnectorConfigError, match="shared with the page"):
            await fetch_connector_pages(
                integration,
                {"page_id": "nope"},
                http_client=client,
            )


# ---------------------------------------------------------------------------
# Pagination + truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notion_follows_pagination_cursor(integration) -> None:
    page_id = "abc123"

    call_log: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/v1/pages/{page_id}":
            return httpx.Response(200, json=_page_payload(page_id))
        if request.url.path == f"/v1/blocks/{page_id}/children":
            cursor = request.url.params.get("start_cursor")
            call_log.append(cursor)
            if cursor is None:
                return httpx.Response(
                    200,
                    json=_blocks_payload(
                        [_block("paragraph", {"rich_text": [_rt("page 1")]})],
                        has_more=True,
                        next_cursor="c-2",
                    ),
                )
            if cursor == "c-2":
                return httpx.Response(
                    200,
                    json=_blocks_payload(
                        [_block("paragraph", {"rich_text": [_rt("page 2")]})]
                    ),
                )
        raise AssertionError(f"unexpected call: {request.url}")

    async with _make_client(handler) as client:
        pages = await fetch_connector_pages(
            integration, {"page_id": page_id}, http_client=client
        )

    assert len(pages) == 1
    body = pages[0].body_md
    assert "page 1" in body and "page 2" in body
    # First call with no cursor, second with ``c-2`` — exactly two
    # block-listing requests.
    assert call_log == [None, "c-2"]
