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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
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

# Every audit-action prefix emitted from `backend/app/api/v1/routes/*` lives
# here. Keeping this list explicit rather than "allow anything" serves two
# purposes: (1) a typo in the console filter surfaces as 422 instead of a
# silent empty page, (2) ops stumbling on this file gets a rough catalog of
# what we actually record without grepping every route module. When a new
# mutation module is added, extend this tuple so its filters work — the
# unit test `test_audit_log_rejects_unknown_action_filter` guards against
# accidentally loosening the check to "accept everything".
_ALLOWED_ACTION_PREFIXES = (
    "workspace.",
    "member.",
    "integration.",
    "artifact_repo.",
    "auth.",
    "pipeline.",
    "pipeline_run.",
    "repo.",
    "repos.",
    "invite.",
    "improvement.",
    "clarification.",
    "agent.",
    "agent_dispatch.",
    "process.",
)

# Target-kind values actually minted by the writer routes. Kept in lock-step
# with the `target_kind=` literals in `backend/app/api/v1/routes/*`.
_ALLOWED_TARGET_KINDS = frozenset(
    {
        "workspace",
        "user",
        "api_token",
        "integration",
        "artifact_repo",
        "pipeline",
        "pipeline_run",
        "workspace_repo",
        "workspace_repos",
        "workspace_invite",
        "github_installation",
        "improvement",
        "clarification",
        "process",
    }
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


def _validate_target_kind(target_kind: str | None) -> str | None:
    """Reject bogus ``?target_kind=`` the same way we do for actions."""
    if target_kind is None:
        return None
    candidate = target_kind.strip()
    if not candidate:
        return None
    if candidate not in _ALLOWED_TARGET_KINDS:
        raise HTTPException(
            status_code=422,
            detail=(
                "unknown target_kind filter; expected one of "
                f"{sorted(_ALLOWED_TARGET_KINDS)}"
            ),
        )
    return candidate


def _coerce_actor_filter(actor: str | None) -> str | None:
    """Normalise an actor filter into a lowercase substring.

    Empty strings / None are "no filter". We match case-insensitively on
    either the user email or the token name so operators can search for
    ``denis`` or ``ship-cli`` without memorising the exact casing.
    """
    if actor is None:
        return None
    trimmed = actor.strip().lower()
    return trimmed or None


def _coerce_datetime(value: str | None, field: str) -> datetime | None:
    """Parse an ISO-8601 ``since`` / ``until`` param.

    Accepts:
    - ``YYYY-MM-DD`` (date only, interpreted as UTC midnight)
    - Full ISO-8601 timestamps with or without timezone (naive → UTC)

    Returns ``None`` when the caller didn't pass the param or passed an
    empty string. Raises 422 with a helpful pointer otherwise so a
    typo'd date doesn't silently match everything.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    candidate = trimmed
    # Python's fromisoformat rejects the trailing "Z" that the HTML5
    # datetime-local picker can emit; accept it as UTC.
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"invalid {field}: expected ISO-8601 date/time, got {value!r}",
        ) from exc
    if parsed.tzinfo is None:
        # Treat naive input as UTC — the column is UTC-normalised on write,
        # and a naive compare on asyncpg would crash.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
    actor: str | None = Query(
        default=None,
        description=(
            "Case-insensitive substring match on actor user email or token "
            "name. Matches either (user email OR token name) so operators "
            "can hunt by identity without knowing which column carried it."
        ),
    ),
    target_kind: str | None = Query(
        default=None,
        description="Exact match on target_kind (e.g. 'user', 'pipeline').",
    ),
    since: str | None = Query(
        default=None,
        description="ISO-8601 lower bound (inclusive). Accepts 'YYYY-MM-DD' or full timestamp.",
    ),
    until: str | None = Query(
        default=None,
        description="ISO-8601 upper bound (exclusive). Accepts 'YYYY-MM-DD' or full timestamp.",
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AuditLogPage:
    """Return audit-log rows newest-first, paginated by descending ``id``.

    Admin/owner only. The ``id`` column is a BigInteger autoincrement so
    the cursor is monotonic even when wall-clocks skew across containers.

    All filters AND together: ``?action=member&actor=denis&since=2026-04-01``
    means "member.* rows authored by anyone whose email or token-name
    contains 'denis', on or after 2026-04-01 UTC".
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    action_filter = _validate_action_filter(action)
    target_filter = _validate_target_kind(target_kind)
    actor_filter = _coerce_actor_filter(actor)
    since_dt = _coerce_datetime(since, field="since")
    until_dt = _coerce_datetime(until, field="until")
    if since_dt is not None and until_dt is not None and since_dt > until_dt:
        raise HTTPException(
            status_code=422,
            detail="since must be earlier than or equal to until",
        )

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
    if target_filter is not None:
        stmt = stmt.where(AuditLog.target_kind == target_filter)
    if actor_filter is not None:
        needle = f"%{actor_filter}%"
        stmt = stmt.where(
            func.lower(actor_user.email).like(needle)
            | func.lower(actor_token.name).like(needle)
        )
    if since_dt is not None:
        stmt = stmt.where(AuditLog.created_at >= since_dt)
    if until_dt is not None:
        stmt = stmt.where(AuditLog.created_at < until_dt)

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
