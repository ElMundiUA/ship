"""Confluence connector — fetch one page into markdown."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Mapping

import httpx

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import safe_decrypt

from . import ConnectorConfigError, ConnectorPage, ConnectorUnsupported, register


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


@register("confluence")
async def fetch_confluence_pages(
    integration: Integration,
    resource_ref: Mapping[str, Any],
    http_client: httpx.AsyncClient | None = None,
) -> list[ConnectorPage]:
    page_id = str(resource_ref.get("page_id") or "").strip()
    if not page_id:
        raise ConnectorUnsupported("resource_ref.page_id is required")

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

    owns_client = http_client is None
    http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await http.get(
            f"{site_url}/wiki/api/v2/pages/{page_id}",
            auth=(email, token),
            params={"body-format": "storage"},
            headers={"Accept": "application/json"},
        )
    finally:
        if owns_client:
            await http.aclose()
    response.raise_for_status()
    payload = response.json()
    title = str(payload.get("title") or f"Confluence page {page_id}")
    body = ((payload.get("body") or {}).get("storage") or {}).get("value") or ""
    markdown = _html_to_markdown(body)
    if not markdown:
        markdown = f"# {title}\n\n<empty Confluence page>"

    return [
        ConnectorPage(
            slug=f"confluence-{page_id}",
            title=title,
            body_md=f"# {title}\n\n{markdown}",
            page_ref={
                "page_id": page_id,
                "title": title,
                "url": payload.get("_links", {}).get("webui"),
            },
        )
    ]


__all__ = ["fetch_confluence_pages"]
