"""Runs API — dispatch a Routine, list/read runs, callback ingestion.

Successor to ``routes/pipelines.py`` (retired alongside the Pipeline
table in B1+B2). Every endpoint that used to address a ``Pipeline`` row
now addresses a :class:`backend.app.db.models.lanes.Routine` row
directly. The schema bound to GitHub-Actions dispatch is:

- ``POST /v1/workspaces/{ws}/routines/{routine_id}/runs`` mints a
  :class:`RoutineRun` and ``workflow_dispatch``-es the routine's
  starter workflow. Returns ``202`` with a ``running`` row on success;
  ``412`` with a structured ``code`` field when a precondition fails
  (workflow not installed, GitHub App gone, lane unsupported).
- ``POST /v1/runs/{run_id}/result`` is the public callback the
  dispatched workflow hits to report success/failure. Token verifier
  is SHA-256 against :attr:`RoutineRun.run_token_hash` + 30-minute JWT
  exp, so a stolen ``run_id`` alone can't fake a result. Accepts
  long-lived ``SHIP_RUN_TOKEN`` as an alternative for lanes on cron /
  push / PR triggers that can't carry a per-run JWT through
  ``workflow_dispatch.inputs``.

The Pipeline-era ``GET pipelines`` / ``PATCH pipelines/{id}`` toggle /
``POST .../install`` endpoints all retired: routines are declared in
``.ship/config.yml`` and the wizard owns the install PR, so there's no
mutable per-row config left on the runtime side. Soft-disable is on
the roadmap and will land as a column on Routine.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.lanes import Routine, RoutineRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.github.app_auth import GitHubAppMisconfigured
from backend.app.integrations.github.workflows import (
    WorkflowDispatchError,
    dispatch_workflow,
    list_repo_workflows,
)
from backend.app.services import starter_workflows


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lane id ↔ starter workflow mapping
# ---------------------------------------------------------------------------

# Static map after Phase 2.4 Step D snipped the catalog-derived
# ``list_lane_recipes`` builder. The five entries below are every
# lane id this module ever resolved through the recipes path; new
# lanes get a row here. ``code_map`` is resolver-only (no starter
# YAML), so it intentionally maps to ``None`` — callers fall back to
# the "Coming with presets" state instead of 412-ing the user.
#
# A routine's ``pattern`` column carries the catalog slug for routines
# declared via ``.ship/config.yml``; this map is the fallback / source
# of truth for lanes whose pattern isn't populated yet.
_LANE_WORKFLOW_MAP: dict[str, str | None] = {
    "pr_review": "pr-and-ci-gate",
    "daily_standup": "scheduled-sdlc-lane",
    "code_map": None,
    "tech_debt": "parallel-audit-lanes",
    "self_heal": "pipeline-self-heal",
}


def _lane_id_to_workflow_id(lane_id: str) -> str | None:
    return _LANE_WORKFLOW_MAP.get(lane_id)


def _routine_workflow_id(routine: Routine) -> str | None:
    """Catalog slug for a routine's starter workflow, or ``None``.

    Prefers the explicit ``Routine.pattern`` (declared in
    ``.ship/config.yml``) over the lane-id fallback map. ``None`` only
    when both are unset / unknown — caller falls back to the
    "kind_not_supported" 412 path.
    """
    if routine.pattern:
        return routine.pattern
    return _lane_id_to_workflow_id(routine.lane_id)


def _workflow_file_for_routine(routine: Routine) -> str | None:
    """Basename the customer repo will contain, via starter-workflow lookup."""
    workflow_id = _routine_workflow_id(routine)
    if workflow_id is None:
        return None
    return starter_workflows.install_filename(workflow_id)


def _supports_run(routine: Routine) -> bool:
    """True iff we can dispatch the workflow for ``routine``."""
    workflow_id = _routine_workflow_id(routine)
    if workflow_id is None:
        return False
    entry = starter_workflows.get(workflow_id)
    if entry is None:
        return False
    return starter_workflows.read_yaml(workflow_id) is not None


# ---------------------------------------------------------------------------
# Run-token (callback bearer) helpers
# ---------------------------------------------------------------------------

# Subject baked into the callback JWT so a stray Auth0 / install-state
# token can't accidentally satisfy the verifier — defence in depth.
_RUN_TOKEN_SUBJECT: Final[str] = "ship.routine.run"
# Workflow runs typically finish in seconds–minutes; 30 minutes is
# plenty of head-room for a slow runner without leaving a long replay
# window if the inputs leak out of the runner logs.
_RUN_TOKEN_TTL_SECONDS: Final[int] = 30 * 60


def _mint_run_token(run_id: uuid.UUID, settings: Settings) -> str:
    issued_at = int(time.time())
    claims = {
        "sub": _RUN_TOKEN_SUBJECT,
        "rid": str(run_id),
        "nonce": secrets.token_urlsafe(8),
        "iat": issued_at,
        "exp": issued_at + _RUN_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def _decode_run_token(token: str, settings: Settings) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token is invalid or expired",
        ) from exc
    if claims.get("sub") != _RUN_TOKEN_SUBJECT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token has wrong subject",
        )
    raw = claims.get("rid")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token missing rid",
        )
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token has malformed rid",
        ) from exc


def _hash_run_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Shared run-token dependency (used by callback + agent-surface ingress)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunTokenContext:
    """Validated run-token claims bundled with the resolved run row.

    Produced by :func:`get_run_token_context` so downstream handlers
    (routine-authored clarifications, improvements, chat messages)
    never re-validate the token themselves. Treat every field as
    already authenticated — the dependency guarantees:

    - JWT signature ok, ``sub`` == ``ship.routine.run``,
      ``exp`` in the future.
    - ``run_id`` matches an existing :class:`RoutineRun` row.
    - SHA-256 of the raw token matches
      :attr:`RoutineRun.run_token_hash` (belt-and-braces against a
      forged token signed with a leaked secret).

    ``workspace_id`` + ``routine_id`` are snapshotted at validation
    time to avoid re-issuing a SELECT in the handler body.
    ``auth_mode`` records which auth path validated the bearer so
    audit logs and debug tooling can tell "legacy dispatch" from
    "lane-scheduled runner" apart without guessing off the token
    shape.
    """

    run_id: uuid.UUID
    routine_id: uuid.UUID
    workspace_id: uuid.UUID
    raw_token: str
    # "jwt" (per-run JWT) or "repo" (long-lived SHIP_RUN_TOKEN).
    # Only the dual-mode endpoint sets "repo"; handlers that only
    # mount :func:`get_run_token_context` always see "jwt".
    auth_mode: str = "jwt"


def _parse_bearer(authorization: str | None) -> str:
    """Extract the raw bearer value; raise 401 on every failure mode."""

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    raw_token = authorization.split(" ", 1)[1].strip()
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="empty bearer token",
        )
    return raw_token


async def get_run_token_context(
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RunTokenContext:
    """FastAPI dependency that validates the ``Authorization`` header.

    This is the **JWT-only** dependency. Used by clarifications,
    improvements, and chat endpoints — paths where the run id lives
    in the JWT claims and there's no URL path parameter we could
    cross-check a long-lived repo token against.

    Endpoints on the lane-triggered callback path (cron / push / PR
    lanes that can't carry a per-run JWT through
    ``workflow_dispatch.inputs``) use :func:`get_run_or_repo_token_context`
    instead — it accepts either JWT *or* long-lived ``SHIP_RUN_TOKEN``
    and requires a ``run_id`` path parameter for cross-check.
    """
    raw_token = _parse_bearer(authorization)

    run_id = _decode_run_token(raw_token, settings)
    run = await session.get(RoutineRun, run_id)
    if (
        run is None
        or run.run_token_hash is None
        or not secrets.compare_digest(
            run.run_token_hash, _hash_run_token(raw_token)
        )
    ):
        # Collapse "run gone", "token never issued", and "hash
        # mismatch" into a single 401 so tenants can't fingerprint
        # which branch they hit.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token is invalid, expired, or for a missing run",
        )
    return RunTokenContext(
        run_id=run.id,
        routine_id=run.routine_id,
        workspace_id=run.workspace_id,
        raw_token=raw_token,
        auth_mode="jwt",
    )


def _looks_like_jwt(raw_token: str) -> bool:
    """Cheap shape check: compact JWTs are three base64url segments."""

    return raw_token.count(".") == 2


async def get_run_or_repo_token_context(
    run_id: uuid.UUID = Path(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RunTokenContext:
    """Dual-mode auth for the ``/runs/{run_id}/result`` callback.

    Accepts **either** a per-run JWT (legacy workflow_dispatch path)
    **or** a long-lived ``SHIP_RUN_TOKEN`` minted via
    :mod:`backend.app.services.repo_tokens` and stored in repo Actions
    secrets. Endpoints that mount this dependency *must* carry a
    ``{run_id}`` path parameter — it's the cross-reference for both
    paths.

    Both paths converge on the same :class:`RunTokenContext` shape so
    downstream handlers don't branch on auth_mode unless they want to.
    """

    raw_token = _parse_bearer(authorization)

    if _looks_like_jwt(raw_token):
        jwt_rid = _decode_run_token(raw_token, settings)
        run = await session.get(RoutineRun, jwt_rid)
        if (
            run is None
            or run.run_token_hash is None
            or not secrets.compare_digest(
                run.run_token_hash, _hash_run_token(raw_token)
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="run token is invalid, expired, or for a missing run",
            )
        if run.id != run_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="run token does not match path run_id",
            )
        return RunTokenContext(
            run_id=run.id,
            routine_id=run.routine_id,
            workspace_id=run.workspace_id,
            raw_token=raw_token,
            auth_mode="jwt",
        )

    # Long-lived repo-token path. Local import avoids a potential
    # circular dep if ``repo_tokens`` ever grows imports from routes.
    from backend.app.services.repo_tokens import verify_repo_callback_token

    repo = await verify_repo_callback_token(session, raw_token)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token is invalid, expired, or for a missing run",
        )
    run = await session.get(RoutineRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token is invalid, expired, or for a missing run",
        )
    routine = await session.get(Routine, run.routine_id)
    if routine is None or routine.repo_id != repo.id:
        # Forgery case: caller has a valid token for repo A but is
        # trying to report against a run that belongs to repo B.
        # Do not disclose which side is the mismatch.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token does not authorize this run",
        )
    return RunTokenContext(
        run_id=run.id,
        routine_id=run.routine_id,
        workspace_id=run.workspace_id,
        raw_token=raw_token,
        auth_mode="repo",
    )


def _read_starter_yaml_for_routine(routine: Routine) -> str:
    """Return the YAML body for the routine's starter workflow or 412/500."""
    workflow_id = _routine_workflow_id(routine)
    if workflow_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Routine lane {routine.lane_id!r} has no starter "
                    "workflow. Coming with Phase 3 presets."
                ),
            },
        )
    content = starter_workflows.read_yaml(workflow_id)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Workflow {workflow_id!r} for routine "
                    f"{routine.lane_id!r} has no installable YAML yet."
                ),
            },
        )
    return content


