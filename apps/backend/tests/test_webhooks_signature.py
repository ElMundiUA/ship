"""Signature verification for webhook handlers.

Tests that webhook endpoints for GitHub App and Linear reject forged
signatures with 401, while accepting valid signatures.

Coverage:
- GitHub App webhook: missing signature → 401; wrong signature → 401;
  tampered body → 401; valid signature → 200.
- Linear webhook: not yet implemented (marked as P0 finding in T07).
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest


GITHUB_WEBHOOK_SECRET = "wh_test_secret"


@pytest.fixture
def github_webhook_env(monkeypatch: pytest.MonkeyPatch):
    """Configure env vars for GitHub webhook signature verification."""
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", GITHUB_WEBHOOK_SECRET)
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign_github(body: bytes, secret: str = GITHUB_WEBHOOK_SECRET) -> str:
    """Generate GitHub HMAC-SHA256 signature."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


# ==============================================================================
# GitHub App Webhook Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_github_webhook_accepts_valid_signature(
    v1_client, github_webhook_env
) -> None:
    """Valid GitHub signature with correct body → 200 OK."""
    payload = {
        "action": "created",
        "installation": {"id": 123, "account": {"login": "acme"}},
    }
    body = json.dumps(payload).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _sign_github(body),
        },
    )
    # Webhook accepts the delivery and returns 200.
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["event"] == "installation"


@pytest.mark.asyncio
async def test_github_webhook_rejects_missing_signature(
    v1_client, github_webhook_env
) -> None:
    """Missing X-Hub-Signature-256 header → 401 Unauthorized."""
    payload = {"action": "created"}
    body = json.dumps(payload).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "installation"},
        # Explicitly no X-Hub-Signature-256 header
    )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_github_webhook_rejects_wrong_signature(
    v1_client, github_webhook_env
) -> None:
    """Wrong signature (signed with different secret) → 401 Unauthorized."""
    payload = {"action": "created"}
    body = json.dumps(payload).encode("utf-8")
    # Sign with wrong secret
    wrong_signature = _sign_github(body, secret="wrong_secret")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": wrong_signature,
        },
    )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_github_webhook_rejects_tampered_body(
    v1_client, github_webhook_env
) -> None:
    """Body tampered after signing → 401 Unauthorized.

    The signature is computed over the original body. If the body changes
    (even a single byte), the signature no longer matches.
    """
    original_body = b'{"action":"created"}'
    signature = _sign_github(original_body)

    # Send a different body with the signature from the original.
    tampered_body = b'{"action":"deleted"}'
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=tampered_body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": signature,
        },
    )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_github_webhook_rejects_malformed_signature_header(
    v1_client, github_webhook_env
) -> None:
    """Malformed signature header (missing sha256= prefix) → 401."""
    payload = {"action": "created"}
    body = json.dumps(payload).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            # Valid hex but missing "sha256=" prefix
            "X-Hub-Signature-256": "0" * 64,
        },
    )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_github_webhook_rejects_when_secret_unconfigured(
    v1_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backend misconfigured (no secret env var) → all webhooks rejected with 401.

    This is a P0 safety requirement: a stray deploy with a missing
    GITHUB_APP_WEBHOOK_SECRET should NEVER accept webhook traffic.
    """
    # Unset the secret
    monkeypatch.delenv("GITHUB_APP_WEBHOOK_SECRET", raising=False)
    from backend.app.core.config import get_settings

    get_settings.cache_clear()

    payload = {"action": "created"}
    body = json.dumps(payload).encode("utf-8")
    valid_signature = _sign_github(body, secret=GITHUB_WEBHOOK_SECRET)

    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": valid_signature,
        },
    )
    # Even a validly-signed payload is rejected when the backend has no secret.
    assert response.status_code == 401, response.text

    get_settings.cache_clear()


# ==============================================================================
# Linear Webhook Tests (P0 Finding)
# ==============================================================================


@pytest.mark.asyncio
async def test_linear_webhook_not_implemented() -> None:
    """Linear webhook endpoint does not exist yet (P0 finding for T07).

    Task: E07-T07 requires Linear webhook signature validation.
    Current state: Linear OAuth install exists, but no webhook handler.
    Implementation path:
    1. Add POST /v1/webhooks/linear endpoint to linear_oauth.py.
    2. Implement Linear's HMAC signature verification (differs from GitHub).
    3. Add similar 401-rejection tests (missing sig, wrong sig, tampered body).

    This test serves as documentation that the work is outstanding.
    """
    # Placeholder: Linear webhook handler not yet implemented.
    # When implemented, add analogous tests to verify signature rejection.
    pass
