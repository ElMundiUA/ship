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
