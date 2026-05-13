"""Per-repo tracker binding (Wizard v2 iter 4).

The Wizard v2 "pick tracker per repo" step needs exactly three
operations, all scoped to ``/workspaces/{ws}/repos/{repo}/tracker``:

1. ``GET`` — "which tracker does this repo route its tickets
   to?". Returns the per-repo ``Integration`` row if one exists
   (``kind`` + ``config``), otherwise the workspace-level default
   for the same kind if one exists, otherwise ``{kind: null}``.
   The ``source`` field (``"repo"`` / ``"workspace"`` / ``"none"``)
   tells the UI which layer produced the answer so it can render
   "inherited" badges accurately.

2. ``PUT`` — idempotent create-or-update of the per-repo binding.
   Body is ``{kind, config}``; we pin ``repo_id`` from the path so
   the partial-unique index ``uq_integrations_ws_repo_kind`` fires
   instead of ``uq_integrations_ws_kind_global``. No ``secret``
   field: per-repo tracker rows ride on the **workspace-level**
   OAuth connection for the same kind (it holds the token). That
   keeps "one Linear OAuth connection, many repo bindings" working
   without re-sharing tokens.

3. ``DELETE`` — remove the per-repo row so the repo falls back to
   the workspace-level default (or to "no tracker" if none).

Scope boundaries worth noting
-----------------------------

- Linear / Notion OAuth remain workspace-level. Iter 4 does not
  add per-repo OAuth flows. The wizard drives users through the
  existing workspace-level install first and then binds repos to
  team/project ids it reads from the Linear/Jira API using the
  workspace token.
- Valid kinds here are a **strict subset** of the general
  integrations table: only ticket-router kinds (``linear`` /
  ``github`` / ``jira``). Anything else (``slack`` / ``otlp`` /
  ``notion``) stays workspace-level and is rejected.
- ``kind=github`` means "file issues on this GitHub repo via the
  App installation we already have". No extra config is required
  — the repo row itself is the destination — but the binding row
  exists so reconciliation logic can answer "where do I file
  this?" with one lookup instead of walking integrations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos/{repo_id}/tracker",
    tags=["tracker-binding"],
)


# Ticket-router kinds only. Everything else in ``INTEGRATION_KINDS``
# stays workspace-level — per-repo Slack bindings etc. would be an
# RFC of their own.
TRACKER_KINDS: frozenset[str] = frozenset({"linear", "github", "jira"})


# Tracker kinds whose per-repo binding requires a workspace-level OAuth
# row to inherit auth from. ``github`` is excluded because it rides on
# the GitHub App installation token, not on a separate OAuth row in
# ``integrations``. ``jira`` is excluded because Jira lives on a PAT
# stored in a per-tenant Atlassian native-integration row, also not in
# ``integrations``.
#
# Without this gate, an operator can save a per-repo Linear/Notion
# binding before the workspace-level OAuth lands. The tracker resolver
# then can't find a token, every downstream call returns 401, and the
# operator sees an "ok"-coloured binding that does nothing — the exact
# state the dogfood transcript on 2026-05-02 caught. Returning 412 here
# stops that path at write time.
_OAUTH_INHERITED_TRACKER_KINDS: frozenset[str] = frozenset({"linear", "notion"})


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TrackerBindingOut(BaseModel):
    """Per-repo tracker binding with inheritance metadata."""

    repo_id: uuid.UUID
    kind: str | None = None
    config: dict = Field(default_factory=dict)
    # ``repo`` (per-repo row exists), ``workspace`` (falling back to
    # the workspace-level default), or ``none`` (no binding at any
    # level). The UI renders an "inherited" badge when ``source ==
    # "workspace"`` so operators can tell "this repo chose Linear"
    # from "this repo inherits Linear from the workspace".
    source: str
    # Echo the workspace-level default kind so the UI can show
    # "inheriting from workspace default" even when the repo has
    # its own different kind set — handy for the "override /
    # inherit" toggle on the wizard.
    workspace_default_kind: str | None = None
    # OAuth-discovered metadata from the workspace-level row, surfaced
    # so the wizard can replace the legacy "type a team key" text
    # input with a real dropdown. Today this is just ``team_options``
    # (Linear); Notion will land its database picker here too once
    # the equivalent capability lands. Empty dict when the workspace
    # row has no extra metadata to share.
    workspace_default_config: dict = Field(default_factory=dict)
    # ``True`` when the workspace-level tracker row for the matching
    # kind has a usable OAuth token (``secret_ciphertext`` populated).
    # The wizard uses this to disable the Linear/Notion pills with a
    # "connect at the workspace level first" deeplink instead of
    # quietly 412-ing on save.
    workspace_oauth_connected: bool = False


class TrackerBindingIn(BaseModel):
    """PUT body — the wizard's tracker step."""

    kind: str = Field(min_length=1, max_length=32)
    config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_kind(self) -> "TrackerBindingIn":
        if self.kind not in TRACKER_KINDS:
            allowed = ", ".join(sorted(TRACKER_KINDS))
            raise ValueError(
                f"unsupported tracker kind {self.kind!r}; expected one of: {allowed}"
            )
        return self


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_repo(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> WorkspaceRepo:
    row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="repo not found"
        )
    return row


