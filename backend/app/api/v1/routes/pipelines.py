"""Pipelines API — list, toggle, run, install workflow, run callback (Day-4 Phase-1).

Day 3 of the pilot landed the dashboard with a *stub* "Run now" that
just inserted a ``succeeded`` row. Day-4 Phase-1 turns that into an
honest GitHub Actions ``workflow_dispatch`` for the ``pr_review``
pipeline kind end-to-end:

- ``POST /workspaces/{ws}/pipelines/{id}/runs`` (Run now) probes the
  customer repo for the starter workflow and either dispatches it
  (returning a ``running`` row) or returns ``412 workflow_not_installed``
  with an install hint.
- ``POST /workspaces/{ws}/pipelines/{id}/install`` opens a PR in the
  customer repo via the App's ``contents:write`` permission, adding
  the starter workflow YAML so Run now becomes available once merged.
- ``POST /v1/pipelines/runs/{run_id}/result`` (no session — bearer
  ``run_token`` only) is the callback the dispatched workflow hits to
  report success/failure. Token verification compares against the
  SHA-256 stored on the ``PipelineRun`` row + a 5-minute JWT exp, so
  a stolen ``run_id`` alone can't fake a result.

All four other pipeline kinds (``daily_standup``, ``code_map``,
``tech_debt``, ``self_heal``) keep their seed rows but the dispatcher
returns ``412 kind_not_supported_yet`` — the dashboard renders a
"Coming with presets" badge for them. Phase 2 lifts that restriction.
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
from backend.app.db.models.fleet_lanes import FleetLane
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.pipelines import Pipeline, PipelineRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.github.app_auth import GitHubAppMisconfigured
from backend.app.integrations.github.workflows import (
    StarterWorkflowPR,
    WorkflowDispatchError,
    commit_starter_workflow,
    dispatch_workflow,
    invalidate_workflow_list_cache,
    list_repo_workflows,
)
from backend.app.services import catalog as catalog_service
from backend.app.services import starter_workflows
from backend.app.services.lane_recipes import list_lane_recipes


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline kind ↔ catalog workflow mapping
# ---------------------------------------------------------------------------

# ``list_lane_recipes`` is the authoritative lane → workflow_id map
# post-RFC-0008 C3.3 (``pr_review`` → ``pr-and-ci-gate``, ``self_heal``
# → ``pipeline-self-heal``, …). Adding a new lane = dropping a new
# pattern ARTIFACT.md with ``lane_id`` + workflow.yml pair, no code
# churn in this module. We compute the map lazily per-call so catalog
# mtime changes pick up without a process restart.


def _lane_id_to_workflow_id(lane_id: str) -> str | None:
    for recipe in list_lane_recipes():
        if recipe.lane_id == lane_id:
            return recipe.workflow_id
    return None


def _workflow_file_for_lane_id(lane_id: str) -> str | None:
    """Basename the customer repo will contain, via starter-workflow lookup.

    Returns ``None`` when the lane has no backed starter (e.g.
    ``code_map`` — resolver-only) so callers know to fall back to the
    "Coming with presets" state instead of 412-ing the user.
    """
    workflow_id = _lane_id_to_workflow_id(lane_id)
    if workflow_id is None:
        return None
    return starter_workflows.install_filename(workflow_id)


def _supports_run(lane_id: str) -> bool:
    """True iff we can both dispatch and (re)install the workflow for ``lane_id``."""
    workflow_id = _lane_id_to_workflow_id(lane_id)
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
_RUN_TOKEN_SUBJECT: Final[str] = "ship.pipeline.run"
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
    (pipeline-authored clarifications, improvements, chat messages)
    never re-validate the token themselves. Treat every field as
    already authenticated — the dependency guarantees:

    - JWT signature ok, ``sub`` == ``ship.pipeline.run``,
      ``exp`` in the future.
    - ``run_id`` matches an existing :class:`PipelineRun` row.
    - SHA-256 of the raw token matches
      :attr:`PipelineRun.run_token_hash` (belt-and-braces against a
      forged token signed with a leaked secret).

    ``workspace_id`` + ``pipeline_id`` are snapshotted at validation
    time to avoid re-issuing a SELECT in the handler body.
    ``auth_mode`` records which auth path validated the bearer so
    audit logs and debug tooling can tell "legacy dispatch" from
    "lane-scheduled runner" apart without guessing off the token
    shape.
    """

    run_id: uuid.UUID
    pipeline_id: uuid.UUID
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
    improvements, and chat pipeline endpoints — paths where the run
    id lives in the JWT claims and there's no URL path parameter we
    could cross-check a long-lived repo token against.

    Endpoints on the lane-triggered callback path (cron / push / PR
    lanes that can't carry a per-run JWT through
    ``workflow_dispatch.inputs``) use :func:`get_run_or_repo_token_context`
    instead — it accepts either JWT *or* long-lived ``SHIP_RUN_TOKEN``
    and requires a ``run_id`` path parameter for cross-check.

    Raises ``401`` on every failure mode so tenants can't fingerprint
    our validator; ``404`` is reserved for the run-missing case (the
    legitimate race where a run got deleted between token issuance
    and callback — still authenticated, just orphaned).
    """
    raw_token = _parse_bearer(authorization)

    run_id = _decode_run_token(raw_token, settings)
    run = await session.get(PipelineRun, run_id)
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
        pipeline_id=run.pipeline_id,
        workspace_id=run.workspace_id,
        raw_token=raw_token,
        auth_mode="jwt",
    )


def _looks_like_jwt(raw_token: str) -> bool:
    """Cheap shape check: compact JWTs are three base64url segments.

    Used only as a routing hint for the dual-auth dependency — not
    a security boundary; both validation paths return 401 on
    failure regardless.
    """

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
    :mod:`backend.app.services.repo_tokens` and stored in repo
    Actions secrets. Endpoints that mount this dependency *must*
    carry a ``{run_id}`` path parameter — it's the cross-reference
    for both paths.

    Route order:

    1. If the bearer looks like a JWT (three base64url segments)
       validate via the JWT path exactly like
       :func:`get_run_token_context`, then confirm ``claims.rid``
       matches the path ``run_id``.
    2. Otherwise hash it and look for a matching ``WorkspaceRepo``;
       if found, fetch the ``Pipeline`` for ``run_id`` and confirm
       ``pipeline.repo_id == repo.id``.

    Both paths converge on the same :class:`RunTokenContext` shape
    so downstream handlers don't branch on auth_mode unless they
    want to.
    """

    raw_token = _parse_bearer(authorization)

    if _looks_like_jwt(raw_token):
        jwt_rid = _decode_run_token(raw_token, settings)
        run = await session.get(PipelineRun, jwt_rid)
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
            pipeline_id=run.pipeline_id,
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
    run = await session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token is invalid, expired, or for a missing run",
        )
    pipeline = await session.get(Pipeline, run.pipeline_id)
    if pipeline is None or pipeline.repo_id != repo.id:
        # Forgery case: caller has a valid token for repo A but is
        # trying to report against a run that belongs to repo B.
        # Do not disclose which side is the mismatch.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token does not authorize this run",
        )
    return RunTokenContext(
        run_id=run.id,
        pipeline_id=run.pipeline_id,
        workspace_id=run.workspace_id,
        raw_token=raw_token,
        auth_mode="repo",
    )


