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
from datetime import datetime, timedelta, timezone

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
from backend.app.security.encryption import encrypt, safe_decrypt


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

    # E14 provisioning. Auto-pick team if exactly one (typical solo
    # operator); leave unset and surface a banner if more — the wizard
    # then routes the operator to a "pick a team" step before the
    # integration is actually usable.
    try:
        from backend.app.integrations.linear.tracker_adapter import LinearTracker
        from backend.app.services import linear_provisioner

        live = LinearTracker(token.access_token)
        teams = await linear_provisioner.list_teams(live)
        merged = dict(row.config or {})
        merged["team_options"] = [
            {"id": t["id"], "key": t["key"], "name": t["name"]}
            for t in teams
        ]
        if len(teams) == 1:
            picked = teams[0]
            result = await linear_provisioner.provision_team(
                tracker=live, team_key=picked["key"], settings=settings
            )
            merged.update(
                {
                    "team_id": result.team_id,
                    "team_key": result.team_key,
                    "state_id_by_name": result.state_id_by_name,
                    "label_id_by_stage": result.label_id_by_stage,
                    "signal_label_ids": result.signal_label_ids,
                    "canonical_to_native": result.canonical_to_native,
                    "canonical_resolution_meta": result.canonical_resolution_meta,
                    "fsm_provisioned": True,
                }
            )
            logger.info(
                "Linear FSM provisioned for workspace=%s team=%s "
                "(%d stage labels, %d signal labels, %d states, "
                "canonical_to_native=%d entries)",
                workspace_id,
                picked["key"],
                len(result.label_id_by_stage),
                len(result.signal_label_ids),
                len(result.state_id_by_name),
                len(result.canonical_to_native),
            )
        else:
            merged["fsm_provisioned"] = False
            logger.info(
                "Linear team picker pending for workspace=%s: %d teams visible",
                workspace_id,
                len(teams),
            )
        row.config = merged
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Linear FSM provisioning failed (workspace=%s): %s",
            workspace_id,
            exc,
        )
        row.last_health_error = f"fsm_provisioning_failed: {exc!s}"[:500]

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
    # ELS: persist Linear's TTL on the credential so the refresh
    # service can pre-emptively swap before the token actually
    # expires. ``expires_in`` is seconds-until-expiry from Linear; we
    # store the absolute deadline so reads don't have to track the
    # minting moment separately.
    if token.expires_in:
        credential.expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=token.expires_in
        )
    else:
        # Tokens without an expiry signal (e.g., long-lived app-actor
        # tokens, or operator-pasted PATs) leave ``expires_at`` NULL —
        # the refresh service treats NULL as "do not pre-emptively
        # refresh", consistent with PAT semantics.
        credential.expires_at = None

    # Refresh-token credential row: separate ``kind`` so the
    # refresh service can fetch it without confusing it with the
    # access token. Linear rotates the refresh token on every refresh,
    # so this row is overwritten on each successful refresh too. When
    # Linear declines to issue a refresh token (e.g., older OAuth app
    # config), we revoke any pre-existing refresh credential so the
    # row never points at a stale value.
    refresh_credential = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == native.id,
                NativeIntegrationCredential.kind == "refresh_token",
            )
        )
    ).scalar_one_or_none()
    if token.refresh_token:
        if refresh_credential is None:
            refresh_credential = NativeIntegrationCredential(
                installation_id=native.id,
                kind="refresh_token",
                secret_ciphertext=encrypt(token.refresh_token),
            )
            session.add(refresh_credential)
        else:
            refresh_credential.secret_ciphertext = encrypt(token.refresh_token)
        refresh_credential.secret_fingerprint = hashlib.sha256(
            token.refresh_token.encode("utf-8")
        ).hexdigest()
        refresh_credential.scopes = scopes
        refresh_credential.last_rotated_at = datetime.now(timezone.utc)
        refresh_credential.revoked_at = None
        refresh_credential.expires_at = None  # refresh tokens themselves don't expire on Linear
        refresh_credential.updated_at = datetime.now(timezone.utc)
    elif refresh_credential is not None and refresh_credential.revoked_at is None:
        refresh_credential.revoked_at = datetime.now(timezone.utc)
        refresh_credential.updated_at = datetime.now(timezone.utc)

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


