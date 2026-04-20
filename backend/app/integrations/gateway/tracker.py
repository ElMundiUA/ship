"""Tracker gateway interface — tickets, transitions, comments.

Pilot adapters: GitHub Issues / Projects (covered by the GitHub App),
Linear (Day 2), Notion (Day 2). Future: Jira, Asana, ClickUp, Azure Boards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
class ListedIssue:
    """One labelled ticket as returned by the projection-facing listing.

    ``ref`` is what the adapter hands back for subsequent calls
    (``list_comments``, ``remove_label``, ``comment``) — the vendor's
    stable identifier. ``display_id`` is the human-readable form Ship
    stores on :class:`Clarification.tracker_issue_key` (``ENG-42``,
    ``owner/repo#123``, Notion page slug); the two diverge on Linear
    where ``ref.id`` is a UUID but humans think in ``ENG-42``.
    ``url`` is the deep-link for the UI.
    """

    ref: TicketRef
    display_id: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class CommentRef:
    """One comment on a tracker ticket — the shape the clarifications
    projection needs.

    We carry the vendor's raw comment identifier (``id``) because Ship
    uses ``(provider, issue, comment)`` as the dedup key when ingesting
    clarifications — re-running the projection must be idempotent even
    if the ticket gains new comments between polls. ``body`` is the
    markdown the agent wrote (Ship scans it for the ``@ship`` markers);
    ``author`` is best-effort (GitHub gives us logins, Linear gives
    display names, Notion gives user ids) and is used only for audit
    metadata.
    """

    id: str
    body: str
    author: str | None
    created_at: datetime
    url: str | None = None


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

    # -----------------------------------------------------------------
    # Clarifications projection (D13)
    #
    # Optional surface — only trackers that participate in the
    # clarifications read-model need to implement these. Adapters that
    # don't (Notion in the pilot, any tracker where labels aren't
    # first-class) raise :class:`NotImplementedError`; the sync service
    # catches that and skips the tracker cleanly. We did not make these
    # top-level abstract methods because they'd force churn on every
    # adapter Ship grows before we know that tracker fits the model.
    # -----------------------------------------------------------------

    async def list_issues_with_label(
        self, label: str, *, limit: int = 100
    ) -> list[ListedIssue]:
        """All open issues carrying ``label`` on the connected workspace.

        Used by the clarifications projection — the agent marks a
        ticket with ``ship:needs-clarification`` and Ship polls this
        call to pick it up. Adapters filter to whatever the vendor
        considers "still needs attention" (open on GitHub, non-done
        states on Linear). ``limit`` caps the per-poll batch; the
        projection iterates.

        Returns :class:`ListedIssue` — a ``TicketRef`` for subsequent
        API calls plus the human-readable ``display_id`` and ``url``
        Ship projects into the row.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not participate in the "
            "clarifications projection"
        )

    async def list_comments(self, ticket: TicketRef) -> list[CommentRef]:
        """All comments on ``ticket``, oldest first.

        Scanned for ``@ship clarification:`` / ``@ship answer:``
        markers by the projection. Adapters must return them in
        chronological order so the "most recent clarification" +
        "answer that came after it" pairing works without extra
        sorting in the service layer.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not list comments"
        )

    async def remove_label(self, ticket: TicketRef, label: str) -> None:
        """Remove ``label`` from ``ticket``; no-op if the label isn't set.

        Used when a human answers a clarification from the Ship
        console (we write the answer back as a comment, then strip
        ``ship:needs-clarification`` so the agent / projection moves
        on). Adapters treat "label isn't attached" as success — the
        caller shouldn't need to check first.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot remove labels"
        )
