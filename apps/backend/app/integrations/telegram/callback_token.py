"""Signed single-use ``callback_data`` for Telegram inline buttons
(ELS-253).

Telegram caps ``callback_data`` at 64 bytes, so a JWT (the
:mod:`bind_state` pattern) does not fit. Same security properties,
compact encoding instead:

- the HEAVY payload (option list, thread id) lives in the durable
  :class:`TelegramPendingAction` row (ELS-252);
- ``callback_data`` carries only ``c|<row_id_hex>|<idx>|<sig>`` where
  ``sig`` is a truncated HMAC-SHA256 over ``row_id|idx|token_nonce``
  keyed with ``settings.jwt_secret`` — the same secret source as
  ``bind_state._secret``;
- the per-row ``token_nonce`` is random and server-side only, so a
  forged/tampered callback cannot be signed without both the nonce
  and the secret;
- TTL is enforced server-side via the row's ``expires_at`` (set from
  ``CALLBACK_TTL_SECONDS``), and single-use via the atomic
  ``consumed_at`` claim — both checked before any Navigator turn runs.

Wire format budget: ``c|`` (2) + 32 hex + ``|`` + ≤2 digits + ``|`` +
16 sig chars = 54 bytes worst case, comfortably under the 64-byte cap
(pinned by a unit test).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Final

from backend.app.core.config import Settings

# Mirrors bind_state's short-window philosophy. Choice keyboards are
# conversational — ten minutes of validity matches the bind nonce and
# keeps a forgotten keyboard from being a standing attack surface.
CALLBACK_TTL_SECONDS: Final[int] = 10 * 60

_SIG_CHARS: Final[int] = 16
_PREFIX: Final[str] = "c"


def _secret(settings: Settings) -> str:
    return settings.jwt_secret


def sign_callback(
    settings: Settings, *, action_id: uuid.UUID, idx: int, nonce: str
) -> str:
    mac = hmac.new(
        _secret(settings).encode("utf-8"),
        f"{action_id.hex}|{idx}|{nonce}".encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()[:_SIG_CHARS]


def build_callback_data(
    settings: Settings, *, action_id: uuid.UUID, idx: int, nonce: str
) -> str:
    sig = sign_callback(settings, action_id=action_id, idx=idx, nonce=nonce)
    data = f"{_PREFIX}|{action_id.hex}|{idx}|{sig}"
    # Telegram rejects >64-byte callback_data at send time with an
    # opaque 400; fail loudly at build time instead.
    if len(data.encode("utf-8")) > 64:
        raise ValueError(f"callback_data exceeds 64 bytes: {data!r}")
    return data


@dataclass(frozen=True, slots=True)
class ParsedCallback:
    action_id: uuid.UUID
    idx: int
    sig: str


def parse_callback_data(data: str) -> ParsedCallback | None:
    """Parse ``c|<hex>|<idx>|<sig>``; ``None`` for malformed or
    legacy (``c|<idx>``) payloads — callers treat both as stale."""
    parts = (data or "").split("|")
    if len(parts) != 4 or parts[0] != _PREFIX:
        return None
    try:
        action_id = uuid.UUID(hex=parts[1])
        idx = int(parts[2])
    except (ValueError, TypeError):
        return None
    if idx < 0 or not parts[3]:
        return None
    return ParsedCallback(action_id=action_id, idx=idx, sig=parts[3])


def verify_callback(
    settings: Settings, *, parsed: ParsedCallback, nonce: str
) -> bool:
    expected = sign_callback(
        settings, action_id=parsed.action_id, idx=parsed.idx, nonce=nonce
    )
    return hmac.compare_digest(expected, parsed.sig)