# ---------------------------------------------------------------------------
# Routers — workspace-scoped (RBAC) + global (callback only)
# ---------------------------------------------------------------------------


router = APIRouter(
    prefix="/workspaces/{workspace_id}/routines",
    tags=["runs"],
)

# Separate router for endpoints that don't fit the workspace prefix.
# Mounted under ``/v1`` directly so the dispatched workflow only needs
# the run id + bearer token (no session, no workspace path component).
public_router = APIRouter(prefix="/runs", tags=["runs"])


_RUNS_PAGE_LIMIT = 20
_RUNS_PAGE_DEFAULT = 10


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RunSummaryArtifact(BaseModel):
    """One artifact a routine produced (PR, issue, comment, doc, …)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="'pr' | 'issue' | 'comment' | 'doc' | …")
    title: str
    ref: str | None = None


class RunSummaryFindingsBySeverity(BaseModel):
    """Per-severity finding bucket (RFC-0010 §RunSummary)."""

    model_config = ConfigDict(extra="forbid")

    low: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    critical: int = Field(default=0, ge=0)


class RunSummaryEscalation(BaseModel):
    """Hint that the run already kicked an inbox item to a human.

    Pattern-emitted diagnostic — the *authoritative* escalation
    record is :class:`backend.app.db.models.inbox.RunEscalation`,
    written by the intake service. This list is kept on the run
    purely so the Runs detail UI can render badges without an
    extra hop.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["clarification", "improvement", "failure", "approval", "exception"]
    reason: str = Field(
        ..., description="Matches a profile rule's ``when:`` key."
    )


