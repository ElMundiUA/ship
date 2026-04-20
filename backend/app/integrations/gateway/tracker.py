"""Tracker gateway interface — tickets, transitions, comments.

Pilot adapters: GitHub Issues / Projects (covered by the GitHub App),
Linear (Day 2), Notion (Day 2). Future: Jira, Asana, ClickUp, Azure Boards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TicketRef:
    """Vendor-discriminated identifier for a single tracker item.

    Each tracker has a different natural identifier (Linear "ENG-123",
    GitHub "owner/repo#42", Notion "page-uuid"); we keep the raw vendor id
    in ``id`` and let the kind drive interpretation.
    """

    kind: Literal["github_issues", "linear", "notion"]
    workspace_hint: str | None  # org/team/database id, vendor-specific
    id: str


@dataclass(frozen=True, slots=True)
class CreatedTicket:
    """Result of :meth:`TrackerGateway.create_ticket`.

    We return both the vendor-discriminated ``ref`` (for downstream
    API calls — comment, transition) and the user-visible URL (for
    the agent to cite in its chat response). ``display_id`` is the
    human-readable identifier when the vendor has one (``ENG-123``
    for Linear, ``#42`` for GitHub Issues); Notion falls back to the
    page UUID.
    """

    ref: TicketRef
    url: str
    display_id: str


@runtime_checkable
class TrackerGateway(Protocol):
    """The pipeline-side surface for any ticket tracker."""

    async def list_tickets(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Most recently updated tickets for the connected workspace.

        Used by the dashboard and the daily-standup pipeline. Vendor
        adapters normalise to ``{"id", "title", "url", "status",
        "updated_at"}`` at minimum.
        """
        ...

    async def transition(self, ticket: TicketRef, *, to_state: str) -> None:
        """Move a ticket to ``to_state`` (vendor-specific state name)."""
        ...

    async def comment(self, ticket: TicketRef, *, body: str) -> None:
        """Append a markdown comment authored by the Ship integration."""
        ...

    async def create_ticket(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        project_hint: str | None = None,
    ) -> CreatedTicket:
        """Create a new ticket/issue/page on the connected tracker.

        ``title`` maps to the headline the tracker renders in lists.
        ``body`` is markdown; vendors that don't natively render
        markdown (Notion) do a best-effort conversion.

        ``labels`` is a vendor-portable hint — Linear / GitHub Issues
        treat it as tags. Notion ignores it in the first cut because
        its "labels" are per-database multi-select columns, which
        need richer schema awareness than the agent has.

        ``project_hint`` disambiguates which team/project/database
        the ticket lands in for vendors that span multiple
        (Linear teams, Notion databases, GitHub repos). When the
        connection has exactly one, ``None`` is fine.

        Raises :class:`ValueError` on vendor-side validation errors
        (e.g. "no GitHub repo configured") so the agent can bubble
        the reason up to the user rather than a generic 500.
        """
        ...
