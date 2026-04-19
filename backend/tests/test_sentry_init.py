"""Sentry integration smoke tests (RFC-0006 phase 2.1).

Goals:
- An empty ``SENTRY_DSN`` keeps the SDK off — laptops, CI, and the test
  database fixture must never accidentally ship events to a real project.
- A valid DSN flips the cached "initialised" flag to True so subsequent
  calls become idempotent.
- The header scrubber strips bearer tokens and cookies before they leave
  the host.
"""

from __future__ import annotations

import pytest

from backend.app.core import sentry as sentry_module
from backend.app.core.config import get_settings


@pytest.fixture(autouse=True)
def _reset_sentry_state(monkeypatch):
    """Clear cached settings + the "already initialised" guard between tests."""
    sentry_module._reset_for_tests()
    get_settings.cache_clear()
    yield
    sentry_module._reset_for_tests()
    get_settings.cache_clear()


def test_init_is_noop_when_dsn_is_empty(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert sentry_module.init_sentry(service_name="ship-server") is False


def test_init_returns_true_when_dsn_set(monkeypatch):
    # Use the documented Sentry "no-op" DSN so we don't accidentally hit
    # the network during tests; the SDK accepts the format and just keeps
    # everything in memory until flushed.
    monkeypatch.setenv(
        "SENTRY_DSN",
        "https://public@o0.ingest.sentry.io/0",
    )
    assert sentry_module.init_sentry(service_name="ship-server") is True
    # Second call is idempotent.
    assert sentry_module.init_sentry(service_name="ship-server") is True


def test_before_send_strips_authorization_header():
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer ship_pat_secret_value_should_not_leak",
                "Cookie": "appSession=abc123",
                "User-Agent": "ship-cli/1.2.3",
            },
            "cookies": {"appSession": "abc123"},
        }
    }
    cleaned = sentry_module._before_send(event, {})
    headers = cleaned["request"]["headers"]
    assert headers["Authorization"] == "[scrubbed]"
    assert headers["Cookie"] == "[scrubbed]"
    assert headers["User-Agent"] == "ship-cli/1.2.3"
    assert cleaned["request"]["cookies"] == "[scrubbed]"
