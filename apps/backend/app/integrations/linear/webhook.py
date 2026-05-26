"""Linear webhook signature verification.

Linear signs each webhook delivery with HMAC-SHA256 over the raw
request body using the secret configured at ``webhookCreate`` time.
The signature lives in the ``Linear-Signature`` header (hex digest,
no scheme prefix — unlike GitHub's ``sha256=...``).

Verify against **raw bytes** before parsing JSON: re-serialising the
parsed payload would break any delivery that wasn't already canonical
(Linear's serialiser is not stable across event types).

Use :func:`verify_signature` against ``request.body()`` before doing
anything with the payload. The function uses constant-time comparison
to sidestep timing attacks.
"""

from __future__ import annotations

import hashlib
import hmac

from backend.app.core.config import Settings


_SIG_HEADER = "Linear-Signature"


class InvalidWebhookSignature(ValueError):
    """Raised when the delivered signature header is missing or wrong."""


def verify_signature(
    raw_body: bytes, signature_header: str | None, *, settings: Settings
) -> None:
    """Verify the ``Linear-Signature`` header against ``raw_body``.

    Raises :class:`InvalidWebhookSignature` on any mismatch. Returns
    ``None`` on success — callers don't need a truthy return because
    the only valid path is "no exception".
    """
    if not settings.linear_webhook_secret:
        # Fail closed: a misconfigured backend should never accept
        # un-authenticated webhook traffic. Operators who really want
        # to disable verification can stub this in tests.
        raise InvalidWebhookSignature(
            "LINEAR_WEBHOOK_SECRET is not configured; "
            "webhook delivery rejected"
        )
    if not signature_header:
        raise InvalidWebhookSignature(
            f"missing {_SIG_HEADER} header"
        )
    expected = hmac.new(
        settings.linear_webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    # Linear delivers the hex digest unprefixed. Some integrations
    # have been seen to send ``sha256=…`` — accept either to keep
    # our handler robust against minor Linear API shape drift.
    delivered = signature_header.strip()
    if delivered.startswith("sha256="):
        delivered = delivered.removeprefix("sha256=")
    if not hmac.compare_digest(expected, delivered):
        raise InvalidWebhookSignature("signature mismatch")


__all__ = ["InvalidWebhookSignature", "verify_signature"]
