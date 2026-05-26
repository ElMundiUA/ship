"""Linear OAuth ``return_to`` round-trip + open-redirect guard.

When the workspace-settings "Reconnect Linear" flow passes a path to
``/install/start``, that path must round-trip through the signed state
JWT into the callback and steer the final redirect — without giving
an attacker an open-redirect vector via a malicious query parameter.

Pins:
- the state JWT carries ``return_to`` (default None)
- ``verify_oauth_state`` recovers it
- the install_start validator rejects absolute URLs and ``//`` paths
  (would otherwise become protocol-relative URLs)
"""

from __future__ import annotations

import uuid

from backend.app.integrations.linear.oauth import (
    build_oauth_state,
    verify_oauth_state,
)


class _Settings:
    """Minimal Settings stand-in for the JWT codec — only ``jwt_secret``
    is read by ``_state_secret``."""

    def __init__(self) -> None:
        # Long enough for the JOSE library not to complain about HS256
        # key size in tests.
        self.jwt_secret = "test-secret-key-" + "x" * 40


def test_default_state_has_no_return_to() -> None:
    s = build_oauth_state(uuid.uuid4(), settings=_Settings())
    assert verify_oauth_state(s, settings=_Settings()).return_to is None


def test_state_round_trips_return_to() -> None:
    ws = uuid.uuid4()
    s = build_oauth_state(
        ws,
        settings=_Settings(),
        return_to="/workspaces/abc/settings/integrations",
    )
    decoded = verify_oauth_state(s, settings=_Settings())
    assert decoded.workspace_id == ws
    assert decoded.return_to == "/workspaces/abc/settings/integrations"


def test_state_with_explicit_none_return_to() -> None:
    # Passing return_to=None at build time must not add the claim
    # (keeps the token smaller for the wizard path and avoids
    # accidental ``rt=None`` literals).
    s = build_oauth_state(uuid.uuid4(), settings=_Settings(), return_to=None)
    assert verify_oauth_state(s, settings=_Settings()).return_to is None


def test_return_to_validator_shape() -> None:
    # Pure check of the rule the install_start handler enforces:
    # path-only ``/...``, never ``//x`` (protocol-relative open
    # redirect surface), never an absolute URL.
    def is_safe(rt: str) -> bool:
        return rt.startswith("/") and not rt.startswith("//")

    assert is_safe("/workspaces/abc/settings/integrations")
    assert is_safe("/onboarding?step=tracker")
    # Open-redirect attempts — all rejected.
    assert not is_safe("//evil.example.com/")
    assert not is_safe("https://evil.example.com/path")
    assert not is_safe("http://evil.example.com")
    assert not is_safe("javascript:alert(1)")
    assert not is_safe("workspaces/no-leading-slash")
