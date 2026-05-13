"""Unit tests for :class:`GitHubIssuesTracker`.

We mock httpx.AsyncClient so the test can exercise request shape +
response parsing without network. The GitHub App auth layer is
short-circuited via a fake fetch_installation_token so we don't
need private-key fixtures.
"""

from __future__ import annotations

import httpx
import pytest

from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.github import issues_tracker as it_module
from backend.app.integrations.github.issues_tracker import GitHubIssuesTracker


class _StubSettings:
    """Minimal duck type — the tracker only passes it into
    ``fetch_installation_token`` which we stub out anyway."""


class _MockTransport(httpx.AsyncBaseTransport):
    """Capture requests + return canned responses.

    Kept deliberately tiny — one request at a time, one response
    matcher based on path suffix. If a test needs more it should
    pass a list.
    """

    def __init__(self, matcher):
        self._matcher = matcher
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._matcher(request)


@pytest.mark.asyncio
async def test_create_ticket_posts_correct_payload(monkeypatch) -> None:
    async def _fake_token(*_, **__):
        return "ghs_test_token"

    monkeypatch.setattr(it_module, "fetch_installation_token", _fake_token)

    def match(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/repos/acme/web/issues")
        import json

        body = json.loads(request.content.decode("utf-8"))
        assert body["title"] == "Add retry policy"
        assert body["labels"] == ["agent-filed"]
        return httpx.Response(
            201,
            json={
                "number": 42,
                "html_url": "https://github.com/acme/web/issues/42",
            },
        )

    transport = _MockTransport(match)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = GitHubIssuesTracker(
            installation_id=1,
            owner="acme",
            repo="web",
            settings=_StubSettings(),  # type: ignore[arg-type]
            client=client,
        )
        created = await tracker.create_ticket(
            title="Add retry policy",
            body="Retries + idempotency for /charge.",
            labels=["agent-filed"],
        )

    assert created.display_id == "acme/web#42"
    assert created.url == "https://github.com/acme/web/issues/42"
    assert created.ref.kind == "github_issues"
    assert created.ref.id == "42"


@pytest.mark.asyncio
async def test_transition_rejects_unknown_state() -> None:
    tracker = GitHubIssuesTracker(
        installation_id=1,
        owner="acme",
        repo="web",
        settings=_StubSettings(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="open/closed"):
        await tracker.transition(
            TicketRef(kind="github_issues", workspace_hint="acme/web", id="42"),
            to_state="in-progress",
        )


# ---------------------------------------------------------------------------
# ticket_type label mapping (ELS-69)
# ---------------------------------------------------------------------------


async def _capture_create(
    monkeypatch, ticket_type, caller_labels
):
    """Spin up the tracker, fire ``create_ticket``, return the
    decoded request body labels list (or None if absent)."""
    import json

    async def _fake_token(*_, **__):
        return "ghs"

    monkeypatch.setattr(it_module, "fetch_installation_token", _fake_token)

    captured: dict = {}

    def match(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured["body"] = body
        return httpx.Response(
            201,
            json={
                "number": 1,
                "html_url": "https://github.com/acme/web/issues/1",
            },
        )

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        tracker = GitHubIssuesTracker(
            installation_id=1,
            owner="acme",
            repo="web",
            settings=_StubSettings(),  # type: ignore[arg-type]
            client=client,
        )
        await tracker.create_ticket(
            title="t",
            body="b",
            labels=caller_labels,
            ticket_type=ticket_type,
        )
    return captured["body"].get("labels")


@pytest.mark.asyncio
async def test_create_ticket_bug_prepends_type_label(monkeypatch) -> None:
    labels = await _capture_create(
        monkeypatch, ticket_type="bug", caller_labels=["regression"]
    )
    # Ordered: type:bug prepended, caller-supplied second.
    assert labels == ["type:bug", "regression"]


@pytest.mark.asyncio
async def test_create_ticket_dedups_explicit_type_label(monkeypatch) -> None:
    labels = await _capture_create(
        monkeypatch,
        ticket_type="bug",
        caller_labels=["type:bug", "regression"],
    )
    assert labels == ["type:bug", "regression"]


@pytest.mark.asyncio
async def test_create_ticket_default_passes_labels_unchanged(
    monkeypatch,
) -> None:
    """When ``ticket_type`` is omitted, the labels list is identical to
    today's golden — no ``type:*`` injection on the default path."""
    labels = await _capture_create(
        monkeypatch, ticket_type=None, caller_labels=["agent-filed"]
    )
    assert labels == ["agent-filed"]


@pytest.mark.asyncio
async def test_issue_number_parser_handles_owner_repo_prefix(monkeypatch) -> None:
    async def _fake_token(*_, **__):
        return "tok"

    monkeypatch.setattr(it_module, "fetch_installation_token", _fake_token)

    def match(request: httpx.Request) -> httpx.Response:
        # We pass ``"acme/web#42"`` as the id; the tracker must
        # strip the prefix and PATCH ``/issues/42``.
        assert request.url.path.endswith("/issues/42")
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        tracker = GitHubIssuesTracker(
            installation_id=1,
            owner="acme",
            repo="web",
            settings=_StubSettings(),  # type: ignore[arg-type]
            client=client,
        )
        await tracker.transition(
            TicketRef(
                kind="github_issues",
                workspace_hint="acme/web",
                id="acme/web#42",
            ),
            to_state="closed",
        )
