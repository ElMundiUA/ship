"""The notification seam (ELS-222) — ``notify()`` + channel adapters.

One interface for every place the engine "tells a human something".
Routing is per-workspace (:mod:`backend.app.services.notify_config`),
behind the global ``SHIP_NOTIFY_CHANNELS`` kill-switch (default off →
inbox-only, today's behavior, so flipping an emit-site onto this seam
is an observable no-op until a workspace opts in).

Invariants:

* ``notify()`` NEVER raises into the engine control path. Channel
  failures collapse into ``ChannelResult(ok=False, detail=…)``.
* The Inbox channel is the default for every level and reuses the
  intake helpers (truncation / headline / category / priority) so its
  rows are byte-identical with what the emit-sites build today. Flips
  pass their original explicit field values via ``inbox_overrides``.
* Channels are EGRESS ONLY. Nothing here reads comments/email back or
  triggers FSM transitions — the STATUS field stays the only
  transition signal (tracker_fsm.py:277).
* Site-level dedup (audit-row windows, intake-handle pre-checks) stays
  in the CALLER — this seam routes the emission, it does not own the
  decision to emit.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.tenancy import Workspace
from backend.app.services.notify_config import (
    ChannelRouting,
    NotifyChannel,
    NotifyLevel,
    get_channel_routing,
)

logger = logging.getLogger("ship.notify")


# Default level → InboxItem.type mapping. Emit-site flips that need a
# different type (e.g. the dispatcher's ``clarification`` at ACTION
# level) pass it explicitly via ``inbox_overrides["type"]``.
_LEVEL_TO_INBOX_TYPE: dict[NotifyLevel, str] = {
    NotifyLevel.INFO: "improvement",
    NotifyLevel.ACTION: "clarification",
    NotifyLevel.BLOCKER: "blocker",
}


@dataclass(frozen=True)
class NotifyContext:
    """One emission, channel-agnostic."""

    workspace_id: uuid.UUID
    title: str
    body: str
    level: NotifyLevel
    ticket_ref: str | None = None
    repo_id: uuid.UUID | None = None
    dedup_key: str | None = None  # becomes InboxItem.intake_handle
    payload: dict[str, Any] = field(default_factory=dict)
    # Exact InboxItem field overrides for byte-identical emit-site
    # flips: type, summary, headline, category, priority, status,
    # intake_reason, source_table, source_id, auto_resolvable,
    # stale_after. Anything absent is derived.
    inbox_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelResult:
    channel: NotifyChannel
    ok: bool
    skipped: bool = False
    detail: str | None = None


@dataclass(frozen=True)
class NotifyResult:
    results: tuple[ChannelResult, ...]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def for_channel(self, channel: NotifyChannel) -> ChannelResult | None:
        for r in self.results:
            if r.channel == channel:
                return r
        return None


class Channel(Protocol):
    channel: NotifyChannel

    async def emit(
        self,
        session: AsyncSession,
        ctx: NotifyContext,
        routing: ChannelRouting,
    ) -> ChannelResult: ...


# ---------------------------------------------------------------------------
# Inbox channel — the default; wraps the intake field conventions
# ---------------------------------------------------------------------------


class InboxChannel:
    channel = NotifyChannel.INBOX

    async def emit(
        self,
        session: AsyncSession,
        ctx: NotifyContext,
        routing: ChannelRouting,
    ) -> ChannelResult:
        from backend.app.db.models.inbox import InboxItem
        from backend.app.services.inbox.classification import (
            category_from_type,
            priority_for_item,
        )
        from backend.app.services.inbox.headline import derive_headline
        from backend.app.services.inbox.intake import (
            _SUMMARY_MAX_LEN,
            _TITLE_MAX_LEN,
            _truncate,
        )

        o = ctx.inbox_overrides
        item_type = o.get("type") or _LEVEL_TO_INBOX_TYPE[ctx.level]
        # Override title/summary are used VERBATIM (the flipped emit-site
        # already applied its own truncation — byte-identity). The default
        # ctx fields get the intake truncation conventions.
        if "title" in o:
            title = o["title"]
        else:
            title = _truncate(ctx.title, _TITLE_MAX_LEN)
        if "summary" in o:
            summary = o["summary"]
        else:
            summary = _truncate(ctx.body or "", _SUMMARY_MAX_LEN) or None

        kwargs: dict[str, Any] = {
            "workspace_id": ctx.workspace_id,
            "repo_id": ctx.repo_id,
            "type": item_type,
            "title": title,
            "summary": summary,
            "payload": dict(ctx.payload),
            "status": o.get("status", "new"),
            "intake_handle": o.get("intake_handle", ctx.dedup_key),
            "intake_reason": o.get("intake_reason"),
            "source_table": o.get("source_table"),
            "source_id": o.get("source_id"),
        }
        # category / priority / headline / auto_resolvable / stale_after
        # are byte-identity-sensitive: emit-sites that omit them today
        # get the DB server defaults (category='attention', priority=50,
        # headline NULL). So the channel only sets them when the flip
        # passes them explicitly, or when derivation is opted into
        # (derive_classification / derive_headline=True — matching the
        # intake builder's behavior for new, non-flip callers).
        if "category" in o:
            kwargs["category"] = o["category"]
        elif o.get("derive_classification"):
            kwargs["category"] = category_from_type(item_type)
        if "priority" in o:
            kwargs["priority"] = o["priority"]
        elif o.get("derive_classification"):
            kwargs["priority"] = priority_for_item(
                category=str(kwargs.get("category") or category_from_type(item_type)),
                item_type=item_type,
            )
        if "headline" in o:
            kwargs["headline"] = o["headline"]
        elif o.get("derive_headline"):
            kwargs["headline"] = derive_headline(summary=summary, title=title)
        if "auto_resolvable" in o:
            kwargs["auto_resolvable"] = o["auto_resolvable"]
        if "stale_after" in o:
            kwargs["stale_after"] = o["stale_after"]

        session.add(InboxItem(**kwargs))
        return ChannelResult(channel=self.channel, ok=True)


# ---------------------------------------------------------------------------
# Linear-comment channel — context egress onto the ticket thread
# ---------------------------------------------------------------------------


class LinearCommentChannel:
    channel = NotifyChannel.LINEAR

    async def emit(
        self,
        session: AsyncSession,
        ctx: NotifyContext,
        routing: ChannelRouting,
    ) -> ChannelResult:
        if not ctx.ticket_ref:
            return ChannelResult(
                channel=self.channel, ok=True, skipped=True,
                detail="skipped_no_ticket",
            )
        from backend.app.integrations.gateway.tracker import TicketRef
        from backend.app.services.tracker_resolver import resolve_for_workspace

        resolved = await resolve_for_workspace(
            session=session,
            settings=get_settings(),
            workspace_id=ctx.workspace_id,
        )
        if resolved is None:
            return ChannelResult(
                channel=self.channel, ok=True, skipped=True,
                detail="skipped_no_tracker",
            )
        ref = TicketRef(
            kind=resolved.kind, workspace_hint=None, id=str(ctx.ticket_ref)
        )
        body = f"**[Ship {ctx.level.value.upper()}]** {ctx.title}\n\n{ctx.body}"
        await resolved.gateway.comment(ref, body=body)
        return ChannelResult(channel=self.channel, ok=True)


# ---------------------------------------------------------------------------
# Email channel — fail-closed recipient, structured skip
# ---------------------------------------------------------------------------


class EmailChannel:
    channel = NotifyChannel.EMAIL

    async def emit(
        self,
        session: AsyncSession,
        ctx: NotifyContext,
        routing: ChannelRouting,
    ) -> ChannelResult:
        if not routing.email_to:
            # Founder decision: never guess a recipient.
            return ChannelResult(
                channel=self.channel, ok=True, skipped=True,
                detail="skipped_no_email_to",
            )
        from backend.app.services.email.sender import (
            EmailAddress,
            EmailMessage,
            get_email_sender,
        )
        from backend.app.services.email.templates import (
            render_notification_email,
        )

        rendered = render_notification_email(
            subject=ctx.title,
            body_markdown=ctx.body,
            level=ctx.level.value,
            ticket_ref=ctx.ticket_ref,
        )
        result = await get_email_sender().send(
            EmailMessage(
                to=EmailAddress(email=routing.email_to),
                subject=rendered.subject,
                html=rendered.html,
                text=rendered.text,
                tags={
                    "kind": "engine_notification",
                    "level": ctx.level.value,
                    "workspace": str(ctx.workspace_id),
                },
            )
        )
        if not result.sent:
            return ChannelResult(
                channel=self.channel, ok=False,
                detail=result.detail or f"send failed ({result.provider})",
            )
        return ChannelResult(channel=self.channel, ok=True)


_CHANNEL_IMPLS: dict[NotifyChannel, Channel] = {
    NotifyChannel.INBOX: InboxChannel(),
    NotifyChannel.LINEAR: LinearCommentChannel(),
    NotifyChannel.EMAIL: EmailChannel(),
}


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


async def notify(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    title: str,
    body: str,
    level: NotifyLevel,
    ticket_ref: str | None = None,
    repo_id: uuid.UUID | None = None,
    dedup_key: str | None = None,
    payload: dict[str, Any] | None = None,
    inbox_overrides: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> NotifyResult:
    """Route one engine emission to the workspace's channels.

    Never raises. The Inbox channel is part of every routing list (see
    :mod:`notify_config`), so even a fully broken linear/email setup
    still lands the letter where it lands today.
    """
    ctx = NotifyContext(
        workspace_id=workspace_id,
        title=title,
        body=body,
        level=level,
        ticket_ref=ticket_ref,
        repo_id=repo_id,
        dedup_key=dedup_key,
        payload=dict(payload or {}),
        inbox_overrides=dict(inbox_overrides or {}),
    )
    try:
        workspace = await session.get(Workspace, workspace_id)
    except Exception as exc:  # noqa: BLE001 — emit path must not die
        logger.warning("notify: workspace load failed ws=%s: %s", workspace_id, exc)
        workspace = None
    if workspace is None:
        routing = ChannelRouting()
    else:
        routing = get_channel_routing(workspace, settings=settings)

    results: list[ChannelResult] = []
    for channel in routing.channels_for(level):
        impl = _CHANNEL_IMPLS[channel]
        try:
            results.append(await impl.emit(session, ctx, routing))
        except Exception as exc:  # noqa: BLE001 — one channel must not sink the rest
            logger.warning(
                "notify: channel=%s failed ws=%s ticket=%s: %s",
                channel.value, workspace_id, ctx.ticket_ref, exc,
            )
            results.append(
                ChannelResult(
                    channel=channel, ok=False, detail=str(exc)[:300]
                )
            )
    return NotifyResult(results=tuple(results))


__all__ = [
    "Channel",
    "ChannelResult",
    "EmailChannel",
    "InboxChannel",
    "LinearCommentChannel",
    "NotifyContext",
    "NotifyLevel",
    "NotifyResult",
    "notify",
]