def _read_starter_yaml(lane_id: str) -> str:
    """Return the YAML body for the lane's starter workflow or 412/500.

    ``kind_not_supported_yet`` when we have no starter mapping for the
    lane (e.g. ``code_map`` — resolver-only, no starter workflow
    today). ``500`` only when the mapping exists but the YAML file on
    disk is missing — a release-packaging bug the operator needs to
    see, not a tenant-actionable condition. The error ``code`` field
    keeps its pre-rename value to avoid breaking Console clients that
    branch on it.
    """
    workflow_id = _lane_id_to_workflow_id(lane_id)
    if workflow_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Pipeline lane {lane_id!r} has no starter workflow. "
                    "Coming with Phase 3 presets."
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
                    f"Workflow {workflow_id!r} for lane {lane_id!r} has no "
                    "installable YAML yet. Coming with presets."
                ),
            },
        )
    return content


# ---------------------------------------------------------------------------
# Routers — workspace-scoped (RBAC) + global (callback only)
# ---------------------------------------------------------------------------


router = APIRouter(
    prefix="/workspaces/{workspace_id}/pipelines",
    tags=["pipelines"],
)

# Separate router for endpoints that don't fit the workspace prefix.
# Mounted under ``/v1`` directly so the dispatched workflow only needs
# the run id + bearer token (no session, no workspace path component).
public_router = APIRouter(prefix="/pipelines", tags=["pipelines"])


_RUNS_PAGE_LIMIT = 20
_RUNS_PAGE_DEFAULT = 10


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PipelineOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    workflow_id: str
    enabled: bool
    config: dict
    last_run_at: datetime | None
    last_run_status: str | None
    created_at: datetime
    updated_at: datetime
    # Day-4 Phase-1 additions — drive the dashboard card states
    # (installed / not-installed / coming-soon) without a second
    # round-trip per render.
    repo_id: uuid.UUID | None
    repo_full_name: str | None
    workflow_installed: bool | None
    workflow_file: str | None
    supports_run: bool


class PipelineToggleIn(BaseModel):
    enabled: bool


