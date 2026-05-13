"""Worker job: re-evaluate ``Integration.status`` for stale or pending rows.

Pairs with :mod:`backend.app.services.secret_probe` — that module knows *how*
to validate a single secret; this module knows *which* rows to look at and
how to write the verdict back without leaking ciphertext or wedging the
worker on a bad third-party.

Selection policy
----------------

A row is eligible for a probe when:

- ``secret_ciphertext IS NOT NULL`` — nothing to validate without a secret.
- ``status = 'pending'`` (newly upserted, never probed yet), **or**
- ``last_health_at`` is older than :data:`REPROBE_AFTER_SECONDS` (re-probe so
  rotated upstream tokens or revoked permissions don't sit on a stale ``ok``
  for days).

Rows in ``error`` are also re-probed on the same cadence so a legitimate fix
(operator rotated the upstream token) doesn't require the user to click
"Save secret" again with the same value.

Concurrency
-----------

We process up to :data:`MAX_PER_TICK` rows per cron tick and execute the
probes in parallel with :func:`asyncio.gather`. The HTTP probes themselves
have a hard 6s timeout (see :mod:`secret_probe`), so the worst-case
wall-clock per tick is roughly that timeout — short enough to fit comfortably
inside the 30s arq tick.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import or_, select

from backend.app.db.models.tenancy import Integration
from backend.app.db.session import get_sessionmaker
from backend.app.security.encryption import decrypt
from backend.app.services.secret_probe import probe_one


log = logging.getLogger("ship.worker.secret_probe")

REPROBE_AFTER_SECONDS = 30 * 60  # half an hour — fast enough to catch rotations
MAX_PER_TICK = 32  # bounded fan-out so a misconfigured workspace can't starve others


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _probe_row(row: Integration) -> tuple[uuid.UUID, str, str | None]:
    """Decrypt + probe a single row. Always returns a verdict — never raises."""
    if row.secret_ciphertext is None:
        return row.id, "pending", None
    try:
        plaintext = decrypt(row.secret_ciphertext)
    except InvalidToken:
        # The ciphertext was written under a different ENCRYPTION_KEY than we
        # have now. Surfacing this as ``error`` is the right thing — the
        # operator either rotates the secret or restores the key.
        return row.id, "error", "stored secret cannot be decrypted with current ENCRYPTION_KEY"

    status, message = await probe_one(row.kind, plaintext, row.config or {})
    return row.id, status, message


async def probe_pending_secrets(*, max_rows: int = MAX_PER_TICK) -> dict[str, Any]:
    """Re-evaluate the next batch of integration rows.

    Returns a small summary dict so the cron caller can log a useful line and
    so the test suite can assert on counts. Safe to call from anywhere with a
    reachable database; no Redis dependency.
    """
    cutoff = _now() - timedelta(seconds=REPROBE_AFTER_SECONDS)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        stmt = (
            select(Integration)
            .where(Integration.secret_ciphertext.is_not(None))
            .where(
                or_(
                    Integration.status == "pending",
                    Integration.last_health_at.is_(None),
                    Integration.last_health_at < cutoff,
                )
            )
            # Process the freshly-upserted ones first so a brand-new save
            # turns green within one tick.
            .order_by(Integration.status.desc(), Integration.updated_at.asc())
            .limit(max_rows)
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return {"checked": 0, "ok": 0, "error": 0, "skipped": 0}

        verdicts = await asyncio.gather(*[_probe_row(row) for row in rows])

        ok = err = 0
        index = {row.id: row for row in rows}
        now = _now()
        for row_id, status, message in verdicts:
            row = index[row_id]
            row.status = status
            row.last_health_at = now
            row.last_health_error = message
            if status == "ok":
                ok += 1
            else:
                err += 1
        await session.commit()
        return {
            "checked": len(rows),
            "ok": ok,
            "error": err,
            "skipped": 0,
        }


async def cron_probe_pending_secrets(ctx: dict) -> dict[str, Any]:
    """arq entrypoint. Logs a one-line summary so docker logs stay readable."""
    summary = await probe_pending_secrets()
    log.info(
        "secret_probe tick: checked=%d ok=%d error=%d",
        summary.get("checked", 0),
        summary.get("ok", 0),
        summary.get("error", 0),
    )
    return summary


__all__ = [
    "MAX_PER_TICK",
    "REPROBE_AFTER_SECONDS",
    "cron_probe_pending_secrets",
    "probe_pending_secrets",
]
