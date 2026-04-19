"""Cross-tenant isolation tests (RFC-0006 phase 2.6).

Goal: catch any v1 route that forgets ``_require_membership`` (or its
equivalent) and accidentally lets a token from workspace ``A`` see, mutate,
or even confirm the existence of workspace ``B``'s data.

Strategy:

- Seed two completely independent (org, user, workspace, PAT) tuples.
- Hit every workspace-scoped endpoint (read + write) using ``token_a``
  against ``workspace_b``'s id and assert the response is **404** — never
  200, never 403. 403 would leak the fact that the workspace exists, and
  200 would obviously be a security hole.
- Cover the audit log specifically because it joins user/token tables and
  is the easiest place to accidentally leak actor info across tenants.

These tests run against the same Postgres fixture as the rest of the v1
suite; if the database is unavailable, ``db_conftest`` skips them.
"""

from __future__ import annotations

import secrets
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture
async def two_workspaces(db_session: AsyncSession):
    """Provision two completely separate (org, user, workspace, PAT) tuples.

    Built inline (rather than depending on two copies of ``seed_workspace``)
    so we can be sure neither tuple shares any row with the other —
    important because ``_require_membership`` matches on ``user_id`` *and*
    ``workspace_id`` together, and a half-shared fixture would mask bugs.
    """
    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        Org,
        OrgMember,
        User,
        Workspace,
        WorkspaceMember,
    )

    async def make_tenant(label: str):
        user = User(
            email=f"{label}-{uuid.uuid4().hex[:6]}@example.com",
            display_name=f"{label} owner",
        )
        db_session.add(user)
        await db_session.flush()
        org = Org(
            slug=f"{label}-{uuid.uuid4().hex[:8]}",
            name=f"{label} org",
            plan="free",
        )
        db_session.add(org)
        await db_session.flush()
        db_session.add(OrgMember(org_id=org.id, user_id=user.id, role="org_owner"))
        ws = Workspace(
            org_id=org.id,
            slug=f"{label}-ws-{uuid.uuid4().hex[:6]}",
            name=f"{label} workspace",
        )
        db_session.add(ws)
        await db_session.flush()
        db_session.add(
            WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
        )
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
        return {"user": user, "org": org, "workspace": ws, "raw": raw}

    a = await make_tenant("alpha")
    b = await make_tenant("bravo")
    return a, b


# Endpoints that must return 404 when ``user_a`` reaches for ``workspace_b``.
# Format: (method, path_template, body, expected_status)
# ``path_template`` uses the literal placeholder ``{ws}`` for the foreign id.
_READ_ENDPOINTS = [
    ("GET", "/v1/workspaces/{ws}", None),
    ("GET", "/v1/workspaces/{ws}/members", None),
    ("GET", "/v1/workspaces/{ws}/integrations", None),
    ("GET", "/v1/workspaces/{ws}/artifact-repos", None),
    ("GET", "/v1/workspaces/{ws}/audit-log", None),
    ("GET", "/v1/workspaces/{ws}/knowledge", None),
    ("GET", "/v1/workspaces/{ws}/artifacts/tools", None),
]