class RunSummaryArtifact(BaseModel):
    """One artifact a play produced (PR, issue, comment, doc, …).

    ``ref`` is the URL or external identifier (PR url, issue
    number, doc id) — left optional because some artifact types
    (e.g. an inline ``comment``) don't have an addressable ref.
    """

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
    """RFC-0010 §RunSummary contract — outcome of a pipeline run.

    All fields optional; an empty dict ``{}`` is a valid summary
    (and the column default for legacy rows). Pattern-authored,
    never derived in the UI: ``outcome_text`` is the single-line
    sentence the Runs list renders verbatim, the rest are
    structured signals the FE cards / Inbox intake consume.

    ``extra='forbid'`` so a typo in a pattern's reporter trips a
    422 at the callback rather than silently dropping data into
    a key the UI never looks at.
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


class PipelineRunOut(BaseModel):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    trigger: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    summary: str | None
    payload: dict
    # RFC-0010 §RunSummary — surfaced on every run row so the FE list
    # / detail can render outcome-first. Defaults to an empty
    # ``RunSummary()`` for legacy rows whose ``pipeline_runs.outcome``
    # is ``{}::jsonb``.
    outcome: RunSummary = Field(default_factory=RunSummary)
    created_at: datetime


class PipelineRunIn(BaseModel):
    """Optional client-supplied payload for a manual run."""

    note: str | None = Field(
        default=None,
        max_length=500,
        description="Optional human note shown in the run history.",
    )
    repo_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Workspace repo to dispatch against. When set, the pipeline is "
            "rebound to this repo before dispatch (``Pipeline.repo_id`` "
            "mutates) so subsequent Run-now calls from other surfaces stay "
            "consistent. When omitted, the existing binding is used (or "
            "the sole-repo auto-bind heuristic kicks in)."
        ),
    )


class PipelineInstallIn(BaseModel):
    """Optional payload for install-workflow requests.

    Same ``repo_id`` semantics as :class:`PipelineRunIn` — if the user
    fires Install from a specific repo's swimlane card, we rebind
    before opening the PR so both the Install and the follow-up Run
    target the same repo.
    """

    repo_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Workspace repo to open the install PR against. Rebinds the "
            "pipeline when different from the current binding."
        ),
    )


class PipelineInstallOut(BaseModel):
    pr_url: str
    pr_number: int
    branch: str


class PipelineRunResultIn(BaseModel):
    """Body of the ``POST /pipelines/runs/{run_id}/result`` callback.

    Backwards-compatible: pre-P3-01 callers send only
    ``status`` (+ optional ``summary`` / ``metrics``); P3-01 adds
    ``outcome`` for pattern-authored RunSummary payloads. When
    ``outcome`` is absent we leave ``pipeline_runs.outcome`` at its
    default ``{}::jsonb`` so the legacy reporter shape keeps working.
    """

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
            "``pipeline_runs.outcome``. When absent, the column is left "
            "untouched (so a re-callback can clear nothing it didn't set)."
        ),
    )


_TERMINAL_STATUSES: Final[set[str]] = {"succeeded", "failed", "cancelled"}

_INBOX_OPEN: Final[tuple[str, ...]] = ("new", "snoozed")


async def _emit_self_heal_blocker_inbox(
    session: AsyncSession,
    *,
    run: PipelineRun,
    pipeline: Pipeline,
) -> None:
    """Mirror a failed self-heal run into the inbox (trusted callback path only)."""
    if pipeline.lane_id != "self_heal" or run.status != "failed":
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
    summary = (run.summary or pipeline.name or "Self-heal")[:500]
    title = f"Self-heal could not fix: {summary}"[:255]
    session.add(
        InboxItem(
            workspace_id=run.workspace_id,
            repo_id=pipeline.repo_id,
            type="blocker",
            title=title,
            summary=summary,
            payload={
                "kind": "self_heal_failed",
                "pipeline_id": str(pipeline.id),
                "run_id": str(run.id),
            },
            status="new",
            source_table="self_heal_run",
            source_id=run.id,
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_out(
    row: Pipeline,
    *,
    repo: WorkspaceRepo | None = None,
    workflow_installed: bool | None = None,
) -> PipelineOut:
    workflow_file = _workflow_file_for_lane_id(row.lane_id)
    return PipelineOut(
        id=row.id,
        kind=row.lane_id,
        name=row.name,
        workflow_id=row.workflow_id,
        enabled=row.enabled,
        config=row.config or {},
        last_run_at=row.last_run_at,
        last_run_status=row.last_run_status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        repo_id=row.repo_id,
        repo_full_name=repo.full_name if repo else None,
        workflow_installed=workflow_installed,
        workflow_file=workflow_file,
        supports_run=_supports_run(row.lane_id),
    )


def _run_to_out(row: PipelineRun) -> PipelineRunOut:
    # ``outcome`` was added in P3-01 (migration 0033). Validate via
    # :class:`RunSummary` so a row with stale / future-shape JSON
    # surfaces loudly here rather than as a silent client-side parse
    # error. Empty dicts (legacy default) round-trip as ``RunSummary()``.
    raw_outcome = row.outcome or {}
    outcome = RunSummary.model_validate(raw_outcome)
    return PipelineRunOut(
        id=row.id,
        pipeline_id=row.pipeline_id,
        trigger=row.trigger,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        summary=row.summary,
        payload=row.payload or {},
        outcome=outcome,
        created_at=row.created_at,
    )


async def _load_pipeline(
    session: AsyncSession, workspace_id: uuid.UUID, pipeline_id: uuid.UUID
) -> Pipeline:
    stmt = select(Pipeline).where(
        Pipeline.workspace_id == workspace_id, Pipeline.id == pipeline_id
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return row


async def _auto_bind_pipeline_to_repo(
    session: AsyncSession, pipeline: Pipeline
) -> WorkspaceRepo | None:
    """If the workspace has exactly one GH-backed repo, bind and return it.

    Phase-3 convenience: legacy pipelines (seeded before Day-4 added
    the binding FK) or pipelines created under edge-cases can have
    ``repo_id=None``. In the common case — a single activated repo —
    the "right" binding is obvious and asking the user "pick a repo"
    is just friction on a pilot with one-repo tenants. We bind
    in-place and let the caller re-check. For workspaces with multiple
    activated repos the answer isn't obvious so we return ``None`` and
    the 412 picker flow kicks in.

    Idempotent (caller already knows ``pipeline.repo_id`` is ``None``).
    The mutation is flushed so subsequent ``session.get`` by callers
    in the same request see the new binding.
    """
    stmt = (
        select(WorkspaceRepo)
        .where(WorkspaceRepo.workspace_id == pipeline.workspace_id)
        .where(WorkspaceRepo.installation_id.is_not(None))
        .order_by(WorkspaceRepo.full_name)
    )
    rows = (await session.execute(stmt)).scalars().all()
    if len(rows) != 1:
        return None
    repo = rows[0]
    pipeline.repo_id = repo.id
    # Pin ``updated_at`` client-side — same rationale as
    # :func:`enrich_pipelines` (avoid the ORM expiring the attribute
    # after the onupdate server-default fires).
    pipeline.updated_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info(
        "auto-bound pipeline pipeline_id=%s workspace_id=%s repo_id=%s (%s)",
        pipeline.id,
        pipeline.workspace_id,
        repo.id,
        repo.full_name,
    )
    return repo


async def _load_repo_and_install(
    session: AsyncSession,
    pipeline: Pipeline,
    *,
    explicit_repo_id: uuid.UUID | None = None,
) -> tuple[WorkspaceRepo, GitHubInstallation]:
    """Resolve the repo + install backing a pipeline or raise 412.

    Centralised so the dispatcher and the install endpoint reuse the
    same precondition story. The 412s carry a ``code`` field the
    console can switch on to decide which CTA to show (re-bind, install
    workflow, reinstall the App).

    Repo selection follows three tiers in order of preference:

    1. **Explicit override** (``explicit_repo_id``) — the UI posted
       the card from a specific repo's swimlane, so that repo wins
       even if the pipeline was previously bound elsewhere. Rebinds
       the pipeline in-place so subsequent surfaces stay consistent.
    2. **Existing binding** (``pipeline.repo_id``) — use whatever
       ``/repos/activate`` or a prior override set.
    3. **Sole-repo auto-bind** — if the workspace has exactly one
       activated repo, bind to it. Convenience for legacy seeds that
       predate the FK.

    Returns 412 ``pipeline_not_bound`` only when no tier resolves to
    a concrete repo (0 or multiple repos, nothing explicit, nothing
    stored).
    """
    if explicit_repo_id is not None:
        target = await session.get(WorkspaceRepo, explicit_repo_id)
        if target is None or target.workspace_id != pipeline.workspace_id:
            # Cross-workspace or gone — refuse rather than silently
            # falling back to a different repo the user didn't pick.
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={
                    "code": "pipeline_not_bound",
                    "message": (
                        "Requested repo isn't activated in this workspace. "
                        "Pick a repo from the swimlane the card lives in."
                    ),
                },
            )
        if pipeline.repo_id != target.id:
            pipeline.repo_id = target.id
            # Keep ``updated_at`` in sync client-side so the onupdate
            # default doesn't expire the attribute mid-request.
            pipeline.updated_at = datetime.now(timezone.utc)
            await session.flush()
            logger.info(
                "rebound pipeline pipeline_id=%s workspace_id=%s repo_id=%s (%s)",
                pipeline.id,
                pipeline.workspace_id,
                target.id,
                target.full_name,
            )
        repo: WorkspaceRepo | None = target
    elif pipeline.repo_id is None:
        repo = await _auto_bind_pipeline_to_repo(session, pipeline)
        if repo is None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={
                    "code": "pipeline_not_bound",
                    "message": (
                        "No activated repo to bind this pipeline to. "
                        "Open the onboarding wizard and activate a repo, "
                        "or pick one from the Repos tab."
                    ),
                },
            )
    else:
        repo = await session.get(WorkspaceRepo, pipeline.repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "pipeline_not_bound",
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
    return f"{base}/v1/pipelines/runs/{run_id}/result"


# ---------------------------------------------------------------------------
# Routes — workspace-scoped
# ---------------------------------------------------------------------------


async def enrich_pipelines(
    session: AsyncSession,
    pipelines: list[Pipeline],
    *,
    settings: Settings,
) -> list[PipelineOut]:
    """Resolve repo + workflow-installed flags for a batch of pipelines.

    Shared between :func:`list_pipelines` and
    :func:`backend.app.api.v1.routes.dashboard.get_dashboard` so both
    surfaces present the dashboard's three card states (Run-now /
    Install-workflow / Coming-soon) consistently. Probe errors are
    swallowed per (install, repo) pair so one bad GitHub call doesn't
    blank an unrelated card.
    """
    # Phase-3 convenience: before the probe, opportunistically bind any
    # still-unbound pipelines to the workspace's sole activated repo.
    # This is the same rule the dispatcher applies on Run-now/Install,
    # but doing it at dashboard-load time means the UI renders
    # "Install workflow PR" instead of a confusing "Coming with presets"
    # badge on refresh. Only touches pipelines that share the same
    # workspace_id (the caller already filtered by workspace).
    unbound = [p for p in pipelines if p.repo_id is None]
    if unbound:
        now_utc = datetime.now(timezone.utc)
        workspace_ids = {p.workspace_id for p in unbound}
        mutated = False
        for workspace_id in workspace_ids:
            repo_candidates = (
                await session.execute(
                    select(WorkspaceRepo)
                    .where(WorkspaceRepo.workspace_id == workspace_id)
                    .where(WorkspaceRepo.installation_id.is_not(None))
                    .order_by(WorkspaceRepo.full_name)
                )
            ).scalars().all()
            if len(repo_candidates) != 1:
                continue
            default_repo = repo_candidates[0]
            for pipeline in unbound:
                if pipeline.workspace_id == workspace_id:
                    pipeline.repo_id = default_repo.id
                    # Set ``updated_at`` client-side so the server-side
                    # ``onupdate=now()`` doesn't expire the attribute on
                    # the ORM row — otherwise the sync ``_row_to_out``
                    # below tries to lazy-reload it and explodes in the
                    # async context.
                    pipeline.updated_at = now_utc
                    mutated = True
        if mutated:
            await session.flush()

    repo_ids = {r.repo_id for r in pipelines if r.repo_id is not None}
    repos: dict[uuid.UUID, WorkspaceRepo] = {}
    if repo_ids:
        repo_rows = (
            await session.execute(
                select(WorkspaceRepo).where(WorkspaceRepo.id.in_(repo_ids))
            )
        ).scalars().all()
        repos = {r.id: r for r in repo_rows}

    install_ids = {r.installation_id for r in repos.values() if r.installation_id}
    installs: dict[uuid.UUID, GitHubInstallation] = {}
    if install_ids:
        inst_rows = (
            await session.execute(
                select(GitHubInstallation).where(
                    GitHubInstallation.id.in_(install_ids)
                )
            )
        ).scalars().all()
        installs = {r.id: r for r in inst_rows}

    workflow_sets: dict[tuple[int, str], frozenset[str]] = {}
    out: list[PipelineOut] = []
    for row in pipelines:
        repo = repos.get(row.repo_id) if row.repo_id else None
        workflow_file = _workflow_file_for_lane_id(row.lane_id)
        if repo is None or workflow_file is None or not _supports_run(row.lane_id):
            out.append(_row_to_out(row, repo=repo, workflow_installed=None))
            continue
        install = installs.get(repo.installation_id) if repo.installation_id else None
        if install is None or install.suspended_at is not None:
            out.append(_row_to_out(row, repo=repo, workflow_installed=False))
            continue
        cache_key = (install.installation_id, repo.full_name)
        if cache_key not in workflow_sets:
            try:
                workflow_sets[cache_key] = await list_repo_workflows(
                    repo, install, settings=settings
                )
            except (
                WorkflowDispatchError,
                httpx.HTTPError,
                GitHubAppMisconfigured,
            ) as exc:
                # Probe is best-effort — dashboard should still render if
                # GitHub is unreachable or the App creds went missing.
                logger.warning(
                    "workflow probe failed repo=%s err=%s", repo.full_name, exc
                )
                workflow_sets[cache_key] = frozenset()
        files = workflow_sets[cache_key]
        installed = workflow_file in files
        out.append(_row_to_out(row, repo=repo, workflow_installed=installed))

    return out


@router.get("", response_model=list[PipelineOut])
async def list_pipelines(
    workspace_id: uuid.UUID,
    scope: Literal["all", "fleet", "repo"] = Query(
        default="all",
        description=(
            "P1-09 scope filter, mirroring ``GET /workspaces/{ws}/lanes``. "
            "``all`` (default) returns every pipeline; ``fleet`` returns "
            "only pipelines whose ``lane_id`` is materialised by a "
            ":class:`FleetLane` in the workspace; ``repo`` returns "
            "pipelines bound to the given ``repo_id`` (required when "
            "``scope=repo``)."
        ),
    ),
    repo_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Workspace repo to narrow ``scope=repo`` results to. 422 "
            "when set together with ``scope=fleet`` / ``scope=all``."
        ),
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[PipelineOut]:
    """All pipelines for the workspace, with workflow-availability flags.

    Accepts the P1-09 ``scope`` + ``repo_id`` filters so the unified
    ``/runs`` console route can drive lanes, fleet, and per-repo
    surfaces against this single endpoint without per-tab branching.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    # Local import to avoid cycles (lanes.py imports nothing from
    # pipelines.py today, but the validator lives there as the
    # canonical owner of the scope/repo_id contract).
    from backend.app.api.v1.routes.lanes import _validate_scope_repo_xor

    _validate_scope_repo_xor(scope, repo_id)

    if scope == "repo":
        target_repo = (
            await session.execute(
                select(WorkspaceRepo).where(
                    WorkspaceRepo.workspace_id == workspace_id,
                    WorkspaceRepo.id == repo_id,
                )
            )
        ).scalar_one_or_none()
        if target_repo is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="repo_id must reference a repo in this workspace",
            )

    stmt = (
        select(Pipeline)
        .where(Pipeline.workspace_id == workspace_id)
        .order_by(Pipeline.created_at)
    )
    if scope == "repo":
        stmt = stmt.where(Pipeline.repo_id == repo_id)
    elif scope == "fleet":
        # Project FleetLane.lane_id values into a subquery so the
        # filter scales with the fleet-lane count, not the pipeline
        # count. Returns no rows when the workspace hasn't declared
        # any fleet lanes (which is the right answer — there's
        # nothing to mirror).
        fleet_lane_ids = (
            select(FleetLane.lane_id)
            .where(FleetLane.workspace_id == workspace_id)
            .scalar_subquery()
        )
        stmt = stmt.where(Pipeline.lane_id.in_(fleet_lane_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return await enrich_pipelines(session, list(rows), settings=settings)


@router.patch("/{pipeline_id}", response_model=PipelineOut)
async def toggle_pipeline(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: PipelineToggleIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PipelineOut:
    """Flip a pipeline on or off. Admin-only."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _load_pipeline(session, workspace_id, pipeline_id)
    if row.enabled == payload.enabled:
        return _row_to_out(row)
    row.enabled = payload.enabled
    row.updated_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="pipeline.toggle",
            target_kind="pipeline",
            target_id=str(row.id),
            payload={"kind": row.lane_id, "enabled": row.enabled},
        )
    )
    await session.flush()
    return _row_to_out(row)


async def dispatch_pipeline_run(
    session: AsyncSession,
    settings: Settings,
    pipeline: Pipeline,
    *,
    trigger: str,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_token_id: uuid.UUID | None = None,
    explicit_repo_id: uuid.UUID | None = None,
    audit_extra: dict[str, Any] | None = None,
) -> PipelineRun:
    """Mint a :class:`PipelineRun`, dispatch it to GitHub Actions, audit.

    Shared core for both the HTTP "Run now" route and the Navigator
    ``play_run_now`` chat tool. Centralising it makes "queue a row but
    forget to dispatch" structurally impossible — both surfaces walk
    the same precondition-check + dispatch + audit path.

    Failure modes are surfaced as :class:`HTTPException` with the
    Day-4-Phase-1 ``code`` vocabulary so callers (FastAPI handlers
    or Navigator tools) can either let them propagate or translate
    them into their own response shapes:

    - ``412 kind_not_supported_yet`` — lane has no starter workflow.
    - ``412 pipeline_not_bound`` — no resolvable workspace repo.
    - ``412 github_app_missing`` — install gone or suspended.
    - ``412 workflow_not_installed`` — YAML missing on default branch.
    - ``502 dispatch_failed`` — GitHub itself rejected the dispatch.

    On success the returned :class:`PipelineRun` already has
    ``status='running'``, ``run_token_hash`` set, and ``last_run_*``
    on the pipeline updated. The caller still owns the outer
    transaction (no commit here).
    """
    workflow_file = _workflow_file_for_lane_id(pipeline.lane_id)
    if workflow_file is None or not _supports_run(pipeline.lane_id):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Pipeline lane {pipeline.lane_id!r} doesn't ship with a "
                    "catalog workflow yet. Coming with presets."
                ),
            },
        )

    repo, install = await _load_repo_and_install(
        session, pipeline, explicit_repo_id=explicit_repo_id
    )

    files = await list_repo_workflows(repo, install, settings=settings)
    if workflow_file not in files:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "workflow_not_installed",
                "workflow_file": workflow_file,
                "repo_full_name": repo.full_name,
                "install_endpoint": (
                    f"/v1/workspaces/{pipeline.workspace_id}/pipelines/"
                    f"{pipeline.id}/install"
                ),
                "message": (
                    f"{repo.full_name!r} doesn't have .github/workflows/"
                    f"{workflow_file}. Open the install PR first."
                ),
            },
        )

    now = datetime.now(timezone.utc)
    run = PipelineRun(
        pipeline_id=pipeline.id,
        workspace_id=pipeline.workspace_id,
        trigger=trigger,
        status="queued",
        started_at=now,
        summary=summary or f"Dispatched {pipeline.name} for {repo.full_name}",
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
        pipeline.last_run_status = "failed"
        pipeline.last_run_at = run.finished_at
        await session.flush()
        # Map upstream-level failures to a 502 so the console can
        # surface "GitHub said no" without confusing the user with our
        # 412 precondition vocabulary.
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
    pipeline.last_run_at = now
    pipeline.last_run_status = "running"
    pipeline.updated_at = now

    audit_payload: dict[str, Any] = {
        "kind": pipeline.lane_id,
        "trigger": trigger,
        "run_id": str(run.id),
        "repo_full_name": repo.full_name,
        "workflow_file": workflow_file,
    }
    if audit_extra:
        audit_payload.update(audit_extra)
    session.add(
        AuditLog(
            workspace_id=pipeline.workspace_id,
            actor_user_id=actor_user_id,
            actor_token_id=actor_token_id,
            action="pipeline.run",
            target_kind="pipeline",
            target_id=str(pipeline.id),
            payload=audit_payload,
        )
    )
    await session.flush()
    return run


@router.post(
    "/{pipeline_id}/runs",
    response_model=PipelineRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_pipeline(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: PipelineRunIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PipelineRunOut:
    """Dispatch a real GitHub Actions ``workflow_dispatch`` for the pipeline.

    Day-4 Phase-1 contract:

    - Returns ``202 Accepted`` with a ``running`` row when GitHub
      acknowledged the dispatch. The actual terminal state arrives
      later via the result callback or the ``workflow_run`` webhook
      (whichever wins the race).
    - Returns ``412`` with a structured ``code`` field when a
      precondition fails — pipeline not bound to a repo
      (``pipeline_not_bound``), GitHub App gone
      (``github_app_missing``), kind not yet supported
      (``kind_not_supported_yet``), or workflow file not yet in the
      customer repo (``workflow_not_installed``). The console reads
      the code to decide which CTA to render (Install / Reinstall /
      Re-bind / Coming-soon).
    - Returns ``502`` when GitHub's dispatch endpoint itself errors.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    pipeline = await _load_pipeline(session, workspace_id, pipeline_id)
    if not pipeline.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline is disabled. Enable it before running.",
        )

    note = (payload.note if payload else None) or None
    explicit_repo_id = payload.repo_id if payload else None
    run = await dispatch_pipeline_run(
        session,
        settings,
        pipeline,
        trigger="manual",
        summary=note,
        payload={"note": note} if note else {},
        actor_user_id=auth.user.id,
        actor_token_id=auth.token.id if auth.token else None,
        explicit_repo_id=explicit_repo_id,
        audit_extra={"note": note},
    )
    return _run_to_out(run)


@router.post(
    "/{pipeline_id}/install",
    response_model=PipelineInstallOut,
    status_code=status.HTTP_201_CREATED,
)
async def install_pipeline_workflow(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: PipelineInstallIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PipelineInstallOut:
    """Open a PR in the bound repo that installs the starter workflow.

    Admin-only. Idempotent in the sense that the customer can press
    Install repeatedly — each call opens a fresh PR with a unique
    branch name (``ship/install-<kind>-<unix>``); merging any of
    them is enough to unlock Run now. We don't look up the prior PR
    because GitHub's "find PR by head ref" API requires either a
    paginated list or a search call, and the timestamped branches
    keep the cost on the customer side (close stale PRs manually).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    pipeline = await _load_pipeline(session, workspace_id, pipeline_id)
    workflow_file = _workflow_file_for_lane_id(pipeline.lane_id)
    if workflow_file is None or not _supports_run(pipeline.lane_id):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Pipeline lane {pipeline.lane_id!r} doesn't ship with a "
                    "starter workflow yet. Coming with presets."
                ),
            },
        )
    explicit_repo_id = payload.repo_id if payload else None
    repo, install = await _load_repo_and_install(
        session, pipeline, explicit_repo_id=explicit_repo_id
    )
    content = _read_starter_yaml(pipeline.lane_id)
    # Deep-link back to the dashboard so the user doesn't get stuck
    # on github.com after merging. ``console_url`` is the console
    # origin (``https://app.ship.…``); we add enough context to show
    # a "welcome back" banner next load.
    return_url = (
        f"{settings.console_url.rstrip('/')}/?ws={workspace_id}"
        f"&installed={pipeline_id}&reason=back_from_pr"
    )
    try:
        result: StarterWorkflowPR = await commit_starter_workflow(
            repo,
            install,
            workflow_file=workflow_file,
            content=content,
            pipeline_kind=pipeline.lane_id,
            settings=settings,
            return_url=return_url,
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "install_pr_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    # Bust the workflow-list cache so the next dashboard render
    # immediately picks up the (eventually) merged file. We also bust
    # in the webhook handler when the PR is merged, but invalidating
    # here means the customer's "I just merged it" refresh isn't held
    # back by our 60-second TTL.
    invalidate_workflow_list_cache(
        installation_id=install.installation_id, full_name=repo.full_name
    )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="pipeline.install",
            target_kind="pipeline",
            target_id=str(pipeline.id),
            payload={
                "kind": pipeline.lane_id,
                "repo_full_name": repo.full_name,
                "workflow_file": workflow_file,
                "pr_url": result.pr_url,
                "pr_number": result.pr_number,
                "branch": result.branch,
            },
        )
    )
    await session.flush()
    return PipelineInstallOut(
        pr_url=result.pr_url,
        pr_number=result.pr_number,
        branch=result.branch,
    )


@router.get("/{pipeline_id}/runs", response_model=list[PipelineRunOut])
async def list_pipeline_runs(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    limit: int = _RUNS_PAGE_DEFAULT,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[PipelineRunOut]:
    """Most-recent-first page of run history. Members can read."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _load_pipeline(session, workspace_id, pipeline_id)
    capped = max(1, min(limit, _RUNS_PAGE_LIMIT))
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .order_by(desc(PipelineRun.started_at), desc(PipelineRun.created_at))
        .limit(capped)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_to_out(r) for r in rows]


@router.get(
    "/{pipeline_id}/runs/{run_id}",
    response_model=PipelineRunOut,
)
async def get_pipeline_run(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    run_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PipelineRunOut:
    """Fetch a single run row (for the console detail page).

    Members can read. The run must belong to ``pipeline_id`` and the
    workspace — otherwise ``404`` so UUID enumeration across tenants
    doesn't leak existence.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _load_pipeline(session, workspace_id, pipeline_id)
    run = await session.get(PipelineRun, run_id)
    if (
        run is None
        or run.pipeline_id != pipeline_id
        or run.workspace_id != workspace_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _run_to_out(run)


# ---------------------------------------------------------------------------
# Routes — global (callback only, bearer-token auth)
# ---------------------------------------------------------------------------


@public_router.post(
    "/runs/{run_id}/result",
    response_model=PipelineRunOut,
)
async def report_run_result(
    run_id: uuid.UUID = Path(...),
    payload: PipelineRunResultIn = ...,
    ctx: RunTokenContext = Depends(get_run_or_repo_token_context),
    session: AsyncSession = Depends(get_session),
) -> PipelineRunOut:
    """Callback endpoint dispatched or lane-triggered workflows hit.

    Authentication is **either** path:

    1. Short-lived per-run JWT (legacy ``workflow_dispatch`` flow).
       Validated in :func:`get_run_or_repo_token_context` by JWT
       signature + ``sub`` + ``exp`` + ``rid`` matching the path,
       plus a sha256 hash cross-check against
       :attr:`PipelineRun.run_token_hash` (belt-and-braces if the
       JWT secret leaks).
    2. Long-lived repo-scoped ``SHIP_RUN_TOKEN`` (RFC-0007 lanes on
       cron / push / PR that have no ``inputs`` channel for a
       per-run JWT). The token sha256 must match
       :attr:`WorkspaceRepo.run_token_hash` and the path ``run_id``
       must belong to a pipeline on the same repo.

    Either path lands here with an authenticated
    :class:`RunTokenContext` and ``run_id`` already cross-checked,
    so the handler goes straight to state transition. Idempotent:
    a duplicate callback for an already-terminal run returns 200
    with the existing row.
    """
    if payload.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"status must be one of {sorted(_TERMINAL_STATUSES)}; "
                f"got {payload.status!r}"
            ),
        )

    run = await session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if run.status in _TERMINAL_STATUSES:
        # Idempotent: workflow's `if: always()` reporter sometimes
        # races with our webhook reconciliation; the first writer wins
        # and the second call is a no-op echo.
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
        # ``model_dump`` preserves field defaults so empty buckets like
        # ``artifacts: []`` and ``approval_payload: {}`` land in the JSONB
        # column verbatim. Downstream readers (Inbox intake, Runs UI)
        # treat the dict as authoritative and don't have to re-merge.
        run.outcome = payload.outcome.model_dump(mode="json")
    run.updated_at = now

    pipeline = await session.get(Pipeline, run.pipeline_id)
    if pipeline is not None:
        pipeline.last_run_status = payload.status
        pipeline.last_run_at = now
        pipeline.updated_at = now

    session.add(
        AuditLog(
            workspace_id=run.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="pipeline.run.callback",
            target_kind="pipeline_run",
            target_id=str(run.id),
            # ``auth_mode`` lets operators triage "which flavour of
            # SHIP_RUN_TOKEN did this callback come in on" without
            # grepping logs — useful once both paths coexist in the
            # wild and we need to see the repo-token rollout ramp.
            payload={
                "status": payload.status,
                "metrics": metrics_payload,
                "auth_mode": ctx.auth_mode,
            },
        )
    )
    if pipeline is not None:
        await _emit_self_heal_blocker_inbox(session, run=run, pipeline=pipeline)
    await session.flush()
    return _run_to_out(run)


class PoliciesPreambleOut(BaseModel):
    """Workspace policies rendered as a markdown preamble.

    ``preamble`` is ``None`` when the workspace has no enabled
    policies — the CLI uses that as the signal to skip prepending
    anything to its stdout (avoiding a stray separator).
    """

    preamble: str | None = Field(
        default=None,
        description=(
            "Markdown rendering of the enabled workspace policies, "
            "ready to prepend to the agent prompt. ``null`` when "
            "the workspace has no enabled policies."
        ),
    )


@public_router.get(
    "/runs/{run_id}/policies-preamble",
    response_model=PoliciesPreambleOut,
)
async def get_run_policies_preamble(
    run_id: uuid.UUID = Path(...),
    ctx: RunTokenContext = Depends(get_run_or_repo_token_context),
    session: AsyncSession = Depends(get_session),
) -> PoliciesPreambleOut:
    """Return the workspace's prose policies for the run's workspace.

    ``shipctl run`` calls this just before emitting pattern bodies
    to stdout and prepends ``preamble`` ahead of the first pattern.
    Same render path as the Navigator-chat injection (see
    ``services.policies.render_policies_preamble``) so both surfaces
    stay in lock-step on what the agent sees.

    Auth mirrors ``POST /runs/{run_id}/result``: per-run JWT or
    long-lived ``SHIP_RUN_TOKEN``. The dependency cross-checks the
    run id against the credential, so the workspace is implicitly
    authorised by membership of the ``PipelineRun`` row.
    """

    from backend.app.services.policies import render_policies_preamble

    preamble = await render_policies_preamble(session, ctx.workspace_id)
    return PoliciesPreambleOut(preamble=preamble)


# ---------------------------------------------------------------------------
# Auto-dispatch (knowledge-gathering pipelines)
# ---------------------------------------------------------------------------

# Pipelines that are safe to fire automatically after the install PR
# merges. The premise: these read-only scans feed the dashboard's
# "initial knowledge" buckets (code map + tech-debt inventory) so the
# operator's first visit after merging isn't a bunch of empty cards.
# Write-heavy or noisy lanes (pr_review, daily_standup) stay manual.
KNOWLEDGE_PIPELINE_LANE_IDS: Final[frozenset[str]] = frozenset(
    {"tech_debt", "code_map"}
)


async def auto_dispatch_knowledge_pipelines(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings,
    trigger: str = "auto_post_install",
) -> list[uuid.UUID]:
    """Fire ``workflow_dispatch`` for every knowledge lane on ``repo``.

    Called from the ``pull_request`` webhook when a
    ``ship/install-*`` branch merges — by then the workflow YAMLs
    the install PR added are live on the default branch, so
    dispatching them succeeds. Each failure is logged but swallowed:
    one missing preset shouldn't block the others, and the operator
    can always hit ``Run now`` manually.

    Returns the list of ``PipelineRun.id`` it created (useful for
    tests and future telemetry).
    """
    candidates = (
        await session.execute(
            select(Pipeline).where(
                Pipeline.workspace_id == repo.workspace_id,
                Pipeline.repo_id == repo.id,
                Pipeline.enabled.is_(True),
                Pipeline.lane_id.in_(KNOWLEDGE_PIPELINE_LANE_IDS),
            )
        )
    ).scalars().all()
    if not candidates:
        return []

    created: list[uuid.UUID] = []
    files = None  # lazy — only probe if we have something runnable
    for pipeline in candidates:
        if not _supports_run(pipeline.lane_id):
            continue
        workflow_file = _workflow_file_for_lane_id(pipeline.lane_id)
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
                pipeline.lane_id,
                repo.full_name,
                workflow_file,
            )
            continue
        now = datetime.now(timezone.utc)
        run = PipelineRun(
            pipeline_id=pipeline.id,
            workspace_id=pipeline.workspace_id,
            trigger=trigger,
            status="queued",
            started_at=now,
            summary=(
                f"Auto-dispatched {pipeline.name} after install PR merge "
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
                pipeline.lane_id,
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
            pipeline.last_run_status = "failed"
            pipeline.last_run_at = run.finished_at
            pipeline.updated_at = run.finished_at
            await session.flush()
            continue
        run.status = "running"
        pipeline.last_run_at = now
        pipeline.last_run_status = "running"
        pipeline.updated_at = now
        session.add(
            AuditLog(
                workspace_id=pipeline.workspace_id,
                actor_user_id=None,
                actor_token_id=None,
                action="pipeline.run",
                target_kind="pipeline",
                target_id=str(pipeline.id),
                payload={
                    "kind": pipeline.lane_id,
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
            pipeline.lane_id,
            repo.full_name,
            run.id,
        )
    return created


# ---------------------------------------------------------------------------
# Auto-dispatch (self-heal lane on CI failure)
# ---------------------------------------------------------------------------


class SelfHealDispatchResult(BaseModel):
    """Return shape for :func:`auto_dispatch_self_heal`.

    ``status`` is one of:

    - ``"dispatched"`` — we enqueued a :class:`PipelineRun` and GitHub
      accepted the ``workflow_dispatch`` call. ``run_id`` is set.
    - ``"skipped:<reason>"`` — we recognised the failure but chose not
      to auto-heal. Reasons:
      - ``pipeline_missing`` — no ``self_heal`` :class:`Pipeline` row.
      - ``pipeline_disabled`` — row exists but ``enabled=False``.
      - ``workflow_not_installed`` — YAML not on the default branch.
      - ``kind_not_supported`` — catalog missing install target.
    - ``"failed:<reason>"`` — dispatch attempted but GitHub refused
      (token expiry, scope regression, …). ``run_id`` *is* set because
      we still persist the failed :class:`PipelineRun` for audit.
    """

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
    """Fire the ``self_heal`` lane in response to a failed CI run.

    Mirrors :func:`auto_dispatch_knowledge_pipelines` but scoped to a
    single kind and wired to the webhook caller so the status can be
    surfaced as a dashboard notification. The failed-run context
    (``failed_run_external_id`` + ``failed_workflow_name``) is baked
    into ``PipelineRun.payload`` so the operator can trace the
    self-heal back to the failure that triggered it from either end.
    """
    pipeline = (
        await session.execute(
            select(Pipeline).where(
                Pipeline.workspace_id == repo.workspace_id,
                Pipeline.repo_id == repo.id,
                Pipeline.lane_id == "self_heal",
            )
        )
    ).scalars().first()
    if pipeline is None:
        return SelfHealDispatchResult(status="skipped:pipeline_missing")
    if not pipeline.enabled:
        return SelfHealDispatchResult(status="skipped:pipeline_disabled")
    if not _supports_run(pipeline.lane_id):
        return SelfHealDispatchResult(status="skipped:kind_not_supported")
    workflow_file = _workflow_file_for_lane_id(pipeline.lane_id)
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
    run = PipelineRun(
        pipeline_id=pipeline.id,
        workspace_id=pipeline.workspace_id,
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
        pipeline.last_run_status = "failed"
        pipeline.last_run_at = run.finished_at
        pipeline.updated_at = run.finished_at
        await session.flush()
        return SelfHealDispatchResult(
            status=f"failed:upstream_{exc.status_code}", run_id=run.id
        )

    run.status = "running"
    pipeline.last_run_at = now
    pipeline.last_run_status = "running"
    pipeline.updated_at = now
    session.add(
        AuditLog(
            workspace_id=pipeline.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="pipeline.run",
            target_kind="pipeline",
            target_id=str(pipeline.id),
            payload={
                "kind": pipeline.lane_id,
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
    "auto_dispatch_knowledge_pipelines",
    "auto_dispatch_self_heal",
    "dispatch_pipeline_run",
    "SelfHealDispatchResult",
    "KNOWLEDGE_PIPELINE_LANE_IDS",
]
