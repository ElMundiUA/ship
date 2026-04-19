"""Workspace audit-log read API (RFC-0006, Phase 2.5).

Audit rows are written by the mutation routes (``workspaces.py``,
``members.py``, ``auth.py``, ``integrations.py``, ``artifact_repos.py``);
this module is read-only on purpose so a misconfigured client cannot
forge an entry.

Authorisation:

- Listing requires a workspace **admin** or **owner**. The audit log
  exposes who-did-what across every member (including PAT activity), so
  members and viewers must not see it. Use ``ROLES_ADMIN`` from
  :mod:`backend.app.api.v1.routes.workspaces` for symmetry with the rest
  of the privileged surface.

Pagination:

- Cursor-based on ``id`` (descending), defaulting to ``limit=50``,
  ``max_limit=200``. Cursors are stable — newer rows do not shift older
  ones — so a long-running export script can resume cleanly.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    _require_membership,
)
from backend.app.api.v1.schemas import (
    AuditLogActorOut,
    AuditLogEntryOut,
    AuditLogPage,
)
from backend.app.db.models.tenancy import ApiToken, AuditLog, User
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/audit-log",
    tags=["audit-log"],
)


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_ALLOWED_ACTION_PREFIXES = (
    "workspace.",
    "member.",
    "integration.",
    "artifact_repo.",
    "auth.",
)


def _validate_action_filter(action: str | None) -> str | None:
    """Reject obviously bogus ``?action=`` filters early.

    We accept either a known prefix (``member.``) or a fully-qualified
    action (``member.invite``). Anything else is a typo by the caller and
    we surface it as 422 instead of silently returning an empty page.
    """
    if action is None:
        return None
    candidate = action.strip()
    if not candidate:
        return None
    if any(
        candidate == prefix.rstrip(".")
        or candidate == prefix
        or candidate.startswith(prefix)
        for prefix in _ALLOWED_ACTION_PREFIXES
    ):
        return candidate
    raise HTTPException(
        status_code=422,
        detail=(
            "unknown action filter; expected one of "
            f"{[p.rstrip('.') for p in _ALLOWED_ACTION_PREFIXES]} or a fully-qualified value"
        ),
    )


@router.get("", response_model=AuditLogPage)
async def list_audit_log(
    workspace_id: uuid.UUID,
    before: int | None = Query(
        default=None,
        ge=1,
        description="Return rows with id < before (cursor for the next page).",
    ),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    action: str | None = Query(
        default=None,
        description="Filter by action prefix (e.g. 'member') or fully-qualified value (e.g. 'member.invite').",
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AuditLogPage:
    """Return audit-log rows newest-first, paginated by descending ``id``.

    Admin/owner only. The ``id`` column is a BigInteger autoincrement so
    the cursor is monotonic even when wall-clocks skew across containers.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    action_filter = _validate_action_filter(action)

    actor_user = aliased(User)
    actor_token = aliased(ApiToken)
    stmt = (
        select(AuditLog, actor_user, actor_token)
        .outerjoin(actor_user, actor_user.id == AuditLog.actor_user_id)
        .outerjoin(actor_token, actor_token.id == AuditLog.actor_token_id)
        .where(AuditLog.workspace_id == workspace_id)
        .order_by(AuditLog.id.desc())
        # +1 so we can tell whether another page exists without a COUNT(*).
        .limit(limit + 1)
    )
    if before is not None:
        stmt = stmt.where(AuditLog.id < before)
    if action_filter is not None:
        # Accept both prefix ("member") and fully-qualified ("member.invite")
        # forms. SQLAlchemy's `like` keeps this readable; the index on
        # `(workspace_id, created_at)` is still used for the workspace
        # filter, so the LIKE only narrows the candidate set.
        stmt = stmt.where(
            (AuditLog.action == action_filter)
            | AuditLog.action.like(f"{action_filter}.%")
            | AuditLog.action.like(f"{action_filter}%")
        )

    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    visible = rows[:limit]

    items: list[AuditLogEntryOut] = []
    for entry, user, token in visible:
        items.append(
            AuditLogEntryOut(
                id=entry.id,
                action=entry.action,
                target_kind=entry.target_kind,
                target_id=entry.target_id,
                payload=entry.payload or {},
                created_at=entry.created_at,
                actor=AuditLogActorOut(
                    user_id=user.id if user is not None else None,
                    user_email=user.email if user is not None else None,
                    token_id=token.id if token is not None else None,
                    token_name=token.name if token is not None else None,
                ),
            )
        )

    next_cursor = items[-1].id if has_more and items else None
    return AuditLogPage(items=items, next_cursor=next_cursor)
