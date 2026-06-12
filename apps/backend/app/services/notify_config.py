"""Per-workspace notification channel routing (ELS-219).

The config surface the Phase-1 ``notify()`` router reads. Routing
lives on the existing ``Workspace.settings`` JSON column (no
migration) under::

    settings["notifications"] = {
        "email_to": "ops@example.com",          # EmailChannel recipient
        "channels": {
            "info":    ["inbox"],
            "action":  ["inbox", "linear"],
            "blocker": ["inbox", "linear", "email"],
        },
    }

Safe-by-default rules (founder decisions baked in):

* Every level defaults to ``["inbox"]`` — today's behavior — so the
  seam is a no-op until a workspace opts a level onto linear/email.
* The global kill-switch ``SHIP_NOTIFY_CHANNELS`` (default ``False``,
  see :class:`backend.app.core.config.Settings`) forces inbox-only
  regardless of per-workspace config: instant global rollback.
* Malformed settings fail **closed** to inbox-only with a logged
  warning — never raise on the engine's emit path.
* A missing ``email_to`` is a structured skip, not a guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.tenancy import Workspace

logger = logging.getLogger("ship.notify_config")


class NotifyLevel(str, Enum):
    """Severity of an engine emission — drives channel routing."""

    INFO = "info"
    ACTION = "action"
    BLOCKER = "blocker"


class NotifyChannel(str, Enum):
    INBOX = "inbox"
    LINEAR = "linear"
    EMAIL = "email"


_SUPPORTED_CHANNELS: Final[frozenset[str]] = frozenset(
    c.value for c in NotifyChannel
)
_INBOX_ONLY: Final[tuple[NotifyChannel, ...]] = (NotifyChannel.INBOX,)


@dataclass(frozen=True)
class ChannelRouting:
    """Resolved per-level channel lists for one workspace."""

    info: tuple[NotifyChannel, ...] = _INBOX_ONLY
    action: tuple[NotifyChannel, ...] = _INBOX_ONLY
    blocker: tuple[NotifyChannel, ...] = _INBOX_ONLY
    email_to: str | None = None

    def channels_for(self, level: NotifyLevel) -> tuple[NotifyChannel, ...]:
        return {
            NotifyLevel.INFO: self.info,
            NotifyLevel.ACTION: self.action,
            NotifyLevel.BLOCKER: self.blocker,
        }[level]


_DEFAULT_ROUTING: Final[ChannelRouting] = ChannelRouting()


def _parse_level(raw: Any, *, workspace_id: Any, level: str) -> tuple[NotifyChannel, ...]:
    """One level's channel list — fail closed to inbox-only on any shape
    surprise. The Inbox channel is always present so a typo'd config can
    never silently drop an engine emission."""
    if raw is None:
        return _INBOX_ONLY
    if not isinstance(raw, (list, tuple)):
        logger.warning(
            "notify_config: ws=%s level=%s is not a list (%r) — inbox-only",
            workspace_id, level, type(raw).__name__,
        )
        return _INBOX_ONLY
    channels: list[NotifyChannel] = [NotifyChannel.INBOX]
    for entry in raw:
        if entry == NotifyChannel.INBOX.value:
            continue  # already first
        if isinstance(entry, str) and entry in _SUPPORTED_CHANNELS:
            channel = NotifyChannel(entry)
            if channel not in channels:
                channels.append(channel)
        else:
            logger.warning(
                "notify_config: ws=%s level=%s unknown channel %r — skipped",
                workspace_id, level, entry,
            )
    return tuple(channels)


def get_channel_routing(
    workspace: Workspace,
    *,
    settings: Settings | None = None,
) -> ChannelRouting:
    """Resolve the workspace's channel routing.

    Honors the global ``SHIP_NOTIFY_CHANNELS`` kill-switch: while it is
    off (the default) every workspace resolves to inbox-only no matter
    what its settings request. Never raises — the engine's emit path
    must not die because an operator fat-fingered a settings blob.
    """
    settings = settings or get_settings()
    raw = (workspace.settings or {}).get("notifications")
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning(
                "notify_config: ws=%s settings.notifications is not an "
                "object — inbox-only", workspace.id,
            )
        raw = {}

    email_to_raw = raw.get("email_to")
    email_to = (
        email_to_raw.strip()
        if isinstance(email_to_raw, str) and email_to_raw.strip()
        else None
    )

    if not settings.notification_channels_enabled:
        # Kill-switch off → inbox-only globally. email_to still resolves
        # so audit/debug surfaces can show what WOULD be used.
        return ChannelRouting(email_to=email_to)

    channels_raw = raw.get("channels")
    if channels_raw is None:
        return ChannelRouting(email_to=email_to)
    if not isinstance(channels_raw, dict):
        logger.warning(
            "notify_config: ws=%s notifications.channels is not an object "
            "— inbox-only", workspace.id,
        )
        return ChannelRouting(email_to=email_to)

    return ChannelRouting(
        info=_parse_level(
            channels_raw.get("info"), workspace_id=workspace.id, level="info"
        ),
        action=_parse_level(
            channels_raw.get("action"), workspace_id=workspace.id, level="action"
        ),
        blocker=_parse_level(
            channels_raw.get("blocker"), workspace_id=workspace.id, level="blocker"
        ),
        email_to=email_to,
    )


__all__ = [
    "ChannelRouting",
    "NotifyChannel",
    "NotifyLevel",
    "get_channel_routing",
]
