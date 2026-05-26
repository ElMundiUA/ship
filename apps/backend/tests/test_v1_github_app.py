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
import secrets
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.integrations import GitHubInstallation
from backend.app.db.models.tenancy import (
    Org,
    OrgMember,
    User,
    Workspace,
    WorkspaceMember,
)


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


# ---------------------------------------------------------------------------
# Accessible-installations listing + cross-workspace attach.
# ---------------------------------------------------------------------------
#
# These tests cover the "reuse an existing install in a new workspace"
# branch the wizard takes when the operator already connected Ship to
# the org under a different workspace. They share a small builder for
# orgs / workspaces / installs because the matrix needs at least two
# workspaces and sometimes a second user.


async def _make_workspace(
    db_session,
    user,
    org=None,
    *,
    role: str = "owner",
    slug_prefix: str = "ws",
):
    """Create a Workspace + Org (if needed) + WorkspaceMember for ``user``."""
    if org is None:
        org = Org(
            slug=f"org-{uuid.uuid4().hex[:8]}",
            name="Test org",
            plan="free",
        )
        db_session.add(org)
        await db_session.flush()
        db_session.add(
            OrgMember(org_id=org.id, user_id=user.id, role="org_owner")
        )
    ws = Workspace(
        org_id=org.id,
        slug=f"{slug_prefix}-{uuid.uuid4().hex[:6]}",
        name=f"{slug_prefix} workspace",
    )
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=ws.id,
            user_id=user.id,
            role=role,
            answer_specialist_slugs=["*"],
        )
    )
    await db_session.flush()
    return ws, org


async def _make_user_with_token(db_session, label: str = "extra"):
    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import ApiToken

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:6]}@example.com",
        display_name=f"{label} user",
    )
    db_session.add(user)
    await db_session.flush()
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=f"{label}-pat",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=["workspace:read", "workspace:write"],
        )
    )
    await db_session.flush()
    return user, raw


