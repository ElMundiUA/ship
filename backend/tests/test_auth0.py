"""Unit tests for ``backend.app.security.auth0``.

Hermetic — generates an RS256 keypair in memory, signs tokens, and patches
``httpx.get`` to return a JWKS document derived from the public key.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwt

from backend.app.core.config import Settings
from backend.app.security import auth0


# ---------------------------------------------------------------------------
# Keypair + JWKS helpers
# ---------------------------------------------------------------------------


def _b64u(value: int | bytes) -> str:
    if isinstance(value, int):
        length = (value.bit_length() + 7) // 8
        value = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


@pytest.fixture()
def rsa_keypair() -> tuple[str, dict[str, Any]]:
    """Return ``(private_pem, jwks)`` for use in token signing + verification."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_numbers = key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "test-kid-1",
                "n": _b64u(public_numbers.n),
                "e": _b64u(public_numbers.e),
            }
        ]
    }
    return pem, jwks


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        SHIP_AUTH_MODE="auth0",
        AUTH0_DOMAIN="ship-test.eu.auth0.com",
        AUTH0_AUDIENCE="https://api.ship.test",
        AUTH0_JWKS_URL="https://jwks.test/jwks.json",
    )


def _sign(
    private_pem: str,
    *,
    sub: str = "auth0|abc123",
    email: str | None = "ada@example.com",
    name: str | None = "Ada Lovelace",
    audience: str = "https://api.ship.test",
    issuer: str = "https://ship-test.eu.auth0.com/",
    extra_claims: dict[str, Any] | None = None,
    kid: str = "test-kid-1",
) -> str:
    import time

    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "sub": sub,
        "aud": audience,
        "iat": now,
        "exp": now + 600,
        "scope": "openid profile email",
    }
    if email:
        claims["email"] = email
    if name:
        claims["name"] = name
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# httpx stubbing
# ---------------------------------------------------------------------------


class _StubJwksResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "jwks fetch failed", request=None, response=None  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture(autouse=True)
def _reset_jwks_cache() -> None:
    auth0.reset_jwks_cache()
    yield
    auth0.reset_jwks_cache()


def _patch_jwks(monkeypatch: pytest.MonkeyPatch, jwks: dict[str, Any]) -> list[str]:
    calls: list[str] = []

    def _fake_get(url: str, **_: Any) -> _StubJwksResponse:
        calls.append(url)
        return _StubJwksResponse(jwks)

    monkeypatch.setattr(httpx, "get", _fake_get)
    return calls


# ---------------------------------------------------------------------------
# validate_access_token
# ---------------------------------------------------------------------------


def test_validates_a_correctly_signed_token(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[str, dict[str, Any]],
    settings: Settings,
) -> None:
    private_pem, jwks = rsa_keypair
    _patch_jwks(monkeypatch, jwks)
    token = _sign(private_pem)

    claims = auth0.validate_access_token(token, settings)

    assert claims["sub"] == "auth0|abc123"
    assert claims["email"] == "ada@example.com"


def test_caches_jwks_across_calls(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[str, dict[str, Any]],
    settings: Settings,
) -> None:
    private_pem, jwks = rsa_keypair
    calls = _patch_jwks(monkeypatch, jwks)
    token = _sign(private_pem)

    auth0.validate_access_token(token, settings)
    auth0.validate_access_token(token, settings)

    assert len(calls) == 1


def test_refetches_on_unknown_kid(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[str, dict[str, Any]],
    settings: Settings,
) -> None:
    private_pem, jwks = rsa_keypair
    calls = _patch_jwks(monkeypatch, jwks)
    bad_token = _sign(private_pem, kid="rotated-kid")

    with pytest.raises(HTTPException) as exc:
        auth0.validate_access_token(bad_token, settings)
    assert exc.value.status_code == 401
    assert "JWKS" in exc.value.detail
    assert len(calls) == 1


