"""Session JWTs and personal access tokens.

A **session JWT** is short-lived and signed with ``JWT_SECRET``; the web UI
stores it after login. A **PAT** is opaque, long-lived, prefixed
``ship_pat_``; we only ever store its SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt


JWT_TYPE_SESSION = "session"
PAT_PREFIX = "ship_pat_"


def mint_session_jwt(
    *,
    user_id: uuid.UUID,
    secret: str,
    algorithm: str = "HS256",
    ttl_seconds: int = 60 * 60 * 12,
    scopes: list[str] | None = None,
) -> tuple[str, datetime]:
    """Return ``(token, expires_at)`` for a freshly-signed session JWT."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds)
    claims = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "type": JWT_TYPE_SESSION,
        "scopes": scopes or [],
    }
    token = jwt.encode(claims, secret, algorithm=algorithm)
    return token, expires_at


def generate_pat() -> str:
    """Return a fresh personal access token as a single opaque string.

    Length budget: prefix (9) + 32 url-safe bytes ≈ 50 chars total — long
    enough to be cryptographically meaningful, short enough to paste once.
    """
    return f"{PAT_PREFIX}{secrets.token_urlsafe(32)}"


def hash_pat(raw: str) -> str:
    """SHA-256 of the raw PAT — what we store in ``api_tokens.hashed_secret``."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
