"""Tests for :mod:`backend.app.api.v1.routes.buckets_resolver`.

The resolver is a read-only projection: no row churn is exercised
here. Each test seeds a specific ladder state directly into the DB
and checks the endpoint's filtering + priority logic:

1. Empty context → only workspace-scoped rows are returned.
2. With ``project_id`` → workspace + project rows for that project
   only. Other projects' rows stay hidden.
3. With ``repo_id`` → workspace + repo rows for that repo only.
4. User overlay → always included for the caller; never for other
   users (the seeded "someone else's" user-scoped row is invisible).
5. Slug collision → every candidate is in ``buckets``, exactly one
   has ``effective=true``, and ``winners_by_slug`` points at it.
6. Archived rows → hidden by default; visible with
   ``?include_archived=true``.
7. Unknown ``repo_id`` / ``project_id`` returns 404.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.tenancy import Project


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Seed helpers — all direct ORM writes so we isolate the resolver from
# the sync service (covered elsewhere) and from the chat CRUD routes.
# ---------------------------------------------------------------------------


async def _seed_repo(db_session, *, workspace_id, full_name="acme/notes"):
    install = GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=10_000 + abs(hash(full_name)) % 10_000,
        account_id=1,
        account_login="acme",
        account_type="Organization",
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace_id,
        installation_id=install.id,
        provider="github",
        external_id=abs(hash(full_name)) % 1_000_000,
        full_name=full_name,
        default_branch="main",
        html_url=f"https://github.com/{full_name}",
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def _seed_project(db_session, *, workspace_id, slug="webapp"):
    project = Project(
        workspace_id=workspace_id,
        slug=slug,
        name=slug.replace("-", " ").title(),
    )
    db_session.add(project)
    await db_session.flush()
    return project


def _make_bucket(
    *,
    workspace_id,
    slug,
    name,
    scope_kind,
    project_id=None,
    repo_id=None,
    user_id=None,
    source_kind=None,
    description=None,
    archived=False,
):
    source = source_kind or (
        BucketSource.REPO_FILES
        if scope_kind == BucketScope.REPO
        else BucketSource.AGENT_MEMORY
    )
    row = KnowledgeBucket(
        workspace_id=workspace_id,
        slug=slug,
        name=name,
        description=description,
        scope_kind=scope_kind,
        source_kind=source,
        project_id=project_id,
        repo_id=repo_id,
        user_id=user_id,
        archived_at=datetime.now(timezone.utc) if archived else None,
    )
    return row


def _slugs(buckets: list[dict[str, Any]]) -> list[str]:
    return [b["slug"] for b in buckets]


def _effective_slugs(buckets: list[dict[str, Any]]) -> list[str]:
    return [b["slug"] for b in buckets if b["effective"]]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_empty_context_returns_only_workspace_scope(
    v1_client, seed_workspace, db_session
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    db_session.add(
        _make_bucket(
            workspace_id=ws.id,
            slug="code-style",
            name="Code style (workspace)",
            scope_kind=BucketScope.WORKSPACE,
        )
    )
    # A repo-scoped row exists but the caller didn't pass repo_id,
    # so the resolver must filter it out.
    repo = await _seed_repo(db_session, workspace_id=ws.id)
    db_session.add(
        _make_bucket(
            workspace_id=ws.id,
            slug="repo-only",
            name="Repo-only",
            scope_kind=BucketScope.REPO,
            repo_id=repo.id,
        )
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 1
    assert body["context"]["repo_id"] is None
    assert body["context"]["project_id"] is None
    assert body["context"]["user_id"] is not None  # caller overlay
    assert _slugs(body["buckets"]) == ["code-style"]
    assert body["winners_by_slug"] == {"code-style": body["buckets"][0]["id"]}


async def test_project_scope_filters_by_project_id(
    v1_client, seed_workspace, db_session
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    proj_a = await _seed_project(db_session, workspace_id=ws.id, slug="alpha")
    proj_b = await _seed_project(db_session, workspace_id=ws.id, slug="beta")
    db_session.add_all(
        [
            _make_bucket(
                workspace_id=ws.id,
                slug="alpha-playbook",
                name="Alpha playbook",
                scope_kind=BucketScope.PROJECT,
                project_id=proj_a.id,
            ),
            _make_bucket(
                workspace_id=ws.id,
                slug="beta-playbook",
                name="Beta playbook",
                scope_kind=BucketScope.PROJECT,
                project_id=proj_b.id,
            ),
        ]
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved",
        params={"project_id": str(proj_a.id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _slugs(body["buckets"]) == ["alpha-playbook"]
    assert body["context"]["project_id"] == str(proj_a.id)


async def test_repo_scope_filters_by_repo_id(
    v1_client, seed_workspace, db_session
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    repo_a = await _seed_repo(
        db_session, workspace_id=ws.id, full_name="acme/alpha"
    )
    repo_b = await _seed_repo(
        db_session, workspace_id=ws.id, full_name="acme/beta"
    )
    db_session.add_all(
        [
            _make_bucket(
                workspace_id=ws.id,
                slug="alpha-kb",
                name="Alpha KB",
                scope_kind=BucketScope.REPO,
                repo_id=repo_a.id,
            ),
            _make_bucket(
                workspace_id=ws.id,
                slug="beta-kb",
                name="Beta KB",
                scope_kind=BucketScope.REPO,
                repo_id=repo_b.id,
            ),
        ]
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved",
        params={"repo_id": str(repo_a.id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _slugs(body["buckets"]) == ["alpha-kb"]


async def test_user_overlay_always_included_for_caller_only(
    v1_client, seed_workspace, db_session
):
    from backend.app.db.models.tenancy import User as UserModel

    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    me = await v1_client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    caller_id = me.json()["id"]

    # Mint a second user directly — the resolver's user-scope clause
    # only cares about ``user_id`` equality, not about membership
    # (that's the route-level ``_require_membership`` guard, separate
    # concern). We want a bucket owned by "someone else" to prove
    # the resolver hides it from the caller.
    other_user = UserModel(
        email=(
            f"other-{datetime.now(timezone.utc).timestamp():.6f}@example.com"
        ),
        display_name="Other",
    )
    db_session.add(other_user)
    await db_session.flush()

    db_session.add(
        _make_bucket(
            workspace_id=ws.id,
            slug="my-pins",
            name="My pins",
            scope_kind=BucketScope.USER,
            user_id=caller_id,
        )
    )
    db_session.add(
        _make_bucket(
            workspace_id=ws.id,
            slug="their-pins",
            name="Their pins",
            scope_kind=BucketScope.USER,
            user_id=other_user.id,
        )
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    slugs = _slugs(body["buckets"])
    assert "my-pins" in slugs
    assert "their-pins" not in slugs


async def test_slug_collision_picks_highest_priority_winner(
    v1_client, seed_workspace, db_session
):
    """workspace ≺ project ≺ repo; winner is the repo row."""
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    project = await _seed_project(db_session, workspace_id=ws.id)
    repo = await _seed_repo(db_session, workspace_id=ws.id)

    db_session.add_all(
        [
            _make_bucket(
                workspace_id=ws.id,
                slug="code-style",
                name="ws",
                scope_kind=BucketScope.WORKSPACE,
            ),
            _make_bucket(
                workspace_id=ws.id,
                slug="code-style",
                name="proj",
                scope_kind=BucketScope.PROJECT,
                project_id=project.id,
            ),
            _make_bucket(
                workspace_id=ws.id,
                slug="code-style",
                name="repo",
                scope_kind=BucketScope.REPO,
                repo_id=repo.id,
            ),
        ]
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved",
        params={"project_id": str(project.id), "repo_id": str(repo.id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # All three candidates are in the list, ordered by priority.
    assert _slugs(body["buckets"]) == ["code-style", "code-style", "code-style"]
    scopes = [b["scope_kind"] for b in body["buckets"]]
    assert scopes == ["workspace", "project", "repo"]
    # Exactly one winner, and it's the repo row.
    effective = [b for b in body["buckets"] if b["effective"]]
    assert len(effective) == 1
    assert effective[0]["scope_kind"] == "repo"
    assert effective[0]["name"] == "repo"
    # ``winners_by_slug`` agrees with the inline ``effective`` flag.
    assert body["winners_by_slug"]["code-style"] == effective[0]["id"]


async def test_user_overlay_beats_repo_in_resolver_ladder(
    v1_client, seed_workspace, db_session
):
    """The user overlay (priority 40) beats repo (30) for the caller.

    This is how per-user pins / private notes override a repo-level
    default without the user having to negotiate with team-wide
    content.
    """
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    me = await v1_client.get("/v1/auth/me", headers=headers)
    caller_id = me.json()["id"]

    repo = await _seed_repo(db_session, workspace_id=ws.id)
    db_session.add_all(
        [
            _make_bucket(
                workspace_id=ws.id,
                slug="onboarding",
                name="team repo copy",
                scope_kind=BucketScope.REPO,
                repo_id=repo.id,
            ),
            _make_bucket(
                workspace_id=ws.id,
                slug="onboarding",
                name="my private override",
                scope_kind=BucketScope.USER,
                user_id=caller_id,
            ),
        ]
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved",
        params={"repo_id": str(repo.id)},
        headers=headers,
    )
    body = resp.json()
    winners = [b for b in body["buckets"] if b["effective"]]
    assert len(winners) == 1
    assert winners[0]["scope_kind"] == "user"
    assert winners[0]["name"] == "my private override"


async def test_archived_rows_hidden_by_default_and_shown_with_flag(
    v1_client, seed_workspace, db_session
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    db_session.add_all(
        [
            _make_bucket(
                workspace_id=ws.id,
                slug="live",
                name="live",
                scope_kind=BucketScope.WORKSPACE,
            ),
            _make_bucket(
                workspace_id=ws.id,
                slug="old",
                name="old",
                scope_kind=BucketScope.WORKSPACE,
                archived=True,
            ),
        ]
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved", headers=headers
    )
    assert _slugs(resp.json()["buckets"]) == ["live"]

    resp_all = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved",
        params={"include_archived": "true"},
        headers=headers,
    )
    assert sorted(_slugs(resp_all.json()["buckets"])) == ["live", "old"]


async def test_unknown_repo_id_returns_404(v1_client, seed_workspace):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved",
        params={"repo_id": bogus},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_unknown_project_id_returns_404(v1_client, seed_workspace):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved",
        params={"project_id": bogus},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_resolved_path_does_not_collide_with_slug_crud(
    v1_client, seed_workspace, db_session
):
    """Sanity: the literal ``/buckets/resolved`` path must not be
    consumed by the ``/buckets/{slug}`` CRUD route. If the routers
    were registered in the wrong order we'd hit chat's ``get_bucket``
    here and either 404 or return a mangled BucketOut.
    """
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/buckets/resolved", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Shape is the resolver response, not a BucketOut: version +
    # context + buckets + winners_by_slug are resolver-only keys.
    assert set(body.keys()) == {
        "version",
        "workspace_id",
        "context",
        "buckets",
        "winners_by_slug",
    }
