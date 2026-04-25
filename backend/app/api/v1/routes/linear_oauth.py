"""Linear OAuth install + callback routes (pilot Day 2 — tracker WOW flow).

Two endpoints:

- ``POST /v1/integrations/linear/install/start`` — admin-only, returns
  the Linear authorize URL bound to the workspace via a signed state
  token.
- ``GET /v1/integrations/linear/install/callback`` — public (no
  session), consumed by Linear's redirect. Validates the state,
  exchanges the auth code for an access token, persists it on a generic
  :class:`Integration` row with ``kind="linear"``, then bounces the
  browser back to the console onboarding ``?step=tracker&linear=
  connected``.

The redirect URI we register with Linear must be exactly
``{SHIP_PUBLIC_URL}/v1/integrations/linear/install/callback``. Operator
checklist lives in ``documentation/internal/linear-oauth-setup.md``.
"""

from __future__ import annotations

import logging
import hashlib
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
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
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.db.session import get_session
from backend.app.integrations.linear.oauth import (
    InvalidLinearState,
    LinearMisconfigured,
    LinearTokenExchangeFailed,
    build_authorize_url,
    build_oauth_state,
    exchange_code_for_token,
    verify_oauth_state,
)
from backend.app.security.encryption import encrypt


logger = logging.getLogger(__name__)

router = APIRouter(tags=["linear-oauth"])


class InstallStartResponse(BaseModel):
    install_url: str
    state: str


def _redirect_uri(settings: Settings) -> str:
    """The exact redirect_uri Linear will POST our auth code to.

    Must be registered byte-for-byte under the Linear OAuth Application
    settings. Trailing slash matters; we strip it on ``public_url`` to
    keep the value canonical.
    """
    return (
        f"{settings.public_url.rstrip('/')}"
        "/v1/integrations/linear/install/callback"
    )


def _console_onboarding_url(
    settings: Settings,
    *,
    workspace_id: uuid.UUID | None,
    step: str = "tracker",
    error: str | None = None,
    success: str | None = None,
) -> str:
    """Build the URL to bounce the browser back to in the wizard.

    Mirrors the helper in ``github_app.py`` (kept local here so the two
    flows are easy to read in isolation; copy-paste over an import is
    the right trade-off for two callers).
    """
    base = f"{settings.console_url.rstrip('/')}/onboarding"
    params: list[str] = [f"step={step}"]
    if workspace_id is not None:
        params.append(f"ws={workspace_id}")
    if error is not None:
        params.append(f"error={error}")
    if success is not None:
        params.append(f"linear={success}")
    return base + "?" + "&".join(params)


