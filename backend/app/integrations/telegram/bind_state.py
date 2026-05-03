"""Signed nonce for the Telegram group → workspace bind flow.

Mirrors the shape of :mod:`backend.app.integrations.notion.oauth` —
JWT-signed, short TTL, no DB row. The nonce is generated bot-side
(when an admin types ``/link`` in a Telegram group) and verified by
the Console-facing confirm endpoint.

The token carries the Telegram chat identity the user is binding
*from* (chat_id + cached title). The user picks the *target* Ship
workspace in Console after the deep-link opens; the workspace_id is
NOT in the nonce. This means a single nonce cannot be replayed to
bind a different chat than the one the user authorised in Telegram.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Final

from jose import JWTError, jwt

from backend.app.core.config import Settings


_NONCE_TTL_SECONDS: Final[int] = 10 * 60
_NONCE_SUBJECT: Final[str] = "ship.telegram.bind"


class InvalidBindNonce(ValueError):
    """Raised when a bind nonce is bad, expired, or has the wrong subject."""


@dataclass(frozen=True, slots=True)
class TelegramBindNonce:
    chat_id: int
    chat_title: str | None
    nonce: str
    issued_at: int
    expires_at: int


def _secret(settings: Settings) -> str:
    return settings.jwt_secret


def build_bind_nonce(
    *,
    chat_id: int,
    chat_title: str | None,
    settings: Settings,
) -> str:
    """Mint a JWT nonce binding a specific Telegram chat for ~10 minutes."""
    issued_at = int(time.time())
    claims = {
        "sub": _NONCE_SUBJECT,
        "cid": chat_id,
        "title": chat_title,
        "nonce": secrets.token_urlsafe(16),
        "iat": issued_at,
        "exp": issued_at + _NONCE_TTL_SECONDS,
    }
    return jwt.encode(claims, _secret(settings), algorithm="HS256")


def verify_bind_nonce(
    token: str, *, settings: Settings
) -> TelegramBindNonce:
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise InvalidBindNonce("nonce is invalid or expired") from exc
    if claims.get("sub") != _NONCE_SUBJECT:
        raise InvalidBindNonce("nonce has wrong subject")
    raw_cid = claims.get("cid")
    raw_nonce = claims.get("nonce")
    if raw_cid is None or not raw_nonce:
        raise InvalidBindNonce("nonce is missing fields")
    try:
        chat_id = int(raw_cid)
    except (TypeError, ValueError) as exc:
        raise InvalidBindNonce("nonce has malformed cid") from exc
    title = claims.get("title")
    return TelegramBindNonce(
        chat_id=chat_id,
        chat_title=str(title) if title is not None else None,
        nonce=str(raw_nonce),
        issued_at=int(claims["iat"]),
        expires_at=int(claims["exp"]),
    )


__all__ = [
    "InvalidBindNonce",
    "TelegramBindNonce",
    "build_bind_nonce",
    "verify_bind_nonce",
]
