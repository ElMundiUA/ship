"""Tests for ``/v1/auth/*`` (RFC-0006).

Covers local-mode signup/login (the laptop / self-hosted bootstrap path) and
PAT minting via the resulting session JWT. Exercised end-to-end through the
ASGI transport so we hit the real password hasher, JWT encoder and DB.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_signup_then_login_returns_session_jwt(v1_client) -> None:
    email = "alice@example.com"
    password = "correct horse battery staple"
    signup = await v1_client.post(
        "/v1/auth/local/signup",
        json={"email": email, "password": password, "display_name": "Alice"},
    )
    assert signup.status_code == 201, signup.text
    body = signup.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == email
    assert body["access_token"]

    login = await v1_client.post(
        "/v1/auth/local/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["email"] == email


async def test_signup_duplicate_email_is_409(v1_client) -> None:
    payload = {"email": "dup@example.com", "password": "password-12345"}
    first = await v1_client.post("/v1/auth/local/signup", json=payload)
    assert first.status_code == 201, first.text
    second = await v1_client.post("/v1/auth/local/signup", json=payload)
    assert second.status_code == 409


async def test_login_wrong_password_is_401(v1_client) -> None:
    await v1_client.post(
        "/v1/auth/local/signup",
        json={"email": "bob@example.com", "password": "rightpassword1"},
    )
    bad = await v1_client.post(
        "/v1/auth/local/login",
        json={"email": "bob@example.com", "password": "wrongpassword1"},
    )
    assert bad.status_code == 401
    # Generic message — we do not leak whether the email exists.
    assert "invalid" in bad.json()["detail"].lower()


async def test_session_jwt_authorises_subsequent_calls(v1_client) -> None:
    signup = await v1_client.post(
        "/v1/auth/local/signup",
        json={"email": "carol@example.com", "password": "password-9999"},
    )
    jwt_token = signup.json()["access_token"]
    me = await v1_client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {jwt_token}"}
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "carol@example.com"


async def test_mint_pat_via_session_jwt(v1_client) -> None:
    signup = await v1_client.post(
        "/v1/auth/local/signup",
        json={"email": "dan@example.com", "password": "password-2222"},
    )
    jwt_token = signup.json()["access_token"]

    minted = await v1_client.post(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"name": "ci-token", "scopes": ["workspace:read"]},
    )
    assert minted.status_code == 201, minted.text
    secret = minted.json()["secret"]
    assert secret.startswith("ship_pat_")

    # The freshly minted PAT should immediately authorise further calls.
    me = await v1_client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {secret}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "dan@example.com"

    # And the listing endpoint must surface the new token (without the secret).
    listed = await v1_client.get(
        "/v1/auth/tokens", headers={"Authorization": f"Bearer {jwt_token}"}
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert "secret" not in rows[0]
    assert rows[0]["name"] == "ci-token"


async def test_revoke_token_invalidates_secret(v1_client) -> None:
    signup = await v1_client.post(
        "/v1/auth/local/signup",
        json={"email": "eve@example.com", "password": "password-3333"},
    )
    jwt_token = signup.json()["access_token"]

    minted = await v1_client.post(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"name": "throwaway"},
    )
    secret = minted.json()["secret"]
    token_id = minted.json()["id"]

    # Sanity: the secret works.
    ok = await v1_client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {secret}"}
    )
    assert ok.status_code == 200

    revoked = await v1_client.delete(
        f"/v1/auth/tokens/{token_id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert revoked.status_code == 204, revoked.text

    # Same secret now bounces with a 401.
    after = await v1_client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {secret}"}
    )
    assert after.status_code == 401

    # And the list view hides revoked rows.
    listed = await v1_client.get(
        "/v1/auth/tokens", headers={"Authorization": f"Bearer {jwt_token}"}
    )
    assert listed.json() == []


async def test_revoke_token_404_for_other_user(v1_client) -> None:
    """Trying to revoke a sibling's token must 404, not 204."""
    a = await v1_client.post(
        "/v1/auth/local/signup",
        json={"email": "owner-a@example.com", "password": "password-aaaa"},
    )
    b = await v1_client.post(
        "/v1/auth/local/signup",
        json={"email": "owner-b@example.com", "password": "password-bbbb"},
    )
    jwt_a = a.json()["access_token"]
    jwt_b = b.json()["access_token"]

    minted = await v1_client.post(
        "/v1/auth/tokens",
        headers={"Authorization": f"Bearer {jwt_a}"},
        json={"name": "owned-by-a"},
    )
    token_id = minted.json()["id"]

    res = await v1_client.delete(
        f"/v1/auth/tokens/{token_id}",
        headers={"Authorization": f"Bearer {jwt_b}"},
    )
    assert res.status_code == 404
