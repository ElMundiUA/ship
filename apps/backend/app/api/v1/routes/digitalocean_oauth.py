"""DigitalOcean OAuth install + callback routes (deploy provider).

Mirrors :mod:`backend.app.api.v1.routes.notion_oauth` — same
``install/start`` + ``install/callback`` shape and the redirect-back-to-
console contract. Two differences:

* DigitalOcean is a **deploy** provider, not a tracker, so we only write
  the ``native_integration_*`` rows (no legacy ``Integration`` row).
* DO ships a ``refresh_token`` and a 30-day ``expires_in``; we persist
  both credentials and the access-token expiry so the refresh cron can
  rotate the pair before it lapses.

The callback bounces the browser back to the console ``/integrations``
surface with a ``digitalocean=connected`` (or ``error=...``) param.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_ADMIN, _require_membership
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import (
    NativeIntegrationAuditEvent,
    NativeIntegrationAuthMode,
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)
from backend.app.db.session import get_session
from backend.app.integrations.digitalocean.oauth import (
    DigitalOceanMisconfigured,
    DigitalOceanTokenBundle,
    DigitalOceanTokenExchangeFailed,
    InvalidDigitalOceanState,
    build_authorize_url,
    build_oauth_state,
    exchange_code_for_token,
    verify_oauth_state,
)
from backend.app.security.encryption import encrypt


logger = logging.getLogger(__name__)

router = APIRouter(tags=["digitalocean-oauth"])


class InstallStartResponse(BaseModel):
    install_url: str
    state: str


def _redirect_uri(settings: Settings) -> str:
    return (
        f"{settings.public_url.rstrip('/')}"
        "/v1/integrations/digitalocean/install/callback"
    )


def _console_return_url(
    settings: Settings,
    *,
    workspace_id: uuid.UUID | None,
    return_path: str | None = None,
    error: str | None = None,
    success: str | None = None,
) -> str:
    path = (
        return_path
        if return_path and return_path.startswith("/") and not return_path.startswith("//")
        else "/integrations"
    )
    base = f"{settings.console_url.rstrip('/')}{path}"
    params: list[str] = []
    if workspace_id is not None and "ws=" not in path:
        params.append(f"ws={workspace_id}")
    if error is not None:
        params.append(urlencode({"error": error}))
    if success is not None:
        params.append(urlencode({"digitalocean": success}))
    return base + (("&" if "?" in base else "?") + "&".join(params) if params else "")


def _upsert_credential(
    session: AsyncSession,
    existing: NativeIntegrationCredential | None,
    *,
    installation_id: uuid.UUID,
    kind: str,
    secret: str,
    scopes: list[str],
    expires_at: datetime | None,
) -> None:
    now = datetime.now(timezone.utc)
    if existing is None:
        existing = NativeIntegrationCredential(
            installation_id=installation_id,
            kind=kind,
            secret_ciphertext=encrypt(secret),
        )
        session.add(existing)
    else:
        existing.secret_ciphertext = encrypt(secret)
    existing.secret_fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    existing.scopes = scopes
    existing.expires_at = expires_at
    existing.last_rotated_at = now
    existing.revoked_at = None
    existing.updated_at = now


@router.post(
    "/integrations/digitalocean/install/start",
    response_model=InstallStartResponse,
)
async def digitalocean_install_start(
    workspace_id: uuid.UUID = Query(
        ..., description="Workspace to attach the DigitalOcean connection to"
    ),
    return_path: str | None = Query(
        default=None,
        description="Console-relative path to return to after OAuth",
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InstallStartResponse:
    if not settings.digitalocean_client_id or not settings.digitalocean_client_secret:
        raise HTTPException(
            status_code=503,
            detail="DigitalOcean OAuth is not configured on this deployment",
        )
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    state = build_oauth_state(
        workspace_id,
        settings=settings,
        return_path=return_path,
    )
    return InstallStartResponse(
        install_url=build_authorize_url(
            state, settings=settings, redirect_uri=_redirect_uri(settings)
        ),
        state=state,
    )


@router.get("/integrations/digitalocean/install/callback")
async def digitalocean_install_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """DigitalOcean-side OAuth redirect target. Public (no session) by design."""
    if error:
        logger.info(
            "DigitalOcean OAuth callback returned error=%s description=%s",
            error,
            error_description,
        )
        ws_id: uuid.UUID | None = None
        return_path: str | None = None
        try:
            if state is not None:
                decoded = verify_oauth_state(state, settings=settings)
                ws_id = decoded.workspace_id
                return_path = decoded.return_path
        except InvalidDigitalOceanState:
            ws_id = None
        return RedirectResponse(
            url=_console_return_url(
                settings,
                workspace_id=ws_id,
                return_path=return_path,
                error=error or "denied",
            ),
            status_code=303,
        )

    if not code or not state:
        return RedirectResponse(
            url=_console_return_url(settings, workspace_id=None, error="bad_state"),
            status_code=303,
        )

    try:
        decoded = verify_oauth_state(state, settings=settings)
    except InvalidDigitalOceanState:
        return RedirectResponse(
            url=_console_return_url(settings, workspace_id=None, error="bad_state"),
            status_code=303,
        )

    workspace_id = decoded.workspace_id
    return_path = decoded.return_path
    try:
        token: DigitalOceanTokenBundle = await exchange_code_for_token(
            code,
            settings=settings,
            redirect_uri=_redirect_uri(settings),
        )
    except DigitalOceanMisconfigured:
        return RedirectResponse(
            url=_console_return_url(
                settings,
                workspace_id=workspace_id,
                return_path=return_path,
                error="not_configured_digitalocean",
            ),
            status_code=303,
        )
    except (DigitalOceanTokenExchangeFailed, httpx.HTTPError) as exc:
        logger.warning("DigitalOcean token exchange failed: %s", exc)
        return RedirectResponse(
            url=_console_return_url(
                settings,
                workspace_id=workspace_id,
                return_path=return_path,
                error="exchange_failed",
            ),
            status_code=303,
        )

    scopes = settings.digitalocean_oauth_scopes.split()
    access_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=token.expires_in)
        if token.expires_in
        else None
    )

    try:
        native_stmt = select(NativeIntegrationInstallation).where(
            NativeIntegrationInstallation.workspace_id == workspace_id,
            NativeIntegrationInstallation.provider
            == NativeIntegrationProvider.DIGITALOCEAN,
            NativeIntegrationInstallation.external_account_id
            == token.external_account_id,
        )
        native = (await session.execute(native_stmt)).scalar_one_or_none()
        native_is_new = native is None
        if native is None:
            native = NativeIntegrationInstallation(
                workspace_id=workspace_id,
                provider=NativeIntegrationProvider.DIGITALOCEAN,
                auth_mode=NativeIntegrationAuthMode.OAUTH,
                external_account_id=token.external_account_id,
            )
            session.add(native)
        native.external_account_name = (
            token.team_name or token.account_email or token.external_account_id
        )
        native.external_account_url = "https://cloud.digitalocean.com"
        native.capabilities = ["deploy"]
        native.scopes = scopes
        native.config = {
            "account_uuid": token.account_uuid,
            "account_email": token.account_email,
            "team_uuid": token.team_uuid,
            "team_name": token.team_name,
        }
        native.status = NativeIntegrationStatus.READY
        native.last_health_at = datetime.now(timezone.utc)
        native.last_health_error = None
        native.connected_at = native.connected_at or datetime.now(timezone.utc)
        native.disabled_at = None
        native.updated_at = datetime.now(timezone.utc)
        await session.flush()

        access_cred = (
            await session.execute(
                select(NativeIntegrationCredential).where(
                    NativeIntegrationCredential.installation_id == native.id,
                    NativeIntegrationCredential.kind == "access_token",
                )
            )
        ).scalar_one_or_none()
        _upsert_credential(
            session,
            access_cred,
            installation_id=native.id,
            kind="access_token",
            secret=token.access_token,
            scopes=scopes,
            expires_at=access_expires_at,
        )

        if token.refresh_token:
            refresh_cred = (
                await session.execute(
                    select(NativeIntegrationCredential).where(
                        NativeIntegrationCredential.installation_id == native.id,
                        NativeIntegrationCredential.kind == "refresh_token",
                    )
                )
            ).scalar_one_or_none()
            _upsert_credential(
                session,
                refresh_cred,
                installation_id=native.id,
                kind="refresh_token",
                secret=token.refresh_token,
                scopes=scopes,
                expires_at=None,
            )

        session.add(
            NativeIntegrationAuditEvent(
                workspace_id=workspace_id,
                installation_id=native.id,
                actor_user_id=None,
                provider=NativeIntegrationProvider.DIGITALOCEAN,
                action=(
                    "native_integration.create"
                    if native_is_new
                    else "native_integration.update"
                ),
                target_kind="installation",
                target_id=str(native.id),
                payload={
                    "auth_mode": NativeIntegrationAuthMode.OAUTH,
                    "team_uuid": token.team_uuid,
                    "capabilities": native.capabilities,
                    "credential_rotated": True,
                },
            )
        )
        await session.flush()
    except SQLAlchemyError as exc:
        logger.exception("DigitalOcean OAuth persistence failed: %s", exc)
        await session.rollback()
        return RedirectResponse(
            url=_console_return_url(
                settings,
                workspace_id=workspace_id,
                return_path=return_path,
                error="persistence_failed",
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=_console_return_url(
            settings,
            workspace_id=workspace_id,
            return_path=return_path,
            success="connected",
        ),
        status_code=303,
    )


__all__ = ["router"]
