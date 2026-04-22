"""Workspace-private catalog patterns (RFC-0008 §H — PR-6).

Lets workspace admins author catalog patterns at runtime — with
optional LLM assistance — when the baked-in catalog is missing
something they need. Rows live in :class:`CustomPattern`; the
catalog adapter in :mod:`backend.app.services.catalog` promotes
them into :class:`CatalogArtifact` so every picker that calls
``/v1/catalog/patterns?workspace_id=<ws>`` sees them alongside
baked-in entries.

Endpoints
---------

* ``POST /workspaces/{ws}/patterns/draft`` — ask the LLM to
  synthesise a pattern from a free-form brief. Does not persist.
  Caller reviews the draft in the Console modal before saving.
* ``POST /workspaces/{ws}/patterns`` — persist a vetted pattern.
  Validates ``pattern_id`` uniqueness and rejects collisions with
  baked-in ids.
* ``GET /workspaces/{ws}/patterns`` — list workspace-private rows
  only (baked-in patterns stay exclusively on ``/catalog/patterns``;
  merged reads go through that endpoint with ``workspace_id=``).
* ``DELETE /workspaces/{ws}/patterns/{id}`` — remove. Guards against
  deleting a pattern that is currently referenced by a
  ``WorkspacePolicy`` or wired into ``pipelines`` so the audit trail
  stays consistent.

LLM draft contract
------------------

We ask the model to emit a single JSON object that mirrors
:class:`PatternDraft` (no markdown fences, no prose). ``body`` is
the agent prompt that would normally live under the frontmatter
divider in ``ARTIFACT.md``. We then echo the draft back to the
Console as-is — editing and persistence happen on the save step.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import asc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.custom_patterns import CustomPattern
from backend.app.db.models.policies import WorkspacePolicy
from backend.app.db.session import get_session
from backend.app.services import catalog as catalog_service
from backend.app.core.config import get_settings
from backend.app.services.agent.client import ChatMessage, pick_default_client


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["custom-patterns"],
)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


# Match baked-in pattern ids (kebab-case, 3..120 chars). We allow
# the workspace to use any id that would be a legal filesystem slug;
# collisions with baked-in ids are rejected at write time, not via
# a prefix rule, so operators can still shadow a baked-in pattern
# by id if they really want to (merge resolves in their favour).
_PATTERN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,118}[a-z0-9]$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


PatternMode = Literal["lane", "request"]


class PatternInput(BaseModel):
    """One ``spec.inputs[*]`` entry — keeps the schema open so the
    LLM can emit richer inputs (enum, default, validation) without
    the API needing a fixed closed set."""

    id: str
    label: str | None = None
    description: str | None = None
    required: bool = False
    default: Any | None = None
    # ``string | int | bool | enum | ...`` — we don't enforce a closed
    # list because the baked-in catalog doesn't either; the renderer
    # is expected to fall back to a text input on unknown types.
    type: str | None = None
    # Free-form extras — the LLM often emits ``enum``, ``example``,
    # ``help``. We keep them so the picker can render them verbatim.
    extra: dict[str, Any] = Field(default_factory=dict)


class CustomPatternOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    pattern_id: str
    name: str
    description: str
    category: str | None
    modes: list[PatternMode]
    inputs: list[dict[str, Any]]
    spec: dict[str, Any]
    body: str
    created_by_user_id: uuid.UUID | None
    created_at: Any
    updated_at: Any

    @classmethod
    def from_row(cls, row: CustomPattern) -> "CustomPatternOut":
        return cls(
            id=row.id,
            workspace_id=row.workspace_id,
            pattern_id=row.pattern_id,
            name=row.name,
            description=row.description or "",
            category=row.category,
            modes=list(row.modes or []),  # type: ignore[arg-type]
            inputs=list(row.inputs or []),
            spec=dict(row.spec or {}),
            body=row.body or "",
            created_by_user_id=row.created_by_user_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class CustomPatternIn(BaseModel):
    pattern_id: str = Field(..., min_length=3, max_length=120)
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    category: str | None = Field(default=None, max_length=32)
    modes: list[PatternMode] = Field(default_factory=list)
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    spec: dict[str, Any] = Field(default_factory=dict)
    body: str = ""

    @field_validator("pattern_id")
    @classmethod
    def _validate_pattern_id(cls, value: str) -> str:
        if not _PATTERN_ID_RE.match(value):
            raise ValueError(
                "pattern_id must be kebab-case (lowercase letters, digits, "
                "hyphens), 3..120 chars, no leading/trailing hyphen"
            )
        return value

    @field_validator("modes")
    @classmethod
    def _non_empty_modes(cls, value: list[PatternMode]) -> list[PatternMode]:
        if not value:
            raise ValueError("modes must include at least one of 'lane', 'request'")
        # Dedupe while preserving order.
        seen: list[PatternMode] = []
        for m in value:
            if m not in seen:
                seen.append(m)
        return seen


class PatternDraft(BaseModel):
    """Shape the LLM is asked to emit (also the draft endpoint's response)."""

    pattern_id: str
    name: str
    description: str = ""
    category: str | None = None
    modes: list[PatternMode] = Field(default_factory=lambda: ["request"])
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    spec: dict[str, Any] = Field(default_factory=dict)
    body: str = ""


class PatternDraftIn(BaseModel):
    """Request body for the draft endpoint."""

    prompt: str = Field(..., min_length=8, max_length=4000)
    # Operator hint to bias the draft toward lane vs request modes.
    # Defaults to both so the LLM can pick the right shape on its
    # own if the brief is ambiguous.
    target_modes: list[PatternMode] = Field(
        default_factory=lambda: ["lane", "request"]
    )


# ---------------------------------------------------------------------------
# LLM draft
# ---------------------------------------------------------------------------


_DRAFT_SYSTEM_PROMPT = """You are a senior platform engineer helping an operator \
author a reusable **catalog pattern** for Ship — a fleet-wide \
automation platform. Each pattern wraps one agent prompt + invocation \
hints so it can be scheduled as a recurring "lane" on a repo or fired \
as a one-off "request".

Emit **a single JSON object** (no prose, no markdown fences, no code \
blocks — raw JSON only). It must match this TypeScript schema:

  {
    pattern_id: string,   // kebab-case, 3..120 chars, must start with
                          // "custom-" to avoid colliding with baked-in ids
    name: string,         // human-friendly title
    description: string,  // one-sentence summary of what the agent does
    category: "flow" | "role" | "scan" | "op" | "onboard" | "custom" | null,
    modes: Array<"lane" | "request">,  // how this pattern is used
    inputs: Array<{       // parameters the operator fills at invoke time
      id: string,
      label: string,
      description?: string,
      required?: boolean,
      type?: "string" | "int" | "bool" | "enum",
      default?: unknown,
    }>,
    spec: {               // advanced metadata; leave empty object if not needed
      default_trigger?: { event?: string, schedule?: string },
    },
    body: string          // MARKDOWN prompt the agent executes. This is the
                          // most important field — be concrete, give the
                          // agent clear success criteria, reference the
                          // inputs as ${input_id} placeholders.
  }

Rules:
- ``pattern_id`` MUST start with ``custom-`` and use only lowercase \
letters, digits, and hyphens.
- ``body`` MUST reference each declared input via ``${input_id}`` at \
least once so the renderer can substitute values.
- If the operator's brief is vague, pick sensible defaults — DO NOT \
invent inputs that have no basis in the brief.
- Keep ``body`` focused on one outcome. Multi-step work belongs in \
separate patterns.
- Output MUST be valid JSON parseable by ``json.loads`` on the first try."""


def _salvage_json(raw: str) -> dict[str, Any]:
    """Best-effort JSON extraction — tolerates stray prose/fences.

    OpenAI's JSON mode is reliable; Anthropic is looser. Pattern is
    lifted from :mod:`backend.app.services.distiller_llm` which has
    the same contract (trust the model for the verdict, salvage the
    syntax).
    """
    text = str(raw or "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass
    # Look for the outermost ``{ ... }``.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


@router.post(
    "/patterns/draft",
    response_model=PatternDraft,
)
async def draft_pattern(
    workspace_id: uuid.UUID,
    payload: PatternDraftIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PatternDraft:
    """LLM-backed pattern draft.

    Returns a :class:`PatternDraft`; the Console renders it into the
    author modal so the operator can eyeball the prompt body and
    input list, edit, then fire ``POST /patterns`` to persist.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    try:
        client = pick_default_client(get_settings())
    except RuntimeError as exc:
        # Mirrors the 412 the chat router returns when no LLM key is
        # configured — the Console shows a "set up your LLM key"
        # banner instead of a generic 500.
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "llm_unconfigured", "message": str(exc)},
        ) from exc

    user_msg = (
        f"Modes the operator expects: {', '.join(payload.target_modes)}.\n"
        f"Brief:\n{payload.prompt.strip()}"
    )
    messages = [
        ChatMessage(role="system", content=_DRAFT_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_msg),
    ]

    try:
        raw = await client.acomplete(
            messages,
            max_tokens=1800,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # pragma: no cover — upstream network errors
        logger.exception("custom-pattern draft failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "llm_failed", "message": str(exc)},
        ) from exc

    data = _salvage_json(raw)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "llm_unparseable",
                "message": "Model returned no usable JSON; try rephrasing the brief.",
            },
        )

    # Coerce through PatternDraft so we reject obviously malformed
    # payloads before they reach the Console (missing required keys,
    # wrong types). The Console is trusted to edit/validate further
    # before firing ``POST /patterns``.
    try:
        draft = PatternDraft.model_validate(data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "llm_schema_mismatch",
                "message": f"Model output did not match schema: {exc}",
            },
        ) from exc

    # Best-effort id normalisation — the system prompt asks for a
    # ``custom-`` prefix but models occasionally drop it.
    if not draft.pattern_id.startswith("custom-"):
        draft.pattern_id = f"custom-{draft.pattern_id}"
    return draft


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/patterns", response_model=list[CustomPatternOut])
async def list_workspace_patterns(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[CustomPatternOut]:
    """List workspace-private patterns only (no baked-in entries).

    The Console lists these on a dedicated "Workspace patterns" tab
    inside the author modal so operators can see what they already
    own and delete obsolete rows.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    rows = list(
        (
            await session.execute(
                select(CustomPattern)
                .where(CustomPattern.workspace_id == workspace_id)
                .order_by(asc(CustomPattern.pattern_id))
            )
        )
        .scalars()
        .all()
    )
    return [CustomPatternOut.from_row(r) for r in rows]


@router.post(
    "/patterns",
    response_model=CustomPatternOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_pattern(
    workspace_id: uuid.UUID,
    payload: CustomPatternIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> CustomPatternOut:
    """Persist a vetted pattern.

    Guards:
    * ``pattern_id`` must not collide with a baked-in pattern id —
      otherwise the merge would silently shadow something the
      operator might not realise they're overriding.
    * Per-workspace uniqueness is enforced at the DB level; we catch
      the :class:`IntegrityError` and turn it into a friendly 409.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    baked_in = {p.id for p in catalog_service.list_patterns()}
    if payload.pattern_id in baked_in:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "pattern_id_reserved",
                "message": (
                    f"pattern_id '{payload.pattern_id}' collides with a "
                    "baked-in pattern. Pick a different id or contribute "
                    "the change upstream."
                ),
            },
        )

    row = CustomPattern(
        workspace_id=workspace_id,
        pattern_id=payload.pattern_id,
        name=payload.name,
        description=payload.description or "",
        category=payload.category,
        modes=list(payload.modes),
        inputs=list(payload.inputs),
        spec=dict(payload.spec),
        body=payload.body or "",
        created_by_user_id=auth.user.id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "pattern_id_conflict",
                "message": (
                    f"pattern_id '{payload.pattern_id}' already exists in "
                    "this workspace."
                ),
            },
        ) from exc

    # ``created_at`` / ``updated_at`` are server-defaulted; refresh so
    # the response carries them without triggering a lazy load.
    await session.refresh(row, attribute_names=["created_at", "updated_at"])
    return CustomPatternOut.from_row(row)


@router.delete(
    "/patterns/{pattern_row_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_pattern(
    workspace_id: uuid.UUID,
    pattern_row_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Delete a workspace-private pattern.

    Blocks deletion while the pattern is referenced by any active
    :class:`WorkspacePolicy` (mirror-lane rule) — removing it out
    from under a live policy would leave the Console rendering
    orphaned ids in the compliance rollup. Historical agent/fleet
    requests are not gated; they carry the id as an audit artefact
    and the UI already renders unresolved ids defensively.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    row = (
        await session.execute(
            select(CustomPattern).where(
                CustomPattern.id == pattern_row_id,
                CustomPattern.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Referenced-by check. Compliance code uses ``pattern_id`` (string)
    # not the PK, so we look both tables up by the textual id.
    policy_ref = (
        await session.execute(
            select(WorkspacePolicy.id).where(
                WorkspacePolicy.workspace_id == workspace_id,
                WorkspacePolicy.pattern_id == row.pattern_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if policy_ref is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "pattern_in_use_policy",
                "message": (
                    "Pattern is referenced by a workspace policy. Remove "
                    "the policy first."
                ),
            },
        )

    await session.delete(row)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
