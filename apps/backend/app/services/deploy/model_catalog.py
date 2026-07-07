"""Model listing for the deploy-planner picker.

The "New deployment" modal (and the onboarding wizard) let the operator
pick which LLM plans the deployment. Remembering exact model ids
(``gemini-2.5-flash`` vs ``gemini-2.0-flash-001`` …) is error-prone and
they churn fast, so we populate a dropdown instead of a free-text box.

Resolution order for the list, best → worst:

1. **Provider live API** (when a key is available — pasted in the modal or
   configured in the server env). This is the gold standard: it returns
   exactly the models that key/plan can call, with native ids.
2. **models.dev** — a keyless, community-maintained catalogue of models
   across vendors, keyed by provider with *native* ids (``claude-sonnet-4-6``,
   ``gpt-4o``, gemini under the ``google`` key). This is what lets the
   dropdown show a fresh list **without any key** — important because the
   keys the operator pastes on onboarding land in GitHub Actions secrets,
   which are write-only (their plaintext can't be read back).
3. **Curated static fallback** — last resort if models.dev is unreachable.

``source`` on the result tells the caller which tier produced the list.
The provider's recommended default is always returned separately so the
picker can offer a "leave it to Ship" choice regardless.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Final

import httpx

from backend.app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# models.dev keyless catalogue. Top-level keys are providers; note Gemini
# lives under ``google``. Each provider node carries a ``models`` dict
# keyed by native model id.
_MODELS_DEV_URL: Final[str] = "https://models.dev/api.json"
_MODELS_DEV_PROVIDER_KEY: Final[dict[str, str]] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google",
    "mistral": "mistral",
}
# In-process TTL cache for the (large-ish) models.dev payload so we hit it
# at most a few times a day across all pickers.
_CATALOG_TTL_SECONDS: Final[float] = 6 * 60 * 60
_catalog_cache: dict[str, object] = {"at": 0.0, "data": None}

# Recommended default model per provider. Mirrors the hard-coded defaults
# in :mod:`backend.app.services.deploy.llm` so "Default" in the dropdown
# and "no override sent" resolve to the same thing.
# The model the "Default" planner choice resolves to, per vendor. Prefer
# rolling "-latest" aliases so it tracks new releases without editing this
# map. Gemini offers a true cross-version alias (``gemini-flash-latest``);
# Anthropic has no bare "newest sonnet" alias so we pin the current
# generation (bump here on a new release — one line, no repo re-seed);
# OpenAI uses the base ``gpt-4o``; Mistral's ``-latest`` rolls on its own.
PROVIDER_DEFAULT_MODEL: Final[dict[str, str]] = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-flash-latest",
    "mistral": "mistral-small-latest",
    # Cursor's catch-all — always valid, incl. Free plans. The picker lists
    # the live slugs (composer, claude-*, …) when the platform key is set.
    "cursor": "auto",
}

# Curated fallback shown when the provider has no backend key configured
# or its list-models call fails. Intentionally short — just enough to be
# useful offline; the live list supersedes it whenever a key is present.
_FALLBACK_MODELS: Final[dict[str, tuple[str, ...]]] = {
    "openai": ("gpt-4o", "gpt-4o-mini", "o3-mini"),
    "anthropic": (
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
    ),
    "gemini": ("gemini-pro-latest", "gemini-flash-latest", "gemini-2.5-pro"),
    "mistral": ("mistral-large-latest", "mistral-small-latest"),
    # Cursor without a platform key: the always-valid slugs. ``composer``
    # is the rolling alias for the latest Composer, so this stays useful
    # without tracking version bumps.
    "cursor": ("auto", "composer", "composer-latest"),
}

# OpenAI returns its whole catalogue (embeddings, audio, image…). We only
# want chat-completion-capable text models in the planner picker, so drop
# ids whose name screams "not a chat model".
_NON_CHAT_MARKERS: Final[tuple[str, ...]] = (
    "embed",
    "whisper",
    "tts",
    "audio",
    "realtime",
    "dall-e",
    "image",
    "vision-preview",
    "moderation",
    "babbage",
    "davinci",
    "ada",
    "curie",
    "search",
    "transcribe",
    # Google media / specialty models — surface as "generateContent" but
    # produce images/audio/video/robotics actions, useless for a planner.
    "imagen",
    "veo",
    "lyria",
    "nano-banana",
    "robotics",
    "computer-use",
    "gemma",
)


@dataclass(frozen=True, slots=True)
class ModelListing:
    """Result of a planner model lookup.

    ``source`` is ``"live"`` when the ids came from the provider API,
    ``"fallback"`` when we used the curated list (no key / upstream
    error). ``error`` carries a short reason on fallback so the UI can
    surface "couldn't reach OpenAI, showing common models" rather than
    silently pretending the list is authoritative.
    """

    provider: str
    models: list[str]
    default_model: str
    source: str
    error: str | None = field(default=None)


def _resolve_key(provider: str, settings: Settings) -> str | None:
    """Return the backend-configured key for ``provider`` (or ``None``)."""

    if provider == "openai":
        return (settings.openai_api_key or "").strip() or None
    if provider == "anthropic":
        return (settings.anthropic_api_key or "").strip() or None
    if provider == "gemini":
        return (settings.deploy_planner_gemini_api_key or "").strip() or None
    if provider == "mistral":
        return (settings.mistral_api_key or "").strip() or None
    if provider == "cursor":
        return (settings.cursor_api_key or "").strip() or None
    return None


def _keep_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


async def list_planner_models(
    provider: str,
    *,
    api_key: str | None = None,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> ModelListing:
    """List selectable models for a planner ``provider``.

    Resolution: provider live API (when a key is available — pasted or
    server env) → keyless models.dev catalogue → curated static fallback.
    Never raises for upstream/key problems; the picker always gets a list,
    and ``source`` (``live`` / ``catalog`` / ``fallback``) says which tier
    produced it.
    """

    settings = settings or get_settings()
    provider = provider.strip().lower()
    default = PROVIDER_DEFAULT_MODEL.get(provider, "")
    fallback = list(_FALLBACK_MODELS.get(provider, ()))

    if provider not in PROVIDER_DEFAULT_MODEL:
        return ModelListing(
            provider=provider,
            models=[],
            default_model="",
            source="fallback",
            error="unsupported provider",
        )

    key = (api_key or "").strip() or _resolve_key(provider, settings)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    try:
        # Tier 1 — provider live API (most accurate to the key's access).
        if key:
            try:
                live = await _fetch_live(provider, key, http)
                if live:
                    return ModelListing(
                        provider=provider,
                        models=live,
                        default_model=default,
                        source="live",
                    )
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.warning(
                    "deploy planner live model list failed for %s: %s",
                    provider,
                    exc,
                )

        # Tier 2 — keyless models.dev catalogue (native ids).
        try:
            catalog = await _fetch_catalog_models(provider, http)
            if catalog:
                return ModelListing(
                    provider=provider,
                    models=catalog,
                    default_model=default,
                    source="catalog",
                )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("models.dev catalogue fetch failed: %s", exc)
    finally:
        if owns_client:
            await http.aclose()

    # Tier 3 — curated static fallback.
    return ModelListing(
        provider=provider,
        models=fallback,
        default_model=default,
        source="fallback",
        error=None if fallback else "no_models",
    )


async def _fetch_catalog_models(
    provider: str, http: httpx.AsyncClient
) -> list[str]:
    """Return chat-capable native model ids for ``provider`` from models.dev."""

    catalog_key = _MODELS_DEV_PROVIDER_KEY.get(provider)
    if not catalog_key:
        return []
    data = await _load_models_dev(http)
    node = data.get(catalog_key) if isinstance(data, dict) else None
    models = node.get("models") if isinstance(node, dict) else None
    if isinstance(models, dict):
        ids = list(models.keys())
    elif isinstance(models, list):
        ids = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
    else:
        return []
    return sorted(i for i in set(ids) if i and _keep_chat_model(i))


async def _load_models_dev(http: httpx.AsyncClient) -> dict:
    """Fetch + TTL-cache the models.dev catalogue (process-wide)."""

    now = time.monotonic()
    cached = _catalog_cache.get("data")
    if cached is not None and (now - float(_catalog_cache["at"])) < _CATALOG_TTL_SECONDS:
        return cached  # type: ignore[return-value]
    resp = await http.get(_MODELS_DEV_URL)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        _catalog_cache["data"] = data
        _catalog_cache["at"] = now
        return data
    return {}


async def _fetch_live(
    provider: str, key: str, http: httpx.AsyncClient
) -> list[str]:
    if provider == "openai":
        return await _fetch_openai_compatible(
            "https://api.openai.com/v1/models", key, http, chat_only=True
        )
    if provider == "mistral":
        return await _fetch_openai_compatible(
            "https://api.mistral.ai/v1/models", key, http, chat_only=False
        )
    if provider == "anthropic":
        return await _fetch_anthropic(key, http)
    if provider == "gemini":
        return await _fetch_gemini(key, http)
    if provider == "cursor":
        return await _fetch_cursor(key, http)
    return []


async def _fetch_openai_compatible(
    url: str, key: str, http: httpx.AsyncClient, *, chat_only: bool
) -> list[str]:
    """List models from an OpenAI-style ``GET /models`` ({"data":[{"id"}]})."""

    resp = await http.get(url, headers={"Authorization": f"Bearer {key}"})
    resp.raise_for_status()
    data = resp.json().get("data", [])
    ids = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
    if chat_only:
        ids = [m for m in ids if _keep_chat_model(m)]
    return sorted(set(ids))


async def _fetch_anthropic(key: str, http: httpx.AsyncClient) -> list[str]:
    resp = await http.get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        params={"limit": 100},
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    ids = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
    # Newest first reads better for Claude (ids sort lexicographically by
    # date-ish suffix); reverse-sort keeps recent models at the top.
    return sorted(set(ids), reverse=True)


async def _fetch_gemini(key: str, http: httpx.AsyncClient) -> list[str]:
    resp = await http.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": key, "pageSize": 200},
    )
    resp.raise_for_status()
    models = resp.json().get("models", [])
    ids: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        methods = m.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        name = m.get("name", "")
        # Names come back as ``models/gemini-2.5-flash`` — strip the prefix.
        model_id = name.split("/", 1)[-1] if "/" in name else name
        if model_id and _keep_chat_model(model_id):
            ids.append(model_id)
    return sorted(set(ids))


async def _fetch_cursor(key: str, http: httpx.AsyncClient) -> list[str]:
    """List Cursor models from ``GET api.cursor.com/v1/models``.

    Response shape: ``{"items": [{"id", "displayName", "aliases": [...]}]}``.
    The ``id`` is the slug ``cursor-agent --model`` accepts; the catch-all
    entry comes back as ``id="default"`` but is invoked as ``auto`` (its
    documented alias), so we surface it that way. Cursor returns a curated
    ordering (Auto / Composer first, then models) — we preserve it rather
    than sorting, and dedupe defensively.
    """

    resp = await http.get(
        "https://api.cursor.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("id") or "").strip()
        if not raw:
            continue
        model_id = "auto" if raw == "default" else raw
        if model_id not in seen:
            seen.add(model_id)
            ids.append(model_id)
    return ids


__all__ = ["ModelListing", "PROVIDER_DEFAULT_MODEL", "list_planner_models"]
