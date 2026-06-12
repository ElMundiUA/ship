"""Channel-routing config + global kill-switch (ELS-219).

Pins the founder defaults: inbox-only everywhere until a workspace
opts in AND the global ``SHIP_NOTIFY_CHANNELS`` switch is on; malformed
settings fail closed; missing ``email_to`` resolves to None (the email
channel later turns that into a structured skip, never a guess).
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.app.services.notify_config import (
    ChannelRouting,
    NotifyChannel,
    NotifyLevel,
    get_channel_routing,
)


def _ws(settings_blob: dict | None) -> SimpleNamespace:
    return SimpleNamespace(id="ws-test", settings=settings_blob)


def _settings(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(notification_channels_enabled=enabled)


INBOX_ONLY = (NotifyChannel.INBOX,)


def test_empty_settings_resolve_inbox_only_all_levels() -> None:
    routing = get_channel_routing(_ws(None), settings=_settings(True))
    for level in NotifyLevel:
        assert routing.channels_for(level) == INBOX_ONLY
    assert routing.email_to is None


def test_kill_switch_off_forces_inbox_only_despite_optin() -> None:
    blob = {
        "notifications": {
            "email_to": "ops@example.com",
            "channels": {"blocker": ["inbox", "linear", "email"]},
        }
    }
    routing = get_channel_routing(_ws(blob), settings=_settings(False))
    assert routing.channels_for(NotifyLevel.BLOCKER) == INBOX_ONLY
    # email_to still resolves for audit/debug display.
    assert routing.email_to == "ops@example.com"


def test_optin_with_switch_on_yields_exact_lists() -> None:
    blob = {
        "notifications": {
            "email_to": "ops@example.com",
            "channels": {
                "info": ["inbox"],
                "blocker": ["inbox", "linear", "email"],
            },
        }
    }
    routing = get_channel_routing(_ws(blob), settings=_settings(True))
    assert routing.channels_for(NotifyLevel.INFO) == INBOX_ONLY
    # action not configured → default inbox-only
    assert routing.channels_for(NotifyLevel.ACTION) == INBOX_ONLY
    assert routing.channels_for(NotifyLevel.BLOCKER) == (
        NotifyChannel.INBOX,
        NotifyChannel.LINEAR,
        NotifyChannel.EMAIL,
    )


def test_inbox_always_present_even_when_omitted() -> None:
    """A workspace asking for linear-only still gets inbox first — a
    config typo can never silently drop an engine emission."""
    blob = {"notifications": {"channels": {"blocker": ["linear"]}}}
    routing = get_channel_routing(_ws(blob), settings=_settings(True))
    assert routing.channels_for(NotifyLevel.BLOCKER) == (
        NotifyChannel.INBOX,
        NotifyChannel.LINEAR,
    )


def test_malformed_settings_fail_closed_not_raise() -> None:
    cases = [
        {"notifications": "yes please"},
        {"notifications": {"channels": "all"}},
        {"notifications": {"channels": {"blocker": "linear"}}},
        {"notifications": {"channels": {"blocker": ["telegraph", 42]}}},
    ]
    for blob in cases:
        routing = get_channel_routing(_ws(blob), settings=_settings(True))
        assert routing.channels_for(NotifyLevel.BLOCKER) == INBOX_ONLY, blob


def test_missing_email_to_is_none_never_guessed() -> None:
    blob = {"notifications": {"email_to": "   "}}
    routing = get_channel_routing(_ws(blob), settings=_settings(True))
    assert routing.email_to is None


def test_default_routing_dataclass_is_inbox_only() -> None:
    routing = ChannelRouting()
    for level in NotifyLevel:
        assert routing.channels_for(level) == INBOX_ONLY
