"""Settings validation tests.

Most of the Settings surface is exercised indirectly by the route
tests; these cover the model-level invariants that are too easy to
regress when adding new env vars (a misconfigured prod boot is much
worse than a noisy unit test failure).
"""

from __future__ import annotations

import pytest

from backend.app.core.config import Settings


def test_cloud_mode_rejects_localhost_public_url() -> None:
    """Cloud bootstrap fails fast when SHIP_PUBLIC_URL points at dev defaults.

    Otherwise the install callback / OAuth dance silently bounces real
    users to ``http://localhost:...`` — the WOW onboarding ended on a
    "site can't be reached" tab during the pilot rollout exactly because
    of this.
    """
    with pytest.raises(ValueError, match="SHIP_PUBLIC_URL"):
        Settings(
            SHIP_AUTH_MODE="auth0",
            SHIP_PUBLIC_URL="http://localhost:8100",
            SHIP_CONSOLE_URL="https://app.ship.test",
            AUTH0_DOMAIN="ship.test.com",
            AUTH0_AUDIENCE="https://api.ship.test",
        )


def test_cloud_mode_rejects_localhost_console_url() -> None:
    with pytest.raises(ValueError, match="SHIP_CONSOLE_URL"):
        Settings(
            SHIP_AUTH_MODE="auth0",
            SHIP_PUBLIC_URL="https://api.ship.test",
            SHIP_CONSOLE_URL="http://localhost:3001",
            AUTH0_DOMAIN="ship.test.com",
            AUTH0_AUDIENCE="https://api.ship.test",
        )


def test_local_mode_keeps_dev_defaults() -> None:
    """``make up`` workflow: localhost defaults stay valid in local mode."""
    settings = Settings(SHIP_AUTH_MODE="local")
    assert "localhost" in settings.public_url
    assert "localhost" in settings.console_url


def test_cloud_mode_accepts_real_hostnames() -> None:
    """Sanity: a properly configured cloud Settings still constructs."""
    settings = Settings(
        SHIP_AUTH_MODE="auth0",
        SHIP_PUBLIC_URL="https://api.ship.test",
        SHIP_CONSOLE_URL="https://app.ship.test",
        AUTH0_DOMAIN="ship.test.com",
        AUTH0_AUDIENCE="https://api.ship.test",
    )
    assert settings.public_url.startswith("https://")
    assert settings.console_url.startswith("https://")


def test_anthropic_vendor_swaps_default_openai_model_ids() -> None:
    """Anthropic + key + default ``gpt-*`` env → Claude ids (avoids 404 model)."""
    settings = Settings(
        SHIP_AUTH_MODE="local",
        AGENT_VENDOR="anthropic",
        ANTHROPIC_API_KEY="sk-ant-api03-test",
    )
    assert settings.agent_model_main.startswith("claude-")
    assert settings.agent_model_fast.startswith("claude-")


def test_anthropic_vendor_respects_explicit_claude_ids() -> None:
    settings = Settings(
        SHIP_AUTH_MODE="local",
        AGENT_VENDOR="anthropic",
        ANTHROPIC_API_KEY="sk-ant-api03-test",
        AGENT_MODEL_MAIN="claude-3-5-haiku-20241022",
        AGENT_MODEL_FAST="claude-3-5-haiku-20241022",
    )
    assert settings.agent_model_main == "claude-3-5-haiku-20241022"
    assert settings.agent_model_fast == "claude-3-5-haiku-20241022"


def test_anthropic_vendor_without_key_keeps_openai_default_models() -> None:
    """OpenAI fallback path still sees ``gpt-*`` defaults."""
    settings = Settings(
        SHIP_AUTH_MODE="local",
        AGENT_VENDOR="anthropic",
        ANTHROPIC_API_KEY="",
        OPENAI_API_KEY="sk-test",
    )
    assert settings.agent_model_main == "gpt-4o"
    assert settings.agent_model_fast == "gpt-4o-mini"
