"""ELS-168 — extract a structured project-and-epics proposal from a
requirements PDF.

Pure-function entry point :func:`extract_proposal_from_pdf` accepts
the raw PDF bytes and returns a validated :class:`MassPlanProposal`.
Implementation calls Anthropic Claude with the doc inlined as a
``document`` content block and forces tool-use on a JSON-schema'd
``propose_mass_plan`` tool — the response is therefore guaranteed to
match the schema or we raise.

Cost / latency are recorded on every call via the
``mass_planning.extraction.cost`` audit row so M8 can build a
dashboard without retro-instrumenting.

Fallback path (env ``SHIP_MASS_PLANNING_USE_DOCLING=1``) is a text +
layout extractor with the same output shape — wired separately and
only consulted when vision-LLM cost crosses a threshold the operator
sets. Not implemented in M1; the env flag is reserved for the
follow-up that needs it.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class ProjectProposal(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: str = Field(..., min_length=1, max_length=2_000)


class EpicProposal(BaseModel):
    """One epic = one ``planning:anchor`` ticket in the target Linear
    project. ``brief`` lands in the ticket body as the PO Brief
    section that downstream decomposition (`wbs` / `tasks`) reads.
    """

    key: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=160)
    brief: str = Field(..., min_length=1, max_length=8_000)
    depends_on: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("key")
    @classmethod
    def _key_kebab_case(cls, v: str) -> str:
        if any(ch.isspace() for ch in v):
            raise ValueError("epic.key cannot contain whitespace")
        if v != v.lower():
            raise ValueError("epic.key must be lowercase")
        return v


class MassPlanProposal(BaseModel):
    project: ProjectProposal
    epics: list[EpicProposal] = Field(..., min_length=1, max_length=24)

    @field_validator("epics")
    @classmethod
    def _no_cycles_no_unknown_deps(
        cls, v: list[EpicProposal]
    ) -> list[EpicProposal]:
        keys = {e.key for e in v}
        for e in v:
            for d in e.depends_on:
                if d not in keys:
                    raise ValueError(
                        f"epic {e.key!r}: depends_on references unknown key "
                        f"{d!r}"
                    )
                if d == e.key:
                    raise ValueError(
                        f"epic {e.key!r}: cannot depend on itself"
                    )
        # Topological-sort check — cycle detection via Kahn's algorithm.
        indeg = {e.key: 0 for e in v}
        for e in v:
            for d in e.depends_on:
                # edge: d → e (d must finish before e can start)
                indeg[e.key] += 1
        order: list[str] = [k for k, n in indeg.items() if n == 0]
        seen: list[str] = []
        while order:
            k = order.pop()
            seen.append(k)
            for e in v:
                if k in e.depends_on:
                    indeg[e.key] -= 1
                    if indeg[e.key] == 0:
                        order.append(e.key)
        if len(seen) != len(v):
            raise ValueError(
                "depends_on graph has at least one cycle — invalid proposal"
            )
        return v


# ---------------------------------------------------------------------------
# Anthropic tool schema (mirrors the pydantic models above; kept inline so
# changes to one side don't drift the other silently).
# ---------------------------------------------------------------------------


_TOOL_SCHEMA: dict[str, Any] = {
    "name": "propose_mass_plan",
    "description": (
        "Emit the structured project + epics + dependencies proposal "
        "derived from the attached requirements document."
    ),
    "input_schema": {
        "type": "object",
        "required": ["project", "epics"],
        "additionalProperties": False,
        "properties": {
            "project": {
                "type": "object",
                "required": ["name", "description"],
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                        "description": (
                            "Short, scannable Linear project name. "
                            "Capture the goal in 3-7 words."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                        "description": (
                            "1-3 sentences. What does shipping this entire "
                            "project deliver to the end-user?"
                        ),
                    },
                },
            },
            "epics": {
                "type": "array",
                "minItems": 1,
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "required": ["key", "title", "brief"],
                    "additionalProperties": False,
                    "properties": {
                        "key": {
                            "type": "string",
                            "pattern": "^[a-z0-9][a-z0-9-]{0,31}$",
                            "description": (
                                "Stable slug, lowercase kebab-case, "
                                "≤32 chars (e.g. 'e1-bootstrap')."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "brief": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 8000,
                            "description": (
                                "1-3 paragraph PO Brief: user-facing "
                                "outcome, what's in scope, what's "
                                "explicitly NOT. Plain prose, no headers."
                            ),
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 16,
                            "description": (
                                "Keys of epics that MUST finish before "
                                "this one can start. Only true blockers, "
                                "not soft preferences."
                            ),
                        },
                    },
                },
            },
        },
    },
}


_SYSTEM_PROMPT = """You are a planning assistant for the Ship pipeline.

