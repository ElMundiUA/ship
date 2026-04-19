"""FastAPI dependencies for the v1 API.

Authentication for v1 supports two token shapes:

1. **Session JWT** — issued by the web UI flow, signed with ``JWT_SECRET``.
2. **Personal Access Token** — long-lived, opaque, prefixed ``ship_pat_``.

Both arrive in ``Authorization: Bearer <token>``. The dependency resolves
either to a :class:`User`, optionally bound to a workspace via the token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.tenancy import ApiToken, User, WorkspaceMember
from backend.app.db.session import get_session
from backend.app.security.tokens import PAT_PREFIX, hash_pat


@dataclass(slots=True)
class AuthContext:
    """Result of authenticating a request."""

    user: User
    token: ApiToken | None  # None for session JWT auth
    scopes: frozenset[str]


# Backwards-compatible alias for code/tests written against the old name.
_hash_token = hash_pat


async def _resolve_pat(session: AsyncSession, raw: str) -> AuthContext:
    digest = hash_pat(raw)
    stmt = select(ApiToken).where(ApiToken.hashed_secret == digest)
    token = (await session.execute(stmt)).scalar_one_or_none()
    if token is None or token.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or revoked token"
        )
    if token.expires_at is not None and token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired"
        )
    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user disabled"
        )
    token.last_used_at = datetime.now(timezone.utc)
    return AuthContext(user=user, token=token, scopes=frozenset(token.scopes or []))


async def _resolve_jwt(
    session: AsyncSession, raw: str, settings: Settings
) -> AuthContext:
    try:
        claims = jwt.decode(raw, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session"
        ) from exc
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="malformed session"
        )
    user = await session.get(User, sub)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user disabled"
        )
    return AuthContext(user=user, token=None, scopes=frozenset(claims.get("scopes", [])))


async def get_current_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raw = authorization.split(" ", 1)[1].strip()
    if raw.startswith(PAT_PREFIX):
        return await _resolve_pat(session, raw)
    return await _resolve_jwt(session, raw, settings)


async def get_current_user(auth: AuthContext = Depends(get_current_auth)) -> User:
    return auth.user


async def require_workspace_role(
    workspace_id: str,
    *,
    accept: tuple[str, ...] = ("owner", "admin", "maintainer", "member", "viewer"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceMember:
    """Assert that the authenticated user is a member of ``workspace_id``
    with one of the accepted roles. Returns the membership row for callers
    that need it (e.g. to record audit context).
    """
    if auth.token is not None and auth.token.workspace_id is not None:
        if str(auth.token.workspace_id) != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="token is scoped to a different workspace",
            )
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == auth.user.id,
    )
    membership = (await session.execute(stmt)).scalar_one_or_none()
    if membership is None or membership.role not in accept:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient role for this workspace",
        )
    return membership


__all__ = [
    "AuthContext",
    "PAT_PREFIX",
    "get_current_auth",
    "get_current_user",
    "require_workspace_role",
]
