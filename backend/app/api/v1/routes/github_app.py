"""GitHub App install + webhook routes (pilot WOW-onboarding).

Three endpoints:

- ``POST /v1/integrations/github/install/start`` — admin-only, returns a
  redirect URL to GitHub's install picker carrying a signed state token
  bound to the workspace.
- ``GET /v1/integrations/github/install/callback`` — public (no session),
  consumed by GitHub's redirect. Validates state, persists the
  installation, then bounces the browser back to the console onboarding.
- ``POST /v1/webhooks/github`` — public, HMAC-verified. For pilot Day 1
  we only acknowledge ``installation`` / ``installation_repositories``
  events to keep the row in sync; PR/CI handlers land on Day 3.

Listed integrations (the generic ``Integration`` row) are *not* used here
— a GitHub App install has its own table because the shape doesn't fit
the "single secret per kind" model (see
:mod:`backend.app.db.models.integrations`).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_ADMIN, _require_membership
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import PullRequest, WorkflowRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.github.app_auth import (
    GitHubAppMisconfigured,
    invalidate_installation_token_cache,
)
from backend.app.integrations.github.oauth import (
    InvalidInstallState,
    build_install_state,
    build_install_url,
    verify_install_state,
)
from backend.app.integrations.github.webhook import (
    InvalidWebhookSignature,
    verify_signature,
)


logger = logging.getLogger(__name__)

router = APIRouter(tags=["github-app"])


class InstallStartResponse(BaseModel):
    """Payload returned by the install-start endpoint."""

    install_url: str
    # Echoed back so the console can store it in sessionStorage for a
    # belt-and-braces second factor on callback (we already verify the
    # signed state on the backend; this is purely UX). Plaintext nonce is
    # fine: it's not a secret, just an opaque correlator.
    state: str


class InstallationOut(BaseModel):
    workspace_id: uuid.UUID
    installation_id: int
    account_login: str | None
    account_type: str | None
    repository_selection: str | None
    installed_at: datetime | None


def _row_to_out(row: GitHubInstallation) -> InstallationOut:
    return InstallationOut(
        workspace_id=row.workspace_id,
        installation_id=row.installation_id,
        account_login=row.account_login,
        account_type=row.account_type,
        repository_selection=row.repository_selection,
        installed_at=row.installed_at,
    )


@router.post(
    "/integrations/github/install/start",
    response_model=InstallStartResponse,
)
async def install_start(
    workspace_id: uuid.UUID = Query(..., description="Workspace to attach the install to"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InstallStartResponse:
    """Mint a signed state token and return the GitHub install URL.

    The console hits this from the "Connect your GitHub" onboarding step;
    the response gets used as ``window.location.assign(install_url)``.
    Admin-only because attaching an App installation is a workspace-wide
    credential.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    state = build_install_state(workspace_id, settings=settings)
    try:
        install_url = await build_install_url(state, settings=settings)
    except GitHubAppMisconfigured as exc:
        # Operator hasn't filled in GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY,
        # so we can neither read the slug from GitHub nor mint installation
        # tokens later. 503 with a clear message → console surfaces the
        # ``app_not_configured`` banner next to the install button.
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    return InstallStartResponse(
        install_url=install_url,
        state=state,
    )


