"""Thin wrapper around OpenAI embeddings (C12 KB + TopicService).

We deliberately call OpenAI directly rather than routing through the
AgentClient: embeddings are a different product (``/embeddings``, not
``/chat/completions``), and the Anthropic side doesn't ship a
first-party embeddings API. Keeping this one-vendor means we don't have
to invent a second abstraction for a service that has one real
implementation anyway.

Anything that needs to embed text in the backend imports
:func:`embed_texts` from here; there's no cache in the first cut
because the call sites (KB indexer, bucket summary writer, TopicService)
each de-dupe before they reach this layer.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from backend.app.core.config import Settings, get_settings


# ``text-embedding-3-small`` emits 1536-d vectors; ``-large`` emits
# 3072. We hard-pin the dimension at 1536 in the migration so the
# DB schema never drifts with the model choice — re-embedding the
# corpus is the cost of switching, not a schema migration.
EMBED_DIM: int = 1536


async def embed_texts(
    texts: Sequence[str], *, settings: Settings | None = None
) -> list[list[float]]:
    """Embed ``texts`` and return a list of 1536-d vectors.

    Raises :class:`RuntimeError` when no API key is configured — call
    sites (KB indexer) surface that as a 409/412 so the operator sees
    "configure OPENAI_API_KEY" rather than a silent no-op.
    """
    if not texts:
        return []
    s = settings or get_settings()
    api_key = s.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Embeddings (KB + buckets) "
            "require it regardless of AGENT_VENDOR."
        )
    # Lazy import so the SDK stays out of the worker hot path.
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    resp = await client.embeddings.create(
        model=s.openai_embed_model,
        input=list(texts),
    )
    return [list(row.embedding) for row in resp.data]


async def embed_text(
    text: str, *, settings: Settings | None = None
) -> list[float]:
    """One-shot convenience wrapper around :func:`embed_texts`."""
    vectors = await embed_texts([text], settings=settings)
    return vectors[0] if vectors else []


__all__ = ["EMBED_DIM", "embed_text", "embed_texts"]
