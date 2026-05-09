"""Linear OAuth token refresh — cadence math + bundle parsing.

Schema-level coverage only. The DB-persistence + Linear API roundtrip
paths are exercised by the existing OAuth callback test
(test_v1_linear_oauth) and the live ``linear_token_refresh_tick`` cron
in production.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.integrations.linear.oauth import LinearTokenBundle
from backend.app.services.linear_token_refresh import _is_due_for_refresh


def test_null_last_rotated_is_due() -> None:
    """A just-installed credential (never rotated) starts the cycle on
    the next tick. Treated as eligible so a brand-new install isn't
    silently skipped because ``last_rotated_at`` was NULL."""
    assert _is_due_for_refresh(None, now=datetime.now(timezone.utc)) is True


def test_recently_rotated_is_not_due() -> None:
    """A token rotated 5 minutes ago is far from the 6h threshold —
    leave it alone."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(minutes=5)
    assert _is_due_for_refresh(fresh, now=now) is False


def test_past_threshold_is_due() -> None:
    """Default 6h max age — anything rotated longer ago refreshes."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    stale = now - timedelta(hours=7)
    assert _is_due_for_refresh(stale, now=now) is True


def test_at_threshold_is_due() -> None:
    """Boundary inclusive — exactly 6h since last rotation triggers
    refresh; avoids a tick-aligned race where the next tick lands a
    second after the boundary and we'd skip another whole period."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    edge = now - timedelta(hours=6)
    assert _is_due_for_refresh(edge, now=now) is True


def test_custom_max_age_overrides_default() -> None:
    """Tighter cadence (e.g., for an integration test) is honored."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    forty_min = now - timedelta(minutes=40)
    assert (
        _is_due_for_refresh(forty_min, now=now, max_age=timedelta(hours=1))
        is False
    )
    assert (
        _is_due_for_refresh(forty_min, now=now, max_age=timedelta(minutes=30))
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
    tokens still parses cleanly — the cron sweep skips that install
    instead of crashing."""
    bundle = LinearTokenBundle(
        access_token="lin_oauth_a",
        token_type="Bearer",
        scope="read,write",
        expires_in=3600,
    )
    assert bundle.refresh_token is None
