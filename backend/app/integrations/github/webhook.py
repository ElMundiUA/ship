"""GitHub webhook signature verification.

GitHub signs every webhook delivery with HMAC-SHA256 over the raw request
body using the secret configured on the App. We **must** verify against
the *raw* bytes — re-serialising the parsed JSON would break any payload
that wasn't already canonical (GitHub's serialiser is not stable).

Use :func:`verify_signature` against ``request.body()`` before doing
anything with the payload. The function uses constant-time comparison to
sidestep timing attacks.
"""

from __future__ import annotations

import hashlib
import hmac

from backend.app.core.config import Settings


_SIG_HEADER = "X-Hub-Signature-256"


class InvalidWebhookSignature(ValueError):
    """Raised when the delivered signature header is missing or wrong."""


def verify_signature(
    raw_body: bytes, signature_header: str | None, *, settings: Settings
) -> None:
    """Verify the ``X-Hub-Signature-256`` header against ``raw_body``.

    Raises :class:`InvalidWebhookSignature` on any mismatch. Returns
    ``None`` on success — callers don't need a truthy return because the
    only valid path is "no exception".
    """
    if not settings.github_app_webhook_secret:
        # Fail closed: a misconfigured backend should never accept
        # un-authenticated webhook traffic, even in dev. Operators who
        # really want to disable verification can stub this in tests.
        raise InvalidWebhookSignature(
            "GITHUB_APP_WEBHOOK_SECRET is not configured; webhook delivery rejected"
        )
    if not signature_header or not signature_header.startswith("sha256="):
        raise InvalidWebhookSignature(
            f"missing or malformed {_SIG_HEADER} header"
        )
    expected = hmac.new(
        settings.github_app_webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    delivered = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, delivered):
        raise InvalidWebhookSignature("signature mismatch")


__all__ = ["InvalidWebhookSignature", "verify_signature"]
