"""Workspace integrations & encrypted secrets (RFC-0006).

Each row in :class:`Integration` represents a third-party connection (Linear,
Slack, GitHub PR webhook, OpenTelemetry exporter, …). The plaintext secret is
encrypted at rest with the :mod:`backend.app.security.encryption` Fernet key
and never returned by the API — only ``has_secret`` is exposed.

Routes here are admin-only because integrations are tenant-wide credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_ADMIN, _require_membership
from backend.app.api.v1.schemas import (
    INTEGRATION_KINDS,
    IntegrationOut,
    IntegrationUpsert,
)
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.db.session import get_session
from backend.app.security.encryption import decrypt, encrypt
from backend.app.services.secret_probe import probe_one


router = APIRouter(
    prefix="/workspaces/{workspace_id}/integrations",
    tags=["integrations"],
)


def _row_to_out(row: Integration) -> IntegrationOut:
    return IntegrationOut(
        id=row.id,
        workspace_id=row.workspace_id,
        kind=row.kind,
        config=row.config or {},
        status=row.status,
        has_secret=row.secret_ciphertext is not None,
        last_health_at=row.last_health_at,
        last_health_error=row.last_health_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_kind(kind: str) -> None:
    if kind not in INTEGRATION_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown integration kind '{kind}'. Allowed: {list(INTEGRATION_KINDS)}",
        )


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[IntegrationOut]:
    # Admins see config + has_secret; we don't expose this list to viewers
    # because some `config` entries may contain quasi-sensitive metadata
    # (channel ids, account names, etc.) that orgs sometimes treat as
    # confidential.
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    stmt = (
        select(Integration)
        .where(Integration.workspace_id == workspace_id)
        .order_by(Integration.kind)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_out(row) for row in rows]


@router.put(
    "/{kind}",
    response_model=IntegrationOut,
    status_code=status.HTTP_200_OK,
)
async def upsert_integration(
    workspace_id: uuid.UUID,
    kind: str,
    payload: IntegrationUpsert,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    """Idempotent create-or-update for a single integration kind.

    PUT is the right verb here: ``(workspace_id, kind)`` is a natural
    composite key (one Slack, one Linear, etc. per workspace), and clients
    don't need to know the row's UUID to edit or replace it. Send
    ``secret = null`` to keep the existing ciphertext while editing config.

    When a non-empty secret is provided the response carries the synchronous
    probe verdict (``status`` is ``ok`` or ``error``, ``last_health_at`` is
    populated). The legacy "wait for the worker to flip pending to ok" loop
    is gone — there is no worker in the cloud SaaS topology.
    """
    if payload.kind != kind:
        raise HTTPException(
            status_code=422,
            detail=f"path kind '{kind}' does not match payload kind '{payload.kind}'",
        )
    _validate_kind(kind)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    stmt = select(Integration).where(
        Integration.workspace_id == workspace_id,
        Integration.kind == kind,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()

    is_new = row is None
    if is_new:
        row = Integration(workspace_id=workspace_id, kind=kind, config=payload.config or {})
        session.add(row)
    else:
        row.config = payload.config or {}

    probed_status: str | None = None
    probed_message: str | None = None
    if payload.secret is not None:
        # Empty string means "clear the secret" — useful when the operator
        # wants to disable an integration without losing its config.
        row.secret_ciphertext = encrypt(payload.secret) if payload.secret else None
        if payload.secret:
            # Cloud SaaS has no background worker, so we probe inline and
            # return the verdict in the same response — the operator sees
            # ok/error instantly instead of waiting for the next cron tick.
            probed_status, probed_message = await probe_one(
                kind, payload.secret, payload.config or {}
            )
            row.status = probed_status
            row.last_health_at = datetime.now(timezone.utc)
            row.last_health_error = probed_message
        else:
            row.status = "pending"
            row.last_health_at = None
            row.last_health_error = None
    elif is_new:
        row.status = "pending"

    row.updated_at = datetime.now(timezone.utc)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="integration already exists for this kind"
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="integration.create" if is_new else "integration.update",
            target_kind="integration",
            target_id=str(row.id),
            # Never log the secret. We do log whether one was rotated so an
            # auditor can prove "secret last touched at T by U" without ever
            # having seen the value.
            payload={
                "kind": kind,
                "config_keys": sorted((payload.config or {}).keys()),
                "secret_rotated": payload.secret is not None,
                "secret_cleared": payload.secret == "",
                # Record the probe verdict in the audit trail so operators can
                # later prove "save was confirmed ok at T" without joining
                # against the integration row.
                "probe_status": probed_status,
            },
        )
    )
    await session.flush()
    return _row_to_out(row)


@router.post("/{kind}/probe", response_model=IntegrationOut)
async def probe_integration(
    workspace_id: uuid.UUID,
    kind: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    """Run the configured health probe right now and persist the verdict.

    This is the "tell me whether the secret I just saved is still good"
    button in the console. It runs the same per-kind validator as the
    background worker but inline, so the operator gets an instant answer
    without waiting for the next cron tick. Result is cached in
    ``status``/``last_health_at``/``last_health_error`` exactly the same way
    the worker does, so there's no drift between manual and automatic runs.
    """
    _validate_kind(kind)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    stmt = select(Integration).where(
        Integration.workspace_id == workspace_id, Integration.kind == kind
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="integration not found")
    if row.secret_ciphertext is None:
        # Honest no-op: there's nothing to probe. Surface as a 409 so the UI
        # can prompt the operator to save a secret first.
        raise HTTPException(
            status_code=409, detail="integration has no secret stored to probe"
        )

    try:
        plaintext = decrypt(row.secret_ciphertext)
    except Exception:  # noqa: BLE001 — InvalidToken or any decryption failure
        # The ciphertext was written under a different ENCRYPTION_KEY than we
        # have now. Mark the row as error so the operator notices, but don't
        # leak crypto details to the client.
        new_status, new_message = (
            "error",
            "stored secret cannot be decrypted with current ENCRYPTION_KEY",
        )
    else:
        new_status, new_message = await probe_one(kind, plaintext, row.config or {})

    row.status = new_status
    row.last_health_at = datetime.now(timezone.utc)
    row.last_health_error = new_message
    await session.flush()
    # Refresh explicitly: some session configurations expire columns after a
    # flush that touched them, and Pydantic's ``from_attributes`` would then
    # trigger sync IO when reading the row — boom, ``MissingGreenlet``.
    await session.refresh(row)
    return _row_to_out(row)


@router.delete("/{kind}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    workspace_id: uuid.UUID,
    kind: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    stmt = select(Integration).where(
        Integration.workspace_id == workspace_id,
        Integration.kind == kind,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="integration not found")
    await session.delete(row)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="integration.delete",
            target_kind="integration",
            target_id=str(row.id),
            payload={"kind": kind},
        )
    )
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
