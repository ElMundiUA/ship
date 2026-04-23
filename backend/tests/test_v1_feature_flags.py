"""Tests for the workspace feature flag endpoints + helper (P2-19).

Pins the spec contract:

- ``GET`` is open to any workspace member; unset flags fall back to
  the registry default (``inbox_v1_enabled`` defaults to True per
  the rollout-safety semantics).
- ``PUT`` is workspace owner-only — ``admin`` is rejected with 403.
- Unknown flag names → 422 (allowlist enforced in
  :func:`backend.app.services.feature_flags.set_flag`).
- Mutations persist to ``workspaces.settings[feature_flags]`` AND
  emit a ``feature_flag.set`` :class:`AuditLog` row.
- :func:`feature_flags.is_enabled` reads the persisted value when
  set and the registry default otherwise.
"""

from __future__ import annotations

import secrets
import uuid

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_admin_user_and_token(db_session, seed_workspace):
    """Add a second user to the workspace with role=admin and mint a PAT.

    The default :func:`seed_workspace` member is an ``owner`` — the
    admin-vs-owner gate test needs both, so we layer this fixture
    on top.
    """
    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        OrgMember,
        User,
        WorkspaceMember,
    )

    _, _owner_raw, workspace = seed_workspace

    admin = User(
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Admin",
    )
    db_session.add(admin)
    await db_session.flush()
    db_session.add(
        OrgMember(org_id=workspace.org_id, user_id=admin.id, role="org_admin")
    )
    db_session.add(
        WorkspaceMember(
            workspace_id=workspace.id, user_id=admin.id, role="admin"
        )
    )

    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    token = ApiToken(
        user_id=admin.id,
        name="admin-test-token",
        hashed_secret=_hash_token(raw),
        prefix=PAT_PREFIX,
        scopes=["workspace:read", "workspace:write"],
    )
    db_session.add(token)
    await db_session.flush()
    return admin, raw


# ---------------------------------------------------------------------------
# GET endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_feature_flags_returns_defaults_for_new_workspace(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/feature-flags",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Spec: ``inbox_v1_enabled`` defaults to True (emergency-disable
    # lever, not opt-in).
    assert body["flags"]["inbox_v1_enabled"] is True


# ---------------------------------------------------------------------------
# PUT endpoint — RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_feature_flag_admin_only(
    v1_client, seed_workspace, seed_admin_user_and_token
) -> None:
    """Admin is rejected (403); owner succeeds (200)."""
    _, owner_raw, workspace = seed_workspace
    _admin, admin_raw = seed_admin_user_and_token

    admin_resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/feature-flags/inbox_v1_enabled",
        headers={"Authorization": f"Bearer {admin_raw}"},
        json={"enabled": False},
    )
    assert admin_resp.status_code == 403, admin_resp.text

    owner_resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/feature-flags/inbox_v1_enabled",
        headers={"Authorization": f"Bearer {owner_raw}"},
        json={"enabled": False},
    )
    assert owner_resp.status_code == 200, owner_resp.text
    assert owner_resp.json()["enabled"] is False


# ---------------------------------------------------------------------------
# PUT endpoint — validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_unknown_feature_flag_returns_422(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace

    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/feature-flags/never_existed",
        headers={"Authorization": f"Bearer {raw}"},
        json={"enabled": True},
    )
    assert resp.status_code == 422
    assert "unknown feature flag" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# PUT endpoint — persistence + audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_feature_flag_persists_and_audit_logs(
    v1_client, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog, Workspace

    _, raw, workspace = seed_workspace

    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/feature-flags/inbox_v1_enabled",
        headers={"Authorization": f"Bearer {raw}"},
        json={"enabled": False},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload == {
        "flag": "inbox_v1_enabled",
        "enabled": False,
        "previous": True,
    }

    # Persisted on the workspace row under the agreed key.
    refreshed = (
        await db_session.execute(
            select(Workspace).where(Workspace.id == workspace.id)
        )
    ).scalar_one()
    assert (
        refreshed.settings.get("feature_flags", {}).get("inbox_v1_enabled")
        is False
    )

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace.id,
                    AuditLog.action == "feature_flag.set",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].payload == {
        "flag": "inbox_v1_enabled",
        "enabled": False,
    }
    assert audits[0].target_kind == "workspace"
    assert audits[0].target_id == str(workspace.id)


# ---------------------------------------------------------------------------
# Helper — is_enabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_enabled_helper_reads_persisted_value(
    db_session, seed_workspace
) -> None:
    from backend.app.services import feature_flags as ff

    user, _raw, workspace = seed_workspace

    await ff.set_flag(
        db_session,
        workspace.id,
        "inbox_v1_enabled",
        False,
        actor_user_id=user.id,
    )
    assert await ff.is_enabled(
        db_session, workspace.id, "inbox_v1_enabled"
    ) is False

    await ff.set_flag(
        db_session,
        workspace.id,
        "inbox_v1_enabled",
        True,
        actor_user_id=user.id,
    )
    assert await ff.is_enabled(
        db_session, workspace.id, "inbox_v1_enabled"
    ) is True


@pytest.mark.asyncio
async def test_is_enabled_default_for_inbox_v1(
    db_session, seed_workspace
) -> None:
    """Per spec: unset reads True for ``inbox_v1_enabled``."""
    from backend.app.services import feature_flags as ff

    _, _raw, workspace = seed_workspace

    assert await ff.is_enabled(
        db_session, workspace.id, "inbox_v1_enabled"
    ) is True
