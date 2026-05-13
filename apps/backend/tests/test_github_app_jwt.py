"""GitHub App JWT minting (``app_auth.mint_app_jwt``).

We don't ping api.github.com here — the only thing we control is the JWT
shape. The token-fetch path is integration-tested via the route tests
with httpx mocked.
"""

from __future__ import annotations

import base64
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from backend.app.core.config import Settings
from backend.app.integrations.github.app_auth import (
    GitHubAppMisconfigured,
    mint_app_jwt,
)


def _generate_pem() -> str:
    """Generate an ephemeral 2048-bit RSA PEM for tests.

    Generating a key per test run keeps the suite hermetic — no checked-in
    secret material, no risk of someone reusing it in dev. ~80 ms one-off
    cost is fine for a tiny module.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _settings_with_env(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> Settings:
    """Build a fresh Settings after setting the requested env vars.

    Pydantic-settings honours ``alias`` strictly (``populate_by_name`` is
    the default ``False`` for the project's Settings), so the only
    reliable way to inject test values is via the environment.
    """
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    return Settings()


def test_mint_app_jwt_includes_required_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pem = _generate_pem()
    settings = _settings_with_env(
        monkeypatch,
        GITHUB_APP_ID="12345",
        GITHUB_APP_PRIVATE_KEY=pem,
    )
    now = 1_700_000_000.0  # fixed clock for reproducibility
    token = mint_app_jwt(settings, now=now)

    # Decode without verifying signature: we trust python-jose's signing
    # path and only want to check claim shape.
    claims = jwt.get_unverified_claims(token)
    assert claims["iss"] == "12345"
    # ``iat`` is backdated 60s for clock-skew tolerance.
    assert claims["iat"] == int(now) - 60
    # Expiry within the documented 10-minute ceiling.
    assert claims["exp"] - claims["iat"] <= 10 * 60


def test_private_key_accepts_escaped_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-line env stores (Bunny et al.) deliver PEM with literal ``\\n``."""
    pem = _generate_pem()
    one_line = pem.replace("\n", "\\n")
    settings = _settings_with_env(
        monkeypatch,
        GITHUB_APP_ID="12345",
        GITHUB_APP_PRIVATE_KEY=one_line,
    )
    # mint_app_jwt would raise on a malformed PEM, so a clean call here
    # is itself the assertion that normalization restored the newlines.
    token = mint_app_jwt(settings, now=1_700_000_000.0)
    claims = jwt.get_unverified_claims(token)
    assert claims["iss"] == "12345"


def test_private_key_accepts_base64_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can paste base64(PEM) when the env field rejects newlines."""
    pem = _generate_pem()
    blob = base64.b64encode(pem.encode("utf-8")).decode("ascii")
    settings = _settings_with_env(
        monkeypatch,
        GITHUB_APP_ID="12345",
        GITHUB_APP_PRIVATE_KEY=blob,
    )
    token = mint_app_jwt(settings, now=1_700_000_000.0)
    claims = jwt.get_unverified_claims(token)
    assert claims["iss"] == "12345"


def test_mint_app_jwt_raises_when_private_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_with_env(
        monkeypatch,
        GITHUB_APP_ID="12345",
        GITHUB_APP_PRIVATE_KEY=None,
    )
    with pytest.raises(GitHubAppMisconfigured):
        mint_app_jwt(settings, now=time.time())