async def _repo_binding(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> Integration | None:
    """First per-repo tracker row for the repo, if any.

    We don't filter by kind because a repo binds to exactly one
    tracker at a time — the wizard PUT overwrites the existing row
    in-place rather than creating parallel rows for different
    kinds. If somebody writes a Jira row then overwrites with a
    Linear row, the Jira row is deleted in the same PUT.
    """

    stmt = (
        select(Integration)
        .where(
            Integration.workspace_id == workspace_id,
            Integration.repo_id == repo_id,
            Integration.kind.in_(TRACKER_KINDS),
        )
        .order_by(Integration.updated_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _workspace_oauth_meta(
    session: AsyncSession, workspace_id: uuid.UUID, kind: str | None
) -> tuple[bool, dict]:
    """Return ``(oauth_connected, public_config)`` for the workspace-
    level row of ``kind``.

    ``oauth_connected`` is True when the row exists AND has a
    ``secret_ciphertext`` (i.e. the OAuth callback actually completed
    or — for PAT providers — a token was saved). The wizard uses this
    to disable the Linear/Notion pills when the workspace row hasn't
    been OAuth-connected.

    ``public_config`` is the safe-to-expose slice of the workspace
    row's ``config``. Today that's the ``team_options`` list saved by
    :mod:`backend.app.api.v1.routes.linear_oauth` after the OAuth
    callback so the FE can render a real team picker. Anything else
    gets stripped — the workspace config can carry secrets-adjacent
    metadata (channel ids, scope strings) that doesn't belong on this
    surface.
    """
    if kind is None:
        return False, {}
    row = (
        await session.execute(
            select(Integration)
            .where(
                Integration.workspace_id == workspace_id,
                Integration.repo_id.is_(None),
                Integration.kind == kind,
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return False, {}
    connected = row.secret_ciphertext is not None
    public: dict = {}
    config = row.config or {}
    team_options = config.get("team_options")
    if isinstance(team_options, list):
        public["team_options"] = team_options
    return connected, public


async def _workspace_default_kind(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[str | None, dict]:
    """Most-recently-updated workspace-level tracker row.

    Workspaces technically can have more than one of the tracker
    kinds (they might have Linear OAuth + a manual Jira). The
    "default" we report is the last-touched one — in practice
    operators only configure one because the old UI allowed one
    workspace-level row per kind. A tie here is harmless because
    the wizard shows the kind explicitly; the "inherited" badge
    just needs *some* answer to render.
    """

    stmt = (
        select(Integration)
        .where(
            Integration.workspace_id == workspace_id,
            Integration.repo_id.is_(None),
            Integration.kind.in_(TRACKER_KINDS),
        )
        .order_by(Integration.updated_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None, {}
    return row.kind, row.config or {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=TrackerBindingOut)
async def get_tracker_binding(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> TrackerBindingOut:
    """Report which tracker this repo routes tickets to.

    Falls back to the workspace-level default when no per-repo row
    exists. ``source`` tells the UI which layer produced the
    answer so it can render "Inherited from workspace default" vs
    "Repo-specific" without a second round-trip.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _resolve_repo(session, workspace_id, repo_id)

    ws_kind, _ = await _workspace_default_kind(session, workspace_id)

    row = await _repo_binding(session, workspace_id, repo_id)
    if row is not None:
        connected, public_cfg = await _workspace_oauth_meta(
            session, workspace_id, row.kind
        )
        return TrackerBindingOut(
            repo_id=repo_id,
            kind=row.kind,
            config=row.config or {},
            source="repo",
            workspace_default_kind=ws_kind,
            workspace_default_config=public_cfg,
            workspace_oauth_connected=connected,
        )

    if ws_kind is not None:
        _, ws_config = await _workspace_default_kind(session, workspace_id)
        connected, public_cfg = await _workspace_oauth_meta(
            session, workspace_id, ws_kind
        )
        return TrackerBindingOut(
            repo_id=repo_id,
            kind=ws_kind,
            config=ws_config,
            source="workspace",
            workspace_default_kind=ws_kind,
            workspace_default_config=public_cfg,
            workspace_oauth_connected=connected,
        )

    return TrackerBindingOut(
        repo_id=repo_id,
        kind=None,
        config={},
        source="none",
        workspace_default_kind=None,
    )


@router.put("", response_model=TrackerBindingOut)
async def set_tracker_binding(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: TrackerBindingIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> TrackerBindingOut:
    """Create or replace the per-repo tracker row.

    Idempotent: one PUT per wizard "save" click updates in place
    when a row exists, creates fresh when it doesn't. Changing
    ``kind`` (e.g. Linear → Jira) is treated as an overwrite of
    the same logical binding — the old row is deleted so the
    repo never has two conflicting tracker rows at once.

    We do **not** touch the workspace-level row here. If the
    caller wants to route the whole workspace to Jira, they edit
    the workspace-level integration via ``/v1/workspaces/{ws}/integrations/jira``
    in the normal way; per-repo rows override but don't reshape it.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    await _resolve_repo(session, workspace_id, repo_id)

    # Per-repo Linear/Notion bindings inherit auth from the workspace-
    # level OAuth row. Reject the bind when no such row exists — a
    # token-less binding would render every downstream tracker call
    # 401 silently. The error code is stable so the FE can swap in a
    # "Connect Linear at the workspace level first" CTA + deeplink.
    if payload.kind in _OAUTH_INHERITED_TRACKER_KINDS:
        ws_oauth_row = (
            await session.execute(
                select(Integration.id)
                .where(
                    Integration.workspace_id == workspace_id,
                    Integration.repo_id.is_(None),
                    Integration.kind == payload.kind,
                    Integration.secret_ciphertext.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if ws_oauth_row is None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={
                    "code": "workspace_oauth_required",
                    "message": (
                        f"Cannot bind {payload.kind} per-repo before the "
                        f"workspace-level {payload.kind} OAuth is "
                        "connected. Run "
                        f"POST /v1/integrations/{payload.kind}/install/start "
                        "first."
                    ),
                    "kind": payload.kind,
                },
            )

    # Delete any existing per-repo tracker rows first so switching
    # kinds doesn't leave stale rows behind. We don't rely on the
    # unique index to handle this because the index is keyed on
    # (workspace, repo, kind) and changing kind would insert a
    # *second* row, not replace.
    stale = await _repo_binding(session, workspace_id, repo_id)
    if stale is not None and stale.kind != payload.kind:
        await session.delete(stale)
        await session.flush()
        stale = None

    row = stale
    is_new = row is None
    if row is None:
        row = Integration(
            workspace_id=workspace_id,
            repo_id=repo_id,
            kind=payload.kind,
            config=payload.config or {},
            status="ok",
        )
        session.add(row)
    else:
        row.config = payload.config or {}

    row.updated_at = datetime.now(timezone.utc)

    try:
        await session.flush()
    except IntegrityError as exc:
        # Only path that can realistically trigger here is a race
        # between two concurrent wizards on the same repo. Surface
        # a 409 so the client can refetch and retry.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="tracker binding already exists; retry with latest",
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="tracker_binding.set" if is_new else "tracker_binding.update",
            target_kind="workspace_repo",
            target_id=str(repo_id),
            payload={
                "kind": payload.kind,
                "config_keys": sorted((payload.config or {}).keys()),
            },
        )
    )
    await session.flush()

    ws_kind, _ = await _workspace_default_kind(session, workspace_id)
    connected, public_cfg = await _workspace_oauth_meta(
        session, workspace_id, row.kind
    )
    return TrackerBindingOut(
        repo_id=repo_id,
        kind=row.kind,
        config=row.config or {},
        source="repo",
        workspace_default_kind=ws_kind,
        workspace_default_config=public_cfg,
        workspace_oauth_connected=connected,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tracker_binding(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Drop the per-repo tracker row so the repo inherits workspace default.

    204 even if no row existed — the wizard's "reset to default"
    button shouldn't care. Returning 404 here would force UI
    callers to branch on a "already gone" response shape.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    await _resolve_repo(session, workspace_id, repo_id)

    row = await _repo_binding(session, workspace_id, repo_id)
    if row is not None:
        await session.delete(row)
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="tracker_binding.delete",
                target_kind="workspace_repo",
                target_id=str(repo_id),
                payload={"kind": row.kind},
            )
        )
        await session.flush()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
