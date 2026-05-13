"""Notion implementation of :class:`TrackerGateway`.

Notion's "tracker" surface is unusual — there's no first-class issue
type. We treat any database with a ``Status`` (or status-like) select
property as a ticket source. The pilot pipelines only ever see the
normalised payload defined in :class:`TrackerGateway`, so this glue
keeps the rest of the stack vendor-agnostic.

Per-token, just like :class:`backend.app.integrations.linear
.tracker_adapter.LinearTracker`. Construction takes the decrypted
access token (the route layer fetches + decrypts the
``Integration.secret_ciphertext``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from backend.app.integrations.gateway.tracker import CreatedTicket, TicketRef


NOTION_API_ROOT = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"


@dataclass(frozen=True, slots=True)
class _ResolvedDataSource:
    id: str
    title_property: str
    # When the database has a ``select``-typed property whose name
    # lowercases to ``"type"`` we capture its exact-case name plus a
    # ``{lowercase-option → exact-case-option}`` lookup so
    # ``create_ticket`` can render an agent-supplied ``ticket_type``
    # without re-fetching the schema. ``None`` means the column
    # doesn't exist; the adapter no-ops on type rather than poking
    # an unknown property.
    type_select_property: str | None = None
    type_options_by_value: dict[str, str] = field(default_factory=dict)


class NotionTracker:
    """Per-token adapter implementing :class:`TrackerGateway`."""

    def __init__(
        self,
        access_token: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = access_token
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.request(
                method,
                f"{NOTION_API_ROOT}{path}",
                headers=self._headers(),
                json=json,
                params=params,
            )
        finally:
            if owns_client:
                await http.aclose()
        response.raise_for_status()
        return response.json()

    async def list_tickets(
        self,
        *,
        limit: int = 10,
        state: str | None = None,
        assignee_me: bool = False,
        query: str | None = None,
        assignee: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recent pages from any database the integration can see.

        Notion forces the user to share specific databases with the
        integration — there is no "list all databases" call that
        returns rows the user has implicitly granted. We do a global
        search filtered to ``object=page`` sorted by ``last_edited_time``
        which is the closest analogue to "recently updated tickets".

        ``state`` / ``query`` / ``assignee_me`` are applied as a
        best-effort client-side filter on the fetched page (Notion's
        search API is too coarse for server-side ticket semantics).
        """
        del assignee  # not available without richer Notion metadata
        fetch_n = max(1, min(max(limit * 3, limit), 50))
        body = await self._request(
            "POST",
            "/search",
            json={
                "filter": {"property": "object", "value": "page"},
                "sort": {
                    "direction": "descending",
                    "timestamp": "last_edited_time",
                },
                "page_size": fetch_n,
            },
        )
        out: list[dict[str, Any]] = []
        for page in body.get("results", []) or []:
            out.append(
                {
                    "id": page.get("id"),
                    "title": _extract_title(page),
                    "url": page.get("url"),
                    "status": _extract_status(page),
                    "updated_at": page.get("last_edited_time"),
                }
            )

        qlow = (query or "").strip().lower()
        if qlow:
            out = [
                row
                for row in out
                if qlow in (row.get("title") or "").lower()
            ]

        st = (state or "all").lower()
        done_like = {"done", "closed", "complete", "completed"}

        def _status_bucket(s: str | None) -> str:
            if not s:
                return "open"
            return "closed" if s.lower() in done_like else "open"

        if st == "open":
            out = [row for row in out if _status_bucket(row.get("status")) == "open"]
        elif st in {"closed", "done", "completed"}:
            out = [row for row in out if _status_bucket(row.get("status")) == "closed"]

        if assignee_me:
            out = []

        return out[:limit]

    async def transition(self, ticket: TicketRef, *, to_state: str) -> None:
        """Update the ``Status`` property of a Notion page.

        We assume the database has a ``Status`` (case-insensitive)
        property of type ``status`` or ``select``. If the property is
        missing or the option name doesn't exist the API returns 400 —
        we surface that verbatim because the user error is "rename your
        column" rather than a Ship bug.
        """
        if ticket.kind != "notion":
            raise ValueError(f"NotionTracker can't transition kind={ticket.kind}")
        await self._request(
            "PATCH",
            f"/pages/{ticket.id}",
            json={
                "properties": {
                    "Status": {
                        # ``status`` and ``select`` share the option
                        # shape; Notion picks the right one based on
                        # the underlying property type.
                        "status": {"name": to_state},
                    }
                }
            },
        )

    async def comment(self, ticket: TicketRef, *, body: str) -> None:
        if ticket.kind != "notion":
            raise ValueError(f"NotionTracker can't comment on kind={ticket.kind}")
        await self._request(
            "POST",
            "/comments",
            json={
                "parent": {"page_id": ticket.id},
                "rich_text": [{"type": "text", "text": {"content": body}}],
            },
        )

    async def create_ticket(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        project_hint: str | None = None,
        project_id: str | None = None,
        ticket_type: Literal["bug", "feature", "task"] | None = None,
    ) -> CreatedTicket:
        """Create a page under a Notion database.

        ``project_hint`` is the target database id. When omitted we
        do a search for the first database the integration can see
        and use that; if there's more than one we bail out so the
        caller surfaces "pick a database" rather than dropping the
        ticket somewhere random.

        ``project_id`` is accepted for protocol parity but ignored —
        Notion's project semantics live in a different surface
        (databases-as-projects) than the gateway's epic model.

        ``labels`` are ignored in the first cut — Notion's
        multi-select label columns differ per database and we
        don't want to guess a column name. The body is rendered
        as a single paragraph block because Notion doesn't accept
        markdown directly; a fuller markdown → Notion blocks
        translation is a Phase-3 polish item.

        ``ticket_type`` (``bug`` / ``feature`` / ``task``) is set on
        the database's ``Type`` select column when (a) the column
        exists (case-insensitive name match) and (b) the column has
        an option whose name matches the value (case-insensitive).
        Both conditions missing → no-op; we don't auto-create the
        column or the option.
        """
        del project_id  # accepted for protocol parity; not applied here
        data_source = await self._resolve_data_source(project_hint)

        properties: dict[str, Any] = {
            data_source.title_property: {
                "title": [{"type": "text", "text": {"content": title}}],
            }
        }
        if ticket_type is not None and data_source.type_select_property:
            option_name = data_source.type_options_by_value.get(
                ticket_type.lower()
            )
            if option_name is not None:
                properties[data_source.type_select_property] = {
                    "select": {"name": option_name}
                }

        create_payload: dict[str, Any] = {
            "parent": {
                "type": "data_source_id",
                "data_source_id": data_source.id,
            },
            "properties": properties,
            # Body as a single paragraph block; good enough for
            # LLM-generated tickets where the body is a short spec.
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": body[:2000]}}
                        ]
                    },
                }
            ],
        }
        created = await self._request("POST", "/pages", json=create_payload)
        page_id = str(created.get("id") or "")
        if not page_id:
            raise ValueError("Notion refused page creation (no id returned).")
        return CreatedTicket(
            ref=TicketRef(kind="notion", workspace_hint=data_source.id, id=page_id),
            url=str(created.get("url") or ""),
            display_id=page_id,
        )

    async def _resolve_database_id(self, hint: str | None) -> str:
        if hint:
            return hint
        # Search for visible data sources; the user shares specific
        # databases/data sources with the integration at install time.
        body = await self._request(
            "POST",
            "/search",
            json={
                "filter": {"property": "object", "value": "data_source"},
                "page_size": 2,
            },
        )
        results = body.get("results", []) or []
        if not results:
            raise ValueError(
                "No Notion data sources are shared with the Ship integration. "
                "Share a database/data source under the tracker connection first."
            )
        if len(results) > 1:
            raise ValueError(
                "Multiple Notion data sources are visible; pass "
                "project_hint=<data-source-id> to pick one."
            )
        return str(results[0]["id"])

    async def _resolve_data_source(self, hint: str | None) -> _ResolvedDataSource:
        """Resolve either a Notion data source id or database container id.

        Notion API version ``2025-09-03`` split databases into a container
        (``/databases/{id}``) plus one or more data sources that own the
        actual schema. User-facing URLs still often expose the database id, so
        accept both shapes here.
        """
        target_id = await self._resolve_database_id(hint)
        try:
            return await self._data_source_from_id(target_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

        db = await self._request("GET", f"/databases/{target_id}")
        data_sources = db.get("data_sources") or []
        if not data_sources:
            raise ValueError(
                f"Notion database {target_id} has no data sources shared with Ship."
            )
        if len(data_sources) > 1:
            raise ValueError(
                "Multiple Notion data sources are visible for this database; pass "
                "project_hint=<data-source-id> to pick one."
            )
        data_source_id = str(data_sources[0].get("id") or "")
        if not data_source_id:
            raise ValueError(
                f"Notion database {target_id} returned a data source without an id."
            )
        return await self._data_source_from_id(data_source_id)

    async def _data_source_from_id(self, data_source_id: str) -> _ResolvedDataSource:
        data_source = await self._request("GET", f"/data_sources/{data_source_id}")
        props = data_source.get("properties") or {}
        title_prop_name = _resolve_title_property_name(props, data_source_id)
        type_prop_name, type_options = _resolve_type_select(props)
        return _ResolvedDataSource(
            id=data_source_id,
            title_property=title_prop_name,
            type_select_property=type_prop_name,
            type_options_by_value=type_options,
        )


def _resolve_title_property_name(props: dict[str, Any], target_id: str) -> str:
    """Notion data sources always have exactly one ``title`` property."""
    for name, prop in props.items():
        if prop.get("type") == "title":
            return str(name)
    # Should never happen for a well-formed data source, but the error should
    # point at schema sharing rather than tell users to rename columns.
    raise ValueError(
        f"Notion data source {target_id} has no title property visible to Ship."
    )


def _resolve_type_select(
    props: dict[str, Any],
) -> tuple[str | None, dict[str, str]]:
    """Locate a ``select``-typed ``Type`` column and its option index.

    Match is case-insensitive on the property name so a database using
    lowercase ``"type"`` works the same as one using ``"Type"``. Only
    the three values the agent can send (``bug`` / ``feature`` /
    ``task``) are indexed — extra options on the column (``"Other"``,
    custom workflow values) are ignored, and a missing option simply
    no-ops at write time. We don't auto-create options.
    """
    wanted = {"bug", "feature", "task"}
    for name, prop in props.items():
        if not isinstance(name, str) or name.lower() != "type":
            continue
        if prop.get("type") != "select":
            continue
        select = prop.get("select") or {}
        options_index: dict[str, str] = {}
        for option in select.get("options") or []:
            opt_name = option.get("name")
            if not isinstance(opt_name, str) or not opt_name:
                continue
            key = opt_name.lower()
            if key in wanted and key not in options_index:
                options_index[key] = opt_name
        return name, options_index
    return None, {}


def _extract_title(page: dict[str, Any]) -> str | None:
    """Best-effort title extraction for a Notion page."""
    props = page.get("properties") or {}
    for prop in props.values():
        if prop.get("type") == "title":
            chunks = prop.get("title") or []
            return "".join(chunk.get("plain_text", "") for chunk in chunks) or None
    return None


def _extract_status(page: dict[str, Any]) -> str | None:
    """Pull the ``Status`` (or first status-like) property's value."""
    props = page.get("properties") or {}
    for name in ("Status", "status"):
        prop = props.get(name)
        if not prop:
            continue
        if prop.get("type") == "status":
            return ((prop.get("status") or {}).get("name")) or None
        if prop.get("type") == "select":
            return ((prop.get("select") or {}).get("name")) or None
    return None


__all__ = ["NotionTracker", "NOTION_API_ROOT", "NOTION_VERSION"]
