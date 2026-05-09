"""Linear OAuth token refresh — pre-emptive expiry swap + parsing.

Schema-level tests only — the integration-shaped DB persistence is
exercised in the existing OAuth callback test (test_v1_linear_oauth) and
the live refresh path is covered by the smoke runbook.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.integrations.linear.oauth import LinearTokenBundle
from backend.app.services.linear_token_refresh import _is_due_for_refresh


def test_no_expiry_means_not_due() -> None:
    """Tokens without an expiry recorded (PATs, legacy installs) should
    never auto-refresh — those are operator-managed."""
    assert _is_due_for_refresh(None, now=datetime.now(timezone.utc)) is False


def test_far_future_expiry_is_not_due() -> None:
    """A token expiring in days has plenty of life — leave alone."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = now + timedelta(days=3)
    assert _is_due_for_refresh(later, now=now) is False


def test_inside_lead_time_is_due() -> None:
    """Default 5min lead — anything inside it should refresh now."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    soon = now + timedelta(minutes=2)
    assert _is_due_for_refresh(soon, now=now) is True


def test_at_lead_time_boundary_is_due() -> None:
    """Boundary inclusive — exactly 5min away triggers refresh.

    Avoids a hypothetical race where the token expires between
    "still safe" and "make the call" window.
    """
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    edge = now + timedelta(minutes=5)
    assert _is_due_for_refresh(edge, now=now) is True


def test_already_expired_is_due() -> None:
    """A token that's already past expiry refreshes — refresh_token
    is the only material we have, the access is dead."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    past = now - timedelta(minutes=1)
    assert _is_due_for_refresh(past, now=now) is True


def test_custom_lead_time_overrides_default() -> None:
    """Tighter lead time (e.g., for an integration test) is honored."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    soon = now + timedelta(seconds=30)
    assert (
        _is_due_for_refresh(soon, now=now, lead_time=timedelta(seconds=10))
        is False
    )
    assert (
        _is_due_for_refresh(soon, now=now, lead_time=timedelta(minutes=1))
        is True
    )


def test_token_bundle_carries_refresh_token() -> None:
    """The dataclass extension is what makes refresh persistence possible."""
    bundle = LinearTokenBundle(
        access_token="lin_oauth_a",
        token_type="Bearer",
        scope="read,write",
        expires_in=3600,
        refresh_token="lin_refresh_x",
    )
    assert bundle.refresh_token == "lin_refresh_x"


def test_token_bundle_refresh_token_optional_for_legacy() -> None:
    """An OAuth install whose Linear app config doesn't issue refresh
    tokens still parses cleanly — the refresh service just degrades to
    "stale-token + log" instead of crashing."""
    bundle = LinearTokenBundle(
        access_token="lin_oauth_a",
        token_type="Bearer",
        scope="read,write",
        expires_in=3600,
    )
    assert bundle.refresh_token is None
