"""Notion OAuth helpers — state minting, authorize URL, code exchange.

Notion's OAuth surface (as of 2026-01) is documented at
https://developers.notion.com/docs/authorization . Endpoints we use:

- ``GET https://api.notion.com/v1/oauth/authorize`` — user-facing
  approval page (workspace + page picker)
- ``POST https://api.notion.com/v1/oauth/token`` — code → access_token
  (HTTP Basic auth with client_id:client_secret)

Notion is a *workspace-scoped* token: the user picks which pages /
databases to share at consent time, and we get back a single
``access_token`` plus metadata about the workspace and bot user. The
tracker adapter reads `databases.search` to enumerate ticket-shaped
databases the user explicitly granted.
"""

from __future__ import annotations

import base64
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from backend.app.core.config import Settings


_STATE_TTL_SECONDS: Final[int] = 5 * 60
_STATE_SUBJECT: Final[str] = "ship.notion.oauth.state"

NOTION_AUTHORIZE_URL: Final[str] = "https://api.notion.com/v1/oauth/authorize"
NOTION_TOKEN_URL: Final[str] = "https://api.notion.com/v1/oauth/token"


class NotionMisconfigured(RuntimeError):
    """Raised when NOTION_CLIENT_ID / NOTION_CLIENT_SECRET are missing."""


class InvalidNotionState(ValueError):
    """Raised when the round-tripped state token is bad/missing/expired."""


class NotionTokenExchangeFailed(RuntimeError):
    """Notion refused to swap our authorization code for an access token."""


@dataclass(frozen=True, slots=True)
class NotionOAuthState:
    workspace_id: uuid.UUID
    nonce: str


@dataclass(frozen=True, slots=True)
class NotionTokenBundle:
    """Subset of Notion's token response we persist + show to the user."""

    access_token: str
    bot_id: str
    workspace_id: str  # Notion-side workspace id (NOT our workspace_id)
    workspace_name: str | None
    workspace_icon: str | None
    owner: dict[str, Any]


def _state_secret(settings: Settings) -> str:
    return settings.jwt_secret


def _require_credentials(settings: Settings) -> tuple[str, str]:
    if not settings.notion_client_id or not settings.notion_client_secret:
        raise NotionMisconfigured(
            "NOTION_CLIENT_ID and NOTION_CLIENT_SECRET must be set"
        )
    return settings.notion_client_id, settings.notion_client_secret


def build_oauth_state(workspace_id: uuid.UUID, *, settings: Settings) -> str:
    issued_at = int(time.time())
    claims = {
        "sub": _STATE_SUBJECT,
        "wid": str(workspace_id),
        "nonce": secrets.token_urlsafe(16),
        "iat": issued_at,
        "exp": issued_at + _STATE_TTL_SECONDS,
    }
    return jwt.encode(claims, _state_secret(settings), algorithm="HS256")


def verify_oauth_state(state: str, *, settings: Settings) -> NotionOAuthState:
    try:
        claims = jwt.decode(
            state,
            _state_secret(settings),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise InvalidNotionState("state token is invalid or expired") from exc
    if claims.get("sub") != _STATE_SUBJECT:
        raise InvalidNotionState("state token has wrong subject")
    raw_wid = claims.get("wid")
    raw_nonce = claims.get("nonce")
    if not raw_wid or not raw_nonce:
        raise InvalidNotionState("state token is missing fields")
    try:
        workspace_id = uuid.UUID(str(raw_wid))
    except ValueError as exc:
        raise InvalidNotionState("state token has malformed wid") from exc
    return NotionOAuthState(workspace_id=workspace_id, nonce=str(raw_nonce))


def build_authorize_url(
    state: str, *, settings: Settings, redirect_uri: str
) -> str:
    """URL the browser opens to start the Notion OAuth dance."""
    client_id, _ = _require_credentials(settings)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        # Notion only supports ``response_type=code``; the other params
        # come from the integration's "Public integration" config in
        # Notion's dashboard (capabilities, requested scopes, etc.).
        "response_type": "code",
        # ``owner=user`` makes Notion bind the integration to the
        # consenting user (vs the workspace as a whole) — required for
        # ticket-style databases that aren't workspace-public.
        "owner": "user",
        "state": state,
    }
    return f"{NOTION_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(
    code: str,
    *,
    settings: Settings,
    redirect_uri: str,
    client: httpx.AsyncClient | None = None,
) -> NotionTokenBundle:
    """Exchange the auth code for a Notion ``access_token`` bundle.

    Notion authenticates the token endpoint with HTTP Basic
    (``client_id:client_secret``) and expects a JSON body — this is
    different from Linear/GitHub, both of which use form-encoded posts.
    """
    client_id, client_secret = _require_credentials(settings)
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await http.post(
            NOTION_TOKEN_URL,
            json=payload,
            headers={
                "Authorization": f"Basic {basic}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                # Notion mandates a versioned API header on every call.
                # 2025-09-03 is the latest GA version as of the pilot;
                # bump as Notion publishes new ones.
                "Notion-Version": "2025-09-03",
            },
        )
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code >= 400:
        raise NotionTokenExchangeFailed(
            f"Notion token endpoint returned HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    body = response.json()
    access_token = body.get("access_token")
    bot_id = body.get("bot_id")
    workspace_id = body.get("workspace_id")
    if not access_token or not bot_id or not workspace_id:
        raise NotionTokenExchangeFailed(
            "Notion token endpoint returned an incomplete payload"
        )
    return NotionTokenBundle(
        access_token=str(access_token),
        bot_id=str(bot_id),
        workspace_id=str(workspace_id),
        workspace_name=body.get("workspace_name"),
        workspace_icon=body.get("workspace_icon"),
        owner=body.get("owner") or {},
    )


__all__ = [
    "InvalidNotionState",
    "NotionMisconfigured",
    "NotionOAuthState",
    "NotionTokenBundle",
    "NotionTokenExchangeFailed",
    "NOTION_AUTHORIZE_URL",
    "NOTION_TOKEN_URL",
    "build_authorize_url",
    "build_oauth_state",
    "exchange_code_for_token",
    "verify_oauth_state",
]