@pytest.mark.asyncio
async def test_list_accessible_installations_dedupes_by_installation_id(
    v1_client, db_session, seed_workspace
) -> None:
    """One install shared across two workspaces collapses into one row.

    The two source workspace slugs ride along on ``workspace_slugs`` so
    the wizard can render "already powers @acme · ws-a / ws-b" context.
    """
    user, raw, ws_a = seed_workspace
    ws_b, _ = await _make_workspace(db_session, user, slug_prefix="ws-b")
    shared_install_id = 901
    for ws in (ws_a, ws_b):
        db_session.add(
            GitHubInstallation(
                workspace_id=ws.id,
                installation_id=shared_install_id,
                account_login="acme",
                account_type="Organization",
                installed_at=datetime.now(timezone.utc),
            )
        )
    await db_session.flush()

    response = await v1_client.get(
        "/v1/integrations/github/installations",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    only = rows[0]
    assert only["installation_id"] == shared_install_id
    assert only["account_login"] == "acme"
    assert sorted(only["workspace_slugs"]) == sorted([ws_a.slug, ws_b.slug])


@pytest.mark.asyncio
async def test_list_accessible_installations_hides_other_users_installs(
    v1_client, db_session, seed_workspace
) -> None:
    """Strangers' installs never appear in the response."""
    _, raw, _ = seed_workspace
    stranger, _ = await _make_user_with_token(db_session, label="stranger")
    stranger_ws, _ = await _make_workspace(
        db_session, stranger, slug_prefix="stranger-ws"
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=stranger_ws.id,
            installation_id=8888,
            account_login="other-org",
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    response = await v1_client.get(
        "/v1/integrations/github/installations",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_accessible_installations_excludes_target_workspace(
    v1_client, db_session, seed_workspace
) -> None:
    """``exclude_workspace_id`` drops installs already bound to that ws.

    The wizard passes the new workspace id so the candidate list shows
    installs the user *could* attach, not ones they already have.
    """
    user, raw, ws_a = seed_workspace
    ws_b, _ = await _make_workspace(db_session, user, slug_prefix="ws-b")
    install_id = 7001
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_a.id,
            installation_id=install_id,
            account_login="acme",
            installed_at=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_b.id,
            installation_id=install_id,
            account_login="acme",
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    response = await v1_client.get(
        "/v1/integrations/github/installations",
        params={"exclude_workspace_id": str(ws_b.id)},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    # The install exists under ws_b → excluded entirely (matching on
    # installation_id, not just workspace_id).
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_accessible_installations_hides_suspended(
    v1_client, db_session, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    db_session.add(
        GitHubInstallation(
            workspace_id=ws.id,
            installation_id=10,
            account_login="acme",
            installed_at=datetime.now(timezone.utc),
            suspended_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    response = await v1_client.get(
        "/v1/integrations/github/installations",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_attach_installation_happy_path(
    v1_client, db_session, seed_workspace
) -> None:
    """Admin on source + target → install row appears under target."""
    user, raw, ws_source = seed_workspace
    ws_target, _ = await _make_workspace(db_session, user, slug_prefix="target")
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_source.id,
            installation_id=5005,
            account_id=42,
            account_login="acme",
            account_type="Organization",
            repository_selection="selected",
            installed_at=datetime.now(timezone.utc),
            settings={"selected_repositories": ["acme/payments"]},
        )
    )
    await db_session.flush()

    response = await v1_client.post(
        "/v1/integrations/github/install/attach",
        json={
            "workspace_id": str(ws_target.id),
            "installation_id": 5005,
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["installation_id"] == 5005
    assert body["account_login"] == "acme"
    assert body["repository_selection"] == "selected"

    rows = (
        await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 5005,
                GitHubInstallation.workspace_id == ws_target.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].settings == {"selected_repositories": ["acme/payments"]}


@pytest.mark.asyncio
async def test_attach_installation_requires_target_admin(
    v1_client, db_session, seed_workspace
) -> None:
    """Caller has source admin but only member role on target → 403."""
    user, raw, ws_source = seed_workspace
    # Spin a second workspace where ``user`` has the lower ``member`` role
    # — they can read it but aren't allowed to bind credentials there.
    ws_target, _ = await _make_workspace(
        db_session, user, role="member", slug_prefix="target"
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_source.id,
            installation_id=6006,
            account_login="acme",
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    response = await v1_client.post(
        "/v1/integrations/github/install/attach",
        json={
            "workspace_id": str(ws_target.id),
            "installation_id": 6006,
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_attach_installation_blocked_when_no_admin_anywhere(
    v1_client, db_session, seed_workspace
) -> None:
    """Caller knows the install id but isn't admin where it lives → 403.

    Source workspace exists, caller is a low-role member there (so the
    install IS in their listing), and they're admin on the target — the
    intended anti-leak is that we still refuse because they don't have
    privilege to redirect the install.
    """
    user, raw, ws_target = seed_workspace  # caller is owner here
    # Stranger owns the source workspace + install.
    stranger, _ = await _make_user_with_token(db_session, label="stranger")
    ws_source, _ = await _make_workspace(
        db_session, stranger, slug_prefix="src"
    )
    # Caller is added as a low-role member on the source workspace so
    # the install IS visible to them via the listing, but they don't
    # have admin there.
    db_session.add(
        WorkspaceMember(
            workspace_id=ws_source.id,
            user_id=user.id,
            role="member",
            answer_specialist_slugs=["*"],
        )
    )
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_source.id,
            installation_id=7007,
            account_login="acme",
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    response = await v1_client.post(
        "/v1/integrations/github/install/attach",
        json={
            "workspace_id": str(ws_target.id),
            "installation_id": 7007,
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "not_admin_on_source_workspace"


@pytest.mark.asyncio
async def test_attach_installation_404_for_unknown_install(
    v1_client, db_session, seed_workspace
) -> None:
    _, raw, ws_target = seed_workspace

    response = await v1_client.post(
        "/v1/integrations/github/install/attach",
        json={
            "workspace_id": str(ws_target.id),
            "installation_id": 999999,
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "installation_not_accessible"


@pytest.mark.asyncio
async def test_attach_installation_idempotent(
    v1_client, db_session, seed_workspace
) -> None:
    """Second attach returns the same row; UniqueConstraint isn't tripped."""
    user, raw, ws_source = seed_workspace
    ws_target, _ = await _make_workspace(db_session, user, slug_prefix="target")
    db_session.add(
        GitHubInstallation(
            workspace_id=ws_source.id,
            installation_id=8008,
            account_login="acme",
            installed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    first = await v1_client.post(
        "/v1/integrations/github/install/attach",
        json={
            "workspace_id": str(ws_target.id),
            "installation_id": 8008,
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert first.status_code == 201, first.text

    second = await v1_client.post(
        "/v1/integrations/github/install/attach",
        json={
            "workspace_id": str(ws_target.id),
            "installation_id": 8008,
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert second.status_code == 201, second.text

    rows = (
        await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 8008,
                GitHubInstallation.workspace_id == ws_target.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
