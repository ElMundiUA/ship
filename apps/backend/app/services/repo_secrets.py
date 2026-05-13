"""Service layer for per-repo Ship-managed secrets (B10).

Three public entry points:

- :func:`upsert_repo_secret` — create or rotate a secret: encrypt for
  at-rest storage, push to GitHub as an Actions secret, persist the
  row in one transaction.
- :func:`delete_repo_secret_row` — remove it from the DB *and*
  from GitHub, tolerating "already gone on one side".
- :func:`list_repo_secrets` — plaintext-free listing for the UI.

The API router thin-wraps these — keeping the SQLAlchemy + httpx
choreography here makes the tests cleaner (we can exercise the
whole upsert-with-mock-GitHub flow without spinning up FastAPI) and
leaves the route handler free to focus on auth, audit, and HTTP
status codes.

Error taxonomy
==============

- :class:`ValueError` — caller bug (bad name, empty plaintext). The
  route turns it into 422.
- :class:`SecretSyncError` — GitHub rejected the sync. The DB row
  still gets written with ``sync_status='error'`` so the operator
  can see "we tried, here's why it failed" instead of a silent
  nothing. The route returns 207 (multi-status) or 202 (accepted,
  not yet synced) depending on call context.
- :class:`WorkflowDispatchError` can still bubble up from the
  GitHub client for upstream 5xx's; the route maps to 502.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.repo_secrets import (
    SYNC_STATUS_ERROR,
    SYNC_STATUS_SYNCED,
    RepoSecret,
)
from backend.app.integrations.github.actions_secrets import (
    delete_repo_secret as gh_delete_secret,
    put_repo_secret as gh_put_secret,
)
from backend.app.integrations.github.workflows import WorkflowDispatchError
from backend.app.security.encryption import encrypt


logger = logging.getLogger("ship.repo_secrets")


# GitHub's Actions secret-name grammar: starts with A-Z or _, then
# any mix of A-Z, 0-9, _. No lowercase (GitHub uppercases anyway,
# but we refuse on the way in so the stored name matches what the
# workflow YAML will reference). The ``GITHUB_`` prefix is reserved
# upstream.
_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_MAX_NAME_LEN: Final[int] = 245
_MAX_PLAINTEXT_LEN: Final[int] = 48 * 1024  # GitHub's upper bound.


class SecretSyncError(RuntimeError):
    """GitHub sync failed but the DB row was written.

    Carries the upstream status code and a human-readable message so
    the caller can decide how to render it (partial success banner
    vs. outright failure). The ``secret`` reference points at the
    row we persisted so the caller can show "stored, not live yet"
    state.
    """

    def __init__(
        self,
        secret: RepoSecret,
        *,
        status_code: int | None,
        message: str,
    ) -> None:
        super().__init__(message)
        self.secret = secret
        self.status_code = status_code
        self.message = message


@dataclass(slots=True)
class SecretListRow:
    """Plaintext-free projection for the list API.

    This is what the UI renders — the plaintext ciphertext never
    leaves the service layer once written. ``masked_hint`` is the
    last 4 plaintext characters, pre-computed at write time so
    listing doesn't need to decrypt (fernet is cheap but listing is
    the hot path and N decryptions is easy to avoid).
    """

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


def validate_secret_name(name: str) -> str:
    """Normalise + validate a name, return the canonical form.

    Canonicalisation: uppercase, strip surrounding whitespace. Raises
    :class:`ValueError` for anything that wouldn't round-trip into a
    GitHub Actions secret slot so we don't wait for the upstream PUT
    to fail.
    """
    if not isinstance(name, str):
        raise ValueError("secret name must be a string")
    candidate = name.strip().upper()
    if not candidate:
        raise ValueError("secret name is required")
    if len(candidate) > _MAX_NAME_LEN:
        raise ValueError(
            f"secret name exceeds GitHub limit ({_MAX_NAME_LEN} chars)"
        )
    if candidate.startswith("GITHUB_"):
        raise ValueError(
            "secret names starting with 'GITHUB_' are reserved by GitHub"
        )
    if not _NAME_RE.match(candidate):
        raise ValueError(
            "secret name must match [A-Z_][A-Z0-9_]* (uppercase, digits, underscore)"
        )
    return candidate


def _make_masked_hint(plaintext: str) -> str | None:
    """Last four plaintext chars for UI display; ``None`` if too short.

    Four chars is the floor where showing a tail without leaking
    useful attacker information still reads as "same key as before"
    for operators (matches GitHub + Vercel + most vendor UIs).
    Shorter plaintexts intentionally get no hint — the rotation
    indicator is still there via ``updated_at``.
    """
    if len(plaintext) < 8:
        return None
    return plaintext[-4:]


def _row_to_list(row: RepoSecret) -> SecretListRow:
    return SecretListRow(
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


async def list_repo_secrets(
    session: AsyncSession, repo: WorkspaceRepo
) -> list[SecretListRow]:
    """Return newest-first list of secrets bound to ``repo`` (no plaintext)."""
    stmt = (
        select(RepoSecret)
        .where(RepoSecret.repo_id == repo.id)
        .order_by(RepoSecret.created_at.desc(), RepoSecret.name.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_list(r) for r in rows]


async def upsert_repo_secret(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings,
    name: str,
    plaintext: str,
    description: str | None,
    actor_user_id: uuid.UUID | None,
) -> RepoSecret:
    """Create or rotate a repo secret, syncing to GitHub.

    Write order on purpose: DB row first (encrypted), then GitHub
    PUT. If GitHub fails we flip ``sync_status='error'`` so the UI
    surfaces "stored but not live" instead of losing the operator's
    typing. The caller is expected to catch :class:`SecretSyncError`
    and render accordingly; if it doesn't, the row is still good and
    the operator can click "Re-sync" from the list UI.
    """
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("secret plaintext is required")
    if len(plaintext) > _MAX_PLAINTEXT_LEN:
        raise ValueError(
            f"secret exceeds GitHub's {_MAX_PLAINTEXT_LEN // 1024}KB limit"
        )
    canonical_name = validate_secret_name(name)
    description = (description or None) and description[:512]

    # Cheap existence check first so we can branch the audit action
    # string and avoid generating a new UUID we'd throw away.
    existing = (
        await session.execute(
            select(RepoSecret).where(
                RepoSecret.repo_id == repo.id,
                RepoSecret.name == canonical_name,
            )
        )
    ).scalars().first()

    ciphertext = encrypt(plaintext)
    hint = _make_masked_hint(plaintext)
    now = datetime.now(timezone.utc)

    if existing is None:
        row = RepoSecret(
            workspace_id=repo.workspace_id,
            repo_id=repo.id,
            name=canonical_name,
            ciphertext=ciphertext,
            masked_hint=hint,
            description=description,
            sync_status=SYNC_STATUS_SYNCED,  # optimistic; reverted on failure
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        session.add(row)
        await session.flush()
        # Pull the server-default ``created_at`` / ``updated_at``
        # back into the ORM. Without this, accessing the columns
        # later in the same request blocks on a lazy SELECT from a
        # sync context and SQLAlchemy raises MissingGreenlet.
        await session.refresh(row)
    else:
        row = existing
        row.ciphertext = ciphertext
        row.masked_hint = hint
        if description is not None:
            row.description = description
        row.updated_by_user_id = actor_user_id
        row.sync_status = SYNC_STATUS_SYNCED  # optimistic
        row.sync_error = None
        row.updated_at = now

    # Sync to GitHub. Failures here are soft: we keep the row,
    # flip ``sync_status`` to ``error``, and bubble up a
    # :class:`SecretSyncError` so the route can pick the right HTTP
    # envelope. The alternative ("raise and rollback the DB row")
    # loses the operator's typing on every transient 5xx — worse UX,
    # worse recovery story.
    try:
        key_id = await gh_put_secret(
            repo,
            install,
            name=canonical_name,
            plaintext=plaintext,
            settings=settings,
        )
    except WorkflowDispatchError as exc:
        logger.warning(
            "repo secret sync failed repo=%s name=%s status=%s",
            repo.full_name,
            canonical_name,
            exc.status_code,
        )
        row.sync_status = SYNC_STATUS_ERROR
        row.sync_error = f"GitHub {exc.status_code}: {exc.message[:256]}"
        row.last_synced_at = None
        await session.flush()
        raise SecretSyncError(
            row, status_code=exc.status_code, message=exc.message
        ) from exc
    except Exception as exc:  # pragma: no cover - network flakes
        logger.warning(
            "repo secret sync crashed repo=%s name=%s: %s",
            repo.full_name,
            canonical_name,
            exc,
        )
        row.sync_status = SYNC_STATUS_ERROR
        row.sync_error = f"sync crashed: {exc!s}"[:2048]
        row.last_synced_at = None
        await session.flush()
        raise SecretSyncError(row, status_code=None, message=str(exc)) from exc

    row.sync_status = SYNC_STATUS_SYNCED
    row.sync_error = None
    row.github_key_id = key_id
    row.last_synced_at = now
    await session.flush()
    # Refresh so ``updated_at`` reflects the onupdate=now() trigger
    # — the same MissingGreenlet trap the first flush avoids.
    await session.refresh(row)
    return row


async def delete_repo_secret_row(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings,
    secret: RepoSecret,
) -> bool:
    """Remove a secret everywhere.

    Order: GitHub first, DB second. If the GitHub delete fails we
    stop and keep the row so the operator can retry — surviving the
    failure with only the DB copy gone leaves a ghost secret live
    on the repo that the UI can no longer find, which is a worse
    failure mode than "delete hasn't completed yet".

    404 from GitHub is treated as success (already gone); anything
    else :class:`WorkflowDispatchError`-shaped raises.
    """
    # Try GitHub first so the DB row survives a transient upstream
    # blip. If GitHub is down the operator can retry and eventually
    # converge.
    await gh_delete_secret(
        repo,
        install,
        name=secret.name,
        settings=settings,
    )
    await session.delete(secret)
    await session.flush()
    return True


__all__ = [
    "SecretListRow",
    "SecretSyncError",
    "delete_repo_secret_row",
    "list_repo_secrets",
    "upsert_repo_secret",
    "validate_secret_name",
]
