"""Symmetric encryption for at-rest workspace secrets (RFC-0006).

Used by the integrations table's ``secret_ciphertext`` column. The threat model
is "stolen Postgres dump or backup": with a separately-managed
:envvar:`ENCRYPTION_KEY`, the secrets in the dump are useless without it.

Key handling
============

- **Production**: set ``ENCRYPTION_KEY`` to a 32-byte url-safe base64 string
  (``cryptography.fernet.Fernet.generate_key()``). Rotate by running the
  re-encryption job (TODO) and then swapping the env var.
- **Local dev / single-node**: if unset, we deterministically derive a key
  from :envvar:`JWT_SECRET` and log a warning. That lets ``docker compose up``
  Just Work for laptops while making the warning impossible to miss in any
  environment that hasn't been hardened.

Wire format
===========

Each ciphertext blob is a Fernet token, which already includes the IV,
ciphertext, MAC, and a ``key_id`` byte for forward-compatible rotation. We
store the raw token bytes in :class:`Integration.secret_ciphertext` — no
extra envelope.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import Settings, get_settings


logger = logging.getLogger(__name__)


def _derive_dev_key(jwt_secret: str) -> bytes:
    """Deterministically derive a Fernet key from the JWT secret.

    Same secret → same key, so existing ciphertexts decrypt across restarts.
    The warning logged once at startup tells operators to set ENCRYPTION_KEY
    explicitly before they trust this with anything sensitive.
    """
    digest = hashlib.sha256(jwt_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _get_cipher(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    raw = settings.encryption_key
    if raw:
        try:
            # Accept either the raw url-safe base64 form or a hex form for
            # convenience (some KMS exports default to hex).
            key = raw.encode("utf-8") if "=" in raw or "-" in raw or "_" in raw else (
                base64.urlsafe_b64encode(bytes.fromhex(raw))
                if all(c in "0123456789abcdefABCDEF" for c in raw)
                else raw.encode("utf-8")
            )
            return Fernet(key)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "ENCRYPTION_KEY is malformed; expected Fernet.generate_key() output "
                "(url-safe base64, 32 bytes after decode) or 64-char hex"
            ) from exc

    # Fall back to a derived key, but make damn sure it's noisy.
    logger.warning(
        "ENCRYPTION_KEY is not set; deriving a key from JWT_SECRET. "
        "DO NOT run like this in production — set ENCRYPTION_KEY to "
        "Fernet.generate_key() output and re-deploy."
    )
    return Fernet(_derive_dev_key(settings.jwt_secret))


def encrypt(plaintext: str) -> bytes:
    """Encrypt ``plaintext`` for at-rest storage. Returns the Fernet token bytes."""
    if not isinstance(plaintext, str):
        raise TypeError("encrypt() expects a string")
    return _get_cipher().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt a previously :func:`encrypt`-ed blob.

    Raises :class:`cryptography.fernet.InvalidToken` if the blob is corrupted
    or was encrypted with a different key. Callers that want graceful
    fallback should catch that explicitly.
    """
    if not isinstance(ciphertext, (bytes, bytearray)):
        raise TypeError("decrypt() expects bytes")
    return _get_cipher().decrypt(bytes(ciphertext)).decode("utf-8")


def safe_decrypt(ciphertext: bytes | None) -> str | None:
    """Best-effort decrypt — returns ``None`` instead of raising.

    Used by code paths that just want to know "is the secret present and
    readable?" without surfacing crypto errors to the user (e.g. an
    integration row written under an old ENCRYPTION_KEY).
    """
    if ciphertext is None:
        return None
    try:
        return decrypt(ciphertext)
    except InvalidToken:
        return None


def reset_cache() -> None:
    """Drop the cached cipher; tests use this when they swap settings."""
    _get_cipher.cache_clear()