def test_rejects_token_with_wrong_audience(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[str, dict[str, Any]],
    settings: Settings,
) -> None:
    private_pem, jwks = rsa_keypair
    _patch_jwks(monkeypatch, jwks)
    token = _sign(private_pem, audience="https://api.someone-else.test")

    with pytest.raises(HTTPException) as exc:
        auth0.validate_access_token(token, settings)
    assert exc.value.status_code == 401


def test_rejects_token_with_wrong_issuer(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[str, dict[str, Any]],
    settings: Settings,
) -> None:
    private_pem, jwks = rsa_keypair
    _patch_jwks(monkeypatch, jwks)
    token = _sign(private_pem, issuer="https://evil.example/")

    with pytest.raises(HTTPException) as exc:
        auth0.validate_access_token(token, settings)
    assert exc.value.status_code == 401


def test_rejects_token_signed_with_different_key(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[str, dict[str, Any]],
    settings: Settings,
) -> None:
    _, jwks = rsa_keypair
    _patch_jwks(monkeypatch, jwks)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    token = _sign(other_pem)  # signed by 'other' but JWKS only knows the original

    with pytest.raises(HTTPException) as exc:
        auth0.validate_access_token(token, settings)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# JIT user mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jit_creates_user_on_first_login(db_session) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import User

    user = await auth0.user_from_claims(
        {"sub": "auth0|new-user", "email": "Newcomer@Example.com", "name": "Newcomer"},
        db_session,
    )

    assert user.email == "newcomer@example.com"
    assert user.external_subject == "auth0|new-user"
    assert user.display_name == "Newcomer"
    # And it actually persists.
    found = (
        await db_session.execute(
            select(User).where(User.external_subject == "auth0|new-user")
        )
    ).scalar_one()
    assert found.id == user.id


@pytest.mark.asyncio
async def test_jit_returns_existing_user_by_subject(db_session) -> None:
    from backend.app.db.models.tenancy import User

    seed = User(
        email="ada@example.com",
        display_name="Ada",
        external_subject="auth0|ada",
    )
    db_session.add(seed)
    await db_session.flush()

    user = await auth0.user_from_claims(
        {"sub": "auth0|ada", "email": "ada@example.com", "name": "Ada Lovelace"},
        db_session,
    )

    assert user.id == seed.id
    # display_name updates from claim drift.
    assert user.display_name == "Ada Lovelace"


@pytest.mark.asyncio
async def test_jit_attaches_subject_to_pre_invited_row(db_session) -> None:
    """Operator pre-creates a User by email; first Auth0 login binds the sub."""
    from backend.app.db.models.tenancy import User

    invited = User(email="invitee@example.com", display_name=None)
    db_session.add(invited)
    await db_session.flush()
    assert invited.external_subject is None

    user = await auth0.user_from_claims(
        {"sub": "auth0|invitee", "email": "invitee@example.com", "name": "I."},
        db_session,
    )

    assert user.id == invited.id
    assert user.external_subject == "auth0|invitee"
    assert user.display_name == "I."


@pytest.mark.asyncio
async def test_jit_requires_email_for_new_user(db_session) -> None:
    with pytest.raises(HTTPException) as exc:
        await auth0.user_from_claims({"sub": "auth0|stranger"}, db_session)
    assert exc.value.status_code == 401
    assert "email" in exc.value.detail.lower()


def test_rejects_when_settings_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings normally reads .env; for this test we want a guaranteed-empty
    # snapshot so we exercise the "operator forgot to set AUTH0_*" branch.
    for var in (
        "AUTH0_DOMAIN",
        "AUTH0_AUDIENCE",
        "AUTH0_ISSUER",
        "AUTH0_JWKS_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    bad_settings = Settings.model_construct(
        auth_mode="auth0",
        auth0_domain=None,
        auth0_audience=None,
        auth0_issuer=None,
        auth0_jwks_url=None,
    )
    with pytest.raises(HTTPException) as exc:
        auth0.validate_access_token("anything", bad_settings)
    assert exc.value.status_code == 500
    assert "AUTH0" in exc.value.detail
