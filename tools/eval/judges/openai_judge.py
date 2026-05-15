"""OpenAI (GPT-5 mini) judge.

OpenAI's prompt cache is **automatic** for prompt prefixes that
exceed ~1024 tokens. We don't have to opt in; we just need to keep
the prefix identical across calls (same rubric + system message
verbatim) so the hash matches.

Token bookkeeping in OpenAI's response:
- ``usage.prompt_tokens``                — total input (cached + fresh)
- ``usage.prompt_tokens_details.cached_tokens`` — served from cache
  (charged at ``cache_read`` rate)
- ``usage.completion_tokens``            — output

OpenAI never bills a separate "cache write" rate — the first call
just charges full input rate. So we map:
- ``input_uncached``  = prompt_tokens - cached_tokens
- ``cache_write``     = 0 (already paid as input on first call)
- ``cache_read``      = cached_tokens
- ``output``          = completion_tokens
"""

from __future__ import annotations

import json
import time
from typing import Any

import openai

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
rubric. Return JSON only — no markdown wrapping, no preface, no
trailing prose. If the rubric specifies an output shape, match it
exactly. Be terse in rationales (one sentence per criterion). Score
conservatively: a missing field is 0 points, not half-credit; an
over-confident "8/10" with no rationale is the classic judge bias
and we will discount you for it.

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


def _build_messages(req: JudgeRequest) -> list[dict[str, Any]]:
    """System block first (cached prefix), then artifact in a user
    turn. Same content shape as the Anthropic side so judge
    comparisons are apples-to-apples."""
    system_text = (
        f"{SYSTEM_PREAMBLE}\n\n"
        f"=== RUBRIC for routine `{req.routine}` ===\n\n"
        f"{req.rubric}"
    )
    user_text = (
        "Score the artifact below against the rubric.\n\n"
        "```json\n"
        f"{json.dumps(req.artifact, ensure_ascii=False, indent=2)}\n"
        "```\n\n"
        "Return JSON only."
    )
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]


def _parse_json(text: str) -> dict[str, Any]:
    """Same multi-rung tolerance as the Claude judge — ``response_format
    json_object`` usually guarantees valid JSON but token truncation
    in the middle of a long ``improvements`` array still happens."""
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


def run(req: JudgeRequest, *, client: openai.OpenAI | None = None) -> JudgeResult:
    cli = client or openai.OpenAI()
    price = prices.REGISTRY.get(req.model)
    if price is None:
        raise JudgeError(f"no price entry for model {req.model!r}")

    messages = _build_messages(req)

    started = time.monotonic()
    try:
        # ``response_format=json_object`` constrains the output to
        # valid JSON — saves us from defensive ``_parse_json`` for
        # GPT-5 mini's response surface. The system message still
        # has to mention "JSON only" or the model errors out.
        # GPT-5-mini only accepts the default ``temperature=1`` — passing
        # anything else 400s with ``unsupported_value``. Reasoning models
        # in the 5-family run on a fixed sampler; if a future judge model
        # restores tunable temperature, set it here.
        #
        # ``max_completion_tokens`` includes reasoning tokens that the
        # model burns before emitting the visible answer. On dense
        # rubrics like decomposition's 8 criteria, 2000 truncates the
        # response to an empty string (we see ``json_parse_failed`` on
        # an empty content). 4000 leaves comfortable headroom; cost
        # impact is bounded because the model rarely fills the budget.
        response = cli.chat.completions.create(
            model=req.model,
            messages=messages,  # type: ignore[arg-type]
            response_format={"type": "json_object"},
            max_completion_tokens=4000,
        )
    except openai.OpenAIError as exc:
        raise JudgeError(f"OpenAI API error: {exc}") from exc
    latency_ms = int((time.monotonic() - started) * 1000)

    choice = response.choices[0]
    raw_text = (choice.message.content or "").strip()

    failures: list[str] = []
    try:
        raw = _parse_json(raw_text)
    except json.JSONDecodeError as exc:
        failures.append(f"json_parse_failed: {exc}")
        raw = {"_unparsed": raw_text, "_error": str(exc)}

    score = float(raw.get("score", 0) or 0) if isinstance(raw, dict) else 0.0
    would_ship = bool(raw.get("would_ship", False)) if isinstance(raw, dict) else False

    usage = response.usage
    cached = 0
    if usage and getattr(usage, "prompt_tokens_details", None) is not None:
        # SDK shape: ``CompletionUsage.prompt_tokens_details.cached_tokens``
        cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
    prompt = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion = getattr(usage, "completion_tokens", 0) if usage else 0

    breakdown = TokenBreakdown(
        input_uncached=max(0, prompt - cached),
        cache_write=0,
        cache_read=cached,
        output=completion,
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
