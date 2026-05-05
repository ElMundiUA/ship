"""Workspace project prioritisation surface (Dashboard v2 — PR-1).

Three endpoints under ``/v1/workspaces/{workspace_id}/priorities``:

- ``GET /priorities`` — denormalised payload for the dashboard
  prioritizer block: every visible tracker project enriched with
  saved ordinal, completion fraction, tracker connection state,
  workspace-level autonomy state, and a one-line ``last_action``
  trust anchor.
- ``POST /priorities/reorder`` — bulk replace of the ordering. The
  request carries the canonical ``project_native_id`` order; we wipe
  the table for this workspace and re-insert in one transaction.
  Audit-log row dropped on every reorder.
- ``POST /priorities/autonomy`` — flip the workspace-level pause
  switch. Persisted into ``Workspace.settings['autonomy_paused']``
  so we don't grow another column for a single bool.

Tracker projects come from the bound ``TrackerGateway`` (today only
Linear). Adapters that don't model projects raise NotImplementedError
inside ``list_projects`` and we surface ``tracker_supports_projects =
false`` to the client.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.dashboard_priorities import WorkspaceProjectPriority
from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
)
from backend.app.db.models.pipelines import PullRequest
from backend.app.db.models.tenancy import AuditLog, Integration, Workspace
from backend.app.db.session import get_session
from backend.app.services.tracker_resolver import resolve_for_workspace


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/priorities",
    tags=["dashboard-priorities"],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TrackerSyncOut(BaseModel):
    """Connection state for the workspace's bound tracker.

    ``status`` is ``connected`` when an integration row exists and
    ``last_health_error`` is null. ``error`` carries the most recent
    health failure when ``status == "error"``. ``disconnected`` means
    no integration row at all — the prioritizer renders the connect
    CTA empty state.
    """

    kind: Literal["linear", "jira"] | None
    status: Literal["connected", "error", "disconnected"]
    last_health_at: datetime | None
    last_health_error: str | None
    supports_projects: bool
    # OAuth scopes the stored token was issued with. We render them in
    # the error empty-state so an admin can eyeball whether ``read``
    # is actually granted (a 401 from ``projects`` without ``read`` is
    # the same Linear error string as a revoked token, but a different
    # fix — re-OAuth with a wider scope set vs. reconnect).
    scopes: list[str] | None


class PriorityProjectOut(BaseModel):
    """One row in the prioritizer list."""

    project_native_id: str
    name: str
    slug: str | None
    state: str | None
    url: str | None
    color: str | None
    # Saved priority position (0 = top). NULL when the project hasn't
    # been prioritised yet — the UI sorts these *after* prioritised
    # rows by ``updated_at`` and renders the drag handle in the
    # "fresh" affordance.
    ordinal: int | None
    # Completion magnitude. ``total`` is None when the tracker can't
    # tell us — the UI renders ``—`` and skips the bar.
    completed: int | None
    total: int | None


class UpNextOut(BaseModel):
    """Up-next pinned strip — single line above the prioritizer list."""

    project_native_id: str
    project_name: str
    color: str | None


class LastActionOut(BaseModel):
    """Most recent thing the bot did, for the right-rail trust anchor."""

    label: str
    href: str | None
    ts: datetime


class PrioritiesOut(BaseModel):
    projects: list[PriorityProjectOut]
    tracker: TrackerSyncOut
    autonomy_paused: bool
    up_next: UpNextOut | None
    last_action: LastActionOut | None


class ReorderIn(BaseModel):
    """Body for ``POST /reorder`` — canonical order of project ids.

    The list is the new full ordering: any project_native_id missing
    from the list is unprioritised (its row is deleted). Duplicates
    are rejected with 400.
    """

    order: list[str] = Field(min_length=0, max_length=200)


class AutonomyIn(BaseModel):
    paused: bool


class AutonomyOut(BaseModel):
    autonomy_paused: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_AUTONOMY_KEY = "autonomy_paused"


def _autonomy_paused(workspace: Workspace) -> bool:
    """Read the workspace-level autonomy pause flag from settings JSONB."""
    return bool((workspace.settings or {}).get(_AUTONOMY_KEY, False))


async def _load_workspace(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Workspace:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        # ``_require_membership`` already 404s for non-members, so this
        # arm only fires for inconsistent state.
        raise HTTPException(status_code=404, detail="workspace not found")
    return workspace


def _completion_counts(
    progress: float | None, scope: float | None
) -> tuple[int | None, int | None]:
    """Convert Linear's ``progress``/``scope`` pair into ``(completed,
    total)`` integers. Returns ``(None, None)`` when either field is
    missing or scope is zero — the UI renders ``—`` in that case.
    """
    if progress is None or scope is None:
        return (None, None)
    if scope <= 0:
        return (0, 0)
    total = int(round(scope))
    completed = int(round(progress * scope))
    if completed > total:
        completed = total
    if completed < 0:
        completed = 0
    return (completed, total)


async def _last_action_for(
    session: AsyncSession, workspace_id: uuid.UUID
) -> LastActionOut | None:
    """Pick the most recent agent-visible action for the trust anchor.

    For v1 the canonical signal is the most recent merged PR — that's
    what reads as "the bot just shipped". When no merged PR is in
    scope we fall back to the most recent priorities-reorder audit
    row so the strip still has *something* meaningful to render right
    after the operator drags rows around.
    """
    pr = (
        await session.execute(
            select(PullRequest)
            .where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.merged.is_(True),
                PullRequest.merged_at.is_not(None),
            )
            .order_by(desc(PullRequest.merged_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if pr is not None and pr.merged_at is not None:
        title = pr.title or ""
        repo = pr.repo_full_name or ""
        label_parts: list[str] = ["merged"]
        if repo:
            label_parts.append(f"#{pr.number} · {repo}")
        else:
            label_parts.append(f"#{pr.number}")
        if title:
            label_parts.append(title)
        return LastActionOut(
            label=" · ".join(label_parts),
            href=pr.html_url or None,
            ts=pr.merged_at,
        )

    audit = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "dashboard.priorities.reorder",
            )
            .order_by(desc(AuditLog.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if audit is not None:
        return LastActionOut(
            label="reordered priorities",
            href=None,
            ts=audit.created_at,
        )
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=PrioritiesOut)
async def get_priorities(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PrioritiesOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    workspace = await _load_workspace(session, workspace_id)

    saved_rows = (
        await session.execute(
            select(WorkspaceProjectPriority)
            .where(WorkspaceProjectPriority.workspace_id == workspace_id)
            .order_by(WorkspaceProjectPriority.ordinal.asc())
        )
    ).scalars().all()
    saved_by_id: dict[str, WorkspaceProjectPriority] = {
        row.project_native_id: row for row in saved_rows
    }

    # Resolve tracker via the canonical helper (native installation
    # first, then legacy ``Integration`` row) so the dashboard reads
    # the same token the agent's runtime authenticates with. A
    # workspace with a fresh native install + a stale legacy row
    # used to surface the legacy 401 here even though the agent kept
    # working — the resolver fix flips the precedence.
    tracker = None
    fetch_error: str | None = None
    try:
        tracker = await resolve_for_workspace(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "tracker resolve failed for workspace=%s err=%s",
            workspace_id,
            exc,
        )
        fetch_error = str(exc)

    raw_projects: list[dict] = []
    supports_projects = False
    if tracker is not None:
        supports_projects = True
        try:
            raw_projects = await tracker.gateway.list_projects(limit=50)
        except NotImplementedError:
            supports_projects = False
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "tracker list_projects failed workspace=%s err=%s",
                workspace_id,
                exc,
            )
            fetch_error = str(exc)

    projects: list[PriorityProjectOut] = []
    seen_ids: set[str] = set()
    for raw in raw_projects:
        native_id = str(raw.get("id") or "")
        if not native_id or native_id in seen_ids:
            continue
        seen_ids.add(native_id)
        completed, total = _completion_counts(
            raw.get("progress"), raw.get("scope")
        )
        saved = saved_by_id.get(native_id)
        projects.append(
            PriorityProjectOut(
                project_native_id=native_id,
                name=str(raw.get("name") or ""),
                slug=str(raw.get("slug") or "") or None,
                state=str(raw.get("state") or "") or None,
                url=str(raw.get("url") or "") or None,
                color=str(raw.get("color") or "") or None,
                ordinal=saved.ordinal if saved else None,
                completed=completed,
                total=total,
            )
        )

    # Sort: prioritised rows first by ordinal, then unprioritised by
    # name (stable, doesn't depend on tracker's update-time ordering
    # which is a moving target).
    projects.sort(
        key=lambda p: (
            p.ordinal if p.ordinal is not None else 1_000_000,
            p.name.lower(),
        )
    )

    # Tracker connection block. We trust the resolver: a non-None
    # ``ResolvedTracker`` means *some* storage shape (native install
    # or legacy Integration row) handed back a usable token, so
    # surface ``connected`` even when no legacy row exists.
    if tracker is None:
        tracker_out = TrackerSyncOut(
            kind=None,
            status="disconnected",
            last_health_at=None,
            last_health_error=None,
            supports_projects=False,
            scopes=None,
        )
    else:
        kind = (
            "linear"
            if tracker.kind == "linear"
            else "jira"
            if tracker.kind == "jira"
            else None
        )
        # Status reflects the result of the *current* fetch, not
        # whatever ``last_health_error`` happens to be sitting in the
        # source row. The OAuth callback and provisioner write to
        # ``last_health_error`` from independent code paths; mixing
        # them into ``status`` turned a long-resolved blip into a
        # coral "Linear is broken" hairline. Surface the stored error
        # in ``last_health_error`` for ops visibility but only flip
        # ``status`` when the live fetch failed.
        last_health_at = tracker.last_health_at  # type: ignore[assignment]
        tracker_out = TrackerSyncOut(
            kind=kind,
            status="error" if fetch_error else "connected",
            last_health_at=last_health_at,  # type: ignore[arg-type]
            last_health_error=fetch_error or tracker.last_health_error,
            supports_projects=supports_projects,
            scopes=list(tracker.scopes) if tracker.scopes else [],
        )

    autonomy_paused = _autonomy_paused(workspace)

    # Up-next strip pulls from the top of the prioritizer list. We
    # render it even when paused (the UI dims the strip and swaps the
    # copy to "paused · resume to pull").
    up_next: UpNextOut | None = None
    for project in projects:
        if project.ordinal is None:
            break
        up_next = UpNextOut(
            project_native_id=project.project_native_id,
            project_name=project.name,
            color=project.color,
        )
        break

    last_action = await _last_action_for(session, workspace_id)

    return PrioritiesOut(
        projects=projects,
        tracker=tracker_out,
        autonomy_paused=autonomy_paused,
        up_next=up_next,
        last_action=last_action,
    )


@router.post("/reorder", response_model=PrioritiesOut)
async def reorder_priorities(
    workspace_id: uuid.UUID,
    payload: ReorderIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PrioritiesOut:
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_ADMIN
    )
    await _load_workspace(session, workspace_id)

    # Reject duplicates so a buggy client can't end up with two rows
    # claiming the same project under conflicting ordinals.
    if len(payload.order) != len(set(payload.order)):
        raise HTTPException(
            status_code=400,
            detail="reorder.order contains duplicate project_native_id",
        )

    # Bulk replace. The unique (workspace_id, project_native_id)
    # constraint prevents row-level duplicates, but DELETE-then-INSERT
    # is the cleanest way to express "this is the new full ordering".
    await session.execute(
        delete(WorkspaceProjectPriority).where(
            WorkspaceProjectPriority.workspace_id == workspace_id
        )
    )
    for ordinal, native_id in enumerate(payload.order):
        session.add(
            WorkspaceProjectPriority(
                workspace_id=workspace_id,
                project_native_id=native_id,
                ordinal=ordinal,
            )
        )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="dashboard.priorities.reorder",
            target_kind="workspace",
            target_id=str(workspace_id),
            payload={"order": payload.order},
        )
    )
    await session.flush()

    return await get_priorities(
        workspace_id=workspace_id,
        auth=auth,
        session=session,
        settings=settings,
    )


@router.post("/autonomy", response_model=AutonomyOut)
async def set_autonomy(
    workspace_id: uuid.UUID,
    payload: AutonomyIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AutonomyOut:
    """Toggle the workspace-level autonomy pause switch.

    Stored into :attr:`Workspace.settings` JSONB rather than a column
    so we don't grow the schema for a single bool. The agent runtime
    reads this same key when picking the next ticket — paused = stay
    in idle, drop nothing onto the queue.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_ADMIN
    )
    workspace = await _load_workspace(session, workspace_id)

    current = _autonomy_paused(workspace)
    if current != payload.paused:
        new_settings = dict(workspace.settings or {})
        new_settings[_AUTONOMY_KEY] = bool(payload.paused)
        # Reassign rather than mutate-in-place: SQLAlchemy's JSONB
        # change-tracking only sees full reassignment of the column
        # value, not in-place dict mutation.
        workspace.settings = new_settings
        workspace.updated_at = datetime.now(timezone.utc)

        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="dashboard.autonomy.set",
                target_kind="workspace",
                target_id=str(workspace_id),
                payload={"paused": bool(payload.paused)},
            )
        )
        await session.flush()

    return AutonomyOut(autonomy_paused=bool(payload.paused))


