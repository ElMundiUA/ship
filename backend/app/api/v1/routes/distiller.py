"""Distiller v1 surface — Phase 6 ingest + run history.

Mounted under ``/v1/workspaces/{ws}`` to match the rest of the
knowledge surface (``/buckets/...`` routes live in ``chat.py``).
Two endpoints:

- ``POST /buckets/{slug}/distill`` — ingest one blob. Uses
  :func:`backend.app.services.distiller.run_distiller` under the
  hood; maintains the full transaction inside FastAPI's session
  dep so a hook failure rolls everything back. Returns the run
  row so the client can branch on ``decision``.
- ``GET /buckets/{slug}/distill/runs`` — paginated history.
  Useful for the console's "what did the Distiller do against this
  bucket?" panel and for operator debugging.

RBAC:
  - POST requires ``ROLES_MAINTAIN`` — writing to knowledge is a
    maintainer-level action, matching the existing bucket CRUD
    routes in ``chat.py``.
  - GET requires ``ROLES_READ`` — history is workspace-wide info.

We lean on the schemas from ``chat.py`` for the bucket lookup
(``_load_bucket``) so slug resolution stays in one place.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.chat import _load_bucket
from backend.app.api.v1.routes.workspaces import (
    ROLES_MAINTAIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import (
    BucketSource,
    DistillerRun,
)
from backend.app.db.session import get_session
from backend.app.services.agent.client import AgentClient, pick_default_client
from backend.app.services.distiller import (
    Classifier,
    DistillerInput,
    classify_stub,
    run_distiller,
)
from backend.app.services.distiller_llm import make_llm_classifier
from backend.app.services.connectors import (
    ConnectorConfigError,
    ConnectorError,
    ConnectorPage,
    fetch_connector_pages,
)
from backend.app.services.distiller_sources import (
    ingest_connector_page,
    ingest_external_static_upload,
)


# Hard cap on upload size. 1 MiB keeps prompts small and makes the
# "paste a runbook" flow cheap; larger files should go through a
# URL-import later. We enforce this manually instead of relying on
# FastAPI because we want to stream-check against a clean error.
_MAX_UPLOAD_BYTES = 1_000_000

# Content types we accept for text uploads. Extension fallback covers
# editors that send ``application/octet-stream`` for ``.md``.
# Everything is decoded as UTF-8 text and distilled as markdown-ish
# body input — binary formats (pdf, images) stay rejected.
_ALLOWED_CONTENT_TYPES: set[str] = {
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/html",
    "text/css",
    "text/javascript",
    "text/xml",
    "text/csv",
    "text/yaml",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-yaml",
    "application/octet-stream",
}
_ALLOWED_EXTENSIONS: set[str] = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".csv",
    ".html",
    ".htm",
    ".xml",
    ".css",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
}


logger = logging.getLogger(__name__)


# Override hook for tests — lets the fixture inject a fake agent
# client without patching ``pick_default_client`` globally.
_client_override: AgentClient | None = None


def set_distiller_agent_client(client: AgentClient | None) -> None:
    """Test-only: pin the AgentClient used by the ``llm``/``auto`` paths."""
    global _client_override
    _client_override = client


def _resolve_classifier(mode: str) -> tuple[Classifier, str]:
    """Pick the classifier impl for one request.

    * ``stub`` — always deterministic rules (used by tests + when
      the operator wants hand-off audits for a run).
    * ``llm`` — LLM required; raises 503 if no agent is configured.
    * ``auto`` — try LLM, fall back to stub if ``pick_default_client``
      can't resolve credentials. This is the product default.
    """
    choice = (mode or "auto").strip().lower()
    if choice == "stub":
        return classify_stub, "stub"

    if _client_override is not None:
        return make_llm_classifier(_client_override), "llm"

    try:
        client = pick_default_client()
    except RuntimeError as exc:
        if choice == "llm":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "LLM classifier unavailable: no agent API key "
                    f"configured ({exc})"
                ),
            )
        logger.info(
            "distiller: auto classifier → stub (no agent client: %s)", exc
        )
        return classify_stub, "stub"

    return make_llm_classifier(client), "llm"


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["distiller"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DistillIn(BaseModel):
    """Shape of one Distiller invocation.

    ``source_kind`` must be one of
    :class:`backend.app.db.models.agent_memory.BucketSource` so the
    article provenance stays consistent with the bucket categorisation.
    Defaults to ``external_static`` which matches the most common
    console-triggered ingest ("paste/upload a chunk of notes").
    """

    body_md: str = Field(min_length=0, max_length=500_000)
    source_kind: str = Field(default=BucketSource.EXTERNAL_STATIC)
    title_hint: str | None = Field(default=None, max_length=512)
    slug_hint: str | None = Field(default=None, max_length=120)
    provenance: dict[str, Any] = Field(default_factory=dict)
    input_ref: dict[str, Any] = Field(default_factory=dict)
    # ``auto`` = LLM when an agent is configured, stub otherwise.
    # Explicit ``stub`` pins deterministic rules (used by tests and
    # for replaying an ingest without model variance). Explicit
    # ``llm`` forces the LLM path and 503s if no key is configured.
    classifier: Literal["auto", "stub", "llm"] = "auto"


class DistillerRunOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    bucket_id: uuid.UUID
    source_kind: str
    status: str
    decision: str | None
    input_ref: dict[str, Any]
    output_refs: dict[str, Any]
    error: str | None
    created_by_user_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DistillOut(BaseModel):
    """Response from :func:`distill_bucket`.

    Surface the ``DistillerRun`` shape plus a light ``article_ids``
    promotion so callers don't have to dig into ``output_refs`` to
    find what landed. Matches the public contract documented in
    ``backend/docs/knowledge-consolidation.md``.
    """

    run: DistillerRunOut
    decision: str
    article_ids: list[uuid.UUID]
    reason: str | None = None
    classifier: str = "stub"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_to_out(row: DistillerRun) -> DistillerRunOut:
    return DistillerRunOut(
        id=row.id,
        workspace_id=row.workspace_id,
        bucket_id=row.bucket_id,
        source_kind=row.source_kind,
        status=row.status,
        decision=row.decision,
        input_ref=row.input_ref or {},
        output_refs=row.output_refs or {},
        error=row.error,
        created_by_user_id=row.created_by_user_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/buckets/{slug}/distill",
    response_model=DistillOut,
    status_code=status.HTTP_200_OK,
)
async def distill_bucket(
    workspace_id: uuid.UUID,
    slug: str,
    payload: DistillIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DistillOut:
    """Phase 6a stub ingest.

    Validates the bucket exists under the workspace, runs the
    Distiller (stub classifier + article writer), and returns the
    stored run row along with any ids the call produced.

    4xx codes:
      * ``400`` — ``source_kind`` not in
        :class:`backend.app.db.models.agent_memory.BucketSource`.
      * ``403`` — caller is not a workspace maintainer.
      * ``404`` — bucket slug not found under the workspace.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_MAINTAIN
    )
    bucket = await _load_bucket(session, workspace_id, slug)

    if payload.source_kind not in BucketSource.ALL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"source_kind must be one of {BucketSource.ALL}",
        )

    classifier, resolved_mode = _resolve_classifier(payload.classifier)

    outcome = await run_distiller(
        session,
        workspace_id=workspace_id,
        bucket=bucket,
        actor_user_id=auth.user.id,
        inp=DistillerInput(
            body_md=payload.body_md,
            source_kind=payload.source_kind,
            title_hint=payload.title_hint,
            slug_hint=payload.slug_hint,
            provenance=payload.provenance or None,
            input_ref=payload.input_ref or None,
        ),
        classifier=classifier,
    )

    run = (
        await session.execute(
            select(DistillerRun).where(DistillerRun.id == outcome.run_id)
        )
    ).scalars().one()

    return DistillOut(
        run=_run_to_out(run),
        decision=outcome.decision,
        article_ids=outcome.article_ids,
        reason=outcome.reason,
        classifier=outcome.classifier or resolved_mode,
    )


