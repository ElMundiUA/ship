"""MCP OAuth broker (ELS-296) — discovery, DCR, PKCE code grant.

Ship is the OAuth 2.1 authorization server for the MCP edge. Pins:

- discovery metadata (protected-resource + authorization-server) and the
  /mcp 401 ``resource_metadata`` hint that bootstraps the client;
- Dynamic Client Registration mints a public client;
- the console grant endpoint (session-authed) issues a single-use
  PKCE-bound code;
- /oauth/token exchanges code+verifier for a working **user-scoped**
  ``ship_pat_`` (all workspaces + workspace_create), and refuses
  replay / wrong-verifier / wrong-redirect.
"""

from __future__ import annotations

import base64
import hashlib

import pytest


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(b"v" * 48).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


async def _register(client, redirect_uri="http://127.0.0.1:53682/callback") -> str:
    res = await client.post(
        "/oauth/register",
        json={"client_name": "Claude Code (test)", "redirect_uris": [redirect_uri]},
    )
    assert res.status_code == 201, res.text
    return res.json()["client_id"]


async def _grant(client, raw_pat, *, client_id, redirect_uri, challenge):
    return await client.post(
        "/oauth/authorize/grant",
        headers={"Authorization": f"Bearer {raw_pat}"},
        json={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )


def _code_from_redirect(redirect_to: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    q = parse_qs(urlsplit(redirect_to).query)
    assert q.get("state") == ["xyz"]
    return q["code"][0]


@pytest.mark.asyncio
async def test_protected_resource_metadata(v1_client) -> None:
    res = await v1_client.get("/.well-known/oauth-protected-resource")
    assert res.status_code == 200
    body = res.json()
    assert body["resource"].endswith("/mcp")
    assert len(body["authorization_servers"]) == 1
    assert "mcp" in body["scopes_supported"]


@pytest.mark.asyncio
async def test_authorization_server_metadata(v1_client) -> None:
    res = await v1_client.get("/.well-known/oauth-authorization-server")
    assert res.status_code == 200
    body = res.json()
    assert body["token_endpoint"].endswith("/oauth/token")
    assert body["registration_endpoint"].endswith("/oauth/register")
    assert body["authorization_endpoint"].endswith("/oauth/authorize")
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]


@pytest.mark.asyncio
async def test_mcp_401_carries_resource_metadata(v1_client) -> None:
    res = await v1_client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    )
    assert res.status_code == 401
    www = res.headers.get("www-authenticate", "")
    assert "resource_metadata=" in www
    assert "/.well-known/oauth-protected-resource" in www


@pytest.mark.asyncio
async def test_dcr_register_and_validation(v1_client) -> None:
    res = await v1_client.post(
        "/oauth/register",
        json={"client_name": "x", "redirect_uris": ["http://127.0.0.1:9/cb"]},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["client_id"].startswith("mcp_")
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == ["http://127.0.0.1:9/cb"]

    # Missing redirect_uris → rejected.
    bad = await v1_client.post("/oauth/register", json={"client_name": "x"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_full_code_flow_issues_user_scoped_token(
    db_session, v1_client, seed_workspace
) -> None:
    """The grant is user-scoped (all workspaces + create), mirroring the
    operator's manual PAT — NOT pinned to one workspace."""
    from backend.app.db.models.tenancy import ApiToken
    from sqlalchemy import select

    _, raw, _ = seed_workspace
    verifier, challenge = _pkce()
    redirect_uri = "http://127.0.0.1:53682/callback"
    client_id = await _register(v1_client, redirect_uri)

    granted = await _grant(
        v1_client,
        raw,
        client_id=client_id,
        redirect_uri=redirect_uri,
        challenge=challenge,
    )
    assert granted.status_code == 200, granted.text
    code = _code_from_redirect(granted.json()["redirect_to"])

    res = await v1_client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        },
    )
    assert res.status_code == 200, res.text
    tok = res.json()
    assert tok["token_type"] == "Bearer"
    access = tok["access_token"]
    assert access.startswith("ship_pat_")

    # The minted token is user-scoped (workspace_id NULL) — the property
    # that lets the operator's agent see every workspace and create new
    # ones via workspace_create.
    issued = (
        await db_session.execute(
            select(ApiToken).where(ApiToken.name == f"mcp-oauth:{client_id[:24]}")
        )
    ).scalar_one()
    assert issued.workspace_id is None

    # And it works on the MCP edge.
    mcp = await v1_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {access}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_workspaces", "arguments": {}},
        },
    )
    assert mcp.status_code == 200
    assert mcp.json()["result"]["isError"] is False

    # workspace_create is reachable (user-scoped tokens may create;
    # workspace-pinned ones are refused) — proves the "create
    # namespaces" grant.
    created = await v1_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {access}"},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "workspace_create",
                "arguments": {"name": "OAuth made me", "slug": "oauth-made-me"},
            },
        },
    )
    assert created.status_code == 200
    assert created.json()["result"]["isError"] is False


@pytest.mark.asyncio
async def test_pkce_mismatch_refused(db_session, v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    _, challenge = _pkce()
    redirect_uri = "http://127.0.0.1:53682/callback"
    client_id = await _register(v1_client, redirect_uri)
    granted = await _grant(
        v1_client,
        raw,
        client_id=client_id,
        redirect_uri=redirect_uri,
        challenge=challenge,
    )
    code = _code_from_redirect(granted.json()["redirect_to"])

    res = await v1_client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": "the-wrong-verifier",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        },
    )
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_code_is_single_use(db_session, v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    verifier, challenge = _pkce()
    redirect_uri = "http://127.0.0.1:53682/callback"
    client_id = await _register(v1_client, redirect_uri)
    granted = await _grant(
        v1_client,
        raw,
        client_id=client_id,
        redirect_uri=redirect_uri,
        challenge=challenge,
    )
    code = _code_from_redirect(granted.json()["redirect_to"])
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    first = await v1_client.post("/oauth/token", data=payload)
    assert first.status_code == 200
    replay = await v1_client.post("/oauth/token", data=payload)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_token_refuses_redirect_uri_mismatch(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    verifier, challenge = _pkce()
    redirect_uri = "http://127.0.0.1:53682/callback"
    client_id = await _register(v1_client, redirect_uri)
    granted = await _grant(
        v1_client,
        raw,
        client_id=client_id,
        redirect_uri=redirect_uri,
        challenge=challenge,
    )
    code = _code_from_redirect(granted.json()["redirect_to"])
    res = await v1_client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": "http://127.0.0.1:53682/EVIL",
        },
    )
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_grant_refuses_plain_pkce(db_session, v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    redirect_uri = "http://127.0.0.1:53682/callback"
    client_id = await _register(v1_client, redirect_uri)
    res = await v1_client.post(
        "/oauth/authorize/grant",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": "",
            "code_challenge_method": "plain",
            "state": "xyz",
        },
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_grant_refuses_unregistered_redirect(
    db_session, v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    _, challenge = _pkce()
    client_id = await _register(v1_client, "http://127.0.0.1:53682/callback")
    res = await _grant(
        v1_client,
        raw,
        client_id=client_id,
        redirect_uri="https://evil.example.com/cb",
        challenge=challenge,
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "invalid_redirect_uri"
