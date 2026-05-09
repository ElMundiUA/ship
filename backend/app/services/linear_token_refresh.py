"""Linear OAuth access-token refresh service — "set it and forget it".

Backstory: Linear OAuth access tokens expire (Linear documents this as
implementation-defined; in practice tenants observe expiry in days for
``actor=user`` apps). The original install path saved only the
``access_token`` and dropped the ``refresh_token`` from Linear's response,
so when the access expired every running tracker call started 401-ing
and the operator had to round-trip a fresh OAuth consent — losing the
"set it and forget it" property the operator expects from a workspace
integration.

Refresh runs on a fixed cadence rather than per-request: a scheduled
``linear_token_refresh_tick`` cron iterates every READY install whose
last rotation is older than ``LINEAR_TOKEN_REFRESH_HOURS`` and swaps
the pair via Linear's ``grant_type=refresh_token`` flow. The hot path
(tracker resolver, list_tickets, transition…) reads the credential as
written and never triggers refresh inline; pre-rotation keeps a rolling
freshness margin without saddling the picker with a Linear round-trip.

Reactive refresh — :func:`refresh_linear_access_token_now` — stays as
the recovery hook for callers that observe a 401 from a live API call
and want one chance to retry rather than fail the whole tick.

Both paths are no-ops when the workspace doesn't have a refresh token
on file (operator-pasted PATs, legacy installs that never carried one).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)
from backend.app.integrations.linear.oauth import (
    LinearTokenExchangeFailed,
    refresh_access_token,
)
from backend.app.security.encryption import decrypt, encrypt


logger = logging.getLogger(__name__)


# How stale the credential must be before the scheduled tick rotates
# it. Used as the floor on ``last_rotated_at`` — anything older than
# (now - this) gets refreshed. ``LINEAR_TOKEN_REFRESH_HOURS`` from
# settings is the canonical knob; this default keeps the helper
# usable from tests without standing up a Settings.
_DEFAULT_REFRESH_AGE = timedelta(hours=6)


async def _load_active_install(
    session: AsyncSession, workspace_id: uuid.UUID
) -> NativeIntegrationInstallation | None:
    """Load the workspace's READY Linear install or ``None``."""
    return (
        await session.execute(
            select(NativeIntegrationInstallation)
            .where(
                NativeIntegrationInstallation.workspace_id == workspace_id,
                NativeIntegrationInstallation.provider
                == NativeIntegrationProvider.LINEAR,
                NativeIntegrationInstallation.status
                == NativeIntegrationStatus.READY,
                NativeIntegrationInstallation.disabled_at.is_(None),
            )
            .order_by(NativeIntegrationInstallation.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _load_credential(
    session: AsyncSession,
    installation_id: uuid.UUID,
    *,
    kind: str,
) -> NativeIntegrationCredential | None:
    """Load the latest non-revoked credential of ``kind`` for the install."""
    return (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == installation_id,
                NativeIntegrationCredential.kind == kind,
                NativeIntegrationCredential.revoked_at.is_(None),
            )
            .order_by(NativeIntegrationCredential.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _is_due_for_refresh(
    last_rotated_at: datetime | None,
    *,
    now: datetime,
    max_age: timedelta = _DEFAULT_REFRESH_AGE,
) -> bool:
    """True iff a stored access token's last rotation is older than ``max_age``.

    Cadence-based refresh, not expiry-based: the scheduled tick rotates
    every credential whose age crosses the threshold regardless of
    Linear's stated ``expires_in``. NULL ``last_rotated_at`` (just-installed,
    never rotated) is treated as eligible so a fresh install starts the
    rotation cycle on the next tick.
    """
    if last_rotated_at is None:
        return True
    return (now - last_rotated_at) >= max_age


async def _persist_refreshed_pair(
    session: AsyncSession,
    *,
    install: NativeIntegrationInstallation,
    access_credential: NativeIntegrationCredential,
    refresh_credential: NativeIntegrationCredential,
    new_access_token: str,
    new_refresh_token: str,
    expires_in: int | None,
    scope: str | None,
) -> None:
    """Write back rotated access + refresh credentials in one transaction.

    Both rows are updated together to keep the credential pair in sync —
    if the access write succeeds but refresh write fails, the next
    refresh attempt would try a stale refresh_token and 4xx.
    Caller commits.
    """
    now = datetime.now(timezone.utc)

    access_credential.secret_ciphertext = encrypt(new_access_token)
    access_credential.secret_fingerprint = hashlib.sha256(
        new_access_token.encode("utf-8")
    ).hexdigest()
    access_credential.last_rotated_at = now
    access_credential.updated_at = now
    if expires_in:
        access_credential.expires_at = now + timedelta(seconds=expires_in)
    else:
        access_credential.expires_at = None

    refresh_credential.secret_ciphertext = encrypt(new_refresh_token)
    refresh_credential.secret_fingerprint = hashlib.sha256(
        new_refresh_token.encode("utf-8")
    ).hexdigest()
    refresh_credential.last_rotated_at = now
    refresh_credential.updated_at = now

    install.last_health_at = now
    install.last_health_error = None
    install.updated_at = now
    if scope:
        cfg = dict(install.config or {})
        cfg["scope"] = scope
        install.config = cfg

    await session.flush()


async def _refresh_install(
    session: AsyncSession,
    install: NativeIntegrationInstallation,
    *,
    settings: Settings,
) -> bool:
    """Rotate one workspace's Linear access+refresh pair.

    Returns True when Linear accepted the refresh and we persisted the
    rotated pair, False when the install is unrotatable (no refresh
    token / unreadable / Linear declined). On Linear refusal the install
    is marked ERROR with ``last_health_error='refresh_failed: ...'`` so
    the console can surface a re-auth banner; the existing access token
    stays in the row so the next tick still has *something* to use.
    """
    access_credential = await _load_credential(
        session, install.id, kind="access_token"
    )
    refresh_credential = await _load_credential(
        session, install.id, kind="refresh_token"
    )
    if access_credential is None or refresh_credential is None:
        return False
    if not refresh_credential.secret_ciphertext:
        return False
    try:
        refresh_token_str = decrypt(refresh_credential.secret_ciphertext)
    except Exception:  # noqa: BLE001
        logger.warning(
            "linear refresh: refresh-token unreadable workspace=%s install=%s",
            install.workspace_id,
            install.id,
        )
        return False
    if not refresh_token_str:
        return False
    try:
        bundle = await refresh_access_token(
            refresh_token_str, settings=settings, client=None
        )
    except LinearTokenExchangeFailed as exc:
        logger.warning(
            "linear refresh: exchange failed workspace=%s err=%s",
            install.workspace_id,
            exc,
        )
        install.status = NativeIntegrationStatus.ERROR
        install.last_health_error = f"refresh_failed: {str(exc)[:200]}"
        install.last_health_at = datetime.now(timezone.utc)
        install.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return False
    new_refresh_token = bundle.refresh_token or refresh_token_str
    await _persist_refreshed_pair(
        session,
        install=install,
        access_credential=access_credential,
        refresh_credential=refresh_credential,
        new_access_token=bundle.access_token,
        new_refresh_token=new_refresh_token,
        expires_in=bundle.expires_in,
        scope=bundle.scope,
    )
    logger.info(
        "linear refresh: rotated workspace=%s expires_in=%ss",
        install.workspace_id,
        bundle.expires_in,
    )
    return True


async def refresh_all_due_linear_tokens(
    session: AsyncSession,
    *,
    settings: Settings,
    max_age: timedelta | None = None,
) -> dict[str, int]:
    """Iterate every READY Linear install and rotate the credential
    pair when the last rotation is older than ``max_age``.

    Used by the scheduled ``linear_token_refresh_tick`` cron — runs
    every ``LINEAR_TOKEN_REFRESH_HOURS``. Returns a counter dict for
    log + audit visibility; the caller commits.
    """
    if max_age is None:
        max_age = timedelta(hours=settings.linear_token_refresh_hours)
    now = datetime.now(timezone.utc)

    installs = (
        await session.execute(
            select(NativeIntegrationInstallation)
            .where(
                NativeIntegrationInstallation.provider
                == NativeIntegrationProvider.LINEAR,
                NativeIntegrationInstallation.status
                == NativeIntegrationStatus.READY,
                NativeIntegrationInstallation.disabled_at.is_(None),
            )
        )
    ).scalars().all()

    seen = 0
    rotated = 0
    skipped_unrotatable = 0
    skipped_fresh = 0
    failed = 0
    for install in installs:
        seen += 1
        access_credential = await _load_credential(
            session, install.id, kind="access_token"
        )
        if access_credential is None:
            skipped_unrotatable += 1
            continue
        if not _is_due_for_refresh(
            access_credential.last_rotated_at, now=now, max_age=max_age
        ):
            skipped_fresh += 1
            continue
        ok = await _refresh_install(session, install, settings=settings)
        if ok:
            rotated += 1
        else:
            failed += 1
    return {
        "installs_seen": seen,
        "rotated": rotated,
        "skipped_fresh": skipped_fresh,
        "skipped_unrotatable": skipped_unrotatable,
        "refresh_failed": failed,
    }


async def refresh_linear_access_token_now(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    settings: Settings,
) -> str | None:
    """Force-refresh one workspace's Linear access token immediately.

    Recovery hook for callers that observe a 401 from a live API call —
    rotates regardless of ``last_rotated_at`` age. Returns the new
    access token on success, ``None`` when the install has no refresh
    material on file or Linear declined the refresh.
    """
    install = await _load_active_install(session, workspace_id)
    if install is None:
        return None
    ok = await _refresh_install(session, install, settings=settings)
    if not ok:
        return None
    access_credential = await _load_credential(
        session, install.id, kind="access_token"
    )
    if access_credential is None or not access_credential.secret_ciphertext:
        return None
    try:
        return decrypt(access_credential.secret_ciphertext)
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "refresh_all_due_linear_tokens",
    "refresh_linear_access_token_now",
]
