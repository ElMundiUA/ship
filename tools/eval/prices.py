"""Per-token pricing for the judges we run.

Prices are USD per million tokens. Update when the providers'
published prices change — Anthropic & OpenAI publish a stable JSON
endpoint each but we keep a local copy so eval runs are reproducible
without a network round-trip.

Sources (re-check before adjusting):
- Anthropic pricing page: https://www.anthropic.com/pricing
- OpenAI API pricing page: https://platform.openai.com/docs/pricing

Numbers below are the snapshot as of 2026-05-15 — flag in PR review
if the providers have moved on.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per **million** tokens.

    ``cache_write`` is what Anthropic charges for the *first* call
    that populates the prompt-cache entry — typically 1.25× the base
    input rate. ``cache_read`` is what every subsequent hit costs
    (typically 0.1× of base input on Anthropic; 0.5× on OpenAI).

    OpenAI doesn't bill cache-write separately — the first call
    charges full input rate AND populates the cache implicitly. So
    ``cache_write`` is the same number as ``input_per_m`` for OpenAI
    rows; the saving only kicks in on the second hit via the
    ``cache_read`` column.
    """

    name: str
    input_per_m: float
    output_per_m: float
    cache_write_per_m: float
    cache_read_per_m: float


# Anthropic — Claude Sonnet 4.6 (2025-11; pricing matches Sonnet 4.5)
SONNET_4_6 = ModelPrice(
    name="claude-sonnet-4-6",
    input_per_m=3.00,
    output_per_m=15.00,
    cache_write_per_m=3.75,
    cache_read_per_m=0.30,
)

# OpenAI — GPT-5 mini (2026 release; substantially cheaper than 5)
GPT_5_MINI = ModelPrice(
    name="gpt-5-mini",
    input_per_m=0.25,
    output_per_m=2.00,
    cache_write_per_m=0.25,
    cache_read_per_m=0.025,
)


REGISTRY: dict[str, ModelPrice] = {
    SONNET_4_6.name: SONNET_4_6,
    GPT_5_MINI.name: GPT_5_MINI,
}


def cost_usd(
    price: ModelPrice,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Total USD for one judge call.

    ``input_tokens`` are *non-cached* input (the freshly-billed
    portion only; cached chunks are billed separately). The caller
    is expected to subtract cached counts before passing through —
    both SDKs report split totals.
    """
    return (
        input_tokens * price.input_per_m
        + cache_write_tokens * price.cache_write_per_m
        + cache_read_tokens * price.cache_read_per_m
        + output_tokens * price.output_per_m
    ) / 1_000_000
