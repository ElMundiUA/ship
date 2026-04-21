"""Per-repo agent secrets wiring (Wizard v2 iter 3).

The Wizard v2 "pick AI agents" step needs to answer two questions
per-repo without ever persisting plaintext on Ship's side:

1. ``GET /v1/workspaces/{ws}/repos/{repo_id}/agent-secrets``
   — "which of these agents already have their API key set on
   GitHub, and which still need one?". The wizard renders a tidy
   "✓ ready / · needs value" list from the response.

2. ``POST /v1/workspaces/{ws}/repos/{repo_id}/agent-secrets``
   — "here's the plaintext I just collected, push it into
   ``secrets.ANTHROPIC_API_KEY`` / ``CURSOR_API_KEY`` / etc. and
   forget it". Backend uploads sealed-box ciphertext via the
   existing GitHub Actions secrets API and never writes the
   plaintext to the DB.

Authorization: both endpoints require admin membership on the
workspace. There is no viewer-only read path because the response
names an exact secret a viewer has no business knowing about.
Audit-log records the *names* that were set (never the value) so
compliance can later answer "who rotated ANTHROPIC_API_KEY on repo
X and when" without a plaintext leak.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.services.agent_secrets import (
    AGENT_SECRET_CATALOG,
    AgentSecretStatus,
    lookup_agent,
    push_agent_secret,
    resolve_agent_secret_status,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos/{repo_id}/agent-secrets",
    tags=["agent-secrets"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AgentSecretStatusOut(BaseModel):
    """Per-agent wiring state the wizard renders."""

    slug: str
    label: str
    required: bool
    secret_name: str | None
    present: bool
    vendor_url: str | None = None
    description: str | None = None


class AgentSecretsCheckOut(BaseModel):
    """Wrapper so we can add top-level metadata without reshaping."""

    repo_id: uuid.UUID
    agents: list[AgentSecretStatusOut]


class AgentSecretPushIn(BaseModel):
    """One agent's plaintext payload from the wizard.

    We accept the ``slug`` so the wizard doesn't have to translate
    to secret names — the backend catalog owns that mapping.
    ``plaintext`` is stripped on receipt; an empty/whitespace-only
    value is a 422, not a silent no-op.
    """

    slug: str = Field(min_length=1, max_length=64)
    plaintext: str = Field(min_length=1, max_length=8192)


class AgentSecretsPushIn(BaseModel):
    """Batch payload: the wizard sends all provided plaintexts at once."""

    secrets: list[AgentSecretPushIn] = Field(default_factory=list, max_length=10)


class AgentSecretsPushOut(BaseModel):
    """Per-agent result so partial failures are observable."""

    pushed: list[str] = Field(
        default_factory=list,
        description="Agent slugs whose secrets landed on GitHub.",
    )
    failed: list[dict] = Field(
        default_factory=list,
        description=(
            "Agent slugs that failed with error messages. Partial success "
            "is possible: the wizard should re-prompt only for these."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_repo_and_install(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> tuple[WorkspaceRepo, GitHubInstallation]:
    """Fetch the ``(repo, install)`` pair or raise a clean 404.

    The agent-secrets endpoints all need both the repo row (for
    ``full_name`` routing) and its matching install (for the App
    token). We resolve here to keep each route body short and to
    give a single consistent 404 message across them.
    """

    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="repo not found"
        )
    if repo.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="repo has no GitHub App installation bound",
        )
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="installation row missing for repo",
        )
    return repo, install


def _status_out(s: AgentSecretStatus) -> AgentSecretStatusOut:
    return AgentSecretStatusOut(
        slug=s.slug,
        label=s.label,
        required=s.required,
        secret_name=s.secret_name,
        present=s.present,
        vendor_url=s.vendor_url,
        description=s.description,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=AgentSecretsCheckOut)
async def check_agent_secrets(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    slugs: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AgentSecretsCheckOut:
    """Report which agent secrets are already wired on ``repo_id``.

    The wizard calls this right after the "pick agents" step to
    decide which prompts to render. ``slugs`` is an optional
    comma-separated list that filters the catalog to just the
    picks the user made — keeps the GitHub round-trip small. When
    omitted we report on the entire catalog, which is handy for
    the "existing repo settings" view.

    We deliberately avoid 404-ing on unknown slugs: the wizard and
    backend catalogs will drift over time and a stale browser tab
    shouldn't break the user's day. Unknowns are silently dropped.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    repo, install = await _resolve_repo_and_install(session, workspace_id, repo_id)

    if slugs:
        requested = [s.strip() for s in slugs.split(",") if s.strip()]
    else:
        # Full catalog. The wizard can cache this response between
        # reloads since it doesn't change per-user.
        requested = [spec.slug for spec in AGENT_SECRET_CATALOG]

    statuses = await resolve_agent_secret_status(
        repo, install, slugs=requested, settings=settings
    )
    return AgentSecretsCheckOut(
        repo_id=repo_id,
        agents=[_status_out(s) for s in statuses],
    )


@router.post("", response_model=AgentSecretsPushOut, status_code=status.HTTP_200_OK)
async def push_agent_secrets(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: AgentSecretsPushIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AgentSecretsPushOut:
    """Encrypt + push each provided plaintext into repo secrets.

    Idempotent by GitHub semantics: uploading a value for an
    existing secret name overwrites (204); a brand-new name creates
    (201). Either way the response is "pushed". Failures are
    per-agent so the wizard can re-prompt only the offenders — we
    don't abort the batch on the first error.

    Plaintext never touches our DB. The audit log records the
    *names* set (never the value) plus the ``key_id`` the secret
    was sealed under so compliance can trace "rotated at T by U"
    without revealing anything sensitive.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    if not payload.secrets:
        # Empty body is a 400, not a silent "ok nothing to do" —
        # the wizard should never call this with an empty list.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one secret is required",
        )
    repo, install = await _resolve_repo_and_install(session, workspace_id, repo_id)

    pushed: list[str] = []
    failed: list[dict] = []

    for entry in payload.secrets:
        spec = lookup_agent(entry.slug)
        if spec is None or spec.secret_name is None:
            # Unknown or secret-less agent — the UI shouldn't send
            # these. Record as a failure so partial-batch callers
            # notice, but keep going.
            failed.append(
                {
                    "slug": entry.slug,
                    "error": "unknown or does not require a secret",
                }
            )
            continue
        try:
            key_id = await push_agent_secret(
                repo,
                install,
                slug=entry.slug,
                plaintext=entry.plaintext.strip(),
                settings=settings,
            )
        # ``except Exception`` is wide on purpose: GitHub can respond
        # with httpx errors, 4xx envelopes, or libsodium raises if
        # the key shape changes under us. We don't want one bad
        # agent to nuke the whole batch; the wizard uses ``failed``
        # to decide what to re-prompt.
        except Exception as exc:  # noqa: BLE001
            failed.append(
                {
                    "slug": entry.slug,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        pushed.append(entry.slug)
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="agent_secret.push",
                target_kind="workspace_repo",
                target_id=str(repo_id),
                payload={
                    "slug": entry.slug,
                    "secret_name": spec.secret_name,
                    "github_key_id": key_id,
                    "secret_cleared": False,
                },
            )
        )

    await session.flush()
    return AgentSecretsPushOut(pushed=pushed, failed=failed)