class RunSummary(BaseModel):
    """RFC-0010 §RunSummary contract — outcome of a routine run.

    All fields optional; an empty dict ``{}`` is a valid summary
    (and the column default for legacy rows). Pattern-authored,
    never derived in the UI: ``outcome_text`` is the single-line
    sentence the Runs list renders verbatim, the rest are
    structured signals the FE cards / Inbox intake consume.
    """

    model_config = ConfigDict(extra="forbid")

    outcome_text: str | None = Field(default=None, max_length=500)
    headline: str | None = Field(default=None, max_length=200)

    findings_count: int | None = Field(default=None, ge=0)
    findings_by_severity: RunSummaryFindingsBySeverity | None = None

    artifacts: list[RunSummaryArtifact] = Field(default_factory=list, max_length=50)

    requires_approval: bool = False
    approval_payload: dict[str, Any] = Field(default_factory=dict)

    escalations: list[RunSummaryEscalation] = Field(default_factory=list)


class RunOut(BaseModel):
    id: uuid.UUID
    routine_id: uuid.UUID
    trigger: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    summary: str | None
    payload: dict
    outcome: RunSummary = Field(default_factory=RunSummary)
    created_at: datetime


class RunIn(BaseModel):
    """Optional client-supplied payload for a manual run."""

    note: str | None = Field(
        default=None,
        max_length=500,
        description="Optional human note shown in the run history.",
    )