# ---------------------------------------------------------------------------
# Debug — admin-only inspection of the workspace's tracker storage.
# ---------------------------------------------------------------------------


class _DebugSourceOut(BaseModel):
    """One storage row's user-visible state.

    No secret material leaks: we expose token prefixes (first 8
    chars) so the admin can tell ``lin_oauth`` from ``lin_api`` and
    diff against what they expect.
    """

    exists: bool
    status: str | None
    last_health_at: datetime | None
    last_health_error: str | None
    scopes: list[str] | None
    token_prefix: str | None
    token_decryptable: bool


class TrackerDebugOut(BaseModel):
    workspace_id: uuid.UUID
    native_linear: _DebugSourceOut
    legacy_linear: _DebugSourceOut
    resolver_picked_source: Literal["native", "legacy"] | None
    viewer_probe_ok: bool | None
    viewer_probe_error: str | None


@router.get("/_debug-tracker", response_model=TrackerDebugOut)
async def debug_tracker(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TrackerDebugOut:
    """Admin-only diagnostic dump for the workspace's Linear storage.

    Surfaces what's actually persisted (native installation, legacy
    integration row), whether the stored ciphertext can be decrypted
    under the running encryption key, the token's leading prefix
    (so an admin can tell apart OAuth tokens from PATs), and the
    result of a tiny ``viewer { id }`` probe against Linear with
    the resolved token. Lets us answer "the dashboard says 401, why?"
    without shelling into the DB.
    """
    from backend.app.security.encryption import decrypt as _decrypt

    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_ADMIN
    )
    await _load_workspace(session, workspace_id)

    native_install = (
        await session.execute(
            select(NativeIntegrationInstallation)
            .where(
                NativeIntegrationInstallation.workspace_id == workspace_id,
                NativeIntegrationInstallation.provider
                == NativeIntegrationProvider.LINEAR,
            )
            .order_by(NativeIntegrationInstallation.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    native_credential: NativeIntegrationCredential | None = None
    if native_install is not None:
        native_credential = (
            await session.execute(
                select(NativeIntegrationCredential).where(
                    NativeIntegrationCredential.installation_id
                    == native_install.id,
                    NativeIntegrationCredential.kind == "access_token",
                )
            )
        ).scalar_one_or_none()

    native_out = _DebugSourceOut(
        exists=native_install is not None,
        status=native_install.status if native_install else None,
        last_health_at=native_install.last_health_at if native_install else None,
        last_health_error=(
            native_install.last_health_error if native_install else None
        ),
        scopes=(
            list(native_install.scopes or [])
            if native_install
            else None
        ),
        token_prefix=_token_prefix(native_credential, _decrypt),
        token_decryptable=_token_decryptable(native_credential, _decrypt),
    )

    legacy = (
        await session.execute(
            select(Integration)
            .where(
                Integration.workspace_id == workspace_id,
                Integration.repo_id.is_(None),
                Integration.kind == "linear",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    legacy_out = _DebugSourceOut(
        exists=legacy is not None,
        status=legacy.status if legacy else None,
        last_health_at=legacy.last_health_at if legacy else None,
        last_health_error=legacy.last_health_error if legacy else None,
        scopes=_legacy_scopes(legacy),
        token_prefix=_legacy_token_prefix(legacy, _decrypt),
        token_decryptable=_legacy_token_decryptable(legacy, _decrypt),
    )

    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    picked: Literal["native", "legacy"] | None = None
    probe_ok: bool | None = None
    probe_error: str | None = None
    if resolved is not None:
        picked = resolved.source  # type: ignore[assignment]
        try:
            await resolved.gateway._gql(  # type: ignore[attr-defined]
                "query ShipViewerProbe { viewer { id } }", {}
            )
            probe_ok = True
        except Exception as exc:  # noqa: BLE001 — defensive
            probe_ok = False
            probe_error = str(exc)[:500]

    return TrackerDebugOut(
        workspace_id=workspace_id,
        native_linear=native_out,
        legacy_linear=legacy_out,
        resolver_picked_source=picked,
        viewer_probe_ok=probe_ok,
        viewer_probe_error=probe_error,
    )


def _token_prefix(
    credential: NativeIntegrationCredential | None, decrypt
) -> str | None:
    if credential is None or not credential.secret_ciphertext:
        return None
    try:
        token = decrypt(credential.secret_ciphertext)
    except Exception:  # noqa: BLE001
        return None
    return (token or "")[:8] or None


def _token_decryptable(
    credential: NativeIntegrationCredential | None, decrypt
) -> bool:
    if credential is None or not credential.secret_ciphertext:
        return False
    try:
        decrypt(credential.secret_ciphertext)
        return True
    except Exception:  # noqa: BLE001
        return False


def _legacy_scopes(row: Integration | None) -> list[str] | None:
    if row is None:
        return None
    raw = (row.config or {}).get("scope")
    if not raw or not isinstance(raw, str):
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _legacy_token_prefix(row: Integration | None, decrypt) -> str | None:
    if row is None or not row.secret_ciphertext:
        return None
    try:
        token = decrypt(row.secret_ciphertext)
    except Exception:  # noqa: BLE001
        return None
    return (token or "")[:8] or None


def _legacy_token_decryptable(row: Integration | None, decrypt) -> bool:
    if row is None or not row.secret_ciphertext:
        return False
    try:
        decrypt(row.secret_ciphertext)
        return True
    except Exception:  # noqa: BLE001
        return False


__all__ = ["router"]
