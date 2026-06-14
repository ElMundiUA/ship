"""MCP OAuth broker — Ship as the OAuth 2.1 authorization server (ELS-296).

Lets a business operator attach the MCP edge with *add → log in → grant
→ done* instead of pasting a PAT. Ship is the authorization server; the
human login + consent is delegated to the console (which already owns
the Auth0 session + workspace context). The issued access token is a
short-lived **user-scoped** ``ship_pat_`` (``workspace_id = NULL``) —
the operator's agent sees every workspace they belong to and can create
new ones, exactly like their manual PAT — so the existing
``_resolve_pat`` path validates it unchanged.

Surface split:

- **Machine-facing (here, app root):** discovery metadata, Dynamic
  Client Registration (``POST /oauth/register``), the token endpoint
  (``POST /oauth/token`` — public client + PKCE), and the
  session-authed grant endpoint the console calls after consent.
- **Human-facing (console):** ``GET /oauth/authorize`` renders the
  consent screen; an unauthenticated operator hits the normal console
  login first. On approve it POSTs to ``/oauth/authorize/grant`` with
  the session token and 302s the browser back to the client's
  ``redirect_uri`` with the code.

Flow: client DCR → console consent (login if needed) → grant mints a
single-use PKCE-bound code → ``/oauth/token`` exchanges code+verifier
for the scoped PAT.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.core.config import Settings, get_settings
from backend.app.db.models import McpOAuthClient, McpOAuthCode
from backend.app.db.models.tenancy import ApiToken, AuditLog
from backend.app.db.session import get_session
from backend.app.security.tokens import PAT_PREFIX, generate_pat, hash_pat

router = APIRouter(tags=["mcp-oauth"])

# The MCP resource the issued tokens are scoped to (the audience the
# client requests). Kept as the canonical /mcp URL.
MCP_SCOPE = "mcp"
CODE_TTL = timedelta(minutes=10)
# Access-token lifetime. MCP clients re-run the (one-click) consent when
# this lapses; no refresh token in the MVP — a follow-up can add the
# refresh grant if re-consent friction shows up.
ACCESS_TOKEN_TTL_DAYS = 90


def _base(settings: Settings) -> str:
    return settings.public_url.rstrip("/")


def _resource(settings: Settings) -> str:
    return f"{_base(settings)}/mcp"


def _b64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _loopback(uri: str) -> bool:
    host = (urlsplit(uri).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _redirect_uri_allowed(registered: list[str], requested: str) -> bool:
    """Exact match, except loopback URIs match port-insensitively
    (RFC 8252 §7.3 — native apps bind an ephemeral localhost port)."""
    if requested in registered:
        return True
    if not _loopback(requested):
        return False
    req = urlsplit(requested)
    for reg in registered:
        if not _loopback(reg):
            continue
        r = urlsplit(reg)
        if (r.scheme, r.hostname, r.path) == (req.scheme, req.hostname, req.path):
            return True
    return False


# ---------------------------------------------------------------------------
# Discovery (RFC 9728 + RFC 8414)
# ---------------------------------------------------------------------------


def _protected_resource_metadata(settings: Settings) -> dict[str, Any]:
    return {
        "resource": _resource(settings),
        "authorization_servers": [_base(settings)],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [MCP_SCOPE],
        "resource_documentation": f"{_base(settings)}/mcp",
    }


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    return JSONResponse(_protected_resource_metadata(settings))


@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata_mcp(
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    # MCP clients may probe the path-suffixed variant per RFC 9728 §3.1.
    return JSONResponse(_protected_resource_metadata(settings))


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    base = _base(settings)
    console = settings.console_url.rstrip("/")
    return JSONResponse(
        {
            "issuer": base,
            # Human-facing consent lives in the console; token + DCR are
            # machine-facing on the API.
            "authorization_endpoint": f"{console}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [MCP_SCOPE],
        }
    )


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------


@router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
async def register_client(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid_client_metadata")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_client_metadata")

    redirect_uris = body.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or not all(isinstance(u, str) and u for u in redirect_uris)
    ):
        raise HTTPException(
            status_code=400, detail="invalid_redirect_uri"
        )

    client_id = f"mcp_{secrets.token_urlsafe(18)}"
    client = McpOAuthClient(
        client_id=client_id,
        client_name=str(body.get("client_name") or "")[:200] or None,
        redirect_uris=redirect_uris,
        grant_types=["authorization_code"],
        token_endpoint_auth_method="none",
    )
    session.add(client)
    await session.commit()

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "client_id": client_id,
            "client_id_issued_at": int(datetime.now(timezone.utc).timestamp()),
            "client_name": client.client_name,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )


# ---------------------------------------------------------------------------
# Console-facing helpers: describe a client + mint a code after consent
# ---------------------------------------------------------------------------


@router.get("/oauth/clients/{client_id}")
async def describe_client(
    client_id: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Public client metadata for the console consent screen. Session-
    authed so anonymous callers can't enumerate the client table."""
    client = await session.get(McpOAuthClient, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="unknown_client")
    return JSONResponse(
        {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "redirect_uris": client.redirect_uris,
        }
    )


