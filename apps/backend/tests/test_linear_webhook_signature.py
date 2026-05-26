"""Linear webhook signature verification.

Pin the contract that Linear-Signature is checked against raw bytes
and that misconfiguration / missing header / mismatch all fail closed.
The webhook handler itself goes through a route that touches the DB —
that lives behind an integration test. Here we exercise the pure
``verify_signature`` surface.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from backend.app.integrations.linear.webhook import (
    InvalidWebhookSignature,
    verify_signature,
)


class _Settings:
    """Minimal Settings stand-in — verify_signature only reads the one
    attribute, so we don't need to construct the full pydantic model."""

    def __init__(self, secret: str | None) -> None:
        self.linear_webhook_secret = secret


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_accepts_valid_hex_signature() -> None:
    body = b'{"action":"update","type":"Issue"}'
    sig = _sign(body, "topsecret")
    verify_signature(body, sig, settings=_Settings("topsecret"))


def test_accepts_sha256_prefixed_signature() -> None:
    # Some Linear integrations have been seen to ship ``sha256=<hex>``
    # (mirroring GitHub's header shape). Our handler tolerates either
    # encoding so a Linear API shape drift can't dark-launch us.
    body = b'{"action":"update"}'
    sig = "sha256=" + _sign(body, "topsecret")
    verify_signature(body, sig, settings=_Settings("topsecret"))


def test_rejects_when_secret_unset() -> None:
    # Fail-closed: a backend with no LINEAR_WEBHOOK_SECRET configured
    # must reject EVERY delivery, even one signed with the empty
    # string. Otherwise a misconfigured deploy quietly trusts random
    # POSTs.
    with pytest.raises(InvalidWebhookSignature):
        verify_signature(b"{}", "deadbeef", settings=_Settings(None))


def test_rejects_missing_header() -> None:
    with pytest.raises(InvalidWebhookSignature):
        verify_signature(b"{}", None, settings=_Settings("topsecret"))


def test_rejects_wrong_signature() -> None:
    body = b'{"action":"update"}'
    wrong = _sign(body, "different-secret")
    with pytest.raises(InvalidWebhookSignature):
        verify_signature(body, wrong, settings=_Settings("topsecret"))


def test_rejects_tampered_body() -> None:
    # If the operator's reverse-proxy strips/adds a trailing newline,
    # the signature breaks — verify the constant-time check picks
    # that up rather than silently re-canonicalising the body.
    body = b'{"a":1}'
    sig = _sign(body, "topsecret")
    with pytest.raises(InvalidWebhookSignature):
        verify_signature(body + b"\n", sig, settings=_Settings("topsecret"))
