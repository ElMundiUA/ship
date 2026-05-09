"""Linear OAuth access-token refresh service — "set it and forget it".

Backstory: Linear OAuth access tokens expire (Linear documents this as
implementation-defined; in practice tenants observe expiry in days for
``actor=user`` apps). The original install path saved only the
``access_token`` and dropped the ``refresh_token`` from Linear's response,
so when the access expired every running tracker call started 401-ing
and the operator had to round-trip a fresh OAuth consent — losing the
"set it and forget it" property the operator expects from a workspace
integration.

This service owns two complementary fixes:

1. *Pre-emptive refresh* — :func:`ensure_fresh_linear_access_token`
   reads the workspace's Linear credential row, swaps the access token
   via Linear's ``grant_type=refresh_token`` flow when the stored
   ``expires_at`` is at or under the safety lead time, and persists the
   rotated pair (Linear rotates both access AND refresh on each refresh
   call). The tracker resolver calls this just before constructing the
   ``LinearTracker`` so every API call sees a fresh token.

2. *Reactive refresh* — :func:`refresh_linear_access_token_now` runs the
   same refresh path unconditionally, intended as the recovery action
   when a live API call comes back 401 (Linear's "Authentication
   required" envelope) and the caller wants one chance to retry rather
   than fail the whole tick.

Both paths are no-ops when the workspace doesn't have a refresh token
on file (operator-pasted PATs, legacy installs that never carried one)
— in those cases the access token is returned as-is and the operator
will still need to re-authorize on terminal expiry. We log + audit so a
re-auth banner can pick it up later.
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


# Refresh when the access token has this much (or less) lifetime
# remaining. Picked so a long-running cron tick that crosses minute
# boundaries can't observe an expiry mid-flight; small enough that
# we don't refresh on every read.
_REFRESH_LEAD_TIME = timedelta(minutes=5)


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
    expires_at: datetime | None,
    *,
    now: datetime,
    lead_time: timedelta = _REFRESH_LEAD_TIME,
) -> bool:
    """True iff a stored access token is at or under the safety lead time.

    A NULL ``expires_at`` (legacy installs, PATs) returns False — those
    tokens are either non-expiring or self-managed by the operator.
    """
    if expires_at is None:
        return False
    return expires_at - now <= lead_time


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


async def ensure_fresh_linear_access_token(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    settings: Settings,
) -> str | None:
    """Return a usable access token, refreshing pre-emptively if due.

    Returns the (possibly rotated) access token string, or ``None`` when:

    - no Linear install for the workspace
    - install has no access-token credential
    - access token has no expiry recorded AND no refresh token —
      the caller can still try the stored token but we can't help

    On a successful refresh the rotated access + refresh tokens are
    persisted to the credential rows before this returns; callers
    don't need to round-trip the DB again.
    """
    install = await _load_active_install(session, workspace_id)
    if install is None:
        return None
    access_credential = await _load_credential(
        session, install.id, kind="access_token"
    )
    if access_credential is None or not access_credential.secret_ciphertext:
        return None

    try:
        current_token = decrypt(access_credential.secret_ciphertext)
    except Exception:  # noqa: BLE001
        logger.warning(
            "linear refresh: access token unreadable workspace=%s install=%s",
            workspace_id,
            install.id,
        )
        return None
    if not current_token:
        return None

    now = datetime.now(timezone.utc)
    if not _is_due_for_refresh(access_credential.expires_at, now=now):
        return current_token

    refresh_credential = await _load_credential(
        session, install.id, kind="refresh_token"
    )
    if refresh_credential is None or not refresh_credential.secret_ciphertext:
        # Pre-refresh fork: token is past the lead time but we have no
        # refresh material. Hand the stale token back so the caller
        # can still attempt the call (cron ticks tolerate 401s and
        # retry next tick) — terminal expiry will surface as a 401
        # which the operator must resolve via re-auth.
        logger.info(
            "linear refresh: no refresh_token on file workspace=%s — "
            "returning stale access token; operator must re-authorize "
            "on terminal expiry",
            workspace_id,
        )
        return current_token

    try:
        refresh_token_str = decrypt(refresh_credential.secret_ciphertext)
    except Exception:  # noqa: BLE001
        logger.warning(
            "linear refresh: refresh token unreadable workspace=%s install=%s",
            workspace_id,
            install.id,
        )
        return current_token

    try:
        bundle = await refresh_access_token(
            refresh_token_str,
            settings=settings,
            client=None,
        )
    except LinearTokenExchangeFailed as exc:
        # Linear refused the refresh — refresh token might be revoked
        # by the user re-installing the OAuth app, or the grant got
        # invalidated by Linear. Mark the install as needs re-auth so
        # the dashboard can surface the banner; don't sink the caller.
        logger.warning(
            "linear refresh: exchange failed workspace=%s err=%s",
            workspace_id,
            exc,
        )
        install.status = NativeIntegrationStatus.ERROR
        install.last_health_error = f"refresh_failed: {str(exc)[:200]}"
        install.last_health_at = datetime.now(timezone.utc)
        install.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return current_token

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
        workspace_id,
        bundle.expires_in,
    )
    return bundle.access_token


async def refresh_linear_access_token_now(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    settings: Settings,
) -> str | None:
    """Force-refresh the workspace's Linear access token immediately.

    Use as the recovery hook from a 401-on-call path: the access
    token *should* still be inside its expiry window per our store but
    Linear has decided it's not valid (revoked grant, security event,
    out-of-band rotation). Returns the new access token on success or
    ``None`` when there's no refresh material on file.
    """
    install = await _load_active_install(session, workspace_id)
    if install is None:
        return None
    access_credential = await _load_credential(
        session, install.id, kind="access_token"
    )
    refresh_credential = await _load_credential(
        session, install.id, kind="refresh_token"
    )
    if access_credential is None or refresh_credential is None:
        return None
    if not refresh_credential.secret_ciphertext:
        return None
    try:
        refresh_token_str = decrypt(refresh_credential.secret_ciphertext)
    except Exception:  # noqa: BLE001
        return None
    try:
        bundle = await refresh_access_token(
            refresh_token_str, settings=settings, client=None
        )
    except LinearTokenExchangeFailed as exc:
        install.status = NativeIntegrationStatus.ERROR
        install.last_health_error = f"refresh_failed: {str(exc)[:200]}"
        install.last_health_at = datetime.now(timezone.utc)
        install.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return None
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
        "linear refresh (forced): rotated workspace=%s",
        workspace_id,
    )
    return bundle.access_token


__all__ = [
    "ensure_fresh_linear_access_token",
    "refresh_linear_access_token_now",
]
