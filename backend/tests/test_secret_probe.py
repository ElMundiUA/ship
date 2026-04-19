"""Unit tests for the secret-probe service.

We mock ``httpx.AsyncClient`` for the network probes so the suite stays
hermetic — these tests never reach the real Linear / GitHub / Slack APIs.
The format-only probes (jira-without-config, teams, otel, s3-export) are
exercised directly against their pure logic.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from backend.app.services import secret_probe


class _StubResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_body: Any | None = None,
        content_type: str = "application/json",
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.headers = {"content-type": content_type}

    def json(self) -> Any:
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body


class _StubAsyncClient:
    """Patch shim for ``httpx.AsyncClient`` that records calls + returns canned responses."""

    def __init__(self, response: _StubResponse | Exception) -> None:
        self._response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_StubAsyncClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def _send(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
        self.calls.append((method, url, kwargs))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def get(self, url: str, **kwargs: Any) -> _StubResponse:
        return await self._send("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> _StubResponse:
        return await self._send("POST", url, **kwargs)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, response: _StubResponse | Exception
) -> _StubAsyncClient:
    stub = _StubAsyncClient(response)

    def _factory(*_: Any, **__: Any) -> _StubAsyncClient:
        return stub

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return stub


@pytest.mark.asyncio
async def test_linear_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _patch_client(
        monkeypatch,
        _StubResponse(json_body={"data": {"viewer": {"id": "u1", "email": "a@b"}}}),
    )
    status, message = await secret_probe.probe_one("linear", "lin_api_x", {})
    assert status == "ok"
    assert message is None
    method, url, kwargs = stub.calls[0]
    assert (method, url) == ("POST", "https://api.linear.app/graphql")
    assert kwargs["headers"]["Authorization"] == "lin_api_x"


@pytest.mark.asyncio
async def test_linear_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _StubResponse(status_code=401, json_body={}))
    status, message = await secret_probe.probe_one("linear", "lin_api_x", {})
    assert status == "error"
    assert message is not None and "401" in message


@pytest.mark.asyncio
async def test_linear_graphql_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(
        monkeypatch,
        _StubResponse(json_body={"errors": [{"message": "Not authenticated"}]}),
    )
    status, message = await secret_probe.probe_one("linear", "x", {})
    assert status == "error"
    assert message is not None and "Not authenticated" in message


@pytest.mark.asyncio
async def test_linear_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, httpx.ConnectError("dns fail"))
    status, message = await secret_probe.probe_one("linear", "x", {})
    assert status == "error"
    assert message is not None and "network" in message


@pytest.mark.asyncio
async def test_github_uses_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _patch_client(monkeypatch, _StubResponse(json_body={"login": "ada"}))
    status, _ = await secret_probe.probe_one("github", "ghp_token", {})
    assert status == "ok"
    _, _, kwargs = stub.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer ghp_token"


@pytest.mark.asyncio
async def test_github_404_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _StubResponse(status_code=404, json_body={}))
    status, message = await secret_probe.probe_one("github", "x", {})
    assert status == "error"
    assert message is not None and "404" in message


@pytest.mark.asyncio
async def test_slack_auth_test_ok_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(
        monkeypatch,
        _StubResponse(json_body={"ok": False, "error": "invalid_auth"}),
    )
    status, message = await secret_probe.probe_one("slack", "xoxb-bad", {})
    assert status == "error"
    assert message is not None and "invalid_auth" in message

    _patch_client(monkeypatch, _StubResponse(json_body={"ok": True, "team": "T1"}))
    status, _ = await secret_probe.probe_one("slack", "xoxb-good", {})
    assert status == "ok"


@pytest.mark.asyncio
async def test_jira_falls_back_to_format_check_when_config_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without host/user we never hit the network — confirm by leaving no stub.
    status, _ = await secret_probe.probe_one("jira", "atlassian_token_long_enough", {})
    assert status == "ok"


@pytest.mark.asyncio
async def test_jira_uses_basic_auth_when_config_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _patch_client(monkeypatch, _StubResponse(json_body={"accountId": "acc1"}))
    status, _ = await secret_probe.probe_one(
        "jira",
        "jiratoken",
        {"host": "acme.atlassian.net", "user": "ada@example.com"},
    )
    assert status == "ok"
    _, url, kwargs = stub.calls[0]
    assert url == "https://acme.atlassian.net/rest/api/3/myself"
    assert kwargs["auth"] == ("ada@example.com", "jiratoken")


@pytest.mark.asyncio
async def test_notion_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _patch_client(
        monkeypatch,
        _StubResponse(json_body={"object": "user", "id": "bot1", "type": "bot"}),
    )
    status, _ = await secret_probe.probe_one("notion", "secret_xyz", {})
    assert status == "ok"
    method, url, kwargs = stub.calls[0]
    assert (method, url) == ("GET", "https://api.notion.com/v1/users/me")
    assert kwargs["headers"]["Authorization"] == "Bearer secret_xyz"
    assert kwargs["headers"]["Notion-Version"] == "2022-06-28"


@pytest.mark.asyncio
async def test_notion_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _StubResponse(status_code=401, json_body={}))
    status, message = await secret_probe.probe_one("notion", "bad", {})
    assert status == "error"
    assert message is not None and "401" in message


@pytest.mark.asyncio
async def test_notion_unexpected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _StubResponse(json_body={"object": "page"}))
    status, message = await secret_probe.probe_one("notion", "x", {})
    assert status == "error"
    assert message is not None and "users.me" in message


@pytest.mark.asyncio
async def test_webhook_requires_url_and_min_secret_len() -> None:
    s, _ = await secret_probe.probe_one("webhook", "short", {"url": "https://x.com/h"})
    assert s == "error"
    s, _ = await secret_probe.probe_one("webhook", "abcdefghij", {})
    assert s == "error"
    s, _ = await secret_probe.probe_one(
        "webhook", "abcdefghij", {"url": "https://x.com/h"}
    )
    assert s == "ok"


@pytest.mark.asyncio
async def test_otel_endpoint_required() -> None:
    s, _ = await secret_probe.probe_one("otel", "bearer_token_xyz", {})
    assert s == "error"
    s, _ = await secret_probe.probe_one(
        "otel", "bearer_token_xyz", {"endpoint": "https://otlp.example/v1"}
    )
    assert s == "ok"


@pytest.mark.asyncio
async def test_unknown_kind_falls_back_to_format_check() -> None:
    s, _ = await secret_probe.probe_one("brand-new-kind", "anything", {})
    assert s == "ok"
    s, msg = await secret_probe.probe_one("brand-new-kind", "", {})
    assert s == "error"
    assert msg is not None


@pytest.mark.asyncio
async def test_prober_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catch-all in ``probe_one`` keeps the worker resilient."""

    async def _bomb(_: str, __: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setitem(secret_probe.PROBERS, "linear", _bomb)
    status, message = await secret_probe.probe_one("linear", "x", {})
    assert status == "error"
    assert message is not None and "boom" in message
