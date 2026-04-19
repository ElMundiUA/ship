"""End-to-end tests for ``/v1/integrations/github/*`` and the webhook.

Covers:

- ``install/start`` returns a GitHub install URL only for workspace admins.
- ``install/callback`` round-trips a state token and persists a
  :class:`GitHubInstallation` row.
- The webhook route rejects unsigned/wrong-signed deliveries with 401.
- A signed ``installation`` event with action ``deleted`` removes the row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select


WEBHOOK_SECRET = "wh_test_secret"


@pytest.fixture
def github_app_env(monkeypatch: pytest.MonkeyPatch):
    """Configure the minimum env vars for the GitHub App routes.

    The slug + webhook secret are enough for install URL generation and
    webhook signature verification; we don't exercise the App-JWT path
    here because it requires a private key, which the dedicated
    ``test_github_app_jwt.py`` covers.
    """
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    # Bust the lru_cache so the route picks up the new env vars.
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


@pytest.mark.asyncio
async def test_install_start_requires_admin(
    v1_client, seed_workspace, github_app_env
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        "/v1/integrations/github/install/start",
        headers=headers,
        params={"workspace_id": str(workspace.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["install_url"].startswith(
        "https://github.com/apps/ship-test/installations/new?"
    )
    # State is echoed back so the console can sanity-check on return.
    assert body["state"]


@pytest.mark.asyncio
async def test_install_start_404_for_non_member(
    v1_client, seed_user_with_token, github_app_env
) -> None:
    """Strangers can't even discover that a workspace exists."""
    _, raw = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        "/v1/integrations/github/install/start",
        headers=headers,
        params={"workspace_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_install_callback_persists_row(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    from backend.app.db.models.integrations import GitHubInstallation
    from backend.app.integrations.github.oauth import build_install_state

    _, raw, workspace = seed_workspace
    # Mint state directly so we don't depend on the start-endpoint
    # response in this test.
    from backend.app.core.config import get_settings

    state = build_install_state(workspace.id, settings=get_settings())

    response = await v1_client.get(
        "/v1/integrations/github/install/callback",
        params={
            "state": state,
            "installation_id": "987654",
            "setup_action": "install",
        },
        follow_redirects=False,
    )
    # Callback issues a redirect into the console onboarding wizard.
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    # The callback intentionally lands back on the github step so the
    # success banner inside ``GitHubStep`` is the first thing the user
    # sees; the in-banner CTA is what walks them to workflows.
    assert location.startswith("https://ship.test/onboarding?step=github")
    assert "github=installed" in location

    row = (
        await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 987654
            )
        )
    ).scalar_one()
    assert row.workspace_id == workspace.id
    assert row.installed_at is not None


@pytest.mark.asyncio
async def test_install_callback_rejects_bad_state(
    v1_client, github_app_env
) -> None:
    response = await v1_client.get(
        "/v1/integrations/github/install/callback",
        params={
            "state": "not-a-jwt",
            "installation_id": "1",
            "setup_action": "install",
        },
        follow_redirects=False,
    )
    # We bounce back into the wizard with a friendly error code rather
    # than 400-ing the browser tab — much better UX. The redirect points
    # at the github step so the user can re-trigger the install from the
    # same screen they already understand.
    assert response.status_code in (302, 303, 307)
    location = response.headers["location"]
    assert "/onboarding" in location
    assert "step=github" in location
    assert "error=bad_state" in location


@pytest.mark.asyncio
async def test_webhook_rejects_missing_signature(
    v1_client, github_app_env
) -> None:
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=b'{"action":"created"}',
        headers={"X-GitHub-Event": "installation"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_rejects_wrong_signature(
    v1_client, github_app_env
) -> None:
    body = b'{"action":"created"}'
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_deleted_event_removes_row(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    """Uninstalling on GitHub clears our row so re-install starts clean."""
    from backend.app.db.models.integrations import GitHubInstallation

    _, _, workspace = seed_workspace
    row = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=42,
    )
    db_session.add(row)
    await db_session.flush()

    payload = {
        "action": "deleted",
        "installation": {"id": 42, "account": {"login": "acme", "type": "Organization"}},
    }
    body = json.dumps(payload).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200, response.text

    remaining = (
        await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 42
            )
        )
    ).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_webhook_created_event_backfills_account_metadata(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    from backend.app.db.models.integrations import GitHubInstallation

    _, _, workspace = seed_workspace
    db_session.add(
        GitHubInstallation(
            workspace_id=workspace.id,
            installation_id=77,
        )
    )
    await db_session.flush()

    payload = {
        "action": "created",
        "installation": {
            "id": 77,
            "account": {"id": 1, "login": "acme", "type": "Organization"},
            "repository_selection": "selected",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    response = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert response.status_code == 200, response.text

    # Same session is used by the route (dependency override) and by the
    # test, so the route's flush is already visible. Expire to force a
    # SELECT instead of serving from the identity map.
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 77
            )
        )
    ).scalar_one()
    assert row.account_login == "acme"
    assert row.account_type == "Organization"
    assert row.repository_selection == "selected"
