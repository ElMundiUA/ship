"""Chat gateway interface — outbound notifications.

No pilot adapter (Slack lives in package #3). Defined now so the dashboard
"send digest" action can be coded against the protocol from day one.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ChatGateway(Protocol):
    async def post_message(
        self, *, channel: str, text: str, attachments: list[dict[str, Any]] | None = None
    ) -> None:
        """Post ``text`` (and optional rich attachments) to ``channel``."""
        ...