Input: a PDF of product requirements (PRD / spec / RFC).

Decompose the work into a coherent project + epics graph the Ship
agent pipeline can take from there. Each epic becomes a Linear
``planning:anchor`` ticket; downstream agent roles (planning / wbs /
architecture / tasks) read the brief and produce child tickets.

Rules:

1. Prefer 3-12 epics. Fewer larger epics > many tiny ones — each is
   its own SDLC chain with its own dev/qa/code-review cycle.
2. Epic ``key``: lowercase, kebab-case, ≤32 chars, prefixed by
   ``e<index>-`` so reading the order is obvious (``e1-bootstrap``,
   ``e2-core-api``, ``e3-ui``).
3. Brief = 1-3 short paragraphs. WHAT user-facing outcome, what's
   in scope, what's explicitly NOT. No HOW (that's the planning
   role's job). No headers / bullet lists.
4. ``depends_on`` = ONLY hard blockers. ``e2-core-api`` depends on
   ``e1-bootstrap`` because the bootstrap creates the schema the
   API needs. ``e2-core-api`` does NOT depend on ``e3-ui`` for
   "logical sequencing" — that's a soft preference, leave it out.
5. No cycles. No self-depends.
6. Do NOT invent epics not grounded in the doc.

Emit your proposal via the ``propose_mass_plan`` tool. No prose
response — the tool call IS the response.
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


class ExtractionResult(BaseModel):
    """Wrap the proposal + the cost / latency stats the caller (and
    M8 dashboard) want."""

    proposal: MassPlanProposal
    input_tokens: int
    output_tokens: int
    file_bytes: int
    duration_ms: int
    model_id: str
    fallback_used: bool = False


async def extract_proposal_from_pdf(
    pdf_bytes: bytes,
    *,
    api_key: str,
    model: str = "claude-sonnet-4-5-20250929",
    max_tokens: int = 8_192,
) -> ExtractionResult:
    """Extract a structured proposal from a requirements PDF.

    Single Anthropic call with the PDF inlined as ``document`` +
    forced tool-use. The response's only ``tool_use`` block carries
    the proposal JSON which we validate via :class:`MassPlanProposal`.

    Raises ``ValueError`` on:
    - empty input bytes
    - PDF > 32MB (Anthropic vision cap)
    - validation failure (no cycle / unknown dep / schema mismatch)
    - missing tool-use block in the response
    """
    if not pdf_bytes:
        raise ValueError("pdf_bytes is empty")
    if len(pdf_bytes) > 32 * 1024 * 1024:
        raise ValueError(
            f"PDF is {len(pdf_bytes) / 1024 / 1024:.1f}MB; "
            "Anthropic vision caps inline documents at 32MB"
        )

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    started = time.monotonic()
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "propose_mass_plan"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Read the attached PDF and emit the "
                            "structured proposal."
                        ),
                    },
                ],
            }
        ],
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    # Find the forced tool_use block.
    tool_input: dict[str, Any] | None = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(
            block, "name", None
        ) == "propose_mass_plan":
            tool_input = getattr(block, "input", None)
            break
    if tool_input is None:
        raise ValueError(
            "Anthropic response did not contain a propose_mass_plan "
            "tool_use block — model may have refused or the prompt "
            "needs revision"
        )

    proposal = MassPlanProposal.model_validate(tool_input)

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    log.info(
        "mass_planning.extraction OK model=%s file_kb=%d duration_ms=%d "
        "input_tokens=%d output_tokens=%d epics=%d",
        model,
        len(pdf_bytes) // 1024,
        duration_ms,
        input_tokens,
        output_tokens,
        len(proposal.epics),
    )

    return ExtractionResult(
        proposal=proposal,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        file_bytes=len(pdf_bytes),
        duration_ms=duration_ms,
        model_id=model,
        fallback_used=False,
    )


__all__ = [
    "ProjectProposal",
    "EpicProposal",
    "MassPlanProposal",
    "ExtractionResult",
    "extract_proposal_from_pdf",
]
