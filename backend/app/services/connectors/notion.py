"""Notion connector — fetch a page into markdown for the Distiller.

This is the first real connector wired into the Phase 7c dispatcher.
Scope intentionally narrow:

- ``resource_ref = {"page_id": "<notion-page-uuid>"}`` → fetch the
  page's title + top-level blocks and render them as compact
  markdown. That's it.
- Any other shape (database_id listing, block-tree recursion, etc.)
  raises :class:`ConnectorUnsupported` so the dispatcher falls back
  to the Phase 7b stub body with a warning. We can extend shapes
  incrementally without touching the endpoint.

Why start with single-page: it closes the "connect → sync → article"
loop for 95% of Notion-in-Ship use cases (ops runbook, team charter,
onboarding handbook — each a single long page) without the
pagination + block-tree recursion that a DB-mirror mode would need.

Block coverage matches the Notion block types Ship operators
typically use inside long-form docs:

- heading_1 / heading_2 / heading_3
- paragraph
- bulleted_list_item / numbered_list_item
- to_do (``[x]`` / ``[ ]``)
- quote
- callout
- code (fenced with the declared language)
- divider (``---``)
- child_page (rendered as a link so it's preserved in the markdown)

Unknown types degrade to a short ``<unsupported: <type>>`` marker
rather than getting dropped silently — makes diagnosing "why is
this page missing a section?" a one-line grep.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

import httpx

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import safe_decrypt

from . import ConnectorConfigError, ConnectorPage, ConnectorUnsupported, register


logger = logging.getLogger(__name__)


NOTION_API_ROOT = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"

# Cap the number of blocks we render per page. Each block is one
# API call's worth of rich_text work; Notion's default page_size is
# 100 so ~200 blocks is already two round-trips. Very long pages
# truncate with a trailing note rather than silently cutting off.
_MAX_BLOCKS = 200

# Cap pages pulled from one database/data_source resource_ref. Each
# database entry costs a page fetch + a blocks fetch; without a cap
# a 5k-row database wedges sync runs and starves other resource_refs
# (the ingestion pipeline already caps total documents per source at
# MAX_SOURCE_DOCUMENTS = 100, so this is the per-database ceiling).
_MAX_PAGES_PER_DATABASE = 50


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Accept": "application/json",
    }


async def _get(
    client: httpx.AsyncClient,
    path: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.get(
        f"{NOTION_API_ROOT}{path}",
        headers=_headers(token),
        params=params,
    )
    response.raise_for_status()
    return response.json()


async def _fetch_page(
    client: httpx.AsyncClient, page_id: str, *, token: str
) -> dict[str, Any]:
    return await _get(client, f"/pages/{page_id}", token=token)


async def _fetch_blocks(
    client: httpx.AsyncClient, block_id: str, *, token: str
) -> list[dict[str, Any]]:
    """List top-level children of ``block_id`` up to ``_MAX_BLOCKS``.

    We intentionally do NOT recurse into nested children for v1 —
    nested lists and toggle contents are rarer in the runbook-style
    docs this connector targets, and recursion adds one API call per
    parent block with no cap. Worth adding behind a feature flag
    once we see a concrete need.
    """

    results: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(results) < _MAX_BLOCKS:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        body = await _get(
            client, f"/blocks/{block_id}/children", token=token, params=params
        )
        chunk = body.get("results", []) or []
        results.extend(chunk)
        if not body.get("has_more") or not body.get("next_cursor"):
            break
        cursor = body.get("next_cursor")
    return results[:_MAX_BLOCKS]


# ---------------------------------------------------------------------------
# Rich-text + block rendering
# ---------------------------------------------------------------------------


def _render_rich_text(chunks: list[dict[str, Any]] | None) -> str:
    """Flatten a Notion rich_text array into a single markdown line.

    Notion rich_text items are ``{plain_text, annotations, href}``.
    Ordering matters (order of `annotations` toggles inside the
    emitted markdown reflects the logical wrap order). We
    deliberately ignore coloured backgrounds and mention types other
    than ``user`` because they don't have a crisp markdown analogue.
    """
    if not chunks:
        return ""
    out: list[str] = []
    for chunk in chunks:
        text = chunk.get("plain_text") or ""
        if not text:
            continue
        ann = chunk.get("annotations") or {}
        code = bool(ann.get("code"))
        bold = bool(ann.get("bold"))
        italic = bool(ann.get("italic"))
        strike = bool(ann.get("strikethrough"))
        href = chunk.get("href")

        # Inline code is tricky with other annotations; Notion picks
        # one "visual" treatment and we follow suit — if `code` is
        # set, wrap in backticks and skip the other emphasis layers.
        if code:
            rendered = f"`{text}`"
        else:
            rendered = text
            if bold:
                rendered = f"**{rendered}**"
            if italic:
                rendered = f"*{rendered}*"
            if strike:
                rendered = f"~~{rendered}~~"
        if href:
            rendered = f"[{rendered}]({href})"
        out.append(rendered)
    return "".join(out).strip()


def _render_block(block: dict[str, Any]) -> str:
    """Render a single top-level Notion block as a markdown string.

    Lines returned here do NOT include a trailing newline — the
    caller joins them with `\\n\\n` so blocks get a blank line
    between them, which is how Markdown parsers detect paragraph
    boundaries.
    """

    btype = block.get("type")
    payload = block.get(btype) if btype else None
    if btype is None or payload is None:
        return ""

    if btype == "paragraph":
        return _render_rich_text(payload.get("rich_text"))

    if btype == "heading_1":
        return f"## {_render_rich_text(payload.get('rich_text'))}".rstrip()
    if btype == "heading_2":
        return f"### {_render_rich_text(payload.get('rich_text'))}".rstrip()
    if btype == "heading_3":
        return f"#### {_render_rich_text(payload.get('rich_text'))}".rstrip()

    if btype == "bulleted_list_item":
        return f"- {_render_rich_text(payload.get('rich_text'))}".rstrip()
    if btype == "numbered_list_item":
        # Numbered markdown lists renumber themselves inside most
        # viewers; using "1." everywhere is the idiomatic trick.
        return f"1. {_render_rich_text(payload.get('rich_text'))}".rstrip()
    if btype == "to_do":
        checked = "x" if payload.get("checked") else " "
        return f"- [{checked}] {_render_rich_text(payload.get('rich_text'))}".rstrip()

    if btype == "quote":
        inner = _render_rich_text(payload.get("rich_text"))
        return f"> {inner}" if inner else ""

    if btype == "callout":
        icon = (payload.get("icon") or {}).get("emoji")
        inner = _render_rich_text(payload.get("rich_text"))
        prefix = f"{icon} " if icon else ""
        return f"> {prefix}{inner}".rstrip()

    if btype == "code":
        language = (payload.get("language") or "").strip() or ""
        inner = _render_rich_text(payload.get("rich_text"))
        return f"```{language}\n{inner}\n```" if inner else ""

    if btype == "divider":
        return "---"

    if btype == "child_page":
        title = (payload.get("title") or "Untitled").strip()
        child_id = block.get("id") or ""
        suffix = f" ({child_id})" if child_id else ""
        return f"- 📄 Subpage: {title}{suffix}"

    return f"<unsupported: {btype}>"


# ---------------------------------------------------------------------------
# Public title extraction + page fetch entry point
# ---------------------------------------------------------------------------


def _extract_title(page: dict[str, Any]) -> str:
    """Pull the display title out of a Notion ``pages.retrieve`` payload.

    Notion stores the title in one of the page's properties, keyed
    by the property name the user set on the database (usually
    "Name" or "Title"). The safe move is to scan the properties and
    pick the first one whose type is ``title``.
    """
    props = page.get("properties") or {}
    for _, prop in props.items():
        if (prop or {}).get("type") == "title":
            rendered = _render_rich_text(prop.get("title"))
            if rendered:
                return rendered
    # Occasionally a page has no title property (e.g. child_page blocks
    # during probe). Fall back to something non-empty.
    return "Untitled Notion page"


def _normalize_page_id(raw: str) -> str:
    """Accept either canonical UUID or hyphen-less Notion id."""
    cleaned = raw.strip()
    if not cleaned:
        raise ConnectorUnsupported("resource_ref.page_id is empty")
    # Notion APIs accept both forms. Return the cleaned input so the
    # caller sees exactly what they passed on the wire.
    return cleaned


async def _render_one_page(
    client: httpx.AsyncClient, page_id: str, *, token: str
) -> ConnectorPage:
    """Fetch one Notion page + its top-level blocks → ConnectorPage.

    Lifted out of the dispatcher so database-mode can call it once per
    entry without duplicating the markdown rendering / truncation rule.
    """
    page = await _fetch_page(client, page_id, token=token)
    title = _extract_title(page)
    url = page.get("url") or ""
    last_edited = page.get("last_edited_time") or ""
    blocks = await _fetch_blocks(client, page_id, token=token)

    rendered_blocks = [_render_block(b) for b in blocks]
    rendered_blocks = [line for line in rendered_blocks if line]
    if len(blocks) >= _MAX_BLOCKS:
        rendered_blocks.append(
            f"> _(truncated after {_MAX_BLOCKS} blocks — extend the Notion "
            f"connector if you need deeper pages)_"
        )

    header: list[str] = [f"# {title}"]
    meta_bits: list[str] = []
    if url:
        meta_bits.append(f"[Open in Notion]({url})")
    if last_edited:
        meta_bits.append(f"last edited {last_edited}")
    if meta_bits:
        header.append(" · ".join(meta_bits))
    body = "\n\n".join(header + rendered_blocks).strip() + "\n"

    return ConnectorPage(
        slug=page_id,
        title=title,
        body_md=body,
        page_ref={
            "page_id": page_id,
            "url": url,
            "last_edited_time": last_edited,
        },
    )


async def _resolve_data_source_id(
    client: httpx.AsyncClient, raw_id: str, *, token: str
) -> str:
    """Resolve a Notion ``database_id`` or ``data_source_id`` to a
    queryable data_source id.

    Notion API ``2025-09-03`` split the legacy "database" into a
    container (``/databases/{id}``) plus 1+ data sources that own the
    schema. Operator-facing URLs still expose database ids, so accept
    both shapes:

    1. Try ``GET /data_sources/{id}`` — succeeds if ``raw_id`` already
       points at a data source.
    2. On 404, fall back to ``GET /databases/{id}`` and pick the only
       (or first) data source. We don't currently surface multi-source
       picks in the wizard; if a closed-beta operator hits this we'll
       extend the picker rather than guessing.
    """
    try:
        await _get(client, f"/data_sources/{raw_id}", token=token)
        return raw_id
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
    db = await _get(client, f"/databases/{raw_id}", token=token)
    sources = db.get("data_sources") or []
    if not sources:
        raise ConnectorConfigError(
            f"notion database {raw_id} has no data sources — re-share it with the integration"
        )
    first = sources[0] if isinstance(sources[0], dict) else {}
    data_source_id = str(first.get("id") or "")
    if not data_source_id:
        raise ConnectorConfigError(
            f"notion database {raw_id} returned a malformed data source list"
        )
    return data_source_id


async def _query_data_source_pages(
    client: httpx.AsyncClient, data_source_id: str, *, token: str, limit: int
) -> list[str]:
    """List page ids in a data source, newest-edited first, up to ``limit``."""
    page_ids: list[str] = []
    cursor: str | None = None
    while len(page_ids) < limit:
        body: dict[str, Any] = {
            "page_size": min(100, limit - len(page_ids)),
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
        }
        if cursor:
            body["start_cursor"] = cursor
        response = await client.post(
            f"{NOTION_API_ROOT}/data_sources/{data_source_id}/query",
            headers=_headers(token),
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
        for entry in payload.get("results") or []:
            if isinstance(entry, dict) and entry.get("object") == "page" and entry.get("id"):
                page_ids.append(str(entry["id"]))
                if len(page_ids) >= limit:
                    break
        if not payload.get("has_more") or not payload.get("next_cursor"):
            break
        cursor = payload.get("next_cursor")
    return page_ids


@register("notion")
async def fetch_notion_pages(
    integration: Integration,
    resource_ref: Mapping[str, Any],
    http_client: httpx.AsyncClient | None,
) -> list[ConnectorPage]:
    """Fetcher entry point registered for ``Integration.kind='notion'``.

    Supported shapes:

    - ``{"page_id": "<uuid>"}`` — fetch a single page.
    - ``{"database_id": "<uuid>"}`` / ``{"data_source_id": "<uuid>"}`` —
      treat the database (or its data source under v2025-09-03) as a
      collection: query the entries newest-first, fetch each as a
      ConnectorPage. Capped at ``_MAX_PAGES_PER_DATABASE`` per ref.

    Shape validation runs BEFORE secret decryption so an integration
    that's missing its secret but targets an unsupported shape still
    resolves via stub fallback instead of 502'ing.
    """

    if not isinstance(resource_ref, Mapping):
        raise ConnectorUnsupported("resource_ref must be an object")

    raw_page_id = resource_ref.get("page_id")
    raw_database_id = resource_ref.get("database_id") or resource_ref.get("data_source_id")
    page_mode = isinstance(raw_page_id, str) and bool(raw_page_id.strip())
    db_mode = isinstance(raw_database_id, str) and bool(raw_database_id.strip())

    if not page_mode and not db_mode:
        raise ConnectorUnsupported(
            "notion resource_ref needs page_id or database_id"
        )

    token = safe_decrypt(integration.secret_ciphertext)
    if not token:
        raise ConnectorConfigError(
            "notion integration has no readable access token — "
            "reconnect the integration"
        )

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        if db_mode:
            return await _fetch_database_pages(
                client, _normalize_page_id(str(raw_database_id)), token=token
            )
        return [
            await _fetch_single_page(
                client, _normalize_page_id(str(raw_page_id)), token=token
            )
        ]
    finally:
        if owns_client:
            await client.aclose()


async def _fetch_single_page(
    client: httpx.AsyncClient, page_id: str, *, token: str
) -> ConnectorPage:
    try:
        return await _render_one_page(client, page_id, token=token)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403, 404):
            raise ConnectorConfigError(
                f"notion returned HTTP {status} for page {page_id} — is the "
                "integration shared with the page?"
            ) from exc
        raise


async def _fetch_database_pages(
    client: httpx.AsyncClient, database_id: str, *, token: str
) -> list[ConnectorPage]:
    try:
        data_source_id = await _resolve_data_source_id(client, database_id, token=token)
        page_ids = await _query_data_source_pages(
            client, data_source_id, token=token, limit=_MAX_PAGES_PER_DATABASE
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403, 404):
            raise ConnectorConfigError(
                f"notion returned HTTP {status} for database {database_id} — is the "
                "integration shared with it?"
            ) from exc
        raise

    pages: list[ConnectorPage] = []
    for page_id in page_ids:
        try:
            pages.append(await _render_one_page(client, page_id, token=token))
        except httpx.HTTPStatusError as exc:
            # One inaccessible page in a database shouldn't fail the whole
            # ref — log and continue. The dispatcher's stub fallback isn't
            # right here because we've already produced some valid pages.
            logger.warning(
                "notion database %s: skipping page %s (HTTP %s)",
                database_id,
                page_id,
                exc.response.status_code if exc.response is not None else "?",
            )
    return pages


__all__ = ["fetch_notion_pages"]
