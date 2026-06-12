"""LLM access for the deploy planner — one ``complete_json`` entry point.

Production path reuses Ship's configured agent vendor via
``pick_default_client`` (OpenAI or Anthropic). All vendors are driven the
same way: we embed the target JSON schema in the prompt, ask for JSON, and
salvage-parse the first JSON object from the reply (Anthropic ignores
``response_format``, so we never rely on it being honoured).

⚠️  LOCAL-DEV Gemini fallback
----------------------------
When neither an OpenAI nor an Anthropic key is configured (a bare laptop),
and ``DEPLOY_PLANNER_ALLOW_DEV_FALLBACK=true``, we fall back to Google
Gemini over its REST API (no SDK dependency). This exists ONLY so the
planner can be exercised end-to-end locally. It is gated, logs a loud
warning, and MUST NOT be enabled in any deployed environment. The key
lives only in the gitignored ``.env``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Final

import httpx

from backend.app.core.config import Settings, get_settings
from backend.app.services.agent.client import ChatMessage, pick_default_client


logger = logging.getLogger(__name__)

_GEMINI_ENDPOINT: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class PlannerLLMError(RuntimeError):
    """No usable LLM is configured, or the model returned unparseable output."""


@dataclass(frozen=True, slots=True)
class PlannerLLMCredentials:
    provider: str
    api_key: str
    model: str | None = None


def _salvage_json(raw: str) -> dict[str, Any]:
    """Parse the first JSON object out of a model reply.

    Tolerant of prose-wrapped or fenced output (```json ... ```), which is
    what Anthropic/Gemini commonly return even when asked for pure JSON.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # Fall back to the widest brace-balanced span.
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise PlannerLLMError(f"model returned unparseable JSON: {exc}") from exc
    raise PlannerLLMError("model reply contained no JSON object")


def _has_ship_vendor_key(settings: Settings) -> bool:
    return bool(settings.anthropic_api_key or settings.openai_api_key)


def _gemini_dev_fallback_enabled(settings: Settings) -> bool:
    return bool(
        settings.deploy_planner_allow_dev_fallback
        and settings.deploy_planner_gemini_api_key
    )


async def complete_json(
    *,
    system: str,
    user: str,
    max_tokens: int = 2048,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    credentials: PlannerLLMCredentials | None = None,
) -> dict[str, Any]:
    """Return a parsed JSON object from the planner LLM.

    Raises :class:`PlannerLLMError` when no LLM is configured or the reply
    cannot be parsed.
    """
    settings = settings or get_settings()

    if credentials is not None:
        return await _byo_complete_json(
            system=system,
            user=user,
            max_tokens=max_tokens,
            credentials=credentials,
            client=client,
        )

    if _has_ship_vendor_key(settings):
        agent = pick_default_client(settings)
        raw = await agent.acomplete(
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
            max_tokens=max_tokens,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        return _salvage_json(raw)

    if _gemini_dev_fallback_enabled(settings):
        logger.warning(
            "deploy planner using LOCAL-DEV Gemini fallback (%s) — this MUST "
            "NOT run in a deployed environment; configure OPENAI_API_KEY or "
            "ANTHROPIC_API_KEY for production.",
            settings.deploy_planner_gemini_model,
        )
        return await _gemini_complete_json(
            system=system,
            user=user,
            max_tokens=max_tokens,
            settings=settings,
            client=client,
        )

    raise PlannerLLMError(
        "No LLM configured for the deploy planner. Set OPENAI_API_KEY or "
        "ANTHROPIC_API_KEY (production), or for local dev set "
        "DEPLOY_PLANNER_GEMINI_API_KEY and DEPLOY_PLANNER_ALLOW_DEV_FALLBACK=true."
    )


async def _byo_complete_json(
    *,
    system: str,
    user: str,
    max_tokens: int,
    credentials: PlannerLLMCredentials,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    provider = credentials.provider.strip().lower()
    api_key = credentials.api_key.strip()
    model = credentials.model.strip() if credentials.model else None
    if not api_key:
        raise PlannerLLMError("LLM API key is required for deploy planning.")
    if provider in {"openai", "chatgpt"}:
        return await _openai_complete_json(
            system=system,
            user=user,
            max_tokens=max_tokens,
            api_key=api_key,
            model=model,
            client=client,
        )
    if provider == "anthropic":
        return await _anthropic_complete_json(
            system=system,
            user=user,
            max_tokens=max_tokens,
            api_key=api_key,
            model=model,
            client=client,
        )
    if provider == "gemini":
        settings = get_settings()
        patched = settings.model_copy(
            update={
                "deploy_planner_gemini_api_key": api_key,
                "deploy_planner_gemini_model": model
                or settings.deploy_planner_gemini_model,
            }
        )
        return await _gemini_complete_json(
            system=system,
            user=user,
            max_tokens=max_tokens,
            settings=patched,
            client=client,
        )
    if provider == "mistral":
        return await _mistral_complete_json(
            system=system,
            user=user,
            max_tokens=max_tokens,
            api_key=api_key,
            model=model,
            client=client,
        )
    raise PlannerLLMError("Unsupported deploy planner LLM provider.")


async def _openai_complete_json(
    *,
    system: str,
    user: str,
    max_tokens: int,
    api_key: str,
    model: str | None,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        resp = await http.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            json={
                "model": model or "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
        )
    finally:
        if owns_client:
            await http.aclose()
    if resp.status_code >= 400:
        raise PlannerLLMError(f"OpenAI returned HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PlannerLLMError(f"OpenAI reply had unexpected shape: {exc}") from exc
    return _salvage_json(text)


async def _anthropic_complete_json(
    *,
    system: str,
    user: str,
    max_tokens: int,
    api_key: str,
    model: str | None,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        resp = await http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Accept": "application/json",
            },
            json={
                "model": model or "claude-3-5-haiku-latest",
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "temperature": 0.0,
                "max_tokens": max_tokens,
            },
        )
    finally:
        if owns_client:
            await http.aclose()
    if resp.status_code >= 400:
        raise PlannerLLMError(
            f"Anthropic returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    try:
        parts = resp.json()["content"]
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    except (KeyError, TypeError) as exc:
        raise PlannerLLMError(f"Anthropic reply had unexpected shape: {exc}") from exc
    return _salvage_json(text)


async def _mistral_complete_json(
    *,
    system: str,
    user: str,
    max_tokens: int,
    api_key: str,
    model: str | None,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        resp = await http.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            json={
                "model": model or "mistral-small-latest",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
        )
    finally:
        if owns_client:
            await http.aclose()
    if resp.status_code >= 400:
        raise PlannerLLMError(f"Mistral returned HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PlannerLLMError(f"Mistral reply had unexpected shape: {exc}") from exc
    return _salvage_json(text)


async def _gemini_complete_json(
    *,
    system: str,
    user: str,
    max_tokens: int,
    settings: Settings,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    url = _GEMINI_ENDPOINT.format(model=settings.deploy_planner_gemini_model)
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0,
            "maxOutputTokens": max_tokens,
        },
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        resp = await http.post(
            url,
            params={"key": settings.deploy_planner_gemini_api_key},
            json=body,
            headers={"Accept": "application/json"},
        )
    finally:
        if owns_client:
            await http.aclose()
    if resp.status_code >= 400:
        raise PlannerLLMError(
            f"Gemini returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    payload = resp.json()
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PlannerLLMError(f"Gemini reply had unexpected shape: {exc}") from exc
    return _salvage_json(text)


__all__ = ["PlannerLLMCredentials", "PlannerLLMError", "complete_json"]
