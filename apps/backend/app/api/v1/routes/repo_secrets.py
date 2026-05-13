"""Per-repo Ship-managed Actions secrets API (B10).

Surfaces three routes under
``/v1/workspaces/{workspace_id}/repos/{repo_id}/secrets``:

- ``GET``      — newest-first list. Plaintext never leaves this
  process; each row carries a ``masked_hint`` (last 4 chars) plus
  ``sync_status`` so the UI can render "stored but not live yet"
  distinctly from "synced".
- ``POST``     — create or rotate a secret by name. Admin-only.
  On sync failure returns 200 with ``sync_status='error'`` so the
  operator's typing is never lost to a transient upstream 5xx;
  outright validation mistakes still return 422.
- ``DELETE``   — remove everywhere (GitHub first, DB second).
  Idempotent against double-clicks.

Why admin-only? Secrets are high-blast-radius — one leaked API key
is the entire workspace's credit. ``ROLES_ADMIN`` matches the
integrations + repo-activation pattern: members can read
non-secret data, admins write the sensitive plumbing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.lanes import Routine
from backend.app.db.models.repo_secrets import RepoSecret
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.github.workflows import WorkflowDispatchError
from backend.app.services.starter_workflows import get as get_starter_workflow
from backend.app.services.repo_secrets import (
    SecretListRow,
    SecretSyncError,
    delete_repo_secret_row,
    list_repo_secrets,
    upsert_repo_secret,
    validate_secret_name,
)


logger = logging.getLogger("ship.repo_secrets.api")


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos/{repo_id}/secrets",
    tags=["secrets"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SecretOut(BaseModel):
    """Plaintext-free projection of one :class:`RepoSecret`."""

    id: uuid.UUID
    name: str
    masked_hint: str | None
    description: str | None
    sync_status: str
    sync_error: str | None
    last_synced_at: datetime | None
    github_key_id: str | None
    created_by_user_id: uuid.UUID | None
    updated_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class SecretListOut(BaseModel):
    items: list[SecretOut]


class RequiredSecretOut(BaseModel):
    """One row of the "what does this repo need" matrix.

    The UI shows a warning card when ``missing=true`` so the
    operator can add the secret before the cron run fires. Pipelines
    that declare no ``required_secrets`` in their catalog entry
    aren't in the response at all — silence means "no obligations".
    """

    name: str
    required_by: list[str] = Field(
        default_factory=list,
        description=(
            "Pipeline ``kind`` values (``pr_review`` / ``self_heal`` / "
            "``code_map`` / …) that declare this secret as required."
        ),
    )
    stored: bool = Field(
        description="True iff a :class:`RepoSecret` row exists for this name."
    )
    sync_status: str | None = Field(
        default=None,
        description=(
            "``sync_status`` of the stored secret if present, otherwise "
            "``None``. Useful for surfacing 'stored but not live yet' on "
            "the required-secrets matrix."
        ),
    )


class RequiredSecretsOut(BaseModel):
    items: list[RequiredSecretOut]


class SecretUpsertIn(BaseModel):
    """Create-or-rotate payload. ``value`` is plaintext on the wire; we
    only see it on the ``POST`` and never return it afterwards."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=245,
        description=(
            "GitHub Actions secret name. Normalised to uppercase; must "
            "match ``[A-Z_][A-Z0-9_]*`` and cannot start with ``GITHUB_``."
        ),
    )
    value: str = Field(
        ..., min_length=1, description="Plaintext secret value."
    )
    description: str | None = Field(
        default=None,
        max_length=512,
        description="Free-form operator note; never surfaced to workflows.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_out(row: SecretListRow) -> SecretOut:
    return SecretOut(
        id=row.id,
        name=row.name,
        masked_hint=row.masked_hint,
        description=row.description,
        sync_status=row.sync_status,
        sync_error=row.sync_error,
        last_synced_at=row.last_synced_at,
        github_key_id=row.github_key_id,
        created_by_user_id=row.created_by_user_id,
        updated_by_user_id=row.updated_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _db_row_to_out(row: RepoSecret) -> SecretOut:
    return SecretOut(
        id=row.id,
        name=row.name,
        masked_hint=row.masked_hint,
        description=row.description,
        sync_status=row.sync_status,
        sync_error=row.sync_error,
        last_synced_at=row.last_synced_at,
        github_key_id=row.github_key_id,
        created_by_user_id=row.created_by_user_id,
        updated_by_user_id=row.updated_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _resolve_repo_and_install(
    session: AsyncSession, workspace_id: uuid.UUID, repo_id: uuid.UUID
) -> tuple[WorkspaceRepo, GitHubInstallation]:
    """Load the repo + its Installation, 404/409'ing where appropriate.

    409 (not 404) is the right status for "repo exists but has no
    GitHub App installation" — the row exists, it just can't
    satisfy the Actions-secret contract without a live install.
    """
    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.id == repo_id,
            )
        )
    ).scalars().first()
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if repo.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repo is not backed by a GitHub App installation.",
        )
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "GitHub App installation for this repo is missing or "
                "suspended. Reinstall the Ship app."
            ),
        )
    return repo, install


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=SecretListOut)
async def list_secrets(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> SecretListOut:
    """Plaintext-free list of Ship-managed secrets on a repo.

    Members can read the names/metadata (useful for "which keys do
    I need to rotate before Friday's demo"); only admins can write.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.id == repo_id,
            )
        )
    ).scalars().first()
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    rows = await list_repo_secrets(session, repo)
    return SecretListOut(items=[_row_to_out(r) for r in rows])


@router.get("/required", response_model=RequiredSecretsOut)
async def list_required_secrets(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RequiredSecretsOut:
    """Return the ``required_secrets`` matrix for a repo.

    Walks every declared routine on the repo, reads its starter
    workflow's ``required_secrets``, and merges with what's stored
    so the UI can render "missing" / "stored" / "stored but not
    synced" indicators next to each name.

    Enabled and disabled routines are both included: a disabled
    routine might still need its secret if the user re-enables it,
    and surfacing it pre-emptively saves one round trip of
    "enable lane → error → add secret → enable again".
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.id == repo_id,
            )
        )
    ).scalars().first()
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    routines = (
        await session.execute(
            select(Routine).where(
                Routine.workspace_id == workspace_id,
                Routine.repo_id == repo.id,
            )
        )
    ).scalars().all()

    # Merge by secret name, collecting the set of routine *lane_ids*
    # that demand it. ``lane_id`` (not ``id``) because the UI groups
    # "PR review needs ANTHROPIC_API_KEY" rather than per-row.
    required_by: dict[str, list[str]] = {}
    for routine in routines:
        if not routine.pattern:
            continue
        entry = get_starter_workflow(routine.pattern)
        if entry is None:
            continue
        for name in entry.required_secrets:
            bucket = required_by.setdefault(name, [])
            if routine.lane_id not in bucket:
                bucket.append(routine.lane_id)

    if not required_by:
        return RequiredSecretsOut(items=[])

    stored_rows = (
        await session.execute(
            select(RepoSecret).where(
                RepoSecret.repo_id == repo.id,
                RepoSecret.name.in_(list(required_by.keys())),
            )
        )
    ).scalars().all()
    stored_by_name = {row.name: row for row in stored_rows}

    items: list[RequiredSecretOut] = []
    for name in sorted(required_by.keys()):
        stored = stored_by_name.get(name)
        items.append(
            RequiredSecretOut(
                name=name,
                required_by=sorted(required_by[name]),
                stored=stored is not None,
                sync_status=stored.sync_status if stored else None,
            )
        )
    return RequiredSecretsOut(items=items)


