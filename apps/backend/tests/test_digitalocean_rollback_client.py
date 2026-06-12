from __future__ import annotations

import json

import httpx
import pytest

from backend.app.integrations.digitalocean import client as do_client


@pytest.mark.asyncio
async def test_validate_rollback_posts_expected_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"valid": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await do_client.validate_rollback(
            "app-1",
            "dep-1",
            token="tok",
            skip_pin=True,
            client=http,
        )

    assert result == {"valid": True}
    assert seen == {
        "method": "POST",
        "path": "/v2/apps/app-1/rollback/validate",
        "body": {"deployment_id": "dep-1", "skip_pin": True},
    }


@pytest.mark.asyncio
async def test_rollback_app_posts_expected_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"deployment": {"id": "rollback-dep"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await do_client.rollback_app(
            "app-1",
            "dep-1",
            token="tok",
            skip_pin=True,
            client=http,
        )

    assert result == {"deployment": {"id": "rollback-dep"}}
    assert seen == {
        "method": "POST",
        "path": "/v2/apps/app-1/rollback",
        "body": {"deployment_id": "dep-1", "skip_pin": True},
    }