@router.get("/integrations/github/install/callback")
async def install_callback(
    state: str,
    installation_id: int,
    setup_action: str | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """GitHub's redirect target after the user picks repos and confirms.

    No authentication — GitHub's redirect carries no user session, and we
    rely on the signed ``state`` token to prove this callback was kicked
    off by an admin who legitimately started the install. After
    persistence we bounce the browser back to
    ``/onboarding?step=tracker`` (the next step in the wizard).
    """
    try:
        decoded = verify_install_state(state, settings=settings)
    except InvalidInstallState:
        # Tampered / expired ``state`` token. Don't 500, and don't dump
        # the raw exception — bounce the user back into the wizard with
        # a friendly error code so the onboarding UI can render a
        # human-readable banner. ``ws=`` is unknown here (the state was
        # the only carrier), so fall back to the install entry point
        # which re-asks the user to start over.
        return RedirectResponse(
            url=(
                f"{settings.console_url.rstrip('/')}/onboarding"
                "?step=github&error=bad_state"
            )
        )

    # ``setup_action`` is "install" on first install, "update" on
    # repo-selection edit, "request" when the user lacks org permission
    # and submitted an admin-approval request. We treat install/update
    # identically; "request" means there's nothing to persist yet.
    if setup_action == "request":
        # Bounce back; the console UI will surface "awaiting org admin".
        return RedirectResponse(
            url=_console_onboarding_url(settings, step="github", reason="request")
        )

    stmt = select(GitHubInstallation).where(
        GitHubInstallation.installation_id == installation_id
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = GitHubInstallation(
            workspace_id=decoded.workspace_id,
            installation_id=installation_id,
            installed_at=datetime.now(timezone.utc),
        )
        session.add(row)
        action_kind = "github.install.create"
    else:
        # Re-install (e.g. into a different workspace) — overwrite the
        # workspace binding because the unique key is installation_id.
        row.workspace_id = decoded.workspace_id
        row.installed_at = datetime.now(timezone.utc)
        row.suspended_at = None
        action_kind = "github.install.update"

    row.updated_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            workspace_id=decoded.workspace_id,
            actor_user_id=None,  # callback has no session; authoritative
            actor_token_id=None,  # link is on installation_id below.
            action=action_kind,
            target_kind="github_installation",
            target_id=str(installation_id),
            payload={
                "installation_id": installation_id,
                "setup_action": setup_action,
            },
        )
    )
    await session.flush()

    # Drop any stale cached token in case we updated the same install row.
    invalidate_installation_token_cache(installation_id)

    # Jump straight to the repo picker — that's the next thing the user
    # has to do, and the picker page renders a "GitHub App installed"
    # success banner above the list. Staying on the github step would
    # require an extra "Pick repos →" click for no payoff.
    return RedirectResponse(
        url=_console_onboarding_url(settings, step="repos", reason="installed")
    )


