"""GitHub App install flow: state-token + redirect URL helpers.

The "Install on GitHub" button in the console hits ``/v1/integrations/
github/install/start`` which:

1. Mints a short-lived signed *state* token containing the workspace_id
   the install should attach to + a CSRF nonce. We sign with our existing
   ``JWT_SECRET`` so we don't need a new key to manage.
2. Redirects the browser to GitHub's install URL with that state in the
   query string.

GitHub then bounces the user back with ``?installation_id=&setup_action=
install&state=<our-token>``; we verify the token and persist the
``installation_id`` against the workspace.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from jose import JWTError, jwt

from backend.app.core.config import Settings
from backend.app.integrations.github.app_auth import (
    GitHubAppMisconfigured,
    fetch_app_slug,
)


# State tokens are short-lived (the user has to be redirected back within
# this window). Five minutes is plenty for the OAuth dance and gives no
# replay-attack window worth speaking of.
_STATE_TTL_SECONDS: Final[int] = 5 * 60
# Subject string baked into the state JWT so a stray Auth0 / PAT JWT can't
# accidentally satisfy the verifier — defence in depth.
_STATE_SUBJECT: Final[str] = "ship.gh.app.install.state"


class InvalidInstallState(ValueError):
    """Raised when the round-tripped state token is missing/expired/bad."""


@dataclass(frozen=True, slots=True)
class InstallState:
    workspace_id: uuid.UUID
    nonce: str


def _state_secret(settings: Settings) -> str:
    # Reuse JWT_SECRET because the install flow only ever sees this token
    # round-trip through the same backend; cross-service trust is not in
    # play here. If we later want a separate rotation cadence, swap to a
    # dedicated env var without touching call sites.
    return settings.jwt_secret


def build_install_state(workspace_id: uuid.UUID, *, settings: Settings) -> str:
    """Sign a JWT carrying ``workspace_id`` + a CSRF nonce."""
    issued_at = int(time.time())
    claims = {
        "sub": _STATE_SUBJECT,
        "wid": str(workspace_id),
        "nonce": secrets.token_urlsafe(16),
        "iat": issued_at,
        "exp": issued_at + _STATE_TTL_SECONDS,
    }
    return jwt.encode(claims, _state_secret(settings), algorithm="HS256")


def verify_install_state(state: str, *, settings: Settings) -> InstallState:
    """Decode + validate the state token returned by GitHub."""
    try:
        claims = jwt.decode(
            state,
            _state_secret(settings),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise InvalidInstallState("install state token is invalid or expired") from exc
    if claims.get("sub") != _STATE_SUBJECT:
        # Reject any well-formed JWT that wasn't minted for this purpose.
        raise InvalidInstallState("install state token has wrong subject")
    raw_wid = claims.get("wid")
    raw_nonce = claims.get("nonce")
    if not raw_wid or not raw_nonce:
        raise InvalidInstallState("install state token is missing fields")
    try:
        workspace_id = uuid.UUID(str(raw_wid))
    except ValueError as exc:
        raise InvalidInstallState("install state token has malformed wid") from exc
    return InstallState(workspace_id=workspace_id, nonce=str(raw_nonce))


async def build_install_url(state: str, *, settings: Settings) -> str:
    """Return the GitHub URL that opens the App install picker.

    The slug is discovered live from GitHub's ``GET /app`` (cached for an
    hour) so operators only need ``GITHUB_APP_ID`` + ``GITHUB_APP_PRIVATE_KEY``
    to wire up the WOW onboarding — ``GITHUB_APP_SLUG`` becomes an optional
    override for unusual setups (e.g. a reverse proxy in front of github.com,
    or a one-off integration test where the App credentials are absent).
    """
    slug = (settings.github_app_slug or "").strip()
    if not slug:
        try:
            slug = await fetch_app_slug(settings=settings)
        except GitHubAppMisconfigured:
            # Operator hasn't even set the App credentials. We still need
            # to return *something* — the calling endpoint will surface a
            # 503 once it sees we have no slug + no creds. Falling back to
            # the literal "" yields an obviously-broken URL we'd rather
            # not emit, so we raise instead.
            raise
    base = f"https://github.com/apps/{slug}/installations/new"
    return f"{base}?{urlencode({'state': state})}"


__all__ = [
    "InstallState",
    "InvalidInstallState",
    "build_install_state",
    "build_install_url",
    "verify_install_state",
]