@router.post(
    "/buckets/{slug}/upload",
    response_model=DistillOut,
    status_code=status.HTTP_200_OK,
)
async def upload_to_bucket(
    workspace_id: uuid.UUID,
    slug: str,
    file: UploadFile = File(..., description="Text / markdown file to ingest"),
    classifier: str = Form(default="auto"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DistillOut:
    """Phase 7 entry — external-static upload surface.

    Accepts a single text/markdown file via multipart/form-data,
    decodes the bytes as UTF-8 (strict — we don't want silent
    replacement chars trashing the index), and hands the content
    off to :func:`ingest_external_static_upload`. The Distiller
    then decides new/update/skip under the bucket's taxonomy and
    writes a :class:`BucketArticle` row.

    4xx codes:
      * ``400`` — file exceeded size cap, wrong content type, or
        non-UTF-8 bytes.
      * ``403`` — caller lacks ``ROLES_MAINTAIN``.
      * ``404`` — bucket slug not found under the workspace.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_MAINTAIN
    )
    bucket = await _load_bucket(session, workspace_id, slug)

    filename = (file.filename or "upload").strip()
    content_type = (file.content_type or "").lower()

    extension = ""
    if "." in filename:
        extension = "." + filename.rsplit(".", 1)[1].lower()

    if (
        content_type
        and content_type.split(";", 1)[0].strip() not in _ALLOWED_CONTENT_TYPES
        and extension not in _ALLOWED_EXTENSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "unsupported content type "
                f"{content_type!r} — upload plain-text UTF-8 files "
                "(markdown, json, csv, html, source, …)"
            ),
        )

    raw = await file.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"file too large ({len(raw)} bytes, max {_MAX_UPLOAD_BYTES})",
        )

    try:
        body_md = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"file is not valid UTF-8: {exc}",
        )

    chosen, resolved_mode = _resolve_classifier(classifier)
    outcome = await ingest_external_static_upload(
        session,
        workspace_id=workspace_id,
        bucket=bucket,
        actor_user_id=auth.user.id,
        filename=filename,
        content_type=content_type or None,
        body_md=body_md,
        classifier=chosen,
    )

    run = (
        await session.execute(
            select(DistillerRun).where(DistillerRun.id == outcome.run_id)
        )
    ).scalars().one()

    return DistillOut(
        run=_run_to_out(run),
        decision=outcome.decision,
        article_ids=outcome.article_ids,
        reason=outcome.reason,
        classifier=outcome.classifier or resolved_mode,
    )


@router.post(
    "/buckets/{slug}/sync",
    response_model=DistillOut,
    status_code=status.HTTP_200_OK,
)
async def sync_connector_bucket(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DistillOut:
    """Phase 7c — manually refresh a connector-proxy bucket.

    Resolution order for the body the Distiller sees:

    1. The connector registry
       (:mod:`backend.app.services.connectors`) is asked for a
       fetcher matching ``Integration.kind``. If one is registered
       and it can handle the stored ``resource_ref``, we fetch the
       real third-party page and ingest its markdown. Notion is the
       first kind wired via this path.
    2. If no fetcher is registered, or the fetcher declines the
       shape (e.g. Notion v1 only handles ``{page_id}``, not
       ``{database_id}``), we fall back to the Phase 7b stub body
       so the bucket's sync loop keeps working in the UI without
       forcing every connector to land at the same time.
    3. Each invocation records one :class:`DistillerRun`. Repeated
       syncs against unchanged content resolve to ``skip`` via
       ``content_sha`` dedup — same as Phase 7b.

    4xx/5xx codes:
      * ``400`` — bucket is not ``connector_proxy`` or the stored
        ``source_ref`` is missing the connector handle.
      * ``403`` — caller lacks ``ROLES_MAINTAIN``.
      * ``404`` — bucket slug not found under the workspace.
      * ``502`` — a real fetcher raised a
        :class:`ConnectorConfigError` (bad token, missing grant,
        upstream 4xx). Re-connect the integration and retry.
    """

    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_MAINTAIN
    )
    bucket = await _load_bucket(session, workspace_id, slug)

    if bucket.source_kind != BucketSource.CONNECTOR_PROXY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"bucket {slug!r} has source_kind={bucket.source_kind!r} "
                "— sync is only defined for connector_proxy"
            ),
        )

    source_ref = bucket.source_ref or {}
    integration_kind = str(source_ref.get("integration_kind") or "").strip()
    if not integration_kind:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "connector_proxy bucket is missing source_ref.integration_kind; "
                "re-create the bucket so the connector handle is recorded"
            ),
        )
    resource_ref = source_ref.get("resource_ref") or {}
    if not isinstance(resource_ref, dict):
        resource_ref = {}

    # Load the integration row so the fetcher can decrypt its
    # secret. We load it even when we don't have a fetcher wired —
    # that way a broken integration (deleted row, revoked token) is
    # a clean 400 before we commit to the ingest path.
    integration_row = await _load_integration(
        session,
        workspace_id=workspace_id,
        source_ref=source_ref,
        kind=integration_kind,
    )

    classifier, resolved_mode = _resolve_classifier("stub")

    # ---- Real fetcher path (Phase 7c) -------------------------------
    pages: list[ConnectorPage] = []
    if integration_row is not None:
        try:
            pages = await fetch_connector_pages(integration_row, resource_ref)
        except ConnectorConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"connector config error: {exc}",
            ) from exc
        except ConnectorError as exc:
            logger.exception(
                "connector sync: fetcher failure kind=%s slug=%s",
                integration_kind,
                slug,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"connector fetch failed: {exc}",
            ) from exc

    # Single-page semantics for v1 — if a fetcher returned multiple
    # pages we still only ingest the first and log the rest so the
    # operator can see what was skipped. Multi-page sync is a
    # natural Phase 7d extension but would require switching the
    # response shape, which we keep stable here.
    if len(pages) > 1:
        logger.info(
            "connector sync: fetcher returned %d pages; ingesting first only "
            "(multi-page support is Phase 7d work)",
            len(pages),
        )

    page_ref: dict[str, Any]
    body_md: str
    if pages:
        first = pages[0]
        page_ref = {
            "slug": first.slug,
            "title": first.title,
            **dict(first.page_ref),
            "resource_ref": resource_ref,
        }
        body_md = first.body_md
    else:
        # ---- Stub fallback (preserved from Phase 7b) ----------------
        page_ref = {
            "slug": f"{integration_kind}-index",
            "title": f"{integration_kind.title()} · {slug}",
            "resource_ref": resource_ref,
        }
        lines = [
            f"# {integration_kind.title()} · {bucket.name}",
            "",
            f"- Source kind: `{bucket.source_kind}`",
            f"- Integration: `{integration_kind}`",
        ]
        if resource_ref:
            pairs = ", ".join(
                f"{k}={v!r}" for k, v in sorted(resource_ref.items())
            )
            lines.append(f"- Resource: {pairs}")
        lines.extend(
            [
                "",
                "> Connector fetcher is not wired for this integration or "
                "resource_ref shape. This is a stand-in body so the Distiller "
                "loop stays observable. See `backend/docs/"
                "knowledge-consolidation.md` Phase 7c for wired connectors.",
            ]
        )
        body_md = "\n".join(lines).strip()

    outcome = await ingest_connector_page(
        session,
        workspace_id=workspace_id,
        bucket=bucket,
        actor_user_id=auth.user.id,
        connector_kind=integration_kind,
        page_ref=page_ref,
        body_md=body_md,
        classifier=classifier,
    )

    run = (
        await session.execute(
            select(DistillerRun).where(DistillerRun.id == outcome.run_id)
        )
    ).scalars().one()

    return DistillOut(
        run=_run_to_out(run),
        decision=outcome.decision,
        article_ids=outcome.article_ids,
        reason=outcome.reason,
        classifier=outcome.classifier or resolved_mode,
    )


async def _load_integration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    source_ref: dict[str, Any],
    kind: str,
):
    """Resolve the Integration row a connector bucket points at.

    Returns ``None`` when the row can't be found — the caller falls
    back to the stub body with a logged warning rather than 4xx'ing
    because the bucket row is still referentially valid; only the
    fetch half is degraded.
    """

    from backend.app.db.models.tenancy import Integration

    raw_id = source_ref.get("integration_id")
    candidates = []
    if isinstance(raw_id, str) and raw_id.strip():
        try:
            candidates.append(uuid.UUID(raw_id.strip()))
        except ValueError:
            logger.info(
                "connector sync: source_ref.integration_id=%r isn't a UUID",
                raw_id,
            )

    stmt = select(Integration).where(
        Integration.workspace_id == workspace_id,
        Integration.kind == kind,
    )
    if candidates:
        stmt = stmt.where(Integration.id == candidates[0])

    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        logger.warning(
            "connector sync: integration row missing for kind=%s ws=%s — "
            "falling back to stub body",
            kind,
            workspace_id,
        )
    return row


@router.get(
    "/buckets/{slug}/distill/runs",
    response_model=list[DistillerRunOut],
)
async def list_distiller_runs(
    workspace_id: uuid.UUID,
    slug: str,
    limit: int = 50,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[DistillerRunOut]:
    """Recent runs for the bucket, newest first.

    ``limit`` is clamped to [1, 200]; the console's history panel
    only needs the last ~20 rows in practice. An older-style cursor
    pagination can layer in later without touching this shape.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    bucket = await _load_bucket(session, workspace_id, slug)

    clamped = max(1, min(int(limit), 200))
    rows = (
        await session.execute(
            select(DistillerRun)
            .where(
                DistillerRun.workspace_id == workspace_id,
                DistillerRun.bucket_id == bucket.id,
            )
            .order_by(desc(DistillerRun.created_at))
            .limit(clamped)
        )
    ).scalars().all()
    return [_run_to_out(r) for r in rows]
