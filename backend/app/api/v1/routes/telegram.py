"""Telegram bot bind + management endpoints (Console-facing).

Flow:

    1. Admin types ``/link`` in a Telegram group; the bot mints a
       short-lived nonce locally and DMs the admin a deep-link to
       Console: ``console.ship/integrations/telegram/bind?nonce=…``.
    2. Console calls :func:`bind_preview` to show the chat title +
       expiry, then asks the user to pick a workspace.
    3. Console calls :func:`bind_confirm` with ``{nonce, workspace_id}``.
       This route creates / updates the ``telegram_chat_link`` row and
       mints a workspace-scoped service PAT under which the bot calls
       Navigator on behalf of this group.
    4. List + delete endpoints power the "Linked Telegram groups"
       Console settings page; deleting a link revokes the PAT first
       so the bot stops answering immediately.

Identity model is shared workspace — no per-user mapping.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.tenancy import ApiToken, AuditLog
from backend.app.db.models.telegram import TelegramChatLink
from backend.app.db.session import get_session
from backend.app.integrations.telegram.bind_state import (
    InvalidBindNonce,
    verify_bind_nonce,
)
from backend.app.security.encryption import encrypt
from backend.app.security.tokens import (
    PAT_PREFIX,
    generate_pat,
    hash_pat,
)


router = APIRouter(prefix="/integrations/telegram", tags=["telegram"])


# Scope the bot's PAT carries. Narrow on purpose — this token sits in
# the bot worker's process memory and any leak should not give read
# access to anything outside the chat surface.
_BOT_PAT_SCOPES: list[str] = ["chat:read", "chat:write"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BindPreviewOut(BaseModel):
    chat_id: int
    chat_title: str | None
    expires_at: datetime
    # If True, the chat is already bound to some workspace — the
    # confirm step will overwrite it. The Console surfaces this so the
    # admin doesn't silently steal a chat from another team.
    already_bound_workspace_id: uuid.UUID | None


class BindConfirmIn(BaseModel):
    nonce: str
    workspace_id: uuid.UUID


class TelegramLinkOut(BaseModel):
    id: uuid.UUID
    telegram_chat_id: int
    title: str | None
    workspace_id: uuid.UUID
    linked_by_user_id: uuid.UUID
    has_active_pat: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/bind/preview", response_model=BindPreviewOut)
async def bind_preview(
    nonce: str = Query(..., min_length=10),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BindPreviewOut:
    """Show the user what chat they're about to bind, before they pick a workspace."""
    _ = auth  # auth required to gate the endpoint, identity not used here
    try:
        decoded = verify_bind_nonce(nonce, settings=settings)
    except InvalidBindNonce as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = (
        await session.execute(
            select(TelegramChatLink).where(
                TelegramChatLink.telegram_chat_id == decoded.chat_id
            )
        )
    ).scalar_one_or_none()

    return BindPreviewOut(
        chat_id=decoded.chat_id,
        chat_title=decoded.chat_title,
        expires_at=datetime.fromtimestamp(decoded.expires_at, tz=timezone.utc),
        already_bound_workspace_id=(
            existing.workspace_id if existing is not None else None
        ),
    )


