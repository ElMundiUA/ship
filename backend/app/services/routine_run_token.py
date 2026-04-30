"""Mint and verify per-routine-run JWTs.

A routine run hands a Cursor Cloud agent a short-lived bearer that lets
it talk back to Ship — comment on tickets, drop inbox items, etc. —
without ever holding a Linear/GitHub credential. The token is
HS256-signed with ``settings.jwt_secret`` and carries enough claims
(workspace, repo, ticket, agent) for the callback handler to know
*who* is acting and *where* it is allowed to write.

Mirrors the ``_mint_run_token`` helper in
:mod:`backend.app.api.v1.routes.pipelines` but with a distinct subject
and richer claims so a pipeline-callback bearer can't be replayed
against a routine-callback handler (and vice versa).
"""

from __future__ import annotations

import secrets
import time
import uuid
from typing import Final

from fastapi import HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from backend.app.core.config import Settings


_SUBJECT: Final[str] = "ship.routine.run"
_TTL_SECONDS: Final[int] = 60 * 60  # 1 hour — Cursor agents typically finish in <30 min


class RoutineRunClaims(BaseModel):
    """Decoded claims from a routine-run bearer."""

    workspace_id: uuid.UUID
    repo_id: uuid.UUID
    routine_id: str
    pattern: str
    agent_id: str | None = None
    # Optional ticket the run is bound to. ``None`` for context-free
    # routines (daily digest, learning capture). When set, callbacks
    # that mutate a tracker MUST target this ticket.
    ticket_id: str | None = None
    issued_at: int = Field(default_factory=lambda: int(time.time()))


def mint(*, claims: RoutineRunClaims, settings: Settings) -> str:
    issued = int(time.time())
    payload: dict = {
        "sub": _SUBJECT,
        "iat": issued,
        "exp": issued + _TTL_SECONDS,
        "nonce": secrets.token_urlsafe(8),
        "ws": str(claims.workspace_id),
        "repo": str(claims.repo_id),
        "routine": claims.routine_id,
        "pattern": claims.pattern,
    }
    if claims.agent_id:
        payload["agent"] = claims.agent_id
    if claims.ticket_id:
        payload["ticket"] = claims.ticket_id
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode(*, token: str, settings: Settings) -> RoutineRunClaims:
    try:
        decoded = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="routine token invalid or expired",
        ) from exc
    if decoded.get("sub") != _SUBJECT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="routine token has wrong subject",
        )
    try:
        return RoutineRunClaims(
            workspace_id=uuid.UUID(str(decoded["ws"])),
            repo_id=uuid.UUID(str(decoded["repo"])),
            routine_id=str(decoded["routine"]),
            pattern=str(decoded["pattern"]),
            agent_id=decoded.get("agent"),
            ticket_id=decoded.get("ticket"),
            issued_at=int(decoded.get("iat", 0)),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"routine token malformed: {exc}",
        ) from exc
