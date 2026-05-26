"""Linear OAuth helpers — state minting, authorize URL, code exchange.

Mirrors the shape of :mod:`backend.app.integrations.github.oauth` so the
two flows feel the same to readers. The state token reuses our
``JWT_SECRET`` because it never leaves our backend round-trip — same
reasoning as the GitHub install state.

Linear's OAuth surface (as of 2026-01) is documented at
https://linear.app/developers/oauth-2-0-authentication . Endpoints we
care about:

- ``GET https://linear.app/oauth/authorize`` — user-facing approval page
- ``POST https://api.linear.app/oauth/token`` — code → access_token
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from backend.app.core.config import Settings


_STATE_TTL_SECONDS: Final[int] = 5 * 60
_STATE_SUBJECT: Final[str] = "ship.linear.oauth.state"

LINEAR_AUTHORIZE_URL: Final[str] = "https://linear.app/oauth/authorize"
LINEAR_TOKEN_URL: Final[str] = "https://api.linear.app/oauth/token"


class LinearMisconfigured(RuntimeError):
    """Raised when LINEAR_CLIENT_ID / LINEAR_CLIENT_SECRET are missing."""


class InvalidLinearState(ValueError):
    """Raised when the round-tripped state token is bad/missing/expired."""


class LinearTokenExchangeFailed(RuntimeError):
    """Linear refused to swap our authorization code for an access token."""


@dataclass(frozen=True, slots=True)
class LinearOAuthState:
    workspace_id: uuid.UUID
    nonce: str
    # Optional console path to bounce the browser back to after a
    # successful OAuth callback. Used by the workspace-settings
    # "Reconnect Linear" flow so reconnecting from /integrations
    # lands back on /integrations instead of /onboarding (which is
    # what the wizard-driven path wants). Validated against the
    # console origin before redirect; ``None`` falls back to the
    # onboarding URL the wizard expects.
    return_to: str | None = None


@dataclass(frozen=True, slots=True)
class LinearTokenBundle:
    """Subset of Linear's token response we actually persist.

    ``refresh_token`` is what makes "set and forget" possible: when
    the ``access_token`` nears or crosses ``expires_in``, the refresh
    flow swaps the pair for a new one without bouncing the operator
    back through Linear's consent screen. Linear returns the refresh
    token on both ``authorization_code`` and ``refresh_token`` grants
    when the OAuth app is configured to issue them; ``None`` here
    means the token is non-refreshable and the operator will have to
    re-authorize when the access token expires.
    """

    access_token: str
    token_type: str
    scope: str
    expires_in: int | None  # seconds, when present
    refresh_token: str | None = None


def _state_secret(settings: Settings) -> str:
    return settings.jwt_secret


def _require_credentials(settings: Settings) -> tuple[str, str]:
    if not settings.linear_client_id or not settings.linear_client_secret:
        raise LinearMisconfigured(
            "LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET must be set"
        )
    return settings.linear_client_id, settings.linear_client_secret


def build_oauth_state(
    workspace_id: uuid.UUID,
    *,
    settings: Settings,
    return_to: str | None = None,
) -> str:
    """Sign a JWT carrying ``workspace_id`` + a CSRF nonce.

    ``return_to`` is an optional console path the callback uses to
    bounce the browser back to. Signed alongside the workspace id so
    a tampered query parameter can't redirect the browser somewhere
    other than the page that started the flow.
    """
    issued_at = int(time.time())
    claims = {
        "sub": _STATE_SUBJECT,
        "wid": str(workspace_id),
        "nonce": secrets.token_urlsafe(16),
        "iat": issued_at,
        "exp": issued_at + _STATE_TTL_SECONDS,
    }
    if return_to:
        claims["rt"] = return_to
    return jwt.encode(claims, _state_secret(settings), algorithm="HS256")


def verify_oauth_state(state: str, *, settings: Settings) -> LinearOAuthState:
    try:
        claims = jwt.decode(
            state,
            _state_secret(settings),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise InvalidLinearState("state token is invalid or expired") from exc
    if claims.get("sub") != _STATE_SUBJECT:
        raise InvalidLinearState("state token has wrong subject")
    raw_wid = claims.get("wid")
    raw_nonce = claims.get("nonce")
    if not raw_wid or not raw_nonce:
        raise InvalidLinearState("state token is missing fields")
    try:
        workspace_id = uuid.UUID(str(raw_wid))
    except ValueError as exc:
        raise InvalidLinearState("state token has malformed wid") from exc
    raw_return_to = claims.get("rt")
    return_to = str(raw_return_to) if isinstance(raw_return_to, str) else None
    return LinearOAuthState(
        workspace_id=workspace_id,
        nonce=str(raw_nonce),
        return_to=return_to,
    )


def build_authorize_url(
    state: str, *, settings: Settings, redirect_uri: str
) -> str:
    """URL the browser opens to start the Linear OAuth dance."""
    client_id, _ = _require_credentials(settings)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        # ``actor=user`` makes Linear store the integration as authored
        # by the connecting user (so comments show their avatar). Switch
        # to ``app`` if/when we want comments attributed to "Ship".
        "actor": "user",
        "scope": settings.linear_oauth_scopes,
        "state": state,
        # ``prompt=consent`` forces re-approval if the user has already
        # connected — keeps the UX consistent with first-time installs.
        "prompt": "consent",
    }
    return f"{LINEAR_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(
    code: str,
    *,
    settings: Settings,
    redirect_uri: str,
    client: httpx.AsyncClient | None = None,
) -> LinearTokenBundle:
    """POST the auth code to Linear's token endpoint and return the
    parsed token bundle. Raises :class:`LinearTokenExchangeFailed` on
    any non-2xx response so callers can surface a friendly error.

    The optional ``client`` lets callers reuse a connection pool (FastAPI
    request scope); when not supplied we open and close a short-lived
    one. Linear documents this as ``application/x-www-form-urlencoded``.
    """
    client_id, client_secret = _require_credentials(settings)
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await http.post(
            LINEAR_TOKEN_URL,
            data=payload,
            headers={"Accept": "application/json"},
        )
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code >= 400:
        raise LinearTokenExchangeFailed(
            f"Linear token endpoint returned HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    body = response.json()
    access_token = body.get("access_token")
    if not access_token:
        raise LinearTokenExchangeFailed(
            "Linear token endpoint returned no access_token"
        )
    refresh_raw = body.get("refresh_token")
    return LinearTokenBundle(
        access_token=str(access_token),
        token_type=str(body.get("token_type", "Bearer")),
        scope=str(body.get("scope", settings.linear_oauth_scopes)),
        expires_in=int(body["expires_in"]) if body.get("expires_in") else None,
        refresh_token=str(refresh_raw) if refresh_raw else None,
    )


async def refresh_access_token(
    refresh_token: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> LinearTokenBundle:
    """Mint a fresh access (and rotated refresh) token from the Linear
    token endpoint using ``grant_type=refresh_token``.

    Linear rotates the refresh token on every refresh — the response
    carries both a new ``access_token`` and a new ``refresh_token``
    that supersedes the one we sent. The caller is responsible for
    persisting the new pair before the next call (otherwise the next
    refresh sees a stale refresh token and 4xxs).

    Failures (revoked grant, network 5xx, 4xx with the refresh token
    Linear no longer recognizes) raise :class:`LinearTokenExchangeFailed`
    so the caller can decide whether to mark the integration broken
    or surface a re-auth prompt.
    """
    client_id, client_secret = _require_credentials(settings)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await http.post(
            LINEAR_TOKEN_URL,
            data=payload,
            headers={"Accept": "application/json"},
        )
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code >= 400:
        raise LinearTokenExchangeFailed(
            f"Linear refresh endpoint returned HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    body = response.json()
    access_token = body.get("access_token")
    if not access_token:
        raise LinearTokenExchangeFailed(
            "Linear refresh endpoint returned no access_token"
        )
    refresh_raw = body.get("refresh_token")
    return LinearTokenBundle(
        access_token=str(access_token),
        token_type=str(body.get("token_type", "Bearer")),
        scope=str(body.get("scope", settings.linear_oauth_scopes)),
        expires_in=int(body["expires_in"]) if body.get("expires_in") else None,
        # Some OAuth providers omit refresh_token on rotation if the
        # original one is still valid; fall back to the value the
        # caller supplied so the credential row never empties out.
        refresh_token=str(refresh_raw) if refresh_raw else refresh_token,
    )


__all__ = [
    "InvalidLinearState",
    "LinearMisconfigured",
    "LinearOAuthState",
    "LinearTokenBundle",
    "LinearTokenExchangeFailed",
    "LINEAR_AUTHORIZE_URL",
    "LINEAR_TOKEN_URL",
    "build_authorize_url",
    "build_oauth_state",
    "exchange_code_for_token",
    "refresh_access_token",
    "verify_oauth_state",
]
