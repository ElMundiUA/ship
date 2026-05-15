"""Claude (Anthropic) judge with prompt-cache discipline.

Anthropic's prompt cache is opt-in per content block via
``cache_control: {"type": "ephemeral"}``. The cached portion lives
on Anthropic's side for ~5 minutes; subsequent calls within that
window pay the much cheaper ``cache_read`` rate for those tokens.

We park the rubric markdown + the (long, mostly static) judging
system prompt as ONE cached block. The variable per-call payload —
the artifact JSON — goes in a separate, uncached user message.

Token bookkeeping:
- ``usage.cache_creation_input_tokens`` → cache_write
- ``usage.cache_read_input_tokens``     → cache_read
- ``usage.input_tokens`` (post-SDK 0.40) — already excludes the
  two cache buckets, so it's the **fresh** input total
- ``usage.output_tokens``               → output

Total billable input == input_tokens + cache_creation + cache_read.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic

from tools.eval import prices
from tools.eval.judges.base import (
    JudgeError,
    JudgeRequest,
    JudgeResult,
    TokenBreakdown,
    parse_improvements,
)


SYSTEM_PREAMBLE = """You are an expert software engineering reviewer
scoring the output of an autonomous coding agent against a published
rubric. You return strict JSON only — no markdown wrapping, no
preface, no trailing prose. If the rubric specifies an output shape,
match it exactly. Be terse in rationales (one sentence per
criterion). Score conservatively: a missing field is 0 points, not
half-credit; an over-confident "8/10" with no rationale is the
classic judge bias and we will discount you for it.

In addition to the rubric's required output, ALWAYS include an
``improvements`` array of 1-4 entries. Each entry is one concrete,
actionable edit the operator could make to the agent's *role
prompt* (the markdown file under ``apps/backend/app/resources/
agent_roles/<role>.md``) to lift the score on a specific criterion.
Format:

  {
    "criterion": "<C-id from breakdown>",
    "issue":     "<one sentence on what went wrong in the artifact>",
    "suggested_prompt_edit": "<one or two sentences naming the
        exact section of the role prompt to change, what to add,
        and what should change in the agent's behaviour as a
        result. Concrete edit, not 'be clearer'.>",
    "expected_lift_pts": <int — estimated score gain if applied>
  }

Skip ``improvements`` only if the artifact is already at 95+ AND
all criteria are at full marks. Do not pad with low-value
suggestions just to fill 4 entries — fewer, sharper edits beat
longer lists."""


def _build_messages(req: JudgeRequest) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns ``(system_blocks, user_messages)``.

    The whole rubric + system preamble lives in the cached block.
    The artifact is sent as a fresh user turn each call.
    """
    rubric_block = {
        "type": "text",
        "text": (
            f"{SYSTEM_PREAMBLE}\n\n"
            f"=== RUBRIC for routine `{req.routine}` ===\n\n"
            f"{req.rubric}"
        ),
        # 5-minute ephemeral cache. Sufficient for one eval run that
        # scores all artifacts under the same rubric back-to-back.
        "cache_control": {"type": "ephemeral"},
    }
    system_blocks = [rubric_block]
    user_messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Score the artifact below against the rubric.\n\n"
                        "```json\n"
                        f"{json.dumps(req.artifact, ensure_ascii=False, indent=2)}\n"
                        "```\n\n"
                        "Return JSON only."
                    ),
                }
            ],
        }
    ]
    return system_blocks, user_messages


def _parse_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from Claude output.

    Sonnet occasionally emits malformed JSON: trailing prose past a
    valid object (``Extra data``), or unescaped quotes / missing
    commas mid-string when a long ``improvements`` array carries
    embedded code samples. We walk three rungs of tolerance:

    1. Try strict ``json.loads`` first — the happy path.
    2. Try ``raw_decode`` from the first ``{`` — recovers from
       trailing prose / multi-object output.
    3. Fall back to ``json_repair`` which fixes minor JSON breakage
       (unbalanced quotes, missing commas, smart-quote contamination).
       Last resort — preserves as much content as possible.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].lstrip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start >= 0:
        try:
            decoder = json.JSONDecoder()
            obj, _end = decoder.raw_decode(stripped[start:])
            return obj
        except json.JSONDecodeError:
            pass
    # Last resort — json_repair returns a best-effort parse of
    # malformed JSON. Cheap (no LLM call); preserves the score and
    # most of the improvements list even when one suggestion field
    # has an unescaped quote.
    try:
        from json_repair import repair_json  # type: ignore[import-untyped]
    except ImportError as exc:
        raise json.JSONDecodeError(
            "json-repair missing — pip install json-repair",
            stripped,
            0,
        ) from exc
    repaired = repair_json(stripped[start:] if start >= 0 else stripped)
    return json.loads(repaired)


def run(req: JudgeRequest, *, client: anthropic.Anthropic | None = None) -> JudgeResult:
    """Score one artifact on Claude. Raises :class:`JudgeError` on
    irrecoverable failure."""
    cli = client or anthropic.Anthropic()
    price = prices.REGISTRY.get(req.model)
    if price is None:
        raise JudgeError(f"no price entry for model {req.model!r}")

    system_blocks, user_messages = _build_messages(req)

    started = time.monotonic()
    try:
        response = cli.messages.create(
            model=req.model,
            max_tokens=2000,
            system=system_blocks,
            messages=user_messages,
        )
    except anthropic.APIError as exc:
        raise JudgeError(f"Anthropic API error: {exc}") from exc
    latency_ms = int((time.monotonic() - started) * 1000)

    text_parts = [
        block.text  # type: ignore[attr-defined]
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    raw_text = "".join(text_parts).strip()

    failures: list[str] = []
    try:
        raw = _parse_json(raw_text)
    except json.JSONDecodeError as exc:
        failures.append(f"json_parse_failed: {exc}")
        # Park the malformed text under a sentinel key so the runner
        # can show it in the report without crashing aggregation.
        raw = {"_unparsed": raw_text, "_error": str(exc)}

    score = float(raw.get("score", 0) or 0) if isinstance(raw, dict) else 0.0
    would_ship = bool(raw.get("would_ship", False)) if isinstance(raw, dict) else False

    usage = response.usage
    breakdown = TokenBreakdown(
        input_uncached=getattr(usage, "input_tokens", 0) or 0,
        cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
        output=getattr(usage, "output_tokens", 0) or 0,
    )
    cost = prices.cost_usd(
        price,
        input_tokens=breakdown.input_uncached,
        output_tokens=breakdown.output,
        cache_write_tokens=breakdown.cache_write,
        cache_read_tokens=breakdown.cache_read,
    )

    improvements = parse_improvements(
        raw.get("improvements") if isinstance(raw, dict) else None
    )

    return JudgeResult(
        routine=req.routine,
        model=req.model,
        score=score,
        would_ship=would_ship,
        raw=raw,
        tokens=breakdown,
        cost_usd=cost,
        latency_ms=latency_ms,
        improvements=improvements,
        failures=failures,
    )
