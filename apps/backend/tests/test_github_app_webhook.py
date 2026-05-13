"""HMAC verification for GitHub webhook deliveries.

Defends the property that *only* a delivery signed with our configured
secret is accepted. We don't test the full FastAPI route here — the
``test_v1_github_app.py`` suite covers integration. This file focuses on
the small pure helper, which is the hot path for every delivery.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from backend.app.core.config import Settings
from backend.app.integrations.github.webhook import (
    InvalidWebhookSignature,
    verify_signature,
)


def _settings(
    monkeypatch: pytest.MonkeyPatch, secret: str | None = "wh_test_secret"
) -> Settings:
    if secret is None:
        monkeypatch.delenv("GITHUB_APP_WEBHOOK_SECRET", raising=False)
    else:
        monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", secret)
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    return Settings()


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def test_verify_signature_accepts_correct_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b'{"action":"created"}'
    settings = _settings(monkeypatch)
    verify_signature(body, _sign(body, "wh_test_secret"), settings=settings)


def test_verify_signature_rejects_wrong_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b'{"action":"created"}'
    settings = _settings(monkeypatch)
    with pytest.raises(InvalidWebhookSignature):
        verify_signature(body, _sign(body, "wrong-secret"), settings=settings)


def test_verify_signature_rejects_missing_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    with pytest.raises(InvalidWebhookSignature):
        verify_signature(b"{}", None, settings=settings)


def test_verify_signature_rejects_unsigned_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misconfigured backend MUST refuse all webhook traffic, not accept it.

    This is the most important assertion in the file: a stray deploy with
    an empty ``GITHUB_APP_WEBHOOK_SECRET`` would otherwise let any
    attacker push synthetic events.
    """
    settings = _settings(monkeypatch, secret=None)
    with pytest.raises(InvalidWebhookSignature):
        verify_signature(b"{}", _sign(b"{}", "anything"), settings=settings)