@router.post(
    "/integrations/linear/install/start",
    response_model=InstallStartResponse,
)
async def linear_install_start(
    workspace_id: uuid.UUID = Query(
        ..., description="Workspace to attach the Linear connection to"
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InstallStartResponse:
    """Mint a signed state token and return the Linear authorize URL.

    Admin-only because attaching a tracker is a workspace-wide
    credential. 503 when LINEAR_CLIENT_ID/SECRET aren't configured so
    operators see a clear "wire env vars" message rather than a 500.
    """
    if not settings.linear_client_id or not settings.linear_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Linear OAuth is not configured on this deployment",
        )
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    state = build_oauth_state(workspace_id, settings=settings)
    return InstallStartResponse(
        install_url=build_authorize_url(
            state, settings=settings, redirect_uri=_redirect_uri(settings)
        ),
        state=state,
    )


@router.get("/integrations/linear/install/callback")
async def linear_install_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Linear-side OAuth redirect target. Public (no session) by design.

    Validates the round-tripped state, exchanges the code for an access
    token, persists an :class:`Integration` row with ``kind="linear"``,
    then redirects the browser to the console onboarding wizard.

    On any failure we bounce back to the wizard with ``?error=<code>``
    so the UI shows a friendly banner instead of a JSON error page.
    """
    if error:
        # User clicked "Decline" or Linear returned an error. We still
        # need a workspace_id to redirect cleanly; without state we
        # can't recover it, so fall back to the install entry point.
        logger.info(
            "Linear OAuth callback returned error=%s description=%s",
            error,
            error_description,
        )
        ws_id: uuid.UUID | None = None
        try:
            if state is not None:
                ws_id = verify_oauth_state(state, settings=settings).workspace_id
        except InvalidLinearState:
            ws_id = None
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=ws_id, error=error or "denied"
            ),
            status_code=303,
        )

    if not code or not state:
        # Missing required params — treat as a tampered redirect.
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=None, error="bad_state"
            ),
            status_code=303,
        )

    try:
        decoded = verify_oauth_state(state, settings=settings)
    except InvalidLinearState:
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=None, error="bad_state"
            ),
            status_code=303,
        )

    workspace_id = decoded.workspace_id
    try:
        token = await exchange_code_for_token(
            code,
            settings=settings,
            redirect_uri=_redirect_uri(settings),
        )
    except LinearMisconfigured:
        # Operator removed env vars between start and callback (or the
        # callback hit a misconfigured replica). 503 → bounce with a
        # dedicated error code.
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=workspace_id, error="not_configured_linear"
            ),
            status_code=303,
        )
    except (LinearTokenExchangeFailed, httpx.HTTPError) as exc:
        logger.warning("Linear token exchange failed: %s", exc)
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=workspace_id, error="exchange_failed"
            ),
            status_code=303,
        )

    # Upsert the Integration row. We don't run the secret_probe here —
    # the OAuth dance itself proves the token works, and Linear has no
    # cheap "is this token alive" probe.
    # Workspace-level install only. Per-repo Linear picks (one repo →
    # one Linear team/project) are written via the wizard's per-repo
    # flow and carry ``repo_id != NULL``; we don't want to grab one of
    # those here and overwrite its scope/secret with the workspace
    # install's values.
    stmt = select(Integration).where(
        Integration.workspace_id == workspace_id,
        Integration.kind == "linear",
        Integration.repo_id.is_(None),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    is_new = row is None
    if is_new:
        row = Integration(
            workspace_id=workspace_id,
            kind="linear",
            config={"scope": token.scope, "token_type": token.token_type},
        )
        session.add(row)
    else:
        merged_config = dict(row.config or {})
        merged_config.update(
            {"scope": token.scope, "token_type": token.token_type}
        )
        row.config = merged_config
    row.secret_ciphertext = encrypt(token.access_token)
    row.status = "ok"
    row.last_health_at = datetime.now(timezone.utc)
    row.last_health_error = None
    row.updated_at = datetime.now(timezone.utc)

    scopes = sorted({scope.strip() for scope in token.scope.split(",") if scope.strip()})
    native_stmt = select(NativeIntegrationInstallation).where(
        NativeIntegrationInstallation.workspace_id == workspace_id,
        NativeIntegrationInstallation.provider == NativeIntegrationProvider.LINEAR,
        NativeIntegrationInstallation.external_account_id == "default",
    )
    native = (await session.execute(native_stmt)).scalar_one_or_none()
    native_is_new = native is None
    if native is None:
        native = NativeIntegrationInstallation(
            workspace_id=workspace_id,
            provider=NativeIntegrationProvider.LINEAR,
            auth_mode=NativeIntegrationAuthMode.OAUTH,
            external_account_id="default",
        )
        session.add(native)
    native.external_account_name = "Linear workspace"
    native.external_account_url = "https://linear.app"
    native.capabilities = ["tracker"]
    native.scopes = scopes
    native.config = {"scope": token.scope, "token_type": token.token_type}
    native.status = NativeIntegrationStatus.READY
    native.last_health_at = datetime.now(timezone.utc)
    native.last_health_error = None
    native.connected_at = native.connected_at or datetime.now(timezone.utc)
    native.disabled_at = None
    native.updated_at = datetime.now(timezone.utc)
    await session.flush()

    credential = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == native.id,
                NativeIntegrationCredential.kind == "access_token",
            )
        )
    ).scalar_one_or_none()
    if credential is None:
        credential = NativeIntegrationCredential(
            installation_id=native.id,
            kind="access_token",
            secret_ciphertext=encrypt(token.access_token),
        )
        session.add(credential)
    else:
        credential.secret_ciphertext = encrypt(token.access_token)
    credential.secret_fingerprint = hashlib.sha256(
        token.access_token.encode("utf-8")
    ).hexdigest()
    credential.scopes = scopes
    credential.last_rotated_at = datetime.now(timezone.utc)
    credential.revoked_at = None
    credential.updated_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=None,  # OAuth callback runs without a session
            actor_token_id=None,
            action="integration.create" if is_new else "integration.update",
            target_kind="integration",
            target_id=str(row.id),
            payload={
                "kind": "linear",
                "via": "oauth",
                "scope": token.scope,
            },
        )
    )
    session.add(
        NativeIntegrationAuditEvent(
            workspace_id=workspace_id,
            installation_id=native.id,
            actor_user_id=None,
            provider=NativeIntegrationProvider.LINEAR,
            action=(
                "native_integration.create"
                if native_is_new
                else "native_integration.update"
            ),
            target_kind="installation",
            target_id=str(native.id),
            payload={
                "auth_mode": NativeIntegrationAuthMode.OAUTH,
                "scope": token.scope,
                "capabilities": native.capabilities,
                "credential_rotated": True,
            },
        )
    )
    await session.flush()

    return RedirectResponse(
        url=_console_onboarding_url(
            settings, workspace_id=workspace_id, success="connected"
        ),
        status_code=303,
    )


__all__ = ["router"]
