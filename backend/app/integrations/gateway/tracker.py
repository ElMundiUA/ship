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
