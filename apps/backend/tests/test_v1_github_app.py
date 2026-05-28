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
    # The callback jumps the user straight to the repo picker — the
    # "GitHub App installed" success banner renders above the picker so
    # they know it worked, but they don't need an extra "Pick repos →"
    # click before they can do anything.
    assert location.startswith("https://ship.test/onboarding?step=repos")
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
async def test_install_callback_tolerates_bad_state_for_unknown_install(
    v1_client, github_app_env
) -> None:
    """Forged/expired ``state`` + unknown installation → wizard banner.

    We can't trust the bad state, and we have no install row to fall
    back on, so the only safe move is to ask the user to start over
    through the wizard (which mints a fresh state token tied to a real
    workspace).
    """
    response = await v1_client.get(
        "/v1/integrations/github/install/callback",
        params={
            "state": "not-a-jwt",
            "installation_id": "1",
            "setup_action": "install",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    location = response.headers["location"]
    assert "/onboarding" in location
    assert "step=github" in location
    assert "error=missing_state" in location


@pytest.mark.asyncio
async def test_install_callback_without_state_redirects_to_wizard(
    v1_client, github_app_env
) -> None:
    """No state + unknown installation → wizard banner (not 422).

    GitHub omits ``state`` whenever the user enters the install picker
    outside of our wizard (e.g. clicking "Configure" on the App page).
    The callback used to 422 on the missing query param; we now degrade
    gracefully into the same "start over" prompt as the bad-state case.
    """
    response = await v1_client.get(
        "/v1/integrations/github/install/callback",
        params={
            "installation_id": "424242",
            "setup_action": "update",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    location = response.headers["location"]
    assert "step=github" in location
    assert "error=missing_state" in location


@pytest.mark.asyncio
async def test_install_callback_without_state_refreshes_known_install(
    v1_client, db_session, seed_workspace, github_app_env
) -> None:
    """No state + known installation → idempotent refresh, no workspace move.

    Re-confirming repo selection from the GitHub UI shouldn't re-bind
    the install to a different workspace (we have no signal to tell us
    which one) — we just touch ``updated_at`` and bounce the user into
    the picker so they can carry on.
    """
    from datetime import datetime, timezone

    from backend.app.db.models.integrations import GitHubInstallation

    _, _, workspace = seed_workspace
    db_session.add(
        GitHubInstallation(
            workspace_id=workspace.id,
            installation_id=555111,
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    response = await v1_client.get(
        "/v1/integrations/github/install/callback",
        params={"installation_id": "555111", "setup_action": "update"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    location = response.headers["location"]
    assert "step=repos" in location
    assert "github=installed" in location

    row = (
        await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 555111
            )
        )
    ).scalar_one()
    # Workspace binding stays put — the GitHub redirect carries no
    # signal that would justify re-binding.
    assert row.workspace_id == workspace.id


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


@pytest.mark.asyncio
async def test_install_candidates_empty_without_sibling(
    v1_client, seed_workspace, github_app_env
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.get(
        "/v1/integrations/github/install/candidates",
        headers=headers,
        params={"workspace_id": str(workspace.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["installations"] == []


@pytest.mark.asyncio
async def test_install_candidates_lists_sibling_install(
    v1_client, seed_workspace, db_session, github_app_env
) -> None:
    from datetime import datetime, timezone

    from backend.app.db.models.integrations import GitHubInstallation
    from backend.app.db.models.tenancy import Org, OrgMember, Workspace, WorkspaceMember

    user, raw, ws_a = seed_workspace
    other_org = Org(slug=f"o-{uuid.uuid4().hex[:6]}", name="x", plan="free")
    db_session.add(other_org)
    await db_session.flush()
    db_session.add(OrgMember(org_id=other_org.id, user_id=user.id, role="org_owner"))
    ws_b = Workspace(
        org_id=other_org.id, slug=f"ws-b-{uuid.uuid4().hex[:4]}", name="ws-b"
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_a.id,
            installation_id=424242,
            account_login="acme",
            account_type="Organization",
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.get(
        "/v1/integrations/github/install/candidates",
        headers=headers,
        params={"workspace_id": str(ws_b.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["installations"]) == 1
    assert body["installations"][0]["installation_id"] == 424242
    assert body["installations"][0]["account_login"] == "acme"
    assert body["installations"][0]["source_workspace_slug"] == ws_a.slug


@pytest.mark.asyncio
async def test_install_candidates_excludes_non_admin_sibling(
    v1_client, seed_workspace, db_session, github_app_env
) -> None:
    from datetime import datetime, timezone

    from backend.app.db.models.integrations import GitHubInstallation
    from backend.app.db.models.tenancy import (
        Org,
        OrgMember,
        User,
        Workspace,
        WorkspaceMember,
    )

    user, raw, ws_a = seed_workspace
    other_user = User(
        email=f"other-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Other",
    )
    db_session.add(other_user)
    await db_session.flush()
    other_org = Org(slug=f"o-{uuid.uuid4().hex[:6]}", name="x", plan="free")
    db_session.add(other_org)
    await db_session.flush()
    db_session.add(
        OrgMember(org_id=other_org.id, user_id=other_user.id, role="org_owner")
    )
    ws_other = Workspace(
        org_id=other_org.id, slug=f"ws-other-{uuid.uuid4().hex[:4]}", name="ws-other"
    )
    db_session.add(ws_other)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_other.id, user_id=other_user.id, role="owner")
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_other.id,
            installation_id=999001,
            account_login="secret-org",
            installed_at=datetime.now(timezone.utc),
        )
    )
    other_org_b = Org(slug=f"o-{uuid.uuid4().hex[:6]}", name="y", plan="free")
    db_session.add(other_org_b)
    await db_session.flush()
    db_session.add(
        OrgMember(org_id=other_org_b.id, user_id=user.id, role="org_owner")
    )
    ws_b = Workspace(
        org_id=other_org_b.id, slug=f"ws-b-{uuid.uuid4().hex[:4]}", name="ws-b"
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    await db_session.flush()

    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.get(
        "/v1/integrations/github/install/candidates",
        headers=headers,
        params={"workspace_id": str(ws_b.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["installations"] == []


@pytest.mark.asyncio
async def test_install_link_creates_row_and_lists_repos(
    v1_client, seed_workspace, db_session, github_app_env
) -> None:
    from datetime import datetime, timezone

    from backend.app.db.models.integrations import GitHubInstallation
    from backend.app.db.models.tenancy import Org, OrgMember, Workspace, WorkspaceMember

    user, raw, ws_a = seed_workspace
    other_org = Org(slug=f"o-{uuid.uuid4().hex[:6]}", name="x", plan="free")
    db_session.add(other_org)
    await db_session.flush()
    db_session.add(OrgMember(org_id=other_org.id, user_id=user.id, role="org_owner"))
    ws_b = Workspace(
        org_id=other_org.id, slug=f"ws-b-{uuid.uuid4().hex[:4]}", name="ws-b"
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_a.id,
            installation_id=777888,
            account_login="acme",
            account_type="Organization",
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    headers = {"Authorization": f"Bearer {raw}"}
    link = await v1_client.post(
        "/v1/integrations/github/install/link",
        headers=headers,
        params={"workspace_id": str(ws_b.id)},
        json={"installation_id": 777888},
    )
    assert link.status_code == 200, link.text
    body = link.json()
    assert body["workspace_id"] == str(ws_b.id)
    assert body["installation_id"] == 777888

    rows = (
        await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 777888
            )
        )
    ).scalars().all()
    assert len(rows) == 2
    assert {row.workspace_id for row in rows} == {ws_a.id, ws_b.id}


@pytest.mark.asyncio
async def test_install_link_idempotent(
    v1_client, seed_workspace, db_session, github_app_env
) -> None:
    from datetime import datetime, timezone

    from backend.app.db.models.integrations import GitHubInstallation
    from backend.app.db.models.tenancy import Org, OrgMember, Workspace, WorkspaceMember

    user, raw, ws_a = seed_workspace
    other_org = Org(slug=f"o-{uuid.uuid4().hex[:6]}", name="x", plan="free")
    db_session.add(other_org)
    await db_session.flush()
    db_session.add(OrgMember(org_id=other_org.id, user_id=user.id, role="org_owner"))
    ws_b = Workspace(
        org_id=other_org.id, slug=f"ws-b-{uuid.uuid4().hex[:4]}", name="ws-b"
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_a.id,
            installation_id=333444,
            installed_at=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_b.id,
            installation_id=333444,
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        "/v1/integrations/github/install/link",
        headers=headers,
        params={"workspace_id": str(ws_b.id)},
        json={"installation_id": 333444},
    )
    assert response.status_code == 200, response.text
    rows = (
        await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.workspace_id == ws_b.id,
                GitHubInstallation.installation_id == 333444,
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_install_link_404_for_unknown_installation(
    v1_client, seed_workspace, github_app_env
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        "/v1/integrations/github/install/link",
        headers=headers,
        params={"workspace_id": str(workspace.id)},
        json={"installation_id": 123456789},
    )
    assert response.status_code == 404
