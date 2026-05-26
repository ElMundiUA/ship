"""Linear OAuth scopes — default must include ``admin``.

``admin`` is what Linear requires for ``webhookCreate`` /
``webhookDelete`` GraphQL mutations. Without it the programmatic
webhook provisioning endpoint (``POST .../webhook/provision``)
returns ``Invalid role: admin required`` and operators fall back to
pasting URLs in the Linear settings UI by hand.

This test pins the default so a future "tighten OAuth scopes"
refactor can't quietly drop ``admin`` and re-break programmatic
provisioning.
"""

from __future__ import annotations

from backend.app.core.config import Settings


def test_default_includes_admin_for_webhook_provisioning() -> None:
    settings = Settings()
    scopes = {s.strip() for s in settings.linear_oauth_scopes.split(",")}
    assert "admin" in scopes, (
        "LINEAR_OAUTH_SCOPES default must include 'admin' so "
        "webhookCreate / webhookDelete mutations succeed in the "
        "programmatic provisioning endpoint."
    )


def test_default_keeps_read_write_for_adapter_path() -> None:
    # The tracker_adapter path (list issues, post comments) still
    # needs read+write. Pin them too so a future scope refactor
    # can't shrink to admin-only and break the day-to-day cascade.
    settings = Settings()
    scopes = {s.strip() for s in settings.linear_oauth_scopes.split(",")}
    assert {"read", "write"}.issubset(scopes)
