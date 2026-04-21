"""Notion OAuth install + callback routes (pilot Day 2 — tracker WOW flow).

Mirrors :mod:`backend.app.api.v1.routes.linear_oauth` — same
``install/start`` + ``install/callback`` shape, same redirect-back-to-
console-onboarding contract. Differences are confined to the helper
module (Notion uses HTTP Basic auth + ``Notion-Version`` header).
"""

from __future__ import annotations

import logging
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
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.db.session import get_session
from backend.app.integrations.notion.oauth import (
    InvalidNotionState,
    NotionMisconfigured,
    NotionTokenExchangeFailed,
    build_authorize_url,
    build_oauth_state,
    exchange_code_for_token,
    verify_oauth_state,
)
from backend.app.security.encryption import encrypt


logger = logging.getLogger(__name__)

router = APIRouter(tags=["notion-oauth"])


class InstallStartResponse(BaseModel):
    install_url: str
    state: str


def _redirect_uri(settings: Settings) -> str:
    return (
        f"{settings.public_url.rstrip('/')}"
        "/v1/integrations/notion/install/callback"
    )


def _console_onboarding_url(
    settings: Settings,
    *,
    workspace_id: uuid.UUID | None,
    step: str = "tracker",
    error: str | None = None,
    success: str | None = None,
) -> str:
    base = f"{settings.console_url.rstrip('/')}/onboarding"
    params: list[str] = [f"step={step}"]
    if workspace_id is not None:
        params.append(f"ws={workspace_id}")
    if error is not None:
        params.append(f"error={error}")
    if success is not None:
        params.append(f"notion={success}")
    return base + "?" + "&".join(params)


@router.post(
    "/integrations/notion/install/start",
    response_model=InstallStartResponse,
)
async def notion_install_start(
    workspace_id: uuid.UUID = Query(
        ..., description="Workspace to attach the Notion connection to"
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InstallStartResponse:
    if not settings.notion_client_id or not settings.notion_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Notion OAuth is not configured on this deployment",
        )
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    state = build_oauth_state(workspace_id, settings=settings)
    return InstallStartResponse(
        install_url=build_authorize_url(
            state, settings=settings, redirect_uri=_redirect_uri(settings)
        ),
        state=state,
    )


@router.get("/integrations/notion/install/callback")
async def notion_install_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Notion-side OAuth redirect target. Public (no session) by design."""
    if error:
        logger.info(
            "Notion OAuth callback returned error=%s description=%s",
            error,
            error_description,
        )
        ws_id: uuid.UUID | None = None
        try:
            if state is not None:
                ws_id = verify_oauth_state(state, settings=settings).workspace_id
        except InvalidNotionState:
            ws_id = None
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=ws_id, error=error or "denied"
            ),
            status_code=303,
        )

    if not code or not state:
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=None, error="bad_state"
            ),
            status_code=303,
        )

    try:
        decoded = verify_oauth_state(state, settings=settings)
    except InvalidNotionState:
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
    except NotionMisconfigured:
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=workspace_id, error="not_configured"
            ),
            status_code=303,
        )
    except (NotionTokenExchangeFailed, httpx.HTTPError) as exc:
        logger.warning("Notion token exchange failed: %s", exc)
        return RedirectResponse(
            url=_console_onboarding_url(
                settings, workspace_id=workspace_id, error="exchange_failed"
            ),
            status_code=303,
        )

    # Workspace-scoped row only. Notion is the knowledge-base
    # integration and is always workspace-level today; if per-repo
    # Notion databases land later they'll have their own ``repo_id``
    # rows and this lookup must not accidentally adopt one.
    stmt = select(Integration).where(
        Integration.workspace_id == workspace_id,
        Integration.kind == "notion",
        Integration.repo_id.is_(None),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    is_new = row is None
    config_payload = {
        "bot_id": token.bot_id,
        "notion_workspace_id": token.workspace_id,
        "notion_workspace_name": token.workspace_name,
        "notion_workspace_icon": token.workspace_icon,
    }
    if is_new:
        row = Integration(
            workspace_id=workspace_id,
            kind="notion",
            config=config_payload,
        )
        session.add(row)
    else:
        merged_config = dict(row.config or {})
        merged_config.update(config_payload)
        row.config = merged_config
    row.secret_ciphertext = encrypt(token.access_token)
    row.status = "ok"
    row.last_health_at = datetime.now(timezone.utc)
    row.last_health_error = None
    row.updated_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="integration.create" if is_new else "integration.update",
            target_kind="integration",
            target_id=str(row.id),
            payload={
                "kind": "notion",
                "via": "oauth",
                "notion_workspace_id": token.workspace_id,
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
