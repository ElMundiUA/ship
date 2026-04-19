"""bcrypt-backed password hashing.

Used by the local-mode auth provider (``SHIP_AUTH_MODE=local``) for laptop /
self-hosted bootstrap. SaaS deployments switch to GitHub OAuth / OIDC and
never call these functions.

We talk to ``bcrypt`` (≥4.x) directly rather than going through ``passlib``,
which still relies on the removed ``bcrypt.__about__`` shim and caps every
password to bcrypt's 72-byte hard limit. To keep arbitrary-length passwords
working safely we pre-hash with SHA-256 (32 bytes) before bcrypting — a
common, well-vetted pattern.
"""

from __future__ import annotations

import base64
import hashlib

import bcrypt


def _prehash(plain: str) -> bytes:
    """Compress arbitrary-length input to a 44-byte token bcrypt can swallow.

    Base64(SHA-256(...)) keeps every byte in the printable ASCII range,
    avoiding any null-byte truncation surprise inside libbcrypt.
    """
    return base64.b64encode(hashlib.sha256(plain.encode("utf-8")).digest())


def hash_password(plain: str) -> str:
    salted = bcrypt.hashpw(_prehash(plain), bcrypt.gensalt())
    return salted.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_prehash(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Bad hash format (e.g. user has no password set / migrated from OAuth-only).
        return False
