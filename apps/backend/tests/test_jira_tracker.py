"""Unit tests for :class:`JiraTracker`."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.jira.tracker_adapter import JiraTracker


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, matcher):
        self._matcher = matcher
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._matcher(request)


@pytest.mark.asyncio
async def test_create_ticket_posts_jira_issue_payload() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/rest/api/3/issue")
        body = json.loads(request.content.decode("utf-8"))
        fields = body["fields"]
        assert fields["project"]["key"] == "ENG"
        assert fields["summary"] == "Add SSO"
        assert fields["issuetype"]["name"] == "Task"
        assert fields["labels"] == ["agent-filed"]
        assert fields["description"]["type"] == "doc"
        return httpx.Response(201, json={"key": "ENG-42"})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        tracker = JiraTracker(
            site_url="https://acme.atlassian.net",
            email="ops@example.com",
            api_token="secret",
            default_project="ENG",
            client=client,
        )
        created = await tracker.create_ticket(
            title="Add SSO",
            body="Please wire SAML.",
            labels=["agent-filed"],
        )

    assert created.display_id == "ENG-42"
    assert created.url == "https://acme.atlassian.net/browse/ENG-42"
    assert created.ref.kind == "jira"
    assert created.ref.id == "ENG-42"


@pytest.mark.asyncio
async def test_transition_resolves_transition_by_name() -> None:
    seen: list[str] = []

    def match(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "11", "name": "In Progress"},
                        {"id": "31", "name": "Done"},
                    ]
                },
            )
        body = json.loads(request.content.decode("utf-8"))
        assert body == {"transition": {"id": "31"}}
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        tracker = JiraTracker(
            site_url="https://acme.atlassian.net",
            email="ops@example.com",
            api_token="secret",
            client=client,
        )
        await tracker.transition(
            TicketRef(kind="jira", workspace_hint="ENG", id="ENG-42"),
            to_state="Done",
        )

    assert seen == [
        "GET /rest/api/3/issue/ENG-42/transitions",
        "POST /rest/api/3/issue/ENG-42/transitions",
    ]


@pytest.mark.asyncio
async def test_list_tickets_builds_jql() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/rest/api/3/search")
        jql = request.url.params["jql"]
        assert "project = ENG" in jql
        assert "statusCategory != Done" in jql
        assert 'summary ~ "auth"' in jql
        return httpx.Response(
            200,
            json={
                "issues": [
                    {
                        "id": "10042",
                        "key": "ENG-42",
                        "fields": {
                            "summary": "Auth bug",
                            "status": {"name": "To Do"},
                            "updated": "2026-04-25T10:00:00.000+0000",
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        tracker = JiraTracker(
            site_url="https://acme.atlassian.net",
            email="ops@example.com",
            api_token="secret",
            default_project="ENG",
            client=client,
        )
        tickets = await tracker.list_tickets(state="open", query="auth")

    assert tickets == [
        {
            "id": "ENG-42",
            "title": "Auth bug",
            "url": "https://acme.atlassian.net/browse/ENG-42",
            "status": "To Do",
            "updated_at": "2026-04-25T10:00:00.000+0000",
        }
    ]


# ---------------------------------------------------------------------------
# ticket_type mapping (ELS-69)
# ---------------------------------------------------------------------------


# Single source of truth for the 400 body shape that triggers the
# adapter's issuetype fallback. Vendor-shape drift updates here in
# one place — exact shape varies across Jira Cloud / Server /
# Datacenter; this is the shape we coded against (Cloud REST v3,
# verified via Atlassian's "Create issue" error response docs).
JIRA_INVALID_ISSUETYPE_400_BODY = {
    "errorMessages": [],
    "errors": {"issuetype": "issue type is not valid for this project"},
}


class _SequenceTransport(httpx.AsyncBaseTransport):
    """Return canned responses in declaration order; record requests."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("more requests than canned responses")
        return self._responses.pop(0)


def _captured_issuetypes(transport: _SequenceTransport) -> list[str]:
    return [
        json.loads(req.content.decode("utf-8"))["fields"]["issuetype"]["name"]
        for req in transport.requests
    ]


@pytest.mark.asyncio
async def test_create_ticket_bug_maps_to_jira_bug() -> None:
    transport = _SequenceTransport([httpx.Response(201, json={"key": "ENG-1"})])
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = JiraTracker(
            site_url="https://acme.atlassian.net",
            email="ops@example.com",
            api_token="t",
            default_project="ENG",
            client=client,
        )
        await tracker.create_ticket(
            title="x", body="y", ticket_type="bug"
        )
    assert _captured_issuetypes(transport) == ["Bug"]


@pytest.mark.asyncio
async def test_create_ticket_feature_maps_to_jira_story_happy_path() -> None:
    transport = _SequenceTransport([httpx.Response(201, json={"key": "ENG-2"})])
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = JiraTracker(
            site_url="https://acme.atlassian.net",
            email="ops@example.com",
            api_token="t",
            default_project="ENG",
            client=client,
        )
        await tracker.create_ticket(
            title="x", body="y", ticket_type="feature"
        )
    assert _captured_issuetypes(transport) == ["Story"]


@pytest.mark.asyncio
async def test_create_ticket_feature_falls_back_through_new_feature_to_task(
    caplog,
) -> None:
    transport = _SequenceTransport(
        [
            httpx.Response(400, json=JIRA_INVALID_ISSUETYPE_400_BODY),
            httpx.Response(400, json=JIRA_INVALID_ISSUETYPE_400_BODY),
            httpx.Response(201, json={"key": "ENG-3"}),
        ]
    )
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = JiraTracker(
            site_url="https://acme.atlassian.net",
            email="ops@example.com",
            api_token="t",
            default_project="ENG",
            client=client,
        )
        with caplog.at_level("WARNING"):
            await tracker.create_ticket(
                title="x", body="y", ticket_type="feature"
            )
    assert _captured_issuetypes(transport) == ["Story", "New Feature", "Task"]
    # WARN log carries project key + rejected issuetype name so an
    # operator scanning logs sees which type got downgraded.
    warns = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelname == "WARNING"
    ]
    assert any("Story" in m and "ENG" in m for m in warns)
    assert any("New Feature" in m and "ENG" in m for m in warns)


@pytest.mark.asyncio
async def test_create_ticket_non_issuetype_400_propagates_without_retry() -> None:
    """A 400 whose body does NOT match ``errors.issuetype`` must NOT
    be retried — it's a validation error on some other field
    (summary / project / labels) and downgrading the issuetype would
    paper over the real cause."""
    other_400 = {"errorMessages": ["Summary is required"], "errors": {}}
    transport = _SequenceTransport([httpx.Response(400, json=other_400)])
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = JiraTracker(
            site_url="https://acme.atlassian.net",
            email="ops@example.com",
            api_token="t",
            default_project="ENG",
            client=client,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await tracker.create_ticket(
                title="x", body="y", ticket_type="feature"
            )
    # Exactly one POST — no fallback retry.
    assert len(transport.requests) == 1


@pytest.mark.parametrize("ticket_type", [None, "task"])
@pytest.mark.asyncio
async def test_create_ticket_default_and_task_map_to_jira_task(
    ticket_type,
) -> None:
    transport = _SequenceTransport([httpx.Response(201, json={"key": "ENG-4"})])
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = JiraTracker(
            site_url="https://acme.atlassian.net",
            email="ops@example.com",
            api_token="t",
            default_project="ENG",
            client=client,
        )
        await tracker.create_ticket(
            title="x", body="y", ticket_type=ticket_type
        )
    assert _captured_issuetypes(transport) == ["Task"]
