"""DigitalOcean OAuth helpers — state minting, authorize URL, code/refresh.

DigitalOcean's OAuth surface (as of 2026-05) is documented at
https://docs.digitalocean.com/reference/api/oauth/ . Endpoints we use:

- ``GET  https://cloud.digitalocean.com/v1/oauth/authorize`` — user-facing
  approval page (account / team picker).
- ``POST https://cloud.digitalocean.com/v1/oauth/token`` — both the
  ``authorization_code`` exchange and the ``refresh_token`` rotation.
  Form-encoded (like Linear/GitHub), NOT JSON+Basic (like Notion).

Tokens are **account/team-scoped** and short-lived: DigitalOcean expires
the ``access_token`` after 30 days and returns a single-use
``refresh_token`` with each grant. We persist both and rotate them from a
cron tick (see ``services.digitalocean_token_refresh``) well before
expiry. The ``write`` scope is required to create/manage App Platform
apps; ``read`` lets us poll deployment status.
"""

from __future__ import annotations

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
_STATE_SUBJECT: Final[str] = "ship.digitalocean.oauth.state"

DO_AUTHORIZE_URL: Final[str] = "https://cloud.digitalocean.com/v1/oauth/authorize"
DO_TOKEN_URL: Final[str] = "https://cloud.digitalocean.com/v1/oauth/token"


class DigitalOceanMisconfigured(RuntimeError):
    """Raised when DIGITALOCEAN_CLIENT_ID / _SECRET are missing."""


class InvalidDigitalOceanState(ValueError):
    """Raised when the round-tripped state token is bad/missing/expired."""


class DigitalOceanTokenExchangeFailed(RuntimeError):
    """DigitalOcean refused to swap our code/refresh for an access token."""


@dataclass(frozen=True, slots=True)
class DigitalOceanOAuthState:
    workspace_id: uuid.UUID
    nonce: str
    return_path: str | None = None


@dataclass(frozen=True, slots=True)
class DigitalOceanTokenBundle:
    """Subset of DO's token response we persist + show to the user."""

    access_token: str
    refresh_token: str | None
    token_type: str
    scope: str | None
    expires_in: int | None
    # ``info`` block — identifies the account/team the token acts on.
    account_uuid: str | None
    account_email: str | None
    team_uuid: str | None
    team_name: str | None

    @property
    def external_account_id(self) -> str:
        """Stable id for the connected DO account.

        Prefer the team uuid (App Platform apps live under a team); fall
        back to the personal account uuid, then a sentinel so the upsert
        unique constraint still has a value.
        """
        return self.team_uuid or self.account_uuid or "default"


def _state_secret(settings: Settings) -> str:
    return settings.jwt_secret


def _require_credentials(settings: Settings) -> tuple[str, str]:
    if not settings.digitalocean_client_id or not settings.digitalocean_client_secret:
        raise DigitalOceanMisconfigured(
            "DIGITALOCEAN_CLIENT_ID and DIGITALOCEAN_CLIENT_SECRET must be set"
        )
    return settings.digitalocean_client_id, settings.digitalocean_client_secret


def build_oauth_state(
    workspace_id: uuid.UUID,
    *,
    settings: Settings,
    return_path: str | None = None,
) -> str:
    issued_at = int(time.time())
    claims = {
        "sub": _STATE_SUBJECT,
        "wid": str(workspace_id),
        "nonce": secrets.token_urlsafe(16),
        "iat": issued_at,
        "exp": issued_at + _STATE_TTL_SECONDS,
    }
    if return_path and return_path.startswith("/") and not return_path.startswith("//"):
        claims["return_path"] = return_path[:500]
    return jwt.encode(claims, _state_secret(settings), algorithm="HS256")


def verify_oauth_state(state: str, *, settings: Settings) -> DigitalOceanOAuthState:
    try:
        claims = jwt.decode(
            state,
            _state_secret(settings),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise InvalidDigitalOceanState("state token is invalid or expired") from exc
    if claims.get("sub") != _STATE_SUBJECT:
        raise InvalidDigitalOceanState("state token has wrong subject")
    raw_wid = claims.get("wid")
    raw_nonce = claims.get("nonce")
    if not raw_wid or not raw_nonce:
        raise InvalidDigitalOceanState("state token is missing fields")
    try:
        workspace_id = uuid.UUID(str(raw_wid))
    except ValueError as exc:
        raise InvalidDigitalOceanState("state token has malformed wid") from exc
    raw_return_path = claims.get("return_path")
    return_path = (
        str(raw_return_path)
        if isinstance(raw_return_path, str)
        and raw_return_path.startswith("/")
        and not raw_return_path.startswith("//")
        else None
    )
    return DigitalOceanOAuthState(
        workspace_id=workspace_id,
        nonce=str(raw_nonce),
        return_path=return_path,
    )


def build_authorize_url(state: str, *, settings: Settings, redirect_uri: str) -> str:
    """URL the browser opens to start the DigitalOcean OAuth dance."""
    client_id, _ = _require_credentials(settings)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.digitalocean_oauth_scopes,
        "state": state,
    }
    return f"{DO_AUTHORIZE_URL}?{urlencode(params)}"


def _parse_token_response(body: dict[str, Any]) -> DigitalOceanTokenBundle:
    access_token = body.get("access_token")
    if not access_token:
        raise DigitalOceanTokenExchangeFailed(
            "DigitalOcean token endpoint returned no access_token"
        )
    info = body.get("info") or {}
    return DigitalOceanTokenBundle(
        access_token=str(access_token),
        refresh_token=(str(body["refresh_token"]) if body.get("refresh_token") else None),
        token_type=str(body.get("token_type") or "bearer"),
        scope=(str(body["scope"]) if body.get("scope") else None),
        expires_in=(int(body["expires_in"]) if body.get("expires_in") else None),
        account_uuid=(str(info["uuid"]) if info.get("uuid") else None),
        account_email=(str(info["email"]) if info.get("email") else None),
        team_uuid=(str(info["team_uuid"]) if info.get("team_uuid") else None),
        team_name=(str(info["team_name"]) if info.get("team_name") else None),
    )


async def exchange_code_for_token(
    code: str,
    *,
    settings: Settings,
    redirect_uri: str,
    client: httpx.AsyncClient | None = None,
) -> DigitalOceanTokenBundle:
    """Exchange the authorization code for a DigitalOcean token bundle."""
    client_id, client_secret = _require_credentials(settings)
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    return await _post_token(payload, client=client)


async def refresh_access_token(
    refresh_token: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> DigitalOceanTokenBundle:
    """Rotate an expiring access token using its single-use refresh token."""
    client_id, client_secret = _require_credentials(settings)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    return await _post_token(payload, client=client)


async def _post_token(
    payload: dict[str, str], *, client: httpx.AsyncClient | None
) -> DigitalOceanTokenBundle:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await http.post(
            DO_TOKEN_URL,
            data=payload,
            headers={"Accept": "application/json"},
        )
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code >= 400:
        raise DigitalOceanTokenExchangeFailed(
            f"DigitalOcean token endpoint returned HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    return _parse_token_response(response.json())


__all__ = [
    "DO_AUTHORIZE_URL",
    "DO_TOKEN_URL",
    "DigitalOceanMisconfigured",
    "DigitalOceanOAuthState",
    "DigitalOceanTokenBundle",
    "DigitalOceanTokenExchangeFailed",
    "InvalidDigitalOceanState",
    "build_authorize_url",
    "build_oauth_state",
    "exchange_code_for_token",
    "refresh_access_token",
    "verify_oauth_state",
]
