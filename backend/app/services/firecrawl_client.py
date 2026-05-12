"""Thin async HTTP wrapper around Firecrawl's ``/v1/scrape`` endpoint.

We use Firecrawl for **URL fetching only** — its JS rendering and
PDF extraction are best-in-class. Web *search* runs through the
LLM provider's native server-side search tool (Anthropic's
``web_search_20250305`` today; OpenAI's ``web_search_preview`` if
we ever broaden), so this module deliberately doesn't wrap
``/v1/search`` even though Firecrawl exposes one — the search-grade
quality bar isn't worth the extra integration.

Errors are surfaced as :class:`FirecrawlError` with a stable ``code``
field the tool layer can map to a structured JSON error for the
LLM (so the model can decide to retry or tell the user the key is
missing — without seeing a stack trace).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx


logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://api.firecrawl.dev"
_DEFAULT_TIMEOUT_S = 25.0   # Search ≤ 4s p95, scrape ≤ 18s p95 per docs;
                            # leave headroom for slow targets.


@dataclass(frozen=True)
class FirecrawlError(Exception):
    """Structured Firecrawl error. ``code`` is stable wire — used by
    the agent tool layer to map to a JSON response shape — while
    ``message`` carries the upstream's human text for the LLM."""

    code: str  # "unauthorized" | "rate_limited" | "bad_request" | "upstream_error" | "transport"
    message: str
    status: int | None = None

    def __str__(self) -> str:  # pragma: no cover — trivial
        return f"firecrawl {self.code}: {self.message}"


def _map_status(status: int, body: str) -> FirecrawlError:
    if status == 401:
        return FirecrawlError("unauthorized", body or "Firecrawl rejected the API key", status)
    if status == 429:
        return FirecrawlError("rate_limited", body or "Firecrawl rate limit hit", status)
    if 400 <= status < 500:
        return FirecrawlError("bad_request", body or f"HTTP {status}", status)
    return FirecrawlError("upstream_error", body or f"HTTP {status}", status)


class FirecrawlClient:
    """Per-toolbox client. Reuse via the ToolBox lifecycle."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._transport = transport

    async def __aenter__(self) -> "FirecrawlClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_s,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._client.aclose()

    async def scrape(
        self,
        *,
        url: str,
        formats: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one URL. ``formats=["markdown"]`` is the default for
        token efficiency; pass ``["html"]`` / ``["raw"]`` if the LLM
        explicitly needs them."""
        return await self._post(
            "/v1/scrape",
            payload={
                "url": url,
                "formats": formats or ["markdown"],
            },
        )

    async def _post(self, path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.RequestError as exc:
            raise FirecrawlError(
                "transport",
                f"Firecrawl request failed: {exc}",
            ) from exc
        if resp.status_code >= 400:
            # Firecrawl's structured error body has an ``error`` field;
            # fall back to the raw text if shape isn't what we expect.
            try:
                body = resp.json()
                message = body.get("error") or body.get("message") or resp.text
            except Exception:  # noqa: BLE001
                message = resp.text
            raise _map_status(resp.status_code, str(message)[:500])
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise FirecrawlError(
                "upstream_error",
                f"Firecrawl returned non-JSON: {exc}",
                resp.status_code,
            ) from exc


__all__ = ["FirecrawlClient", "FirecrawlError"]
