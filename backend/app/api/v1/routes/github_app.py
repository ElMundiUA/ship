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
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_ADMIN, _require_membership
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import Pipeline, PipelineRun, PullRequest, WorkflowRun
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
    installation_id: int,
    state: str | None = None,
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

    ``state`` is **optional** because GitHub omits it whenever the user
    enters the install picker outside of our wizard — clicking
    "Configure" on the App page, hitting the App's public install URL
    directly, or following the "Redirect on update" path when GitHub
    didn't have a state to forward (e.g. repo-selection tweak from the
    org settings UI). Three cases:

    1. **state present + valid** → trust the workspace_id from the JWT.
       This is the WOW-onboarding happy path.
    2. **state missing + installation_id already known** → idempotent
       refresh; we already have a workspace binding so we just touch
       ``updated_at`` and let the user back into the dashboard.
    3. **state missing + brand new installation_id** → we genuinely
       don't know which workspace this should attach to. Send the user
       back to the wizard with a banner so they re-enter through the
       "Install" button (which mints a state token tied to their
       workspace).
    """
    decoded_workspace_id: uuid.UUID | None = None
    if state is not None:
        try:
            decoded = verify_install_state(state, settings=settings)
            decoded_workspace_id = decoded.workspace_id
        except InvalidInstallState:
            # Tampered / expired ``state`` token. Treat as missing —
            # falling through to the lookup path is safer than 401-ing
            # the operator out of their own callback URL.
            decoded_workspace_id = None

    # ``setup_action`` is "install" on first install, "update" on
    # repo-selection edit, "request" when the user lacks org permission
    # and submitted an admin-approval request.
    if setup_action == "request":
        # Bounce back; the console UI will surface "awaiting org admin".
        return RedirectResponse(
            url=_console_onboarding_url(settings, step="github", reason="request")
        )

    stmt = select(GitHubInstallation).where(
        GitHubInstallation.installation_id == installation_id
    )
    row = (await session.execute(stmt)).scalar_one_or_none()

    if row is None and decoded_workspace_id is None:
        # Brand new install but the redirect didn't come through our
        # wizard, so we can't pick a workspace deterministically. Don't
        # silently attach to the user's first workspace — instead nudge
        # them back through the wizard where the "Install" button mints
        # a state token bound to a specific workspace.
        return RedirectResponse(
            url=(
                f"{settings.console_url.rstrip('/')}/onboarding"
                "?step=github&error=missing_state"
            )
        )

    if row is None:
        # decoded_workspace_id is non-None here (guarded by the branch
        # above); narrow the type for the persistence path.
        assert decoded_workspace_id is not None
        target_workspace_id = decoded_workspace_id
        row = GitHubInstallation(
            workspace_id=target_workspace_id,
            installation_id=installation_id,
            installed_at=datetime.now(timezone.utc),
        )
        session.add(row)
        action_kind = "github.install.create"
    else:
        # Existing install. If the wizard handed us a state token,
        # re-bind to the (possibly new) workspace; otherwise leave the
        # existing binding alone — the user is just re-confirming repo
        # selection from the GitHub UI.
        if decoded_workspace_id is not None:
            row.workspace_id = decoded_workspace_id
        target_workspace_id = row.workspace_id
        row.installed_at = datetime.now(timezone.utc)
        row.suspended_at = None
        action_kind = "github.install.update"

    row.updated_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            workspace_id=target_workspace_id,
            actor_user_id=None,  # callback has no session; authoritative
            actor_token_id=None,  # link is on installation_id below.
            action=action_kind,
            target_kind="github_installation",
            target_id=str(installation_id),
            payload={
                "installation_id": installation_id,
                "setup_action": setup_action,
                "had_state": state is not None,
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
    elif event == "push":
        # C12: a push to the repo's default branch that touches
        # ``.ship/knowledge/`` invalidates the agent's KB corpus. We
        # run the reindexer inline because corpora are small (dozens
        # of docs). If that becomes a hot spot we'll move it to a
        # background task; for now keeping it in-request gives us a
        # crisp 502 when embeddings are misconfigured instead of a
        # silent delivery-succeeded / indexer-failed split.
        await _apply_push_event_for_kb(session, payload, settings=settings)
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

    # Capture whether the *prior* cached state already knew this PR as
    # merged — we only want to mint the A4 "PR merged" banner on the
    # *transition*, not on every replayed webhook.
    was_merged_before = bool(existing.merged) if existing is not None else False

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

    head_ref = str((pr.get("head") or {}).get("ref") or "")
    just_merged = merged and state == "merged" and not was_merged_before
    is_install_pr = head_ref.startswith("ship/install-")

    # A4 "Return-to-Ship": when a PR that *isn't* our own install PR
    # merges, mint a dashboard banner so the user who clicked Merge
    # on github.com lands back on a welcoming callout instead of an
    # unchanged dashboard. Install PRs already get their own
    # ``back_from_pr`` banner + knowledge-lane auto-dispatch below,
    # so skipping them avoids double-notification noise.
    if just_merged and not is_install_pr:
        from backend.app.services.notifications import (
            record_pr_merged_notification,
        )

        try:
            await record_pr_merged_notification(
                session,
                workspace_id=repo_row.workspace_id,
                pr_external_id=int(external_id),
                pr_number=int(pr.get("number") or 0),
                repo_full_name=repo_row.full_name,
                title=title,
                html_url=html_url,
                author=(user.get("login") or None),
            )
        except Exception as exc:  # pragma: no cover - log & move on
            logger.warning(
                "pr_merged banner write failed repo=%s pr=%s: %s",
                repo_row.full_name,
                external_id,
                exc,
            )

    # When a ``ship/install-*`` PR merges we own the cache that decides
    # whether the dashboard still shows "Install workflow PR →" or
    # flips to "Run now". Bust it eagerly so the next render (or the
    # ``back_from_pr`` landing bounce) reflects reality inside a
    # second instead of waiting out the 60s probe TTL.
    if merged and state == "merged" and is_install_pr:
        from backend.app.integrations.github.workflows import (
            invalidate_workflow_list_cache,
        )

        install_id = install.get("id")
        if isinstance(install_id, int):
            invalidate_workflow_list_cache(
                installation_id=install_id, full_name=repo_row.full_name
            )
            logger.info(
                "busted workflow-list cache after install PR merge "
                "repo=%s branch=%s",
                repo_row.full_name,
                head_ref,
            )

        # Kick off the knowledge-gathering lanes (tech_debt / code_map)
        # so the operator's first post-merge dashboard load lands on
        # actual data instead of empty cards. Best-effort: a flaky
        # dispatch shouldn't fail the webhook ack.
        try:
            install_row = None
            if isinstance(install_id, int):
                install_row = (
                    await session.execute(
                        select(GitHubInstallation).where(
                            GitHubInstallation.installation_id == install_id
                        )
                    )
                ).scalars().first()
            if install_row is not None:
                from backend.app.api.v1.routes.pipelines import (
                    auto_dispatch_knowledge_pipelines,
                )
                from backend.app.core.config import get_settings

                created = await auto_dispatch_knowledge_pipelines(
                    session,
                    repo_row,
                    install_row,
                    settings=get_settings(),
                )
                if created:
                    logger.info(
                        "auto-dispatched %d knowledge run(s) after install PR merge "
                        "repo=%s",
                        len(created),
                        repo_row.full_name,
                    )
        except Exception as exc:  # pragma: no cover - log & move on
            logger.warning(
                "auto-dispatch after install PR merge failed repo=%s: %s",
                repo_row.full_name,
                exc,
            )


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

    # Day-4 Phase-1: if the webhook describes a workflow we
    # dispatched (the starter file uses ``name: Ship · ...``), enrich
    # the matching :class:`PipelineRun` with the GitHub-side run id +
    # html_url so the dashboard can deep-link, and let the webhook
    # win the terminal-state race when the in-runner callback
    # couldn't reach us (e.g. customer egress firewalls).
    is_ship_workflow = name.startswith("Ship · ")
    if is_ship_workflow:
        await _reconcile_pipeline_run_from_webhook(
            session,
            repo_row,
            run,
            html_url=html_url,
        )

    logger.debug("workflow_run cached action=%s run_id=%s", action, external_id)

    # A5 "Self-heal auto-trigger": when a non-Ship workflow completes
    # with conclusion=failure, dispatch the self-heal lane (if the
    # pipeline is enabled & the YAML is installed) and post a banner
    # so the dashboard surfaces the intervention.
    #
    # We skip our own ``Ship · …`` workflows to avoid a feedback
    # loop: self_heal lanes can themselves fail (bad config, upstream
    # outage) and recursively auto-dispatching another self_heal on
    # that failure would amplify the noise we're trying to damp.
    await _maybe_trigger_self_heal(
        session,
        repo_row,
        run,
        html_url=html_url,
        is_ship_workflow=is_ship_workflow,
    )


async def _apply_push_event_for_kb(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    settings: Settings,
) -> None:
    """Re-embed the KB when a push touches ``.ship/knowledge/``.

    We only care about pushes to the repo's default branch — pushes
    to feature branches would force us to keep per-branch indexes,
    which the agent isn't meant to reason about. The default branch
    is also the only ref ``search_repo_kb`` ever resolves against.

    The heuristic for "touches KB" is deliberately loose: we inspect
    the ``commits[].added/modified/removed`` arrays GitHub sends in
    the push payload. Missing those (huge pushes get truncated by
    GitHub) falls back to "run the indexer unconditionally" — the
    indexer itself is SHA-diffed so the worst case is a cheap no-op
    pass.
    """
    from backend.app.services.agent.kb_indexer import KB_ROOT, reindex_repo_kb

    install_id = (payload.get("installation") or {}).get("id")
    repo_ext_id = (payload.get("repository") or {}).get("id")
    repo_row = await _resolve_workspace_repo(session, install_id, repo_ext_id)
    if repo_row is None:
        return

    # Pushes come as ``refs/heads/<branch>``. Skip anything that
    # isn't the default branch.
    ref_name = str(payload.get("ref") or "")
    default_branch = repo_row.default_branch or "main"
    if ref_name != f"refs/heads/{default_branch}":
        return

    # Fast-path: if commits payload is present, only reindex when a
    # KB path is in the diff. Keeps noisy monorepo pushes cheap.
    commits = payload.get("commits") or []
    if commits:
        touched_kb = False
        for commit in commits:
            for key in ("added", "modified", "removed"):
                for path in commit.get(key) or []:
                    if isinstance(path, str) and path.startswith(KB_ROOT + "/"):
                        touched_kb = True
                        break
                if touched_kb:
                    break
            if touched_kb:
                break
        if not touched_kb:
            return

    install = await session.get(GitHubInstallation, repo_row.installation_id)
    if install is None or install.suspended_at is not None:
        return

    try:
        report = await reindex_repo_kb(session, repo_row, install, settings=settings)
    except RuntimeError as exc:
        # Missing OPENAI_API_KEY, almost certainly. Don't fail the
        # webhook — GitHub would just retry and we'd keep failing.
        # Operator sees this in logs + the next manual reindex will
        # surface the same error as a 412.
        logger.warning(
            "push → KB reindex skipped for %s: %s", repo_row.full_name, exc
        )
        return

    logger.info(
        "push → KB reindex for %s: files_indexed=%d chunks_written=%d",
        repo_row.full_name,
        report.files_indexed,
        report.chunks_written,
    )


# Map GitHub ``workflow_run.conclusion`` values onto our
# :class:`PipelineRun.status` vocabulary. ``timed_out`` and ``action_required``
# both map to ``failed`` because the dashboard's run timeline only
# colour-codes terminal failures vs successes; finer detail lives in
# the linked GitHub Actions UI.
_GH_CONCLUSION_TO_STATUS: dict[str, str] = {
    "success": "succeeded",
    "failure": "failed",
    "timed_out": "failed",
    "action_required": "failed",
    "neutral": "succeeded",
    "cancelled": "cancelled",
    "skipped": "cancelled",
    "stale": "failed",
}


async def _maybe_trigger_self_heal(
    session: AsyncSession,
    repo_row: WorkspaceRepo,
    run: dict[str, Any],
    *,
    html_url: str,
    is_ship_workflow: bool,
) -> None:
    """A5: on ``workflow_run.failure`` auto-dispatch the self_heal lane.

    Preconditions stacked in order of cheapest check first: ignore
    non-terminal events, ignore our own ``Ship · …`` workflows (to
    avoid recursive self-heal-on-self-heal loops), then look up the
    installation for ``workflow_dispatch`` and defer to
    :func:`auto_dispatch_self_heal` for the heavy lifting.

    Every branch that *could* have auto-healed (dispatched, skipped,
    failed-at-dispatch) drops a :class:`WorkspaceNotification` so the
    dashboard's banner rail reflects reality; silent branches (non-
    failure events, our own workflows) leave no trace.
    """
    if is_ship_workflow:
        return
    if run.get("status") != "completed":
        return
    if run.get("conclusion") != "failure":
        return
    external_id = run.get("id")
    if not isinstance(external_id, int):
        return

    name = (run.get("name") or run.get("display_title") or "Workflow")[:255]

    # Late import: avoids an import cycle — pipelines.py already
    # imports from backend.app.services.notifications (via
    # auto_dispatch_self_heal's result) and github_app is imported by
    # pipelines.py indirectly through the webhook router registration.
    from backend.app.api.v1.routes.pipelines import auto_dispatch_self_heal
    from backend.app.core.config import get_settings
    from backend.app.services.notifications import (
        record_self_heal_notification,
    )

    # Resolve the installation so auto_dispatch_self_heal can mint the
    # workflow_dispatch JWT. If it's gone we still record a "skipped"
    # banner — the user needs to know Ship *tried* to act.
    install_id = repo_row.installation_id
    install_row = None
    if install_id is not None:
        install_row = (
            await session.execute(
                select(GitHubInstallation).where(
                    GitHubInstallation.id == install_id
                )
            )
        ).scalars().first()
    if install_row is None:
        try:
            await record_self_heal_notification(
                session,
                workspace_id=repo_row.workspace_id,
                kind="self_heal_skipped",
                failed_run_external_id=int(external_id),
                repo_full_name=repo_row.full_name,
                failed_workflow_name=name,
                failed_run_url=html_url or None,
                reason="GitHub App installation missing",
            )
        except Exception as exc:  # pragma: no cover - log & move on
            logger.warning(
                "self_heal notification write failed repo=%s: %s",
                repo_row.full_name,
                exc,
            )
        return

    try:
        result = await auto_dispatch_self_heal(
            session,
            repo_row,
            install_row,
            settings=get_settings(),
            failed_run_external_id=int(external_id),
            failed_workflow_name=name,
        )
    except Exception as exc:  # pragma: no cover - upstream flake
        logger.warning(
            "self-heal auto-dispatch raised repo=%s run=%s: %s",
            repo_row.full_name,
            external_id,
            exc,
        )
        return

    notif_kind: str
    notif_reason: str | None = None
    if result.status == "dispatched":
        notif_kind = "self_heal_dispatched"
    elif result.status.startswith("failed:"):
        notif_kind = "self_heal_skipped"
        notif_reason = f"dispatch error ({result.status.split(':', 1)[1]})"
    elif result.status == "skipped:pipeline_missing":
        # No self_heal row at all — this is the user's first repo
        # activation without our default lane seeded. Stay quiet:
        # advertising an un-configured feature on every CI failure
        # would be spammy.
        return
    elif result.status == "skipped:pipeline_disabled":
        notif_kind = "self_heal_skipped"
        notif_reason = "self_heal pipeline is disabled"
    elif result.status == "skipped:workflow_not_installed":
        notif_kind = "self_heal_skipped"
        notif_reason = "self_heal workflow YAML not installed in the repo"
    elif result.status == "skipped:kind_not_supported":
        return
    else:
        notif_kind = "self_heal_skipped"
        notif_reason = result.status

    try:
        await record_self_heal_notification(
            session,
            workspace_id=repo_row.workspace_id,
            kind=notif_kind,
            failed_run_external_id=int(external_id),
            repo_full_name=repo_row.full_name,
            failed_workflow_name=name,
            failed_run_url=html_url or None,
            healing_run_id=result.run_id,
            reason=notif_reason,
        )
    except Exception as exc:  # pragma: no cover - log & move on
        logger.warning(
            "self_heal notification write failed repo=%s: %s",
            repo_row.full_name,
            exc,
        )


async def _reconcile_pipeline_run_from_webhook(
    session: AsyncSession,
    repo_row: WorkspaceRepo,
    run: dict[str, Any],
    *,
    html_url: str,
) -> None:
    """Bind a GitHub ``workflow_run`` event back to our :class:`PipelineRun`.

    Correlation strategy: pick the most-recent ``queued``/``running``
    pipeline run for this workspace whose pipeline is bound to the
    same repo. Pilot only dispatches one workflow at a time per
    pipeline kind, so the freshest in-flight run is the right match;
    when more than one is in flight we still bind to the freshest one
    rather than guessing wrong with a stale row.

    The callback endpoint is the *primary* truth source — it carries
    a per-pipeline summary the workflow author chose. The webhook
    only fills in fields the callback can't (the GitHub Actions
    ``html_url`` + the upstream ``conclusion`` for cases where the
    callback failed to reach us).
    """
    gh_run_id_raw = run.get("id")
    try:
        gh_run_id = int(gh_run_id_raw) if gh_run_id_raw is not None else None
    except (TypeError, ValueError):
        gh_run_id = None

    # Try the cheap path first: is this exactly the run we already
    # know about (callback recorded gh_workflow_run_id)?
    pipeline_run: PipelineRun | None = None
    if gh_run_id is not None:
        cast_id = str(gh_run_id)
        # ``payload`` is JSONB; ``->'metrics'->>'gh_workflow_run_id'``
        # via the ORM is awkward, so we filter in Python after a
        # bounded "recent runs in this workspace" prefilter. Pilot
        # volumes make this fine; we'd revisit if it ever shows up.
        candidate_stmt = (
            select(PipelineRun)
            .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
            .where(Pipeline.repo_id == repo_row.id)
            .where(PipelineRun.workspace_id == repo_row.workspace_id)
            .order_by(desc(PipelineRun.started_at), desc(PipelineRun.created_at))
            .limit(50)
        )
        candidates = (await session.execute(candidate_stmt)).scalars().all()
        for cand in candidates:
            metrics = (cand.payload or {}).get("metrics") or {}
            if str(metrics.get("gh_workflow_run_id") or "") == cast_id:
                pipeline_run = cand
                break

    if pipeline_run is None:
        # Fallback: most-recent in-flight run for this repo.
        in_flight_stmt = (
            select(PipelineRun)
            .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
            .where(Pipeline.repo_id == repo_row.id)
            .where(PipelineRun.workspace_id == repo_row.workspace_id)
            .where(PipelineRun.status.in_(("queued", "running")))
            .order_by(desc(PipelineRun.started_at), desc(PipelineRun.created_at))
            .limit(1)
        )
        pipeline_run = (
            await session.execute(in_flight_stmt)
        ).scalars().first()

    if pipeline_run is None:
        # Nothing to enrich. The user might have triggered the
        # workflow themselves via the GitHub UI, in which case we
        # leave it at the cached ``WorkflowRun`` row.
        return

    payload = dict(pipeline_run.payload or {})
    metrics = dict(payload.get("metrics") or {})
    if gh_run_id is not None:
        metrics.setdefault("gh_workflow_run_id", gh_run_id)
    if html_url:
        metrics["gh_html_url"] = html_url[:1024]
    if metrics:
        payload["metrics"] = metrics
        pipeline_run.payload = payload

    gh_status = run.get("status") or ""
    gh_conclusion = run.get("conclusion") or ""
    now = datetime.now(timezone.utc)
    pipeline_row: Pipeline | None = None

    if gh_status == "completed" and pipeline_run.status not in {
        "succeeded",
        "failed",
        "cancelled",
    }:
        # Callback hasn't landed (firewall, runner crash, etc.) —
        # accept the webhook's verdict so the dashboard doesn't show
        # "running" forever.
        mapped = _GH_CONCLUSION_TO_STATUS.get(gh_conclusion, "failed")
        pipeline_run.status = mapped
        pipeline_run.finished_at = (
            _parse_iso(run.get("updated_at")) or now
        )
        if not pipeline_run.summary:
            pipeline_run.summary = (
                f"Reconciled from workflow_run webhook ({gh_conclusion or 'unknown'})"
            )
        pipeline_row = await session.get(Pipeline, pipeline_run.pipeline_id)
        if pipeline_row is not None:
            pipeline_row.last_run_status = mapped
            pipeline_row.last_run_at = pipeline_run.finished_at or now
    pipeline_run.updated_at = now


__all__ = ["router", "InstallStartResponse", "InstallationOut"]
