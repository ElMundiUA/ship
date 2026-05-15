"""Shared types for the judge backends.

The Anthropic SDK and the OpenAI SDK have different token-usage
shapes; the wrapper in each ``judges/<provider>.py`` is responsible
for mapping into :class:`TokenBreakdown` so the runner can quote a
single uniform cost line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def parse_improvements(raw: Any) -> list["Improvement"]:
    """Best-effort extraction of the judge's ``improvements`` list.

    Tolerant on field renaming (``suggestion`` vs ``suggested_prompt_edit``)
    so a judge that rephrases the schema doesn't break aggregation.
    Drops malformed entries with a warning rather than failing the
    whole result.
    """
    if not isinstance(raw, list):
        return []
    out: list[Improvement] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                Improvement(
                    criterion=str(entry.get("criterion") or ""),
                    issue=str(entry.get("issue") or ""),
                    suggested_prompt_edit=str(
                        entry.get("suggested_prompt_edit")
                        or entry.get("suggestion")
                        or entry.get("edit")
                        or ""
                    ),
                    expected_lift_pts=int(entry.get("expected_lift_pts", 0) or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    """Payload to score one (routine, artifact) pair on one model.

    ``rubric`` is the markdown rubric body that the judge sees as
    the system / instruction prefix — kept in the cached portion
    of the prompt so every artifact under the same rubric reuses
    the cache. ``artifact`` is the variable per-call payload.
    """

    routine: str
    rubric: str
    artifact: dict[str, Any]
    model: str


@dataclass(slots=True)
class TokenBreakdown:
    """Mapped from each provider's usage shape.

    - ``input_uncached`` — fresh input the model billed at full
      rate
    - ``cache_write`` — tokens that populated the prompt cache on
      *this* call (Anthropic only; OpenAI bills these at input rate
      and reports them as ``cached_tokens`` only on the *next* hit)
    - ``cache_read`` — tokens served from a previously-warmed cache
      (Anthropic ``cache_read_input_tokens`` /
      OpenAI ``prompt_tokens_details.cached_tokens``)
    - ``output`` — tokens the model generated
    """

    input_uncached: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0


@dataclass(slots=True)
class Improvement:
    """One concrete prompt edit the judge suggests."""

    criterion: str
    issue: str
    suggested_prompt_edit: str
    expected_lift_pts: int


@dataclass(slots=True)
class JudgeResult:
    """One judge's verdict for one artifact.

    ``raw`` is the model's parsed JSON output verbatim — the runner
    persists it for review even if downstream tooling only looks at
    ``score`` and ``would_ship``.

    ``improvements`` is the judge's suggested prompt edits — surfaced
    so the operator can act on them without re-reading the full raw
    output. Empty when the judge returned no suggestions (e.g. score
    at 95+).

    ``failures`` carries human-readable notes for non-fatal issues
    (judge returned malformed JSON, raised a known retryable error)
    so the runner can decide whether to retry / annotate the row.
    """

    routine: str
    model: str
    score: float
    would_ship: bool
    raw: dict[str, Any]
    tokens: TokenBreakdown
    cost_usd: float
    latency_ms: int
    improvements: list[Improvement] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


class JudgeError(RuntimeError):
    """Raised when a judge invocation fails irrecoverably (bad JSON
    after retry, auth failure, etc.)."""
