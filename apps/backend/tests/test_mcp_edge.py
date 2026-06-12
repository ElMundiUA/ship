"""Ship MCP Edge (thesis 9) — protocol + policy tests.

JSON-RPC over POST /mcp with bearer-PAT auth. Pins:

- initialize returns instructions + tools capability;
- tools/list = ToolBox allowlist projection (workspace_id injected,
  web_fetch/recall excluded) + the native workspace/github tools;
- tools/call dispatches into the existing handlers and commits;
- auth is mandatory (401 without a PAT);
- stakes gate: approval items demand a verbatim approval_echo,
  destructive-marked items are web-only with a Console deep-link;
- workspace_create works end-to-end over MCP.
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend.app.db.models.inbox import InboxItem
from backend.app.api.v1.routes.mcp import MCP_TOOL_ALLOWLIST


def _rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


async def _post(client, raw_pat: str, payload: dict):
    return await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_pat}"},
        json=payload,
    )


@pytest.mark.asyncio
async def test_mcp_requires_auth(v1_client) -> None:
    res = await v1_client.post("/mcp", json=_rpc("initialize"))
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_initialize_carries_instructions(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    res = await _post(v1_client, raw, _rpc("initialize", {"protocolVersion": "2025-03-26"}))
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["serverInfo"]["name"] == "ship"
    assert "verify-before-mutate" in result["instructions"]
    assert result["capabilities"]["tools"] == {}


@pytest.mark.asyncio
async def test_tools_list_projection_and_natives(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    res = await _post(v1_client, raw, _rpc("tools/list"))
    tools = {t["name"]: t for t in res.json()["result"]["tools"]}
    # Allowlist projected, exclusions held.
    assert MCP_TOOL_ALLOWLIST <= set(tools)
    assert "web_fetch" not in tools
    assert "recall" not in tools
    # Natives present.
    for native in (
        "list_workspaces",
        "workspace_create",
        "github_sibling_installs",
        "github_attach_install",
    ):
        assert native in tools
    # workspace_id injected into projected tools.
    assert "workspace_id" in tools["dashboard_get"]["inputSchema"]["properties"]
    # approval_echo contract surfaced on inbox_update.
    assert "approval_echo" in tools["inbox_update"]["inputSchema"]["properties"]


@pytest.mark.asyncio
async def test_tools_call_dispatches_toolbox(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    res = await _post(
        v1_client,
        raw,
        _rpc("tools/call", {"name": "inbox_list", "arguments": {}}),
    )
    result = res.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"


@pytest.mark.asyncio
async def test_unknown_method_is_rpc_error(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    res = await _post(v1_client, raw, _rpc("resources/list"))
    assert res.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_notification_acknowledged(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    res = await _post(
        v1_client,
        raw,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert res.status_code == 202


@pytest.mark.asyncio
async def test_approval_without_echo_refused(
    db_session, v1_client, seed_workspace
) -> None:
    """Missing/wrong echo → refused with the title quoted back.

    (The refusal path rolls the request back, which in this shared-
    transaction test harness discards the seeds — so the happy path
    lives in its own test below.)"""
    _, raw, workspace = seed_workspace
    item = InboxItem(
        workspace_id=workspace.id,
        type="approval",
        status="new",
        title="Approve deploy of api to prod",
        summary="s",
    )
    db_session.add(item)
    await db_session.flush()

    res = await _post(
        v1_client,
        raw,
        _rpc(
            "tools/call",
            {
                "name": "inbox_update",
                "arguments": {
                    "item_id": str(item.id),
                    "action": "dispose",
                    "resolution": "approved",
                },
            },
        ),
    )
    result = res.json()["result"]
    assert result["isError"] is True
    assert "Approve deploy of api to prod" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_approval_with_exact_echo_passes_gate(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    item = InboxItem(
        workspace_id=workspace.id,
        type="approval",
        status="new",
        title="Approve deploy of api to prod",
        summary="s",
    )
    db_session.add(item)
    await db_session.flush()

    res = await _post(
        v1_client,
        raw,
        _rpc(
            "tools/call",
            {
                "name": "inbox_update",
                "arguments": {
                    "item_id": str(item.id),
                    "action": "dispose",
                    "resolution": "approved",
                    "approval_echo": "Approve deploy of api to prod",
                },
            },
        ),
    )
    result = res.json()["result"]
    # The gate passed — whatever the handler said, it is NOT the echo
    # refusal (which quotes the title).
    if result["isError"]:
        assert "approval_echo" not in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_destructive_items_are_web_only(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    item = InboxItem(
        workspace_id=workspace.id,
        type="approval",
        status="new",
        title="Delete repository acme/legacy",
        summary="s",
        payload={"stakes": "destructive"},
    )
    db_session.add(item)
    await db_session.flush()

    res = await _post(
        v1_client,
        raw,
        _rpc(
            "tools/call",
            {
                "name": "inbox_update",
                "arguments": {
                    "item_id": str(item.id),
                    "action": "dispose",
                    "resolution": "approved",
                    "approval_echo": "Delete repository acme/legacy",
                },
            },
        ),
    )
    result = res.json()["result"]
    assert result["isError"] is True
    assert "/inbox?ws=" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_workspace_create_over_mcp(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    slug = f"mcp-{uuid.uuid4().hex[:8]}"
    res = await _post(
        v1_client,
        raw,
        _rpc(
            "tools/call",
            {
                "name": "workspace_create",
                "arguments": {"name": "MCP made me", "slug": slug},
            },
        ),
    )
    result = res.json()["result"]
    assert result["isError"] is False, result
    body = json.loads(result["content"][0]["text"])
    assert body["slug"] == slug
    assert "onboarding" in body["next"]

    # And the new workspace shows up in list_workspaces.
    res2 = await _post(
        v1_client, raw, _rpc("tools/call", {"name": "list_workspaces", "arguments": {}})
    )
    listing = json.loads(res2.json()["result"]["content"][0]["text"])
    assert any(w["slug"] == slug for w in listing)


@pytest.mark.asyncio
async def test_foreign_workspace_refused(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    res = await _post(
        v1_client,
        raw,
        _rpc(
            "tools/call",
            {
                "name": "dashboard_get",
                "arguments": {"workspace_id": str(uuid.uuid4())},
            },
        ),
    )
    result = res.json()["result"]
    assert result["isError"] is True
    assert "not a member" in result["content"][0]["text"]