_MUTATION_ENDPOINTS = [
    ("PATCH", "/v1/workspaces/{ws}", {"name": "hijacked"}),
    (
        "POST",
        "/v1/workspaces/{ws}/members",
        {"email": "intruder@example.com", "role": "member"},
    ),
    (
        "PUT",
        "/v1/workspaces/{ws}/integrations/notion",
        {"kind": "notion", "config": {"workspace_id": "x"}, "secret": "y"},
    ),
    (
        "POST",
        "/v1/workspaces/{ws}/integrations/notion/probe",
        {},
    ),
    ("DELETE", "/v1/workspaces/{ws}/integrations/notion", None),
    (
        "POST",
        "/v1/workspaces/{ws}/artifact-repos",
        {"kind": "workspace", "url": "https://example.com/repo.git"},
    ),
    (
        "DELETE",
        "/v1/workspaces/{ws}",
        {"slug_confirmation": "anything"},
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", _READ_ENDPOINTS)
async def test_read_endpoints_hide_other_workspace(
    v1_client, two_workspaces, method, path, body
):
    a, b = two_workspaces
    url = path.replace("{ws}", str(b["workspace"].id))
    res = await v1_client.request(method, url, headers=_auth(a["raw"]), json=body)
    # 404 is the only acceptable answer: 200 would expose data, 403 would
    # confirm the workspace's existence.
    assert res.status_code == 404, (
        f"{method} {url} leaked status={res.status_code} body={res.text!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", _MUTATION_ENDPOINTS)
async def test_mutation_endpoints_reject_other_workspace(
    v1_client, two_workspaces, method, path, body
):
    a, b = two_workspaces
    url = path.replace("{ws}", str(b["workspace"].id))
    res = await v1_client.request(method, url, headers=_auth(a["raw"]), json=body)
    assert res.status_code == 404, (
        f"{method} {url} leaked status={res.status_code} body={res.text!r}"
    )


@pytest.mark.asyncio
async def test_list_workspaces_only_returns_caller_membership(
    v1_client, two_workspaces
):
    """``GET /v1/workspaces`` must filter by membership — never join across tenants."""
    a, b = two_workspaces
    res = await v1_client.get("/v1/workspaces", headers=_auth(a["raw"]))
    assert res.status_code == 200, res.text
    ids = {row["id"] for row in res.json()}
    assert str(a["workspace"].id) in ids
    assert str(b["workspace"].id) not in ids


@pytest.mark.asyncio
async def test_audit_log_does_not_leak_other_tenant_actors(
    v1_client, two_workspaces
):
    """Mutating ws-A must not surface anywhere in ws-B's audit log.

    Specifically guards the ``audit-log`` join on user/token tables: a
    naive query that forgets the ``workspace_id`` filter would surface
    actor emails from other tenants.
    """
    a, b = two_workspaces

    # Mint an audit row in workspace A by inviting a member.
    invite = await v1_client.post(
        f"/v1/workspaces/{a['workspace'].id}/members",
        headers=_auth(a["raw"]),
        json={"email": "alpha-only@example.com", "role": "member"},
    )
    assert invite.status_code == 201, invite.text

    # Workspace B's owner reads their own audit log — must be empty of A's row.
    res = await v1_client.get(
        f"/v1/workspaces/{b['workspace'].id}/audit-log",
        headers=_auth(b["raw"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    payloads = [row["payload"] for row in body["items"]]
    actors = [row["actor"].get("user_email") for row in body["items"]]
    assert all(p.get("email") != "alpha-only@example.com" for p in payloads)
    assert a["user"].email not in actors


@pytest.mark.asyncio
async def test_token_revoke_scoped_to_owner_only(
    v1_client, two_workspaces, db_session
):
    """A PAT owned by user A must not be revocable via user B's token.

    ``DELETE /v1/auth/tokens/{id}`` does not have a workspace path — the
    isolation guarantee is per-user. Easy to regress, so we cover it here
    rather than only in ``test_v1_auth``.
    """
    from backend.app.db.models.tenancy import ApiToken
    from sqlalchemy import select

    a, b = two_workspaces
    a_tokens = (
        await db_session.execute(
            select(ApiToken).where(ApiToken.user_id == a["user"].id)
        )
    ).scalars().all()
    assert a_tokens, "alpha should already have a PAT from the fixture"
    target = a_tokens[0]
    res = await v1_client.delete(
        f"/v1/auth/tokens/{target.id}", headers=_auth(b["raw"])
    )
    # Either 404 (preferred — hides existence) or 403 is acceptable; 204
    # would mean we just let bravo wipe alpha's credential.
    assert res.status_code in (403, 404), res.text
