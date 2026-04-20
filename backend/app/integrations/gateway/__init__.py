"""Typed Gateway interfaces for third-party integrations (RFC pilot-plan).

Each vendor namespace (github, linear, notion, …) implements one or more
of these protocols. Domain code only ever holds a reference to a protocol
type, so we can swap the concrete adapter — including a future
``broker`` adapter for on-prem — without touching call sites.

Identifiers are intentionally *typed and discriminated* per vendor to
preserve the native hierarchy (``owner/repo`` for GitHub vs
``org/project/repo`` for Azure DevOps, …) instead of forcing every
vendor through the lowest common denominator.
"""

from backend.app.integrations.gateway.chat import ChatGateway
from backend.app.integrations.gateway.ci import CIGateway
from backend.app.integrations.gateway.code_host import (
    BlobContent,
    CodeHostGateway,
    PullRequestRef,
    RepoRef,
    RepoSummary,
)
from backend.app.integrations.gateway.tracker import (
    CreatedTicket,
    TicketRef,
    TrackerGateway,
)

__all__ = [
    "BlobContent",
    "ChatGateway",
    "CIGateway",
    "CodeHostGateway",
    "CreatedTicket",
    "PullRequestRef",
    "RepoRef",
    "RepoSummary",
    "TicketRef",
    "TrackerGateway",
]
