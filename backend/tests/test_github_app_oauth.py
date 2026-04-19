"""Unit tests for the GitHub App install OAuth helpers.

These cover the *crypto-shaped* surface we control end-to-end (state JWT
mint/verify, install URL building). Routes-level tests live in
``test_v1_github_app.py``.
"""

from __future__ import annotations

import time
import uuid

import pytest
from jose import jwt

from backend.app.core.config import Settings
from backend.app.integrations.github.oauth import (
    InvalidInstallState,
    build_install_state,
    build_install_url,
    verify_install_state,
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Hermetic Settings: no .env / dev defaults leak into the assertions."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-use-anywhere-real")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    return Settings()


def test_install_state_roundtrips(settings: Settings) -> None:
    workspace_id = uuid.uuid4()
    token = build_install_state(workspace_id, settings=settings)
    decoded = verify_install_state(token, settings=settings)
    assert decoded.workspace_id == workspace_id
    # The nonce is regenerated on every mint so the only assertion that
    # makes sense is "non-empty".
    assert decoded.nonce


def test_install_state_rejects_tampered_payload(settings: Settings) -> None:
    token = build_install_state(uuid.uuid4(), settings=settings)
    # Forge an unrelated JWT signed with the same secret but a wrong
    # subject — proves the subject pin is enforced.
    forged = jwt.encode(
        {
            "sub": "not.our.subject",
            "wid": str(uuid.uuid4()),
            "nonce": "abc",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidInstallState):
        verify_install_state(forged, settings=settings)
    # Sanity: the genuine token still verifies.
    verify_install_state(token, settings=settings)


def test_install_state_rejects_expired(settings: Settings) -> None:
    expired = jwt.encode(
        {
            "sub": "ship.gh.app.install.state",
            "wid": str(uuid.uuid4()),
            "nonce": "x",
            "iat": int(time.time()) - 3600,
            "exp": int(time.time()) - 60,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidInstallState):
        verify_install_state(expired, settings=settings)


@pytest.mark.asyncio
async def test_install_url_uses_configured_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinned slug short-circuits the live GitHub /app lookup."""
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-staging")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    settings = Settings()
    state = build_install_state(uuid.uuid4(), settings=settings)
    url = await build_install_url(state, settings=settings)
    assert url.startswith(
        "https://github.com/apps/ship-staging/installations/new?"
    )
    assert f"state={state}" in url


@pytest.mark.asyncio
async def test_install_url_falls_back_to_live_slug_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When GITHUB_APP_SLUG is unset we discover the slug via /app and cache.

    Operators only have to provide GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY
    for the WOW onboarding to work — the slug is read from GitHub itself.
    """
    monkeypatch.delenv("GITHUB_APP_SLUG", raising=False)
    from backend.app.core.config import get_settings
    from backend.app.integrations.github import app_auth

    get_settings.cache_clear()
    settings = Settings()
    app_auth.invalidate_app_metadata_cache()

    async def fake_fetch_app_slug(*, settings, client=None):  # noqa: ARG001
        return "ship-elmundi"

    monkeypatch.setattr(
        "backend.app.integrations.github.oauth.fetch_app_slug",
        fake_fetch_app_slug,
    )
    state = build_install_state(uuid.uuid4(), settings=settings)
    url = await build_install_url(state, settings=settings)
    assert url.startswith(
        "https://github.com/apps/ship-elmundi/installations/new?"
    )
