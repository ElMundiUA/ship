"""Shared JSON-salvage helper for LLM draft endpoints.

OpenAI's JSON mode is reliable; Anthropic is looser. This utility
implements the same "trust the model, salvage the syntax" contract
the distiller and pattern-draft endpoints already use: try a plain
``json.loads`` first, then fall back to extracting the outermost
``{...}`` block in case the model wrapped its output in prose or
markdown fences.

Kept as a module-level function (rather than a class) so callers
just import and use — no state, no configuration.
"""

from __future__ import annotations

import json
from typing import Any


def salvage_json(raw: str) -> dict[str, Any]:
    """Best-effort JSON extraction that tolerates stray prose/fences.

    Returns ``{}`` on failure so callers can distinguish "model
    replied with no usable JSON" (empty dict) from "model did reply"
    (non-empty dict). Never raises.
    """
    text = str(raw or "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


__all__ = ["salvage_json"]
