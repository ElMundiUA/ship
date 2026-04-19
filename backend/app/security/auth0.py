"""Auth0 access-token validation (RFC-0006, Phase 0.3).

Backend mode ``SHIP_AUTH_MODE=auth0`` swaps the locally-issued session JWT
for an Auth0-issued RS256 access token. The Console (Next.js) carries out
the OIDC dance with Auth0 via ``@auth0/nextjs-auth0`` and forwards the
resulting ``access_token`` to us in ``Authorization: Bearer <jwt>``.

This module owns:

- **JWKS cache.** Auth0 publishes signing keys at
  ``https://{domain}/.well-known/jwks.json``. We cache them in-process and
  refresh on a key-id miss (rotation handles itself).
- **Verification.** Decode + verify with python-jose, RS256, audience and
  issuer pinned to the tenant config. Time skew defaults to 60s.
- **JIT mapping.** ``user_from_claims`` upserts a :class:`User` row keyed by
  the Auth0 ``sub`` (with email fallback for pre-invited rows). The mapped
  ``external_subject`` column makes this loop O(1) and prevents accidental
  email collisions when a user changes IdP.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from threading import Lock
from typing import Any

import httpx
from fastapi import HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.tenancy import User


log = logging.getLogger(__name__)


_JWKS_TTL_SECONDS = 60 * 60  # 1h — Auth0 docs recommend ≥ 10m, we go bigger.
_JWKS_FETCH_TIMEOUT = 4.0
_JWT_LEEWAY_SECONDS = 60


class _JwksCache:
    """In-process JWKS cache shared across requests.

    Thread-safe; the actual HTTP fetch is sync (httpx) because JWKS misses
    are rare and we want a hard ceiling on token-validation latency rather
    than dragging in async surface area for one network call.
    """

    def __init__(self) -> None:
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float = 0.0
        self._url: str | None = None
        self._lock = Lock()

    def get(self, kid: str, *, url: str) -> dict[str, Any] | None:
        with self._lock:
            stale = (time.monotonic() - self._fetched_at) > _JWKS_TTL_SECONDS
            different_url = url != self._url
            if kid in self._keys and not stale and not different_url:
                return self._keys[kid]
        # Either the kid isn't cached, the cache expired, or the URL changed.
        # Refresh and try again.
        try:
            resp = httpx.get(url, timeout=_JWKS_FETCH_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("jwks fetch failed: %s", exc)
            return None
        keys = payload.get("keys") if isinstance(payload, Mapping) else None
        if not isinstance(keys, list):
            return None
        with self._lock:
            self._keys = {
                k["kid"]: k
                for k in keys
                if isinstance(k, Mapping) and isinstance(k.get("kid"), str)
            }
            self._fetched_at = time.monotonic()
            self._url = url
            return self._keys.get(kid)

    def reset(self) -> None:
        """Test helper: drop the cache so the next call refetches."""
        with self._lock:
            self._keys.clear()
            self._fetched_at = 0.0
            self._url = None


_cache = _JwksCache()


def reset_jwks_cache() -> None:
    """Public test helper — wipe the module-level JWKS cache."""
    _cache.reset()


def _ensure_settings(settings: Settings) -> tuple[str, str, str]:
    domain = settings.auth0_domain
    audience = settings.auth0_audience
    issuer = settings.resolved_auth0_issuer
    jwks_url = settings.resolved_auth0_jwks_url
    if not (domain and audience and issuer and jwks_url):
        # Hard fail with a helpful message — operators flipping
        # SHIP_AUTH_MODE=auth0 without filling AUTH0_* deserve a clear
        # error instead of opaque 401s on every request.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "SHIP_AUTH_MODE=auth0 but AUTH0_DOMAIN/AUTH0_AUDIENCE are "
                "not configured. Run scripts/bootstrap.sh --auth0 ..."
            ),
        )
    return audience, issuer, jwks_url


def validate_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify an Auth0 access token and return its claims.

    Raises :class:`HTTPException(500)` if the tenant config is incomplete and
    :class:`HTTPException(401)` on any token-verification failure.
    """
    # Config error must surface as 500 — independent of token shape — so
    # operators see "fix your env" instead of getting bombarded with 401s.
    audience, issuer, jwks_url = _ensure_settings(settings)
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="malformed access token",
        ) from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="access token missing key id",
        )

    key = _cache.get(kid, url=jwks_url)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="signing key not found in JWKS",
        )

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[unverified_header.get("alg") or "RS256"],
            audience=audience,
            issuer=issuer,
            options={"leeway": _JWT_LEEWAY_SECONDS},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid access token: {exc!s}",
        ) from exc

    if not claims.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="access token has no subject",
        )
    return claims


async def user_from_claims(
    claims: Mapping[str, Any], session: AsyncSession
) -> User:
    """Resolve (or create) a :class:`User` for a verified Auth0 access token.

    Lookup precedence:

    1. ``users.external_subject == sub`` — the steady-state hot path once a
       user has logged in at least once.
    2. ``users.email == email`` — picks up rows the operator pre-created
       via ``/v1/workspaces/{id}/members`` before the user ever logged in.
       We bind ``external_subject`` on first sight so subsequent requests
       hit the index path.
    3. Insert a fresh row.

    Email is mandatory; Auth0 rules can be configured to require it (the
    "Force Users to Verify Email" flag in the Dashboard).
    """
    sub = str(claims["sub"]).strip()
    email_raw = claims.get("email") or claims.get("https://ship.local/email")
    email = str(email_raw).strip().lower() if email_raw else None
    name = (
        claims.get("name")
        or claims.get("nickname")
        or claims.get("https://ship.local/name")
    )

    user = (
        await session.execute(select(User).where(User.external_subject == sub))
    ).scalar_one_or_none()
    if user is not None:
        if email and user.email != email:
            # Email changed in IdP — keep our row's identity stable but
            # update the displayable address. Audit log catches the change.
            user.email = email
        if name and user.display_name != name:
            user.display_name = str(name)[:200]
        return user

    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "access token has no email claim — enable the 'email' scope "
                "on the Auth0 application or add a custom rule that injects "
                "the user's email address."
            ),
        )

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is not None:
        # Pre-invited row — bind the IdP subject so future requests skip
        # the email lookup.
        user.external_subject = sub
        if name and not user.display_name:
            user.display_name = str(name)[:200]
        return user

    user = User(
        email=email,
        display_name=str(name)[:200] if name else None,
        external_subject=sub,
    )
    session.add(user)
    await session.flush()
    return user


__all__ = [
    "reset_jwks_cache",
    "user_from_claims",
    "validate_access_token",
]