@router.post("/webhooks/github", status_code=status.HTTP_200_OK)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Ingest a GitHub webhook delivery.

    Day 1 scope: verify signature + persist installation lifecycle events.
    Pull-request / workflow_run handlers land on Day 3 when default
    pipelines come online.
    """
    raw = await request.body()
    try:
        verify_signature(raw, x_hub_signature_256, settings=settings)
    except InvalidWebhookSignature as exc:
        # 401 (not 400) so misconfigured ngrok / wrong secret shows up as
        # an auth issue in GitHub's "Recent Deliveries" UI.
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="malformed JSON body") from exc

    event = (x_github_event or "").lower()
    action = payload.get("action")

    if event == "installation":
        await _apply_installation_event(session, payload, action)
        # Explicit flush so callers (and tests) see the row mutations
        # without relying on autoflush ordering.
        await session.flush()
    elif event == "installation_repositories":
        # We don't persist the per-repo selection yet (the API call to
        # ``/installation/repositories`` is the source of truth at request
        # time); just bust the token cache so the next call refetches.
        installation_id = payload.get("installation", {}).get("id")
        if installation_id:
            invalidate_installation_token_cache(int(installation_id))
    elif event == "pull_request":
        # Day-3: write-through cache so the dashboard "Recent PRs" panel
        # has live data without per-render API hops. Drop silently if the
        # PR's repo isn't activated for any workspace — we don't want to
        # cache PRs nobody asked us to track.
        await _apply_pull_request_event(session, payload, action)
        await session.flush()
    elif event == "workflow_run":
        # Same idea for GitHub Actions runs. Useful for self-heal pipeline
        # in Day-4 when we start reacting to ``conclusion=failure``.
        await _apply_workflow_run_event(session, payload, action)
        await session.flush()
    else:
        # Day-1: silently 200 every other event. We log at debug to keep
        # signal high and avoid filling Sentry breadcrumbs with noise.
        logger.debug(
            "github webhook ignored event=%s action=%s", event, action
        )
    return {"ok": True, "event": event, "action": action}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _console_onboarding_url(settings: Settings, *, step: str, reason: str) -> str:
    """Build the redirect URL back into the console onboarding wizard.

    We never trust an arbitrary ``returnTo`` from the query string — the
    onboarding URL is hard-coded under the configured *console* origin
    (not the API origin, which is where ``public_url`` typically points
    in cloud SaaS), and the only variability is the ``step`` + ``reason``
    query params the wizard consumes for messaging.
    """
    base = settings.console_url.rstrip("/")
    return f"{base}/onboarding?step={step}&github={reason}"


async def _apply_installation_event(
    session: AsyncSession, payload: dict[str, Any], action: str | None
) -> None:
    """Mutate :class:`GitHubInstallation` in response to an install event."""
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    if not installation_id:
        # Malformed payload; let GitHub retry with a 200 instead of a
        # blocking 4xx (we have no useful action to take).
        logger.warning("installation event missing installation.id, skipping")
        return

    stmt = select(GitHubInstallation).where(
        GitHubInstallation.installation_id == int(installation_id)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()

    account = installation.get("account") or {}
    repo_selection = installation.get("repository_selection")
    now = datetime.now(timezone.utc)

    if action in {"created", "new_permissions_accepted", "unsuspend"}:
        # ``created`` arrives in parallel with the OAuth callback — the
        # callback usually wins the race because it's sync, but we still
        # backfill account metadata here for the cases where it doesn't.
        if row is not None:
            row.account_id = account.get("id")
            row.account_login = account.get("login")
            row.account_type = account.get("type")
            row.repository_selection = repo_selection
            row.suspended_at = None
            row.updated_at = now
        # If row is None, the OAuth callback hasn't landed yet; no-op and
        # trust the callback to insert the row with the workspace binding.
    elif action == "suspend":
        if row is not None:
            row.suspended_at = now
            row.updated_at = now
            invalidate_installation_token_cache(int(installation_id))
    elif action == "deleted":
        # User uninstalled the App. We delete the row so re-install starts
        # from a clean slate; cascade on workspace_id stays intact because
        # we only delete the install row.
        if row is not None:
            await session.delete(row)
            invalidate_installation_token_cache(int(installation_id))
    else:
        logger.debug("installation event with unhandled action=%s", action)


async def _resolve_workspace_repo(
    session: AsyncSession,
    installation_id: int | None,
    repo_external_id: int | None,
) -> WorkspaceRepo | None:
    """Look up the activated repo row for an incoming webhook event.

    Webhooks carry GitHub's numeric ``installation.id`` and
    ``repository.id``. We map ``installation.id`` → our internal
    :class:`GitHubInstallation` row, then join to :class:`WorkspaceRepo`
    on the (UUID install id + numeric repo id). Returns ``None`` if
    either lookup misses — in which case the webhook is dropped
    silently (the user activated repo X but not repo Y, which is fine).
    """
    if not installation_id or not repo_external_id:
        return None
    install = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == int(installation_id)
            )
        )
    ).scalar_one_or_none()
    if install is None:
        return None
    stmt = select(WorkspaceRepo).where(
        WorkspaceRepo.installation_id == install.id,
        WorkspaceRepo.external_id == int(repo_external_id),
    )
    return (await session.execute(stmt)).scalars().first()


def _parse_iso(value: Any) -> datetime | None:
    """Tolerant ISO-8601 parser for webhook timestamps.

    GitHub serialises with trailing ``Z`` which Python's stdlib only
    learned to parse in 3.11; we normalise to ``+00:00`` for safety.
    Returns ``None`` if the value is missing or malformed — webhooks
    must never crash us over a date field.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _apply_pull_request_event(
    session: AsyncSession, payload: dict[str, Any], action: str | None
) -> None:
    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    install = payload.get("installation") or {}
    external_id = pr.get("id")
    if not external_id:
        logger.warning("pull_request event missing pull_request.id, skipping")
        return

    repo_row = await _resolve_workspace_repo(
        session, install.get("id"), repo.get("id")
    )
    if repo_row is None:
        # PR for a repo the user hasn't activated. Drop silently —
        # the wider GitHub App may receive deliveries for the whole
        # account selection.
        logger.debug(
            "pull_request event for inactive repo external_id=%s",
            repo.get("id"),
        )
        return

    state = pr.get("state") or "open"
    merged = bool(pr.get("merged"))
    # Closed + merged is GitHub's idiom for "merged"; surface it as a
    # synthetic state so the dashboard can colour-code without a join.
    if merged and state == "closed":
        state = "merged"

    user = pr.get("user") or {}
    title = pr.get("title") or "(untitled)"
    html_url = pr.get("html_url") or ""

    existing = (
        await session.execute(
            select(PullRequest).where(
                PullRequest.workspace_id == repo_row.workspace_id,
                PullRequest.external_id == int(external_id),
            )
        )
    ).scalars().first()

    if existing is None:
        session.add(
            PullRequest(
                workspace_id=repo_row.workspace_id,
                repo_id=repo_row.id,
                external_id=int(external_id),
                number=int(pr.get("number") or 0),
                repo_full_name=repo_row.full_name,
                title=title[:1024],
                state=state,
                merged=merged,
                draft=bool(pr.get("draft")),
                author=(user.get("login") or None),
                html_url=html_url[:1024],
                opened_at=_parse_iso(pr.get("created_at")),
                updated_at_external=_parse_iso(pr.get("updated_at")),
                closed_at=_parse_iso(pr.get("closed_at")),
                merged_at=_parse_iso(pr.get("merged_at")),
            )
        )
    else:
        existing.repo_id = repo_row.id
        existing.title = title[:1024]
        existing.state = state
        existing.merged = merged
        existing.draft = bool(pr.get("draft"))
        existing.author = user.get("login") or existing.author
        existing.html_url = html_url[:1024] or existing.html_url
        existing.opened_at = _parse_iso(pr.get("created_at")) or existing.opened_at
        existing.updated_at_external = (
            _parse_iso(pr.get("updated_at")) or existing.updated_at_external
        )
        existing.closed_at = _parse_iso(pr.get("closed_at")) or existing.closed_at
        existing.merged_at = _parse_iso(pr.get("merged_at")) or existing.merged_at

    logger.debug("pull_request cached action=%s pr_id=%s", action, external_id)