# ── Webhook provisioning ──────────────────────────────────────────


_LINEAR_WEBHOOK_RESOURCE_TYPES: tuple[str, ...] = ("Issue",)


class LinearWebhookProvisionOut(BaseModel):
    """Result of ``POST /integrations/linear/webhook/provision``."""

    provisioned: bool
    webhook_id: str
    url: str
    resource_types: list[str]
    # ``True`` when this call replaced an earlier registration (e.g.
    # operator re-ran provisioning after a backend URL change), so the
    # operator can see at a glance whether they need to remove the old
    # entry from Linear's webhook list. We make a best-effort delete
    # of the old one too, but Linear sometimes rejects the delete on
    # an orphaned webhook from a deleted OAuth app — log + continue.
    replaced_previous: bool


def _linear_webhook_url(settings: Settings) -> str:
    """Public URL Linear will POST deliveries to. Matches the route
    mounted in :mod:`linear_webhook` (``POST /v1/webhooks/linear``)."""
    base = settings.public_url.rstrip("/")
    return f"{base}/v1/webhooks/linear"


async def _fetch_live_linear_token(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> tuple[NativeIntegrationInstallation, str]:
    """Return (install, plaintext access_token) for the workspace's
    Linear connection. 404 / 412 if the workspace hasn't connected
    Linear yet or the credential is missing / decrypts to empty.
    """
    install = (
        await session.execute(
            select(NativeIntegrationInstallation).where(
                NativeIntegrationInstallation.workspace_id == workspace_id,
                NativeIntegrationInstallation.provider
                == NativeIntegrationProvider.LINEAR,
                NativeIntegrationInstallation.external_account_id == "default",
            )
        )
    ).scalar_one_or_none()
    if install is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "linear_not_connected",
                "message": (
                    "Connect Linear (OAuth) before provisioning the webhook."
                ),
            },
        )
    cred = (
        await session.execute(
            select(NativeIntegrationCredential)
            .where(
                NativeIntegrationCredential.installation_id == install.id,
                NativeIntegrationCredential.kind == "access_token",
                NativeIntegrationCredential.revoked_at.is_(None),
            )
            .order_by(NativeIntegrationCredential.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=412,
            detail={
                "code": "linear_token_missing",
                "message": (
                    "Linear access_token is not on file; "
                    "re-run the OAuth flow."
                ),
            },
        )
    token = safe_decrypt(cred.secret_ciphertext)
    if not token:
        raise HTTPException(
            status_code=412,
            detail={
                "code": "linear_token_unreadable",
                "message": (
                    "Linear access_token decrypted to empty; "
                    "re-run the OAuth flow."
                ),
            },
        )
    return install, token


async def _linear_team_id_for_workspace(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> str | None:
    """Pull the canonical ``team_id`` saved at OAuth time. ``None``
    means provisioning never set one — caller decides whether to
    register an org-scoped (no team filter) webhook or 422."""
    legacy = (
        await session.execute(
            select(Integration)
            .where(
                Integration.workspace_id == workspace_id,
                Integration.kind == "linear",
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if legacy and legacy.config:
        return legacy.config.get("team_id")
    return None


async def _call_linear_graphql(
    *, token: str, query: str, variables: dict
) -> dict:
    """Single Linear GraphQL request. Raises HTTPException on
    transport / GraphQL errors so the FastAPI handler returns a
    sensible status to the operator instead of a 500."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            "https://api.linear.app/graphql",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
        )
    if resp.status_code >= 500:
        raise HTTPException(
            status_code=502,
            detail=f"Linear upstream {resp.status_code}: {resp.text[:200]}",
        )
    body = resp.json()
    if body.get("errors"):
        raise HTTPException(
            status_code=502,
            detail=f"Linear GraphQL error: {body['errors'][:1]}",
        )
    return body.get("data") or {}


@router.post(
    "/workspaces/{workspace_id}/integrations/linear/webhook/provision",
    response_model=LinearWebhookProvisionOut,
)
async def linear_webhook_provision(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LinearWebhookProvisionOut:
    """Register a Linear webhook against this workspace's installation.

    Idempotent: if the workspace already has a ``webhook_id`` in its
    Integration config, we delete that one and create a fresh one
    (covers backend URL changes + secret rotations). The new id +
    url + resourceTypes are saved back to ``Integration.config``.

    Requires:
    - Workspace has completed Linear OAuth (an access token on file).
    - ``LINEAR_WEBHOOK_SECRET`` is configured on the backend.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_ADMIN
    )
    if not settings.linear_webhook_secret:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "linear_webhook_secret_missing",
                "message": (
                    "LINEAR_WEBHOOK_SECRET is not configured on this "
                    "deployment — set it before provisioning webhooks."
                ),
            },
        )

    _install, token = await _fetch_live_linear_token(
        session, workspace_id=workspace_id
    )
    team_id = await _linear_team_id_for_workspace(
        session, workspace_id=workspace_id
    )

    legacy = (
        await session.execute(
            select(Integration)
            .where(
                Integration.workspace_id == workspace_id,
                Integration.kind == "linear",
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one()  # OAuth flow always inserts one; missing → 500 is fine

    existing_webhook_id: str | None = None
    if isinstance(legacy.config, dict):
        existing_webhook_id = legacy.config.get("webhook_id")

    # Best-effort delete of any prior webhook. Re-provisioning is
    # almost always after a backend URL or secret change, so leaving
    # the previous registration in place would mean duplicate
    # deliveries from Linear.
    replaced = False
    if existing_webhook_id:
        try:
            await _call_linear_graphql(
                token=token,
                query="""mutation($id: String!) {
                  webhookDelete(id: $id) { success }
                }""",
                variables={"id": existing_webhook_id},
            )
            replaced = True
        except HTTPException as exc:
            # Don't block re-provisioning when Linear rejects the
            # delete of an orphaned webhook — log + keep going.
            logger.warning(
                "linear webhook: delete of previous id=%s failed: %s",
                existing_webhook_id, exc.detail,
            )

    public_url = _linear_webhook_url(settings)
    create_input: dict = {
        "url": public_url,
        "secret": settings.linear_webhook_secret,
        "resourceTypes": list(_LINEAR_WEBHOOK_RESOURCE_TYPES),
        "label": "Ship dispatcher",
    }
    if team_id:
        # Team-scoped webhook so Linear only delivers events from
        # this workspace's team. Avoids cross-workspace leakage if
        # the OAuth app is later attached to additional teams.
        create_input["teamId"] = team_id

    data = await _call_linear_graphql(
        token=token,
        query="""mutation($input: WebhookCreateInput!) {
          webhookCreate(input: $input) {
            success
            webhook { id url resourceTypes }
          }
        }""",
        variables={"input": create_input},
    )
    payload = (data.get("webhookCreate") or {})
    if not payload.get("success"):
        raise HTTPException(
            status_code=502,
            detail=(
                f"Linear webhookCreate returned success=false: "
                f"{payload}"
            ),
        )
    webhook = payload.get("webhook") or {}
    webhook_id = str(webhook.get("id") or "")
    if not webhook_id:
        raise HTTPException(
            status_code=502,
            detail="Linear webhookCreate did not return an id",
        )

    # Persist alongside the existing OAuth config so future
    # re-provisioning can find it.
    new_config = dict(legacy.config or {})
    new_config["webhook_id"] = webhook_id
    new_config["webhook_url"] = public_url
    new_config["webhook_resource_types"] = list(_LINEAR_WEBHOOK_RESOURCE_TYPES)
    legacy.config = new_config
    session.add(legacy)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="linear.webhook.provision",
            target_kind="integration",
            target_id=str(legacy.id),
            payload={
                "webhook_id": webhook_id,
                "url": public_url,
                "resource_types": list(_LINEAR_WEBHOOK_RESOURCE_TYPES),
                "team_scoped": bool(team_id),
                "replaced_previous": replaced,
            },
        )
    )
    await session.flush()

    return LinearWebhookProvisionOut(
        provisioned=True,
        webhook_id=webhook_id,
        url=public_url,
        resource_types=list(_LINEAR_WEBHOOK_RESOURCE_TYPES),
        replaced_previous=replaced,
    )


__all__ = ["router"]
