"""Phase 7c — unit tests for the Linear connector fetcher.

Same playbook as ``test_connectors_notion.py``: all Linear GraphQL
calls are stubbed with :class:`httpx.MockTransport`, so no network
and no real Linear workspace required. Assertions use the sort of
issue shape we'd see against the ElMundi workspace (``ELM-*``
identifier, a populated description block, a team, an assignee).
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


@pytest.fixture
def integration() -> Integration:
    return Integration(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="linear",
        config={"linear_workspace_name": "ElMundi"},
        status="ok",
        secret_ciphertext=encrypt("lin_oauth_abcdef"),
    )


@pytest.fixture
def integration_no_secret() -> Integration:
    return Integration(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="linear",
        config={},
        status="ok",
        secret_ciphertext=None,
    )


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _issue_payload(
    *,
    identifier: str = "ELM-42",
    title: str = "Rebuild retriever with bucket scopes",
    description: str = (
        "## Goal\n\n"
        "Switch retriever to `bucket_articles` only.\n\n"
        "## Acceptance\n\n- no more KbChunk\n- scopes respected\n"
    ),
    state_name: str = "In Progress",
    assignee: str = "denys",
    team_key: str = "ELM",
    team_name: str = "ElMundi Core",
) -> dict[str, Any]:
    return {
        "data": {
            "issue": {
                "id": "c2d4ac30-5a1b-4a45-9a6e-65f0b1c2a980",
                "identifier": identifier,
                "title": title,
                "description": description,
                "url": f"https://linear.app/elmundi/issue/{identifier}/{title.lower().replace(' ', '-')}",
                "updatedAt": "2026-04-20T09:15:00.000Z",
                "state": {"name": state_name, "type": "started"},
                "assignee": {"name": assignee, "displayName": assignee},
                "creator": {"name": "ksenia", "displayName": "ksenia"},
                "team": {"key": team_key, "name": team_name},
                "priority": 2,
                "priorityLabel": "High",
                "labels": {
                    "nodes": [
                        {"name": "retriever"},
                        {"name": "phase-5"},
                    ]
                },
            }
        }
    }


def _error_payload(message: str, code: str = "FORBIDDEN") -> dict[str, Any]:
    return {
        "data": None,
        "errors": [
            {
                "message": message,
                "extensions": {"code": code},
            }
        ],
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linear_fetcher_renders_elmundi_issue(integration) -> None:
    received: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.linear.app/graphql"
        assert request.headers["Authorization"] == "Bearer lin_oauth_abcdef"
        received["body"] = request.read()
        return httpx.Response(200, json=_issue_payload())

    async with _make_client(handler) as client:
        pages = await fetch_connector_pages(
            integration, {"issue_id": "ELM-42"}, http_client=client
        )

    assert len(pages) == 1
    page = pages[0]
    assert page.slug == "ELM-42"
    assert page.title.startswith("ELM-42 · ")
    assert page.page_ref["identifier"] == "ELM-42"
    assert "linear.app/elmundi/issue/ELM-42" in page.page_ref["url"]

    body = page.body_md
    assert body.startswith("# ELM-42 · Rebuild retriever with bucket scopes")
    assert "state: **In Progress**" in body
    assert "assignee: denys" in body
    assert "[Open in Linear](" in body
    # Description is embedded verbatim:
    assert "## Goal" in body
    assert "- scopes respected" in body
    # Footer meta block:
    assert "- Priority: High" in body
    assert "- Team: ElMundi Core (ELM)" in body
    assert "- Labels: phase-5, retriever" in body  # sorted
    assert "- Last updated: 2026-04-20T09:15:00.000Z" in body


# ---------------------------------------------------------------------------
# Shape validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linear_rejects_team_key_shape(integration) -> None:
    pages = await fetch_connector_pages(
        integration, {"team_key": "ELM"}, http_client=None
    )
    assert pages == []


@pytest.mark.asyncio
async def test_linear_rejects_missing_issue_id(integration) -> None:
    pages = await fetch_connector_pages(
        integration, {"issue_id": ""}, http_client=None
    )
    assert pages == []


@pytest.mark.asyncio
async def test_linear_shape_check_runs_before_secret_decrypt(
    integration_no_secret,
) -> None:
    pages = await fetch_connector_pages(
        integration_no_secret,
        {"team_key": "ELM"},
        http_client=None,
    )
    assert pages == []


# ---------------------------------------------------------------------------
# Auth + upstream errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linear_missing_secret_raises_config_error(
    integration_no_secret,
) -> None:
    with pytest.raises(ConnectorConfigError, match="no readable access token"):
        await fetch_connector_pages(
            integration_no_secret,
            {"issue_id": "ELM-1"},
            http_client=None,
        )


@pytest.mark.asyncio
async def test_linear_401_is_config_error(integration) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    async with _make_client(handler) as client:
        with pytest.raises(ConnectorConfigError, match="reconnect"):
            await fetch_connector_pages(
                integration,
                {"issue_id": "ELM-1"},
                http_client=client,
            )


@pytest.mark.asyncio
async def test_linear_forbidden_graphql_is_config_error(integration) -> None:
    """A GraphQL ``FORBIDDEN`` is the "integration not invited to team" case.

    Linear returns 200 with an ``errors[0].extensions.code=FORBIDDEN``
    for this, which is why the fetcher inspects the error envelope
    and turns it into a ConfigError (with a share-settings hint)
    instead of a generic 502.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_error_payload("Entity not found", code="FORBIDDEN")
        )

    async with _make_client(handler) as client:
        with pytest.raises(ConnectorConfigError, match="shared with the issue"):
            await fetch_connector_pages(
                integration,
                {"issue_id": "ELM-404"},
                http_client=client,
            )


@pytest.mark.asyncio
async def test_linear_null_issue_is_config_error(integration) -> None:
    """``data.issue == null`` means either wrong id or unshared team.

    We treat it as ConfigError (operator must act) rather than
    silently empty — users would otherwise get a stub page with no
    hint about what went wrong.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"issue": None}})

    async with _make_client(handler) as client:
        with pytest.raises(ConnectorConfigError, match="not visible"):
            await fetch_connector_pages(
                integration,
                {"issue_id": "ELM-999"},
                http_client=client,
            )


# ---------------------------------------------------------------------------
# Determinism / edge shapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linear_no_description_renders_placeholder(integration) -> None:
    """Empty description must render a stable placeholder block.

    Without this, an issue with no description and one with an empty
    string would produce different markdown (blank gap vs missing
    section), which would break content_sha stability on re-sync.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _issue_payload(description="")
        return httpx.Response(200, json=payload)

    async with _make_client(handler) as client:
        pages = await fetch_connector_pages(
            integration, {"issue_id": "ELM-42"}, http_client=client
        )

    body = pages[0].body_md
    assert "_(no description)_" in body