async def _apply_workflow_run_event(
    session: AsyncSession, payload: dict[str, Any], action: str | None
) -> None:
    run = payload.get("workflow_run") or {}
    repo = payload.get("repository") or {}
    install = payload.get("installation") or {}
    external_id = run.get("id")
    if not external_id:
        logger.warning("workflow_run event missing workflow_run.id, skipping")
        return

    repo_row = await _resolve_workspace_repo(
        session, install.get("id"), repo.get("id")
    )
    if repo_row is None:
        logger.debug(
            "workflow_run event for inactive repo external_id=%s",
            repo.get("id"),
        )
        return

    actor = run.get("actor") or run.get("triggering_actor") or {}

    existing = (
        await session.execute(
            select(WorkflowRun).where(
                WorkflowRun.workspace_id == repo_row.workspace_id,
                WorkflowRun.external_id == int(external_id),
            )
        )
    ).scalars().first()

    name = (run.get("name") or run.get("display_title") or "Workflow")[:255]
    html_url = run.get("html_url") or ""

    if existing is None:
        session.add(
            WorkflowRun(
                workspace_id=repo_row.workspace_id,
                repo_id=repo_row.id,
                external_id=int(external_id),
                repo_full_name=repo_row.full_name,
                name=name,
                event=run.get("event"),
                status=run.get("status") or "unknown",
                conclusion=run.get("conclusion"),
                head_branch=run.get("head_branch"),
                head_sha=run.get("head_sha"),
                actor=actor.get("login") if isinstance(actor, dict) else None,
                html_url=html_url[:1024] or None,
                started_at=_parse_iso(run.get("run_started_at"))
                or _parse_iso(run.get("created_at")),
                finished_at=_parse_iso(run.get("updated_at"))
                if run.get("status") == "completed"
                else None,
            )
        )
    else:
        existing.repo_id = repo_row.id
        existing.name = name
        existing.event = run.get("event") or existing.event
        existing.status = run.get("status") or existing.status
        existing.conclusion = run.get("conclusion") or existing.conclusion
        existing.head_branch = run.get("head_branch") or existing.head_branch
        existing.head_sha = run.get("head_sha") or existing.head_sha
        if isinstance(actor, dict) and actor.get("login"):
            existing.actor = actor.get("login")
        existing.html_url = (html_url[:1024] or existing.html_url)
        existing.started_at = (
            _parse_iso(run.get("run_started_at"))
            or _parse_iso(run.get("created_at"))
            or existing.started_at
        )
        if run.get("status") == "completed":
            existing.finished_at = (
                _parse_iso(run.get("updated_at")) or existing.finished_at
            )

    logger.debug("workflow_run cached action=%s run_id=%s", action, external_id)


__all__ = ["router", "InstallStartResponse", "InstallationOut"]