class RunResultIn(BaseModel):
    """Body of the ``POST /v1/runs/{run_id}/result`` callback."""

    status: str = Field(
        ...,
        description=(
            "Terminal status of the run. One of ``succeeded`` / ``failed`` "
            "/ ``cancelled``. Anything else is rejected with 422."
        ),
    )
    summary: str | None = Field(default=None, max_length=1024)
    metrics: dict = Field(default_factory=dict)
    outcome: RunSummary | None = Field(
        default=None,
        description=(
            "RFC-0010 §RunSummary contract. When present, replaces "
            "``routine_runs.outcome``."
        ),
    )


_TERMINAL_STATUSES: Final[set[str]] = {"succeeded", "failed", "cancelled"}

_INBOX_OPEN: Final[tuple[str, ...]] = ("new", "snoozed")


async def _emit_self_heal_blocker_inbox(
    session: AsyncSession,
    *,
    run: RoutineRun,
    routine: Routine,
) -> None:
    """Mirror a failed self-heal run into the inbox (trusted callback path only)."""
    if routine.lane_id != "self_heal" or run.status != "failed":
        return
    exists = await session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == run.workspace_id,
            InboxItem.source_table == "self_heal_run",
            InboxItem.source_id == run.id,
            InboxItem.status.in_(_INBOX_OPEN),
        )
    )
    if int(exists or 0) > 0:
        return
    from backend.app.services.notify import NotifyLevel, notify

    summary = (run.summary or routine.lane_id or "Self-heal")[:500]
    title = f"Self-heal could not fix: {summary}"[:255]
    # headline rides the InboxItem before_insert backstop — same
    # derive_headline(summary, title) the site called explicitly before.
    await notify(
        session,
        workspace_id=run.workspace_id,
        repo_id=routine.repo_id,
        title=title,
        body=summary,
        level=NotifyLevel.BLOCKER,
        payload={
            "kind": "self_heal_failed",
            "routine_id": str(routine.id),
            "run_id": str(run.id),
        },
        inbox_overrides={
            "title": title,
            "summary": summary,
            "source_table": "self_heal_run",
            "source_id": run.id,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_to_out(row: RoutineRun) -> RunOut:
    raw_outcome = row.outcome or {}
    outcome = RunSummary.model_validate(raw_outcome)
    return RunOut(
        id=row.id,
        routine_id=row.routine_id,
        trigger=row.trigger,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        summary=row.summary,
        payload=row.payload or {},
        outcome=outcome,
        created_at=row.created_at,
    )


async def _load_routine(
    session: AsyncSession, workspace_id: uuid.UUID, routine_id: uuid.UUID
) -> Routine:
    stmt = select(Routine).where(
        Routine.workspace_id == workspace_id, Routine.id == routine_id
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return row


async def _load_repo_and_install(
    session: AsyncSession, routine: Routine
) -> tuple[WorkspaceRepo, GitHubInstallation]:
    """Resolve the repo + install backing a routine or raise 412.

    Centralised so the dispatcher and the install endpoint reuse the
    same precondition story. The 412s carry a ``code`` field the
    console can switch on to decide which CTA to show.

    ``Routine.repo_id`` is NOT NULL by construction (every routine is
    bound to the repo whose ``.ship/config.yml`` declared it), so the
    Pipeline-era "auto-bind to sole repo" tier doesn't apply here.
    """
    repo = await session.get(WorkspaceRepo, routine.repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "routine_repo_missing",
                "message": (
                    "Bound repo no longer exists. Re-activate a repo "
                    "from the wizard."
                ),
            },
        )
    if repo.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Repo isn't backed by a GitHub App installation. "
                    "Reinstall the Ship App."
                ),
            },
        )
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "GitHub App installation is suspended or gone. "
                    "Reinstall the Ship App."
                ),
            },
        )
    return repo, install


def _callback_url(settings: Settings, run_id: uuid.UUID) -> str:
    """Build the absolute callback URL the dispatched workflow hits."""
    base = settings.public_url.rstrip("/")
    return f"{base}/v1/runs/{run_id}/result"


# ---------------------------------------------------------------------------
# Dispatcher (shared by HTTP + Navigator chat tool)
# ---------------------------------------------------------------------------


