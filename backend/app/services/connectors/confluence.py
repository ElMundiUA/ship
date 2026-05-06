"""Confluence connector — fetch a single page or a section subtree.

resource_ref shapes
-------------------

- ``{"page_id": "<id>"}`` — fetch one page (legacy single-doc shape).
- ``{"root_page_id": "<id>", "space_id": "<id>"}`` — fetch the page and
  every descendant. This is the "section" shape the wizard picker
  produces; one section in the picker maps to one resource_ref here, and
  each Confluence page in the subtree becomes one ``ConnectorPage`` so
  the ingestion pipeline can fingerprint/skip per-page.

We use ``/wiki/api/v2/pages/{id}/descendants`` which already paginates
flat over the whole subtree; pulling content per descendant via
``GET /pages/{id}?body-format=storage`` keeps the round-trip count
predictable (1 + N).
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Mapping

import httpx

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import safe_decrypt

from . import ConnectorConfigError, ConnectorPage, ConnectorUnsupported, register


# Cap descendants per section so a runaway 50k-page space doesn't
# wedge a sync. Bumped 200 → 2000 with the canon pipeline going
# live — a real-world Confluence section often has thousands of
# leaf pages and the old cap forced operators to pick narrow
# subsections. The ingestion pipeline's ``MAX_SOURCE_DOCUMENTS``
# (5000) is still the hard ceiling per source.
_MAX_DESCENDANTS_PER_SECTION = 2000


class _HtmlToMarkdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "div", "br"}:
            self.parts.append("\n")
        elif tag in {"h1", "h2"}:
            self.parts.append("\n## ")
        elif tag == "h3":
            self.parts.append("\n### ")
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def markdown(self) -> str:
        lines = [line.strip() for line in "".join(self.parts).splitlines()]
        compact: list[str] = []
        previous_blank = False
        for line in lines:
            if not line:
                if not previous_blank:
                    compact.append("")
                previous_blank = True
                continue
            compact.append(line)
            previous_blank = False
        return "\n".join(compact).strip()


def _html_to_markdown(html: str) -> str:
    parser = _HtmlToMarkdown()
    parser.feed(html or "")
    return parser.markdown()


def _resolve_creds(integration: Integration) -> tuple[str, str, str]:
    token = safe_decrypt(integration.secret_ciphertext)
    if not token:
        raise ConnectorConfigError("Confluence integration has no API token")
    config = integration.config or {}
    site_url = str(config.get("site_url") or config.get("site") or "").strip().rstrip("/")
    email = str(config.get("email") or config.get("user") or "").strip()
    if not site_url or not email:
        raise ConnectorConfigError("Confluence integration is missing site_url/email")
    if not site_url.startswith("http"):
        site_url = f"https://{site_url}"
    return site_url, email, token


def _absolute_url(site_url: str, web_link: Any) -> str | None:
    if isinstance(web_link, str) and web_link.startswith("/"):
        return f"{site_url}/wiki{web_link}"
    if isinstance(web_link, str):
        return web_link
    return None


def _page_to_connector_page(
    payload: dict[str, Any], *, site_url: str, fallback_id: str
) -> ConnectorPage:
    page_id = str(payload.get("id") or fallback_id)
    title = str(payload.get("title") or f"Confluence page {page_id}")
    body = ((payload.get("body") or {}).get("storage") or {}).get("value") or ""
    markdown = _html_to_markdown(body)
    if not markdown:
        markdown = "<empty Confluence page>"
    web_link = (payload.get("_links") or {}).get("webui") if isinstance(payload.get("_links"), dict) else None
    return ConnectorPage(
        slug=f"confluence-{page_id}",
        title=title,
        body_md=f"# {title}\n\n{markdown}",
        page_ref={
            "page_id": page_id,
            "title": title,
            "url": _absolute_url(site_url, web_link),
            "space_id": str(payload.get("spaceId") or ""),
        },
    )


async def _get(
    http: httpx.AsyncClient,
    *,
    site_url: str,
    path: str,
    email: str,
    token: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await http.get(
        f"{site_url}{path}",
        auth=(email, token),
        params={k: v for k, v in (params or {}).items() if v is not None},
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    return response.json()


async def _fetch_page(
    http: httpx.AsyncClient,
    *,
    site_url: str,
    email: str,
    token: str,
    page_id: str,
) -> dict[str, Any]:
    return await _get(
        http,
        site_url=site_url,
        path=f"/wiki/api/v2/pages/{page_id}",
        email=email,
        token=token,
        params={"body-format": "storage"},
    )


async def _fetch_descendants(
    http: httpx.AsyncClient,
    *,
    site_url: str,
    email: str,
    token: str,
    root_page_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Walk ``/pages/{id}/descendants`` until we hit the cap or run out.

    Returns the raw v2 page payloads (id + minimal metadata, no body —
    the descendants endpoint doesn't expand bodies). Caller fetches each
    body via ``_fetch_page``.
    """
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(out) < limit:
        payload = await _get(
            http,
            site_url=site_url,
            path=f"/wiki/api/v2/pages/{root_page_id}/descendants",
            email=email,
            token=token,
            params={"limit": min(250, limit - len(out)), "cursor": cursor},
        )
        for entry in payload.get("results") or []:
            if isinstance(entry, dict) and entry.get("id"):
                out.append(entry)
                if len(out) >= limit:
                    break
        next_link = (payload.get("_links") or {}).get("next") if isinstance(payload.get("_links"), dict) else None
        if not isinstance(next_link, str) or "cursor=" not in next_link:
            break
        try:
            from urllib.parse import parse_qs, urlparse

            cursor_values = parse_qs(urlparse(next_link).query).get("cursor")
            if not cursor_values:
                break
            cursor = cursor_values[0]
        except Exception:  # noqa: BLE001
            break
    return out