@router.post(
    "",
    response_model=SecretOut,
    status_code=status.HTTP_200_OK,
    responses={
        # 502: GitHub rejected at public-key fetch / PUT time for a
        # reason we can't attribute to caller input (most 4xx on
        # Actions-secrets endpoints mean "App missing actions:write"
        # which is a precondition).
        502: {"description": "GitHub sync failed (see sync_status)"},
    },
)
async def upsert_secret(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: SecretUpsertIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SecretOut:
    """Create or rotate a Ship-managed repo secret.

    Two-stage semantics: we always write the DB row (encrypted
    at rest), then sync to GitHub. If the sync crashes we return
    the row with ``sync_status='error'`` + ``sync_error`` set so
    the UI can render "stored, not live yet" with a retry button
    — losing the operator's typing on every transient upstream
    blip would be a much worse experience.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    repo, install = await _resolve_repo_and_install(
        session, workspace_id, repo_id
    )

    # Validate the name up-front (shape mistake → 422, not 500).
    try:
        canonical_name = validate_secret_name(payload.name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    sync_ok = True
    sync_error: str | None = None
    try:
        row = await upsert_repo_secret(
            session,
            repo,
            install,
            settings=settings,
            name=payload.name,
            plaintext=payload.value,
            description=payload.description,
            actor_user_id=auth.user.id,
        )
    except ValueError as exc:
        # Validation error from inside the service (e.g. plaintext
        # exceeds GitHub's 48KB cap). 422 with the message so the UI
        # can surface it inline on the form.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except SecretSyncError as exc:
        # DB row was written, GitHub rejected. Keep going — we still
        # want to audit + return the row so the UI can show the
        # retry state.
        row = exc.secret
        sync_ok = False
        sync_error = exc.message
    except WorkflowDispatchError as exc:
        # Raised from the public-key fetch before we got to persist
        # a row. Surface as 502 since there's nothing sensible to
        # return.
        logger.warning(
            "secrets public-key fetch failed repo=%s: %s",
            repo.full_name,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"GitHub actions/secrets API rejected request "
                f"(HTTP {exc.status_code})."
            ),
        ) from exc

    # Audit. Never log plaintext; the hint + sync status is what
    # operators need to reconcile "did this get through". The
    # action string differentiates create vs. rotate so the audit
    # page can filter cleanly.
    action = (
        "repo_secret.created"
        if row.created_at == row.updated_at
        else "repo_secret.rotated"
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action=action,
            target_kind="repo_secret",
            target_id=str(row.id),
            payload={
                "repo_id": str(repo.id),
                "repo_full_name": repo.full_name,
                "secret_name": canonical_name,
                "masked_hint": row.masked_hint,
                "sync_status": row.sync_status,
                "sync_error": sync_error,
            },
        )
    )
    await session.flush()

    # HTTP shape: 200 regardless. The body carries ``sync_status``
    # so the UI can branch on "stored, live" vs "stored, retry"
    # without having to inspect response codes. 502 is reserved for
    # preconditions that blocked persistence (e.g. Actions not
    # enabled on the repo at all, caught above).
    response_row = _db_row_to_out(row)
    _ = sync_ok  # flake8: value carried in `row.sync_status`
    return response_row


@router.delete(
    "/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"description": "Secret not found"},
        502: {"description": "GitHub delete failed"},
    },
)
async def delete_secret(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    secret_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    """Delete a Ship-managed repo secret. Admin-only. Idempotent.

    Deletes on GitHub first, DB second. If the GitHub call fails
    we stop and keep the row so the operator can retry; leaving
    GitHub with a live secret the DB no longer knows about would
    be a silent credential-leak pattern we want to avoid.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    repo, install = await _resolve_repo_and_install(
        session, workspace_id, repo_id
    )

    row = (
        await session.execute(
            select(RepoSecret).where(
                RepoSecret.id == secret_id,
                RepoSecret.repo_id == repo.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    secret_name = row.name  # capture before the row disappears
    try:
        await delete_repo_secret_row(
            session, repo, install, settings=settings, secret=row
        )
    except WorkflowDispatchError as exc:
        logger.warning(
            "repo secret GitHub delete failed repo=%s name=%s: %s",
            repo.full_name,
            secret_name,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"GitHub refused the secret delete (HTTP {exc.status_code}). "
                "Retry in a moment; the secret is still live on GitHub."
            ),
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action="repo_secret.deleted",
            target_kind="repo_secret",
            target_id=str(secret_id),
            payload={
                "repo_id": str(repo.id),
                "repo_full_name": repo.full_name,
                "secret_name": secret_name,
            },
        )
    )
    await session.flush()


__all__ = ["router"]