async def dispatch_routine_run(
    session: AsyncSession,
    settings: Settings,
    routine: Routine,
    *,
    trigger: str,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_token_id: uuid.UUID | None = None,
    audit_extra: dict[str, Any] | None = None,
) -> RoutineRun:
    """Mint a :class:`RoutineRun`, dispatch it to GitHub Actions, audit.

    Shared core for both the HTTP "Run now" route and the Navigator
    chat tool. Centralising it makes "queue a row but forget to
    dispatch" structurally impossible — both surfaces walk the same
    precondition-check + dispatch + audit path.

    Failure modes are surfaced as :class:`HTTPException` with the
    Day-4-Phase-1 ``code`` vocabulary so callers can either let them
    propagate or translate them into their own response shapes:

    - ``412 kind_not_supported_yet`` — lane has no starter workflow.
    - ``412 routine_repo_missing`` — bound repo gone.
    - ``412 github_app_missing`` — install gone or suspended.
    - ``412 workflow_not_installed`` — YAML missing on default branch.
    - ``502 dispatch_failed`` — GitHub itself rejected the dispatch.

    On success the returned :class:`RoutineRun` already has
    ``status='running'``, ``run_token_hash`` set, and ``last_run_*``
    on the routine updated. The caller still owns the outer
    transaction (no commit here).
    """
    workflow_file = _workflow_file_for_routine(routine)
    if workflow_file is None or not _supports_run(routine):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Routine lane {routine.lane_id!r} doesn't ship with a "
                    "catalog workflow yet. Coming with presets."
                ),
            },
        )

    repo, install = await _load_repo_and_install(session, routine)

    files = await list_repo_workflows(repo, install, settings=settings)
    if workflow_file not in files:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "workflow_not_installed",
                "workflow_file": workflow_file,
                "repo_full_name": repo.full_name,
                "message": (
                    f"{repo.full_name!r} doesn't have .github/workflows/"
                    f"{workflow_file}. Re-run the onboarding wizard."
                ),
            },
        )

    now = datetime.now(timezone.utc)
    run = RoutineRun(
        routine_id=routine.id,
        workspace_id=routine.workspace_id,
        trigger=trigger,
        status="queued",
        started_at=now,
        summary=summary or f"Dispatched {routine.lane_id} for {repo.full_name}",
        payload=dict(payload or {}),
    )
    session.add(run)
    # Need ``run.id`` to mint the callback token + URL; flush before
    # talking to GitHub so the FK exists when the callback lands.
    await session.flush()

    token = _mint_run_token(run.id, settings)
    run.run_token_hash = _hash_run_token(token)

    inputs = {
        "ship_run_id": str(run.id),
        "ship_callback_url": _callback_url(settings, run.id),
        "ship_run_token": token,
    }
    try:
        await dispatch_workflow(
            repo,
            install,
            workflow_file,
            inputs=inputs,
            settings=settings,
        )
    except WorkflowDispatchError as exc:
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.summary = (
            f"GitHub dispatch failed (HTTP {exc.status_code}): {exc.message[:256]}"
        )
        routine.last_run_status = "failed"
        routine.last_run_at = run.finished_at
        await session.flush()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "dispatch_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
                "run_id": str(run.id),
            },
        ) from exc

    run.status = "running"
    routine.last_run_at = now
    routine.last_run_status = "running"
    routine.updated_at = now

    audit_payload: dict[str, Any] = {
        "kind": routine.lane_id,
        "trigger": trigger,
        "run_id": str(run.id),
        "repo_full_name": repo.full_name,
        "workflow_file": workflow_file,
    }
    if audit_extra:
        audit_payload.update(audit_extra)
    session.add(
        AuditLog(
            workspace_id=routine.workspace_id,
            actor_user_id=actor_user_id,
            actor_token_id=actor_token_id,
            action="routine.run",
            target_kind="routine",
            target_id=str(routine.id),
            payload=audit_payload,
        )
    )
    await session.flush()
    return run


# ---------------------------------------------------------------------------
# Routes — workspace-scoped
# ---------------------------------------------------------------------------


