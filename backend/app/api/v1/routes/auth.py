"""Authentication routes (RFC-0006).

Two flows are supported in v1:

- **Local mode** (``SHIP_AUTH_MODE=local``) — email + password signup/login,
  intended for laptop / self-hosted bootstrap. The first user becomes the
  org_owner of their auto-created personal Org.
- **PAT mint** (any mode) — once a user is authenticated (via session JWT or
  another PAT with the right scope), they can mint long-lived PATs for the
  CLI / CI. The raw secret is shown exactly once.

GitHub OAuth and OIDC providers slot into this same module as additional
sub-routers (``/auth/github/*``, ``/auth/oidc/*``) once their app credentials
are configured.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.schemas import (
    LocalLoginRequest,
    LocalSignupRequest,
    SessionTokenOut,
    TokenInfoOut,
    TokenMintOut,
    TokenMintRequest,
    UserOut,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.tenancy import (
    ApiToken,
    AuditLog,
    User,
    WorkspaceMember,
)
from backend.app.db.session import get_session
from backend.app.security.passwords import hash_password, verify_password
from backend.app.security.tokens import (
    PAT_PREFIX,
    generate_pat,
    hash_pat,
    mint_session_jwt,
)


router = APIRouter(prefix="/auth", tags=["auth"])


def _ensure_local_mode(settings: Settings) -> None:
    if settings.auth_mode != "local":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="local auth disabled (SHIP_AUTH_MODE != 'local')",
        )


def _session_response(user: User, settings: Settings) -> SessionTokenOut:
    token, expires_at = mint_session_jwt(
        user_id=user.id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.jwt_ttl_seconds,
    )
    return SessionTokenOut(
        access_token=token,
        expires_at=expires_at,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/local/signup",
    response_model=SessionTokenOut,
    status_code=status.HTTP_201_CREATED,
)
async def local_signup(
    payload: LocalSignupRequest,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> SessionTokenOut:
    _ensure_local_mode(settings)
    user = User(
        email=payload.email.lower(),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="email already registered") from exc
    session.add(
        AuditLog(
            actor_user_id=user.id,
            action="auth.signup",
            target_kind="user",
            target_id=str(user.id),
            payload={"mode": "local"},
        )
    )
    await session.flush()
    return _session_response(user, settings)


@router.post("/local/login", response_model=SessionTokenOut)
async def local_login(
    payload: LocalLoginRequest,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> SessionTokenOut:
    _ensure_local_mode(settings)
    stmt = select(User).where(User.email == payload.email.lower())
    user = (await session.execute(stmt)).scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or user.password_hash is None
        or not verify_password(payload.password, user.password_hash)
    ):
        # Generic error: do not leak whether the email exists.
        raise HTTPException(status_code=401, detail="invalid email or password")
    return _session_response(user, settings)


@router.get("/me", response_model=UserOut)
async def get_me(auth: AuthContext = Depends(get_current_auth)) -> UserOut:
    return UserOut.model_validate(auth.user)


# --- Personal access tokens ----------------------------------------------------


@router.post(
    "/tokens",
    response_model=TokenMintOut,
    status_code=status.HTTP_201_CREATED,
)
async def mint_token(
    payload: TokenMintRequest,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> TokenMintOut:
    """Mint a PAT for the current user.

    PATs minted via another PAT inherit the parent's workspace scoping
    constraints — i.e. you cannot use a workspace-scoped token to mint a
    broader token. Session JWTs (interactive logins) can mint freely.
    """
    if auth.token is not None and auth.token.workspace_id is not None:
        if payload.workspace_id is None or payload.workspace_id != auth.token.workspace_id:
            raise HTTPException(
                status_code=403,
                detail="workspace-scoped tokens may only mint within their own workspace",
            )

    if payload.workspace_id is not None:
        member_stmt = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == payload.workspace_id,
            WorkspaceMember.user_id == auth.user.id,
        )
        if (await session.execute(member_stmt)).scalar_one_or_none() is None:
            raise HTTPException(
                status_code=403, detail="not a member of the requested workspace"
            )

    raw = generate_pat()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=payload.ttl_days)
        if payload.ttl_days is not None
        else None
    )
    token = ApiToken(
        user_id=auth.user.id,
        workspace_id=payload.workspace_id,
        name=payload.name,
        hashed_secret=hash_pat(raw),
        prefix=PAT_PREFIX,
        scopes=payload.scopes,
        expires_at=expires_at,
    )
    session.add(token)
    await session.flush()
    session.add(
        AuditLog(
            workspace_id=token.workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="auth.token.mint",
            target_kind="api_token",
            target_id=str(token.id),
            payload={"name": token.name, "scopes": token.scopes},
        )
    )
    await session.flush()
    return TokenMintOut(
        id=token.id,
        name=token.name,
        workspace_id=token.workspace_id,
        scopes=list(token.scopes),
        expires_at=token.expires_at,
        secret=raw,
        created_at=token.created_at,
    )


@router.get("/tokens", response_model=list[TokenInfoOut])
async def list_tokens(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[TokenInfoOut]:
    stmt = (
        select(ApiToken)
        .where(ApiToken.user_id == auth.user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [TokenInfoOut.model_validate(row) for row in rows]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke a PAT owned by the current user.

    Soft-delete: we set ``revoked_at`` instead of dropping the row so the
    audit log keeps a record of the token having existed (and so the
    PAT-resolution path returns a clean "invalid or revoked token" 401 even
    if the operator still has the secret cached locally).
    """
    stmt = select(ApiToken).where(
        ApiToken.id == token_id, ApiToken.user_id == auth.user.id
    )
    token = (await session.execute(stmt)).scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="token not found")
    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        session.add(
            AuditLog(
                workspace_id=token.workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="auth.token.revoke",
                target_kind="api_token",
                target_id=str(token.id),
                payload={"name": token.name},
            )
        )
        await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