@router.post("/oauth/authorize/grant")
async def authorize_grant(
    request: Request,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Operator approved the consent screen (console, under their
    session). Validate the request, mint a single-use PKCE-bound code,
    and return the redirect URL the console should 302 the browser to.

    The grant is **user-scoped, not workspace-scoped**: the operator's
    own agent must see every workspace they belong to AND be able to
    create new ones (``workspace_create`` is refused to workspace-pinned
    tokens). So the issued access token mirrors the operator's manual
    PAT — ``workspace_id = NULL`` — and the MCP edge infers / accepts a
    per-tool ``workspace_id`` as it already does.

    Body: ``{client_id, redirect_uri, code_challenge,
    code_challenge_method, state, scope}``.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid_request")

    client_id = str(body.get("client_id") or "").strip()
    redirect_uri = str(body.get("redirect_uri") or "").strip()
    code_challenge = str(body.get("code_challenge") or "").strip()
    method = str(body.get("code_challenge_method") or "S256").strip()
    state = body.get("state")

    client = await session.get(McpOAuthClient, client_id)
    if client is None:
        raise HTTPException(status_code=400, detail="unknown_client")
    if not _redirect_uri_allowed(client.redirect_uris, redirect_uri):
        raise HTTPException(status_code=400, detail="invalid_redirect_uri")
    if method != "S256" or not code_challenge:
        # PKCE S256 is mandatory — refuse plain/missing challenges.
        raise HTTPException(status_code=400, detail="invalid_request")

    raw_code = secrets.token_urlsafe(32)
    session.add(
        McpOAuthCode(
            code_hash=hash_pat(raw_code),
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method="S256",
            user_id=auth.user.id,
            workspace_id=None,  # user-scoped: all workspaces + create
            scopes=[MCP_SCOPE],
            expires_at=datetime.now(timezone.utc) + CODE_TTL,
        )
    )
    session.add(
        AuditLog(
            workspace_id=None,
            actor_user_id=auth.user.id,
            action="mcp_oauth.authorize.grant",
            target_kind="mcp_oauth_client",
            target_id=client_id,
            payload={"redirect_uri": redirect_uri, "scope": "user_all_workspaces"},
        )
    )
    await session.commit()

    params = {"code": raw_code}
    if isinstance(state, str) and state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return JSONResponse({"redirect_to": f"{redirect_uri}{sep}{urlencode(params)}"})


# ---------------------------------------------------------------------------
# Token endpoint (public client + PKCE; OAuth 2.1 code grant)
# ---------------------------------------------------------------------------


def _token_error(code: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})


@router.post("/oauth/token")
async def token(
    grant_type: str = Form(...),
    code: str | None = Form(default=None),
    code_verifier: str | None = Form(default=None),
    client_id: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    if grant_type != "authorization_code":
        return _token_error("unsupported_grant_type")
    if not code or not code_verifier or not client_id or not redirect_uri:
        return _token_error("invalid_request")

    row = (
        await session.execute(
            select(McpOAuthCode).where(McpOAuthCode.code_hash == hash_pat(code))
        )
    ).scalar_one_or_none()
    if row is None:
        return _token_error("invalid_grant")
    # Single-use + binding checks. A replayed or cross-client/redirect
    # code is refused.
    now = datetime.now(timezone.utc)
    if (
        row.consumed_at is not None
        or row.expires_at <= now
        or row.client_id != client_id
        or row.redirect_uri != redirect_uri
    ):
        return _token_error("invalid_grant")
    # PKCE: the verifier must hash to the stored challenge.
    if _b64url_sha256(code_verifier) != row.code_challenge:
        return _token_error("invalid_grant")

    # Burn the code, mint the access token = a short-lived
    # workspace-scoped PAT (validated by the existing _resolve_pat path).
    row.consumed_at = now
    raw_pat = generate_pat()
    expires_at = now + timedelta(days=ACCESS_TOKEN_TTL_DAYS)
    token_row = ApiToken(
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        name=f"mcp-oauth:{client_id[:24]}",
        hashed_secret=hash_pat(raw_pat),
        prefix=PAT_PREFIX,
        scopes=list(row.scopes or []),
        expires_at=expires_at,
    )
    session.add(token_row)
    session.add(
        AuditLog(
            workspace_id=row.workspace_id,
            actor_user_id=row.user_id,
            action="mcp_oauth.token.issue",
            target_kind="api_token",
            target_id=str(token_row.id),
            payload={"client_id": client_id},
        )
    )
    await session.commit()

    return JSONResponse(
        {
            "access_token": raw_pat,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL_DAYS * 24 * 3600,
            "scope": " ".join(row.scopes or []),
        }
    )