@router.post(
    "/{routine_id}/runs",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_routine(
    workspace_id: uuid.UUID,
    routine_id: uuid.UUID,
    payload: RunIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RunOut:
    """Dispatch a real GitHub Actions ``workflow_dispatch`` for the routine.

    Returns ``202 Accepted`` with a ``running`` row when GitHub
    acknowledged the dispatch. The terminal state arrives later via
    the result callback or the ``workflow_run`` webhook.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    routine = await _load_routine(session, workspace_id, routine_id)
    if not routine.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Routine is disabled.",
        )

    note = (payload.note if payload else None) or None
    run = await dispatch_routine_run(
        session,
        settings,
        routine,
        trigger="manual",
        summary=note,
        payload={"note": note} if note else {},
        actor_user_id=auth.user.id,
        actor_token_id=auth.token.id if auth.token else None,
        audit_extra={"note": note},
    )
    return _run_to_out(run)


@router.get("/{routine_id}/runs", response_model=list[RunOut])
async def list_routine_runs(
    workspace_id: uuid.UUID,
    routine_id: uuid.UUID,
    limit: int = _RUNS_PAGE_DEFAULT,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[RunOut]:
    """Most-recent-first page of run history for a routine."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _load_routine(session, workspace_id, routine_id)
    capped = max(1, min(limit, _RUNS_PAGE_LIMIT))
    stmt = (
        select(RoutineRun)
        .where(RoutineRun.routine_id == routine_id)
        .order_by(desc(RoutineRun.started_at), desc(RoutineRun.created_at))
        .limit(capped)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_to_out(r) for r in rows]


@router.get(
    "/{routine_id}/runs/{run_id}",
    response_model=RunOut,
)
async def get_routine_run(
    workspace_id: uuid.UUID,
    routine_id: uuid.UUID,
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RunOut:
    """Fetch a single run row (for the console detail page)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _load_routine(session, workspace_id, routine_id)
    run = await session.get(RoutineRun, run_id)
    if (
        run is None
        or run.routine_id != routine_id
        or run.workspace_id != workspace_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _run_to_out(run)


# ---------------------------------------------------------------------------
# Routes — global (callback only, bearer-token auth)
# ---------------------------------------------------------------------------


@public_router.post(
    "/{run_id}/result",
    response_model=RunOut,
)
async def report_run_result(
    run_id: uuid.UUID = Path(...),
    payload: RunResultIn = ...,
    ctx: RunTokenContext = Depends(get_run_or_repo_token_context),
    session: AsyncSession = Depends(get_session),
) -> RunOut:
    """Callback endpoint dispatched or lane-triggered workflows hit.

    Authentication is **either** a per-run JWT or a long-lived
    repo-scoped ``SHIP_RUN_TOKEN``. Either path lands here with an
    authenticated :class:`RunTokenContext` and ``run_id`` already
    cross-checked, so the handler goes straight to state transition.
    Idempotent: a duplicate callback for an already-terminal run
    returns 200 with the existing row.
    """
    if payload.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"status must be one of {sorted(_TERMINAL_STATUSES)}; "
                f"got {payload.status!r}"
            ),
        )

    run = await session.get(RoutineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if run.status in _TERMINAL_STATUSES:
        return _run_to_out(run)

    now = datetime.now(timezone.utc)
    run.status = payload.status
    run.finished_at = now
    if payload.summary:
        run.summary = payload.summary[:1024]
    metrics_payload = dict(payload.metrics or {})
    if metrics_payload:
        existing = dict(run.payload or {})
        existing.setdefault("metrics", {}).update(metrics_payload)
        run.payload = existing
    if payload.outcome is not None:
        run.outcome = payload.outcome.model_dump(mode="json")
    run.updated_at = now

    routine = await session.get(Routine, run.routine_id)
    if routine is not None:
        routine.last_run_status = payload.status
        routine.last_run_at = now
        routine.updated_at = now

    session.add(
        AuditLog(
            workspace_id=run.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="routine.run.callback",
            target_kind="routine_run",
            target_id=str(run.id),
            payload={
                "status": payload.status,
                "metrics": metrics_payload,
                "auth_mode": ctx.auth_mode,
            },
        )
    )
    if routine is not None:
        await _emit_self_heal_blocker_inbox(session, run=run, routine=routine)
    await session.flush()
    return _run_to_out(run)


class PoliciesPreambleOut(BaseModel):
    """Workspace policies rendered as a markdown preamble."""

    preamble: str | None = Field(
        default=None,
        description=(
            "Markdown rendering of the enabled workspace policies, "
            "ready to prepend to the agent prompt. ``null`` when "
            "the workspace has no enabled policies."
        ),
    )


@public_router.get(
    "/{run_id}/policies-preamble",
    response_model=PoliciesPreambleOut,
)
async def get_run_policies_preamble(
    run_id: uuid.UUID = Path(...),
    role: str | None = Query(
        default=None,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description=(
            "Role slug the agent picked the run with. When provided, "
            "the preamble includes role-scoped policies in addition "
            "to globals."
        ),
    ),
    ctx: RunTokenContext = Depends(get_run_or_repo_token_context),
    session: AsyncSession = Depends(get_session),
) -> PoliciesPreambleOut:
    """Return the workspace's prose policies for the run's workspace."""

    from backend.app.services.policies import render_policies_preamble

    preamble = await render_policies_preamble(
        session, ctx.workspace_id, role_slug=role
    )
    return PoliciesPreambleOut(preamble=preamble)


# ---------------------------------------------------------------------------
# Auto-dispatch (knowledge-gathering routines)
# ---------------------------------------------------------------------------

# Routines that are safe to fire automatically after the install PR
# merges. The premise: these read-only scans feed the dashboard's
# "initial knowledge" buckets (code map + tech-debt inventory) so the
# operator's first visit after merging isn't a bunch of empty cards.
# Write-heavy or noisy lanes (pr_review, daily_standup) stay manual.
KNOWLEDGE_ROUTINE_LANE_IDS: Final[frozenset[str]] = frozenset(
    {"tech_debt", "code_map"}
)


async def auto_dispatch_knowledge_routines(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings,
    trigger: str = "auto_post_install",
) -> list[uuid.UUID]:
    """Fire ``workflow_dispatch`` for every knowledge lane on ``repo``.

    Called from the ``pull_request`` webhook when a ``ship/install-*``
    branch merges — by then the workflow YAMLs the install PR added
    are live on the default branch. Each failure is logged but
    swallowed: one missing preset shouldn't block the others.

    Returns the list of ``RoutineRun.id`` it created.
    """
    candidates = (
        await session.execute(
            select(Routine).where(
                Routine.workspace_id == repo.workspace_id,
                Routine.repo_id == repo.id,
                Routine.enabled.is_(True),
                Routine.lane_id.in_(KNOWLEDGE_ROUTINE_LANE_IDS),
            )
        )
    ).scalars().all()
    if not candidates:
        return []

    created: list[uuid.UUID] = []
    files = None  # lazy probe
    for routine in candidates:
        if not _supports_run(routine):
            continue
        workflow_file = _workflow_file_for_routine(routine)
        if workflow_file is None:
            continue
        if files is None:
            try:
                files = await list_repo_workflows(
                    repo, install, settings=settings
                )
            except Exception as exc:  # pragma: no cover - upstream flake
                logger.warning(
                    "auto-dispatch skipped (workflow probe failed): %s", exc
                )
                return created
        if workflow_file not in files:
            logger.info(
                "auto-dispatch skipped kind=%s repo=%s: %s not in repo yet",
                routine.lane_id,
                repo.full_name,
                workflow_file,
            )
            continue
        now = datetime.now(timezone.utc)
        run = RoutineRun(
            routine_id=routine.id,
            workspace_id=routine.workspace_id,
            trigger=trigger,
            status="queued",
            started_at=now,
            summary=(
                f"Auto-dispatched {routine.lane_id} after install PR merge "
                f"({repo.full_name})"
            ),
            payload={"auto_trigger": trigger},
        )
        session.add(run)
        await session.flush()
        token = _mint_run_token(run.id, settings)
        run.run_token_hash = _hash_run_token(token)
        inputs = {
            "ship_run_id": str(run.id),
            "ship_callback_url": _callback_url(settings, run.id),
            "ship_run_token": token,
        }
        try:
            await dispatch_workflow(
                repo,
                install,
                workflow_file,
                inputs=inputs,
                settings=settings,
            )
        except WorkflowDispatchError as exc:
            logger.warning(
                "auto-dispatch failed kind=%s repo=%s status=%s: %s",
                routine.lane_id,
                repo.full_name,
                exc.status_code,
                exc.message[:200],
            )
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.summary = (
                f"Auto-dispatch failed (HTTP {exc.status_code}): "
                f"{exc.message[:200]}"
            )
            routine.last_run_status = "failed"
            routine.last_run_at = run.finished_at
            routine.updated_at = run.finished_at
            await session.flush()
            continue
        run.status = "running"
        routine.last_run_at = now
        routine.last_run_status = "running"
        routine.updated_at = now
        session.add(
            AuditLog(
                workspace_id=routine.workspace_id,
                actor_user_id=None,
                actor_token_id=None,
                action="routine.run",
                target_kind="routine",
                target_id=str(routine.id),
                payload={
                    "kind": routine.lane_id,
                    "trigger": trigger,
                    "run_id": str(run.id),
                    "repo_full_name": repo.full_name,
                    "workflow_file": workflow_file,
                },
            )
        )
        await session.flush()
        created.append(run.id)
        logger.info(
            "auto-dispatched knowledge lane kind=%s repo=%s run=%s",
            routine.lane_id,
            repo.full_name,
            run.id,
        )
    return created


# ---------------------------------------------------------------------------
# Auto-dispatch (self-heal lane on CI failure)
# ---------------------------------------------------------------------------


class SelfHealDispatchResult(BaseModel):
    """Return shape for :func:`auto_dispatch_self_heal`."""

    status: str
    run_id: uuid.UUID | None = None


async def auto_dispatch_self_heal(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings,
    failed_run_external_id: int,
    failed_workflow_name: str,
    trigger: str = "auto_self_heal",
) -> SelfHealDispatchResult:
    """Fire the ``self_heal`` lane in response to a failed CI run."""
    routine = (
        await session.execute(
            select(Routine).where(
                Routine.workspace_id == repo.workspace_id,
                Routine.repo_id == repo.id,
                Routine.lane_id == "self_heal",
            )
        )
    ).scalars().first()
    if routine is None:
        return SelfHealDispatchResult(status="skipped:routine_missing")
    if not routine.enabled:
        return SelfHealDispatchResult(status="skipped:routine_disabled")
    if not _supports_run(routine):
        return SelfHealDispatchResult(status="skipped:kind_not_supported")
    workflow_file = _workflow_file_for_routine(routine)
    if workflow_file is None:
        return SelfHealDispatchResult(status="skipped:kind_not_supported")
    try:
        files = await list_repo_workflows(repo, install, settings=settings)
    except Exception as exc:  # pragma: no cover - upstream flake
        logger.warning(
            "self-heal auto-dispatch skipped (workflow probe failed): %s", exc
        )
        return SelfHealDispatchResult(status="skipped:workflow_probe_failed")
    if workflow_file not in files:
        return SelfHealDispatchResult(status="skipped:workflow_not_installed")

    now = datetime.now(timezone.utc)
    run = RoutineRun(
        routine_id=routine.id,
        workspace_id=routine.workspace_id,
        trigger=trigger,
        status="queued",
        started_at=now,
        summary=(
            f"Auto self-heal for {repo.full_name} — "
            f"{failed_workflow_name[:120]} failed"
        ),
        payload={
            "auto_trigger": trigger,
            "failed_run_external_id": failed_run_external_id,
            "failed_workflow_name": failed_workflow_name,
        },
    )
    session.add(run)
    await session.flush()
    token = _mint_run_token(run.id, settings)
    run.run_token_hash = _hash_run_token(token)
    inputs = {
        "ship_run_id": str(run.id),
        "ship_callback_url": _callback_url(settings, run.id),
        "ship_run_token": token,
        "ship_failed_run_id": str(failed_run_external_id),
    }
    try:
        await dispatch_workflow(
            repo,
            install,
            workflow_file,
            inputs=inputs,
            settings=settings,
        )
    except WorkflowDispatchError as exc:
        logger.warning(
            "self-heal auto-dispatch failed repo=%s status=%s: %s",
            repo.full_name,
            exc.status_code,
            exc.message[:200],
        )
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.summary = (
            f"Self-heal dispatch failed (HTTP {exc.status_code}): "
            f"{exc.message[:200]}"
        )
        routine.last_run_status = "failed"
        routine.last_run_at = run.finished_at
        routine.updated_at = run.finished_at
        await session.flush()
        return SelfHealDispatchResult(
            status=f"failed:upstream_{exc.status_code}", run_id=run.id
        )

    run.status = "running"
    routine.last_run_at = now
    routine.last_run_status = "running"
    routine.updated_at = now
    session.add(
        AuditLog(
            workspace_id=routine.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="routine.run",
            target_kind="routine",
            target_id=str(routine.id),
            payload={
                "kind": routine.lane_id,
                "trigger": trigger,
                "run_id": str(run.id),
                "repo_full_name": repo.full_name,
                "workflow_file": workflow_file,
                "failed_run_external_id": failed_run_external_id,
                "failed_workflow_name": failed_workflow_name,
            },
        )
    )
    await session.flush()
    logger.info(
        "auto-dispatched self-heal repo=%s failed_run=%s -> run=%s",
        repo.full_name,
        failed_run_external_id,
        run.id,
    )
    return SelfHealDispatchResult(status="dispatched", run_id=run.id)


__all__ = [
    "router",
    "public_router",
    "auto_dispatch_knowledge_routines",
    "auto_dispatch_self_heal",
    "dispatch_routine_run",
    "SelfHealDispatchResult",
    "KNOWLEDGE_ROUTINE_LANE_IDS",
    "RunOut",
    "RunIn",
    "RunResultIn",
    "RunSummary",
    "RunTokenContext",
    "get_run_token_context",
    "get_run_or_repo_token_context",
    "_run_to_out",
    "_mint_run_token",
    "_hash_run_token",
]
