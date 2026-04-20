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

from typing import Any

import httpx

from backend.app.integrations.gateway.tracker import CreatedTicket, TicketRef


NOTION_API_ROOT = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"


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
    ) -> CreatedTicket:
        """Create a page under a Notion database.

        ``project_hint`` is the target database id. When omitted we
        do a search for the first database the integration can see
        and use that; if there's more than one we bail out so the
        caller surfaces "pick a database" rather than dropping the
        ticket somewhere random.

        ``labels`` are ignored in the first cut — Notion's
        multi-select label columns differ per database and we
        don't want to guess a column name. The body is rendered
        as a single paragraph block because Notion doesn't accept
        markdown directly; a fuller markdown → Notion blocks
        translation is a Phase-3 polish item.
        """
        database_id = await self._resolve_database_id(project_hint)
        title_prop_name = await self._resolve_title_property(database_id)

        create_payload: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": {
                title_prop_name: {
                    "title": [{"type": "text", "text": {"content": title}}],
                }
            },
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
            ref=TicketRef(kind="notion", workspace_hint=database_id, id=page_id),
            url=str(created.get("url") or ""),
            display_id=page_id,
        )

    async def _resolve_database_id(self, hint: str | None) -> str:
        if hint:
            return hint
        # Search for visible databases; the user shares specific
        # databases with the integration at install time.
        body = await self._request(
            "POST",
            "/search",
            json={
                "filter": {"property": "object", "value": "database"},
                "page_size": 2,
            },
        )
        results = body.get("results", []) or []
        if not results:
            raise ValueError(
                "No Notion databases are shared with the Ship integration. "
                "Share a database under the tracker connection first."
            )
        if len(results) > 1:
            raise ValueError(
                "Multiple Notion databases are visible; pass "
                "project_hint=<database-id> to pick one."
            )
        return str(results[0]["id"])

    async def _resolve_title_property(self, database_id: str) -> str:
        """Notion databases always have exactly one ``title`` property."""
        db = await self._request("GET", f"/databases/{database_id}")
        props = db.get("properties") or {}
        for name, prop in props.items():
            if prop.get("type") == "title":
                return str(name)
        # Should never happen for a well-formed database.
        raise ValueError(
            f"Notion database {database_id} has no title property."
        )


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