@register("confluence")
async def fetch_confluence_pages(
    integration: Integration,
    resource_ref: Mapping[str, Any],
    http_client: httpx.AsyncClient | None = None,
) -> list[ConnectorPage]:
    """Fetch one page or a whole section subtree.

    Shape dispatch:

    - ``{root_page_id}``: section mode — fetch root + descendants.
    - ``{page_id}``: legacy single-page mode.
    """
    root_page_id = str(resource_ref.get("root_page_id") or "").strip()
    page_id = str(resource_ref.get("page_id") or "").strip()
    if not root_page_id and not page_id:
        raise ConnectorUnsupported(
            "resource_ref needs root_page_id (section) or page_id (single page)"
        )

    site_url, email, token = _resolve_creds(integration)

    owns_client = http_client is None
    http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    try:
        if root_page_id:
            return await _fetch_section(
                http,
                site_url=site_url,
                email=email,
                token=token,
                root_page_id=root_page_id,
            )
        return [
            _page_to_connector_page(
                await _fetch_page(
                    http,
                    site_url=site_url,
                    email=email,
                    token=token,
                    page_id=page_id,
                ),
                site_url=site_url,
                fallback_id=page_id,
            )
        ]
    finally:
        if owns_client:
            await http.aclose()


async def _fetch_section(
    http: httpx.AsyncClient,
    *,
    site_url: str,
    email: str,
    token: str,
    root_page_id: str,
) -> list[ConnectorPage]:
    root_payload = await _fetch_page(
        http, site_url=site_url, email=email, token=token, page_id=root_page_id
    )
    pages: list[ConnectorPage] = [
        _page_to_connector_page(root_payload, site_url=site_url, fallback_id=root_page_id)
    ]
    descendants = await _fetch_descendants(
        http,
        site_url=site_url,
        email=email,
        token=token,
        root_page_id=root_page_id,
        limit=_MAX_DESCENDANTS_PER_SECTION,
    )
    for descendant in descendants:
        descendant_id = str(descendant.get("id") or "")
        if not descendant_id:
            continue
        body_payload = await _fetch_page(
            http,
            site_url=site_url,
            email=email,
            token=token,
            page_id=descendant_id,
        )
        pages.append(
            _page_to_connector_page(body_payload, site_url=site_url, fallback_id=descendant_id)
        )
    return pages


__all__ = ["fetch_confluence_pages"]
