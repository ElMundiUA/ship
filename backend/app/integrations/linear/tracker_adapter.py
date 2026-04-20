"""GraphQL implementation of :class:`TrackerGateway` against Linear.

Linear's API is GraphQL-only. We keep the surface tiny — just the verbs
the pilot pipelines actually call (``list_tickets``, ``transition``,
``comment``). When a future pipeline needs more (subscribe to changes,
attach files), we add a method here rather than on the protocol so we
don't force every other tracker adapter to implement it.

The adapter is constructed *per request* with an already-decrypted
access token (the route layer pulls it from ``Integration
.secret_ciphertext``). We do **not** cache tokens here; integration
secrets rotate via a re-OAuth dance, and the request scope is short
enough that re-fetching the token costs nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from backend.app.integrations.gateway.tracker import (
    CommentRef,
    CreatedTicket,
    ListedIssue,
    TicketRef,
)


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


class LinearTracker:
    """Per-token adapter implementing :class:`TrackerGateway`."""

    def __init__(
        self,
        access_token: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = access_token
        self._client = client

    async def _gql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.post(
                LINEAR_GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        finally:
            if owns_client:
                await http.aclose()
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            # Surface the first GraphQL error verbatim — the route
            # layer already sanitises before sending to the client.
            first = body["errors"][0]
            raise RuntimeError(
                f"Linear GraphQL error: {first.get('message', first)}"
            )
        return body.get("data") or {}

    async def list_tickets(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Most-recently-updated issues for the authorised workspace."""
        query = """
        query ShipListIssues($first: Int!) {
          issues(first: $first, orderBy: updatedAt) {
            nodes {
              id
              identifier
              title
              url
              state { name type }
              updatedAt
            }
          }
        }
        """
        data = await self._gql(query, {"first": max(1, min(limit, 50))})
        nodes = (data.get("issues") or {}).get("nodes") or []
        out: list[dict[str, Any]] = []
        for n in nodes:
            state_name = (n.get("state") or {}).get("name")
            out.append(
                {
                    "id": n.get("identifier") or n.get("id"),
                    "title": n.get("title"),
                    "url": n.get("url"),
                    "status": state_name,
                    "updated_at": n.get("updatedAt"),
                }
            )
        return out

    async def transition(self, ticket: TicketRef, *, to_state: str) -> None:
        """Move ``ticket`` to a state with name ``to_state``.

        Linear identifies states by UUID, not by name, so we resolve the
        state UUID first via a tiny query against the issue's team.
        """
        if ticket.kind != "linear":
            raise ValueError(f"LinearTracker can't transition kind={ticket.kind}")
        # Resolve issue team + state id in one round-trip.
        resolve = """
        query ShipResolveState($id: String!, $name: String!) {
          issue(id: $id) { id team { id states(filter: {name: {eq: $name}}) {
            nodes { id }
          } } }
        }
        """
        data = await self._gql(resolve, {"id": ticket.id, "name": to_state})
        states = (
            ((data.get("issue") or {}).get("team") or {})
            .get("states")
            or {}
        ).get("nodes") or []
        if not states:
            raise ValueError(
                f"Linear state {to_state!r} not found on issue {ticket.id}"
            )
        state_id = states[0]["id"]
        mutation = """
        mutation ShipTransition($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: { stateId: $stateId }) { success }
        }
        """
        await self._gql(mutation, {"id": ticket.id, "stateId": state_id})

    async def comment(self, ticket: TicketRef, *, body: str) -> None:
        if ticket.kind != "linear":
            raise ValueError(f"LinearTracker can't comment on kind={ticket.kind}")
        mutation = """
        mutation ShipComment($id: String!, $body: String!) {
          commentCreate(input: { issueId: $id, body: $body }) { success }
        }
        """
        await self._gql(mutation, {"id": ticket.id, "body": body})

    async def create_ticket(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        project_hint: str | None = None,
    ) -> CreatedTicket:
        """Create a Linear issue under the requested team.

        ``project_hint`` accepts either a team UUID or a team key
        (``ENG``) — we resolve to UUID up front because the
        ``issueCreate`` mutation requires a UUID. When the user
        omits a hint and the workspace has exactly one team, we
        pick it; otherwise we raise so the caller surfaces a
        "which team?" prompt rather than silently landing the
        ticket in the wrong inbox.

        ``labels`` are resolved by name within the team's label
        set. Unknown labels are dropped (not auto-created) — we
        don't want the agent polluting a customer's Linear with
        freshly-invented tag names.
        """
        team_id = await self._resolve_team_id(project_hint)
        label_ids = (
            await self._resolve_label_ids(team_id, labels) if labels else []
        )

        mutation = """
        mutation ShipCreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier url }
          }
        }
        """
        input_payload: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": body,
        }
        if label_ids:
            input_payload["labelIds"] = label_ids

        data = await self._gql(mutation, {"input": input_payload})
        issue = ((data.get("issueCreate") or {}).get("issue")) or {}
        if not (data.get("issueCreate") or {}).get("success") or not issue:
            raise ValueError("Linear refused issueCreate (no issue returned).")

        return CreatedTicket(
            ref=TicketRef(
                kind="linear",
                workspace_hint=team_id,
                id=str(issue["id"]),
            ),
            url=str(issue.get("url") or ""),
            display_id=str(issue.get("identifier") or issue["id"]),
        )

    # -----------------------------------------------------------------
    # Clarifications projection surface (D13)
    # -----------------------------------------------------------------

    async def list_issues_with_label(
        self, label: str, *, limit: int = 100
    ) -> list[ListedIssue]:
        """Open issues carrying ``label``.

        We filter on Linear's ``labels.name`` server-side so the
        projection cron doesn't pull the full backlog and filter
        client-side. ``state.type != 'completed'`` because a
        done/cancelled ticket with the ``ship:needs-clarification``
        label is almost certainly a stale marker — the cron will
        mark the matching Ship row ``stale`` without erroring.
        """
        query = """
        query ShipLabelledIssues($label: String!, $first: Int!) {
          issues(
            first: $first,
            filter: {
              labels: { name: { eq: $label } },
              state: { type: { nin: ["completed","canceled"] } }
            },
            orderBy: updatedAt
          ) {
            nodes {
              id
              identifier
              url
              team { id }
            }
          }
        }
        """
        data = await self._gql(
            query, {"label": label, "first": max(1, min(limit, 100))}
        )
        nodes = (data.get("issues") or {}).get("nodes") or []
        out: list[ListedIssue] = []
        for node in nodes:
            if not node.get("id"):
                continue
            out.append(
                ListedIssue(
                    ref=TicketRef(
                        kind="linear",
                        workspace_hint=((node.get("team") or {}).get("id")),
                        id=str(node["id"]),
                    ),
                    display_id=str(node.get("identifier") or node["id"]),
                    url=node.get("url"),
                )
            )
        return out

    async def list_comments(self, ticket: TicketRef) -> list[CommentRef]:
        """All comments on the issue, oldest first.

        Linear's ``issue(id:).comments`` paginates by 50; for the
        clarifications path we cap at 100 because the projection
        only cares about recent exchange (and a Linear issue with
        200 comments is unusual). If that ever bites, add a cursor
        loop — the sync service doesn't care about the mechanics.
        """
        if ticket.kind != "linear":
            raise ValueError(
                f"LinearTracker can't list_comments for kind={ticket.kind}"
            )
        query = """
        query ShipComments($id: String!) {
          issue(id: $id) {
            comments(first: 100) {
              nodes {
                id
                body
                createdAt
                url
                user { displayName email }
              }
            }
          }
        }
        """
        data = await self._gql(query, {"id": ticket.id})
        nodes = (
            ((data.get("issue") or {}).get("comments") or {}).get("nodes") or []
        )
        out: list[CommentRef] = []
        for node in nodes:
            created_raw = node.get("createdAt")
            try:
                created_at = _parse_iso8601(created_raw) if created_raw else datetime.min
            except ValueError:
                created_at = datetime.min
            user = node.get("user") or {}
            out.append(
                CommentRef(
                    id=str(node.get("id") or ""),
                    body=str(node.get("body") or ""),
                    author=(user.get("displayName") or user.get("email")),
                    created_at=created_at,
                    url=node.get("url"),
                )
            )
        out.sort(key=lambda c: c.created_at)
        return out

    async def remove_label(self, ticket: TicketRef, label: str) -> None:
        """Strip ``label`` from the issue (no-op if not present)."""
        if ticket.kind != "linear":
            raise ValueError(
                f"LinearTracker can't remove_label for kind={ticket.kind}"
            )
        # Linear removes labels via ``issueUpdate`` with the residual
        # label id set (no dedicated removeLabel mutation). Fetch the
        # current list, strip the match, push it back.
        resolve = """
        query ShipIssueLabels($id: String!) {
          issue(id: $id) {
            labels { nodes { id name } }
          }
        }
        """
        data = await self._gql(resolve, {"id": ticket.id})
        nodes = (
            ((data.get("issue") or {}).get("labels") or {}).get("nodes") or []
        )
        wanted = label.lower()
        remaining = [
            str(n["id"]) for n in nodes
            if str(n.get("name", "")).lower() != wanted
        ]
        if len(remaining) == len(nodes):
            return  # Label wasn't attached; nothing to do.
        mutation = """
        mutation ShipStripLabel($id: String!, $labelIds: [String!]!) {
          issueUpdate(id: $id, input: { labelIds: $labelIds }) { success }
        }
        """
        await self._gql(mutation, {"id": ticket.id, "labelIds": remaining})

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    async def _resolve_team_id(self, hint: str | None) -> str:
        """Accept ``None`` / UUID / team key, return a team UUID."""
        if hint:
            # If it already looks like a UUID, trust it; Linear IDs
            # are 36-char UUIDs, team keys are uppercase short codes.
            if len(hint) >= 32 and "-" in hint:
                return hint
            lookup = """
            query ShipResolveTeam($key: String!) {
              teams(filter: {key: {eq: $key}}) { nodes { id } }
            }
            """
            data = await self._gql(lookup, {"key": hint})
            nodes = (data.get("teams") or {}).get("nodes") or []
            if not nodes:
                raise ValueError(f"Linear team {hint!r} not found.")
            return str(nodes[0]["id"])
        # No hint — fall back to "exactly one team" auto-pick.
        first = """
        query ShipFirstTeam { teams(first: 2) { nodes { id key } } }
        """
        data = await self._gql(first)
        nodes = (data.get("teams") or {}).get("nodes") or []
        if len(nodes) != 1:
            raise ValueError(
                "Linear workspace has multiple teams; pass project_hint="
                "<team-key or id>."
            )
        return str(nodes[0]["id"])

    async def _resolve_label_ids(
        self, team_id: str, labels: list[str]
    ) -> list[str]:
        if not labels:
            return []
        query = """
        query ShipLabels($teamId: String!) {
          team(id: $teamId) { labels { nodes { id name } } }
        }
        """
        data = await self._gql(query, {"teamId": team_id})
        nodes = (
            ((data.get("team") or {}).get("labels") or {}).get("nodes") or []
        )
        want = {lbl.lower() for lbl in labels}
        return [str(n["id"]) for n in nodes if str(n.get("name", "")).lower() in want]


def _parse_iso8601(raw: str) -> datetime:
    """Parse Linear's ``createdAt`` (ISO-8601 with ``Z`` suffix).

    ``datetime.fromisoformat`` only accepts ``+00:00`` before 3.11;
    we're on 3.12 but Linear still ships the ``Z``. Normalise it
    before handing off to keep the adapter portable.
    """
    cleaned = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    return datetime.fromisoformat(cleaned)


__all__ = ["LinearTracker", "LINEAR_GRAPHQL_URL"]