@router.post(
    "/bind/confirm",
    response_model=TelegramLinkOut,
    status_code=status.HTTP_201_CREATED,
)
async def bind_confirm(
    payload: BindConfirmIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TelegramLinkOut:
    """Bind a Telegram chat to a workspace + mint the bot's service PAT.

    Admin-only on the *target* workspace. Re-binding an already-linked
    chat to a different workspace is allowed (admin choice) but revokes
    the prior PAT so the previous workspace's bridge stops answering.
    """
    try:
        decoded = verify_bind_nonce(payload.nonce, settings=settings)
    except InvalidBindNonce as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _require_membership(
        session, payload.workspace_id, auth.user.id, ROLES_ADMIN
    )

    # Mint the service PAT first so we can persist its id on the link
    # row in a single flush.
    raw_pat = generate_pat()
    pat = ApiToken(
        user_id=auth.user.id,
        workspace_id=payload.workspace_id,
        name=(
            f"telegram-bot:{decoded.chat_title}"
            if decoded.chat_title
            else f"telegram-bot:{decoded.chat_id}"
        )[:120],
        hashed_secret=hash_pat(raw_pat),
        prefix=PAT_PREFIX,
        scopes=_BOT_PAT_SCOPES,
    )
    session.add(pat)
    await session.flush()

    existing = (
        await session.execute(
            select(TelegramChatLink).where(
                TelegramChatLink.telegram_chat_id == decoded.chat_id
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    pat_ciphertext = encrypt(raw_pat)

    if existing is None:
        link = TelegramChatLink(
            telegram_chat_id=decoded.chat_id,
            workspace_id=payload.workspace_id,
            linked_by_user_id=auth.user.id,
            service_pat_id=pat.id,
            title=decoded.chat_title,
            pat_secret_ciphertext=pat_ciphertext,
        )
        session.add(link)
        action = "telegram.link.create"
    else:
        # Revoke the prior PAT so the old workspace's bridge falls
        # silent immediately, then point the link at the new PAT.
        if existing.service_pat_id is not None:
            old_pat = (
                await session.execute(
                    select(ApiToken).where(ApiToken.id == existing.service_pat_id)
                )
            ).scalar_one_or_none()
            if old_pat is not None and old_pat.revoked_at is None:
                old_pat.revoked_at = now
        existing.workspace_id = payload.workspace_id
        existing.linked_by_user_id = auth.user.id
        existing.service_pat_id = pat.id
        existing.title = decoded.chat_title
        existing.pat_secret_ciphertext = pat_ciphertext
        existing.updated_at = now
        link = existing
        action = "telegram.link.update"

    await session.flush()

    session.add(
        AuditLog(
            workspace_id=link.workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action=action,
            target_kind="telegram_chat_link",
            target_id=str(link.id),
            payload={
                "telegram_chat_id": link.telegram_chat_id,
                "telegram_chat_title": link.title,
            },
        )
    )
    await session.flush()

    return TelegramLinkOut(
        id=link.id,
        telegram_chat_id=link.telegram_chat_id,
        title=link.title,
        workspace_id=link.workspace_id,
        linked_by_user_id=link.linked_by_user_id,
        has_active_pat=True,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


@router.get("/links", response_model=list[TelegramLinkOut])
async def list_links(
    workspace_id: uuid.UUID = Query(...),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[TelegramLinkOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    rows = (
        await session.execute(
            select(TelegramChatLink, ApiToken)
            .outerjoin(ApiToken, ApiToken.id == TelegramChatLink.service_pat_id)
            .where(TelegramChatLink.workspace_id == workspace_id)
            .order_by(TelegramChatLink.created_at.desc())
        )
    ).all()
    return [
        TelegramLinkOut(
            id=link.id,
            telegram_chat_id=link.telegram_chat_id,
            title=link.title,
            workspace_id=link.workspace_id,
            linked_by_user_id=link.linked_by_user_id,
            has_active_pat=(pat is not None and pat.revoked_at is None),
            created_at=link.created_at,
            updated_at=link.updated_at,
        )
        for link, pat in rows
    ]


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    link_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    link = (
        await session.execute(
            select(TelegramChatLink).where(TelegramChatLink.id == link_id)
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    await _require_membership(session, link.workspace_id, auth.user.id, ROLES_ADMIN)

    now = datetime.now(timezone.utc)
    if link.service_pat_id is not None:
        pat = (
            await session.execute(
                select(ApiToken).where(ApiToken.id == link.service_pat_id)
            )
        ).scalar_one_or_none()
        if pat is not None and pat.revoked_at is None:
            pat.revoked_at = now

    workspace_id = link.workspace_id
    chat_id = link.telegram_chat_id
    await session.delete(link)
    await session.flush()

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="telegram.link.delete",
            target_kind="telegram_chat_link",
            target_id=str(link_id),
            payload={"telegram_chat_id": chat_id},
        )
    )
    await session.flush()


__all__ = ["router"]
