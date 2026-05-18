"""Unit tests for ``backend.app.integrations.telegram.bind_state``."""

from __future__ import annotations

import time

import pytest
from jose import jwt

from backend.app.core.config import Settings
from backend.app.integrations.telegram.bind_state import (
    InvalidBindNonce,
    build_bind_nonce,
    verify_bind_nonce,
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-use-anywhere-real")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield Settings()
    get_settings.cache_clear()


def test_bind_nonce_roundtrip(settings: Settings) -> None:
    token = build_bind_nonce(
        chat_id=-1001234567890, chat_title="Ship crew", settings=settings
    )
    decoded = verify_bind_nonce(token, settings=settings)
    assert decoded.chat_id == -1001234567890
    assert decoded.chat_title == "Ship crew"
    assert decoded.nonce
    assert decoded.expires_at > decoded.issued_at


def test_bind_nonce_rejects_wrong_subject(settings: Settings) -> None:
    forged = jwt.encode(
        {
            "sub": "ship.notion.oauth",
            "cid": 1,
            "nonce": "n",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidBindNonce):
        verify_bind_nonce(forged, settings=settings)


def test_bind_nonce_rejects_missing_cid(settings: Settings) -> None:
    token = jwt.encode(
        {
            "sub": "ship.telegram.bind",
            "nonce": "n",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidBindNonce):
        verify_bind_nonce(token, settings=settings)


def test_bind_nonce_rejects_missing_nonce(settings: Settings) -> None:
    token = jwt.encode(
        {
            "sub": "ship.telegram.bind",
            "cid": 42,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidBindNonce):
        verify_bind_nonce(token, settings=settings)


def test_bind_nonce_rejects_malformed_cid(settings: Settings) -> None:
    token = jwt.encode(
        {
            "sub": "ship.telegram.bind",
            "cid": "not-an-int",
            "nonce": "n",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidBindNonce):
        verify_bind_nonce(token, settings=settings)


def test_bind_nonce_rejects_expired(settings: Settings) -> None:
    expired = jwt.encode(
        {
            "sub": "ship.telegram.bind",
            "cid": 99,
            "nonce": "n",
            "iat": int(time.time()) - 3600,
            "exp": int(time.time()) - 60,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidBindNonce):
        verify_bind_nonce(expired, settings=settings)


def test_bind_nonce_rejects_tampered_signature(settings: Settings) -> None:
    token = build_bind_nonce(chat_id=1, chat_title=None, settings=settings)
    parts = token.split(".")
    assert len(parts) == 3
    tampered = f"{parts[0]}.{parts[1]}.{'A' * len(parts[2])}"
    with pytest.raises(InvalidBindNonce):
        verify_bind_nonce(tampered, settings=settings)
