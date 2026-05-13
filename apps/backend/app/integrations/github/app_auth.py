"""GitHub App authentication primitives (JWT + per-install access tokens).

GitHub App auth is a two-step dance:

1. Mint an **App JWT** signed RS256 with the App's private key. The JWT
   identifies *the App itself* and is short-lived (max 10 min).
2. POST that JWT to ``/app/installations/{id}/access_tokens`` to receive a
   short-lived (~1 h) **installation token** that scopes API calls to the
   repos a single tenant has installed.

We cache the installation token in-process for a few minutes shy of the
expiry GitHub returned, so a chatty endpoint doesn't burn a network round
trip per call. The cache lives at module level — fine for our single-pod
backend; a multi-pod deployment will eat at most one extra mint per pod
on cold start.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import httpx
from jose import jwt

from backend.app.core.config import Settings


GITHUB_API_BASE: Final[str] = "https://api.github.com"
# How long before the GitHub-reported expiry we treat a cached token as
# stale. 60s gives plenty of margin for clock skew + in-flight requests.
_CACHE_SAFETY_MARGIN_SECONDS: Final[int] = 60
# App JWTs are valid for max 10 min; we use 9 min and let the JWT cache
# itself expire naturally on the next mint.
_APP_JWT_TTL_SECONDS: Final[int] = 9 * 60


class GitHubAppMisconfigured(RuntimeError):
    """Raised when GITHUB_APP_* settings are absent at call time.

    Distinguished from a generic ``RuntimeError`` so the API layer can map
    it to a 503 with a helpful "configure the App in env vars" message
    instead of a 500.
    """


@dataclass(slots=True)
class _CachedInstallationToken:
    token: str
    # Unix epoch seconds. We compare against ``time.time()`` instead of
    # storing a ``datetime`` to avoid tz-naive/aware comparisons.
    expires_at: float


# Keyed by installation_id. Module-global is intentional — see header
# docstring for the multi-pod note.
_token_cache: dict[int, _CachedInstallationToken] = {}


def _require_app_credentials(settings: Settings) -> tuple[str, str]:
    """Pull App ID + private key or raise :class:`GitHubAppMisconfigured`."""
    if not settings.github_app_id or not settings.github_app_private_key:
        raise GitHubAppMisconfigured(
            "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY must be set to use the "
            "GitHub App. Configure them in the backend environment and "
            "redeploy."
        )
    return settings.github_app_id, settings.github_app_private_key


def mint_app_jwt(settings: Settings, *, now: float | None = None) -> str:
    """Build a fresh App-level JWT (RS256) good for ~9 minutes.

    Per GitHub's docs the ``iat`` should be backdated 60s to tolerate
    clock skew between us and api.github.com.
    """
    app_id, private_key = _require_app_credentials(settings)
    issued_at = int(now if now is not None else time.time()) - 60
    claims = {
        "iat": issued_at,
        "exp": issued_at + _APP_JWT_TTL_SECONDS,
        "iss": app_id,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


async def fetch_installation_token(
    installation_id: int,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return a valid installation access token, minting if cache is cold.

    The caller normally passes its own :class:`httpx.AsyncClient` so the
    connection pool is shared across the request lifecycle; we open a
    short-lived client only when called outside that context (tests,
    background scripts).
    """
    cached = _token_cache.get(installation_id)
    if cached and cached.expires_at - _CACHE_SAFETY_MARGIN_SECONDS > time.time():
        return cached.token

    app_jwt = mint_app_jwt(settings)
    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await http.post(url, headers=headers)
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code >= 400:
        # Surface the GitHub error body in logs but keep the public-facing
        # message generic — installation_id alone isn't a secret, but the
        # response can sometimes echo App-level metadata.
        raise httpx.HTTPStatusError(
            f"GitHub installation token mint failed: {response.status_code}",
            request=response.request,
            response=response,
        )
    payload = response.json()
    token = payload["token"]
    # ``expires_at`` is ISO 8601 in UTC. We store an epoch float and skip
    # the safety-margin subtraction here — the read-side does it instead so
    # tweaking the margin doesn't require flushing the cache.
    expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    _token_cache[installation_id] = _CachedInstallationToken(
        token=token,
        expires_at=expires_at.replace(tzinfo=timezone.utc).timestamp(),
    )
    return token


@dataclass(slots=True)
class _CachedAppMetadata:
    slug: str
    fetched_at: float


_app_metadata_cache: _CachedAppMetadata | None = None
_APP_METADATA_TTL_SECONDS: Final[int] = 60 * 60  # 1h is plenty — slug never moves.


async def fetch_app_slug(
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return the App's URL slug (e.g. ``ship-elmundi``).

    GitHub assigns the slug from the App display name when it's first
    created and never changes it, so this is the canonical source for the
    install-URL host segment. Caching for an hour keeps onboarding hot —
    a single network call per pod per hour.

    Raises :class:`GitHubAppMisconfigured` when the App credentials are
    missing; surfaces transport errors via :class:`httpx.HTTPStatusError`.
    """
    global _app_metadata_cache

    cached = _app_metadata_cache
    if cached and (time.time() - cached.fetched_at) < _APP_METADATA_TTL_SECONDS:
        return cached.slug

    app_jwt = mint_app_jwt(settings)
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await http.get(f"{GITHUB_API_BASE}/app", headers=headers)
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"GitHub /app metadata fetch failed: {response.status_code}",
            request=response.request,
            response=response,
        )
    payload = response.json()
    slug = str(payload.get("slug") or "").strip()
    if not slug:
        raise GitHubAppMisconfigured(
            "GitHub /app response did not contain a slug. The App likely "
            "needs to be reinstalled or the credentials rotated."
        )
    _app_metadata_cache = _CachedAppMetadata(slug=slug, fetched_at=time.time())
    return slug


def invalidate_app_metadata_cache() -> None:
    """Test helper / ops escape hatch — drop the cached App slug."""
    global _app_metadata_cache
    _app_metadata_cache = None


def invalidate_installation_token_cache(installation_id: int | None = None) -> None:
    """Drop the cached installation token(s).

    Called from the webhook handler when GitHub tells us an install was
    suspended/uninstalled, so the next API call rediscovers the new state
    instead of using a now-revoked token.
    """
    if installation_id is None:
        _token_cache.clear()
    else:
        _token_cache.pop(installation_id, None)


__all__ = [
    "GITHUB_API_BASE",
    "GitHubAppMisconfigured",
    "fetch_app_slug",
    "fetch_installation_token",
    "invalidate_app_metadata_cache",
    "invalidate_installation_token_cache",
    "mint_app_jwt",
]
