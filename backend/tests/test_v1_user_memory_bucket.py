"""Phase 8 — per-user memory bucket isolation and save-to-memory flow.

Covers the three promises Phase 8 makes:

1. **Create-time guard** — ``POST /buckets`` with
   ``scope_kind=user`` can only mint a bucket for the caller, never
   for someone else. Prevents admins from creating "decoy" user
   buckets that the owner can't read.
2. **List/retrieval isolation** — ``GET /buckets`` and the
   ``TopicService.retrieve_buckets`` / ``search_buckets`` agent
   paths all filter ``scope=user`` rows to the caller. No
   cross-user leak. Workspace/project/repo rows stay visible to
   everyone in the workspace as before.
3. **Save-to-memory endpoint** — ``POST
   /chat/threads/{id}/save-to-memory`` mints the ``my-memory``
   bucket on first hit (idempotent), packs the thread into it, and
   does **not** archive the thread (unlike ``/pack``).

Backend-only tests — these never call OpenAI. We monkeypatch the
two blocking bits (``_summarise_thread`` and ``embed_text``)
exactly like the existing pack tests do, so the endpoint behaviour
is what we're really measuring.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import ChatMessage, ChatThread
from backend.app.db.models.tenancy import User, WorkspaceMember
from backend.app.services.bucket_visibility import visible_to_user_clause
from backend.app.services.distiller_sources import (
    USER_MEMORY_NAME,
    USER_MEMORY_SLUG,
    ensure_user_memory_bucket,
)


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


# ---------------------------------------------------------------------------
# Extra-user fixtures (for cross-user isolation tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def second_user_member(db_session, seed_workspace) -> User:
    """A second user who shares the seeded workspace as a regular member.

    Used by isolation tests: writes a ``scope=user`` bucket owned
    by this user, then asserts the seeded caller can't see it.
    """
    _, _, workspace = seed_workspace
    other = User(
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Other member",
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=workspace.id, user_id=other.id, role="member"
        )
    )
    await db_session.flush()
    return other


async def _mint_user_bucket(
    db_session, workspace_id: uuid.UUID, user_id: uuid.UUID, slug: str
) -> KnowledgeBucket:
    """Shortcut to create a USER-scope AGENT_MEMORY bucket for tests."""
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        slug=slug,
        name=slug.replace("-", " ").title(),
        scope_kind=BucketScope.USER,
        source_kind=BucketSource.AGENT_MEMORY,
        user_id=user_id,
    )
    db_session.add(bucket)
    await db_session.flush()
    return bucket


# ---------------------------------------------------------------------------
# ensure_user_memory_bucket helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_user_memory_bucket_mints_once(
    db_session, seed_workspace
) -> None:
    user, _, workspace = seed_workspace

    first = await ensure_user_memory_bucket(
        db_session, workspace_id=workspace.id, user_id=user.id
    )
    second = await ensure_user_memory_bucket(
        db_session, workspace_id=workspace.id, user_id=user.id
    )

    assert first.id == second.id, "helper must be idempotent"
    assert first.slug == USER_MEMORY_SLUG
    assert first.name == USER_MEMORY_NAME
    assert first.scope_kind == BucketScope.USER
    assert first.source_kind == BucketSource.AGENT_MEMORY
    assert first.user_id == user.id


@pytest.mark.asyncio
async def test_ensure_user_memory_bucket_is_per_user(
    db_session, seed_workspace, second_user_member
) -> None:
    """Two users in the same workspace get two distinct buckets.

    Enforced by ``uq_knowledge_buckets_user_slug`` + the helper's
    ``user_id`` argument. Without this, a single ``my-memory`` row
    in the workspace would be shared across all members.
    """
    user, _, workspace = seed_workspace

    mine = await ensure_user_memory_bucket(
        db_session, workspace_id=workspace.id, user_id=user.id
    )
    theirs = await ensure_user_memory_bucket(
        db_session, workspace_id=workspace.id, user_id=second_user_member.id
    )

    assert mine.id != theirs.id
    assert mine.user_id == user.id
    assert theirs.user_id == second_user_member.id
    assert mine.slug == theirs.slug == USER_MEMORY_SLUG


# ---------------------------------------------------------------------------
# Visibility helper — pure predicate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visibility_helper_hides_other_users_scope_user_rows(
    db_session, seed_workspace, second_user_member
) -> None:
    user, _, workspace = seed_workspace

    mine = await _mint_user_bucket(
        db_session, workspace.id, user.id, "my-pins"
    )
    theirs = await _mint_user_bucket(
        db_session, workspace.id, second_user_member.id, "their-pins"
    )

    visible = (
        await db_session.execute(
            select(KnowledgeBucket.id)
            .where(KnowledgeBucket.workspace_id == workspace.id)
            .where(visible_to_user_clause(user.id))
        )
    ).scalars().all()

    assert mine.id in visible
    assert theirs.id not in visible


@pytest.mark.asyncio
async def test_visibility_helper_keeps_non_user_scopes_for_everyone(
    db_session, seed_workspace, second_user_member
) -> None:
    _, _, workspace = seed_workspace
    workspace_bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="shared",
        name="Shared",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(workspace_bucket)
    await db_session.flush()

    # Second user (NOT the seeded caller) should also see the
    # workspace-scoped row via the visibility helper — the helper
    # must never accidentally filter non-USER scopes.
    visible = (
        await db_session.execute(
            select(KnowledgeBucket.id)
            .where(KnowledgeBucket.workspace_id == workspace.id)
            .where(visible_to_user_clause(second_user_member.id))
        )
    ).scalars().all()
    assert workspace_bucket.id in visible


# ---------------------------------------------------------------------------
# API: create_bucket guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_bucket_for_self_succeeds(
    v1_client, seed_workspace
) -> None:
    user, raw, workspace = seed_workspace

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers=_auth(raw),
        json={
            "name": "My pins",
            "slug": "my-pins",
            "scope_kind": "user",
            "source_kind": "agent_memory",
            "user_id": str(user.id),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope_kind"] == "user"
    assert body["user_id"] == str(user.id)


@pytest.mark.asyncio
async def test_create_user_bucket_for_another_user_is_forbidden(
    v1_client, seed_workspace, second_user_member
) -> None:
    _, raw, workspace = seed_workspace

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers=_auth(raw),
        json={
            "name": "Spy pins",
            "slug": "spy-pins",
            "scope_kind": "user",
            "source_kind": "agent_memory",
            "user_id": str(second_user_member.id),
        },
    )
    assert resp.status_code == 403, resp.text
    assert "yourself" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# API: list_buckets isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_buckets_hides_other_users_scope_user_rows(
    v1_client, db_session, seed_workspace, second_user_member
) -> None:
    user, raw, workspace = seed_workspace

    await _mint_user_bucket(db_session, workspace.id, user.id, "my-pins")
    await _mint_user_bucket(
        db_session, workspace.id, second_user_member.id, "their-pins"
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets", headers=_auth(raw)
    )
    assert resp.status_code == 200, resp.text
    slugs = {row["slug"] for row in resp.json()}
    assert "my-pins" in slugs
    assert "their-pins" not in slugs


# ---------------------------------------------------------------------------
# /chat/threads/{id}/save-to-memory
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_thread(db_session, seed_workspace) -> ChatThread:
    """A fresh thread with two exchanges — enough for pack_topic to work."""
    user, _, workspace = seed_workspace
    thread = ChatThread(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="about phase 8 save flow",
        status="active",
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add_all(
        [
            ChatMessage(
                thread_id=thread.id,
                role="user",
                body="How do I save this thread to my memory?",
            ),
            ChatMessage(
                thread_id=thread.id,
                role="assistant",
                body="Hit the 'save to memory' button; it mints a personal bucket.",
            ),
        ]
    )
    await db_session.flush()
    return thread


@pytest.fixture
def stub_topic_pipeline(monkeypatch):
    """Replace the OpenAI-touching parts of pack_topic with deterministic stubs.

    We don't want the test to hit the real LLM/embedder. pack_topic
    calls ``_summarise_thread`` (LLM) and ``embed_text`` (OpenAI
    embeddings), plus the article mirror (which also calls
    ``embed_text``); we stub all three.
    """
    import backend.app.services.agent.topic as topic_mod

    async def _fake_summarise(self, messages: list[Any]) -> tuple[str, str]:
        return "Fake title", "Fake summary body"

    async def _fake_embed(text: str, *, settings=None):  # noqa: ARG001
        return [0.0] * 1536

    monkeypatch.setattr(
        topic_mod.TopicService, "_summarise_thread", _fake_summarise
    )
    monkeypatch.setattr(topic_mod, "embed_text", _fake_embed)
    yield


@pytest.mark.asyncio
async def test_save_to_memory_mints_bucket_on_first_call(
    v1_client,
    db_session,
    seed_workspace,
    seeded_thread,
    stub_topic_pipeline,
) -> None:
    user, raw, workspace = seed_workspace

    # No my-memory bucket yet.
    pre = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.scope_kind == BucketScope.USER,
            )
        )
    ).scalars().all()
    assert pre == []

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{seeded_thread.id}/save-to-memory",
        headers=_auth(raw),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == str(seeded_thread.id)
    assert body["title"] == "Fake title"
    assert body["summary"] == "Fake summary body"

    # Bucket now exists with the canonical my-memory slug + scoped to caller.
    bucket = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.scope_kind == BucketScope.USER,
            )
        )
    ).scalars().one()
    assert bucket.slug == USER_MEMORY_SLUG
    assert bucket.user_id == user.id
    assert bucket.source_kind == BucketSource.AGENT_MEMORY

    # Thread must remain active — unlike /pack which archives.
    await db_session.refresh(seeded_thread)
    assert seeded_thread.status == "active"


@pytest.mark.asyncio
async def test_save_to_memory_is_idempotent_across_calls(
    v1_client,
    db_session,
    seed_workspace,
    seeded_thread,
    stub_topic_pipeline,
) -> None:
    """Second save uses the same bucket, appends a fresh article."""
    user, raw, workspace = seed_workspace

    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{seeded_thread.id}/save-to-memory",
        headers=_auth(raw),
    )
    assert first.status_code == 200
    first_bucket_id = first.json()["bucket_id"]

    # Unarchive so the second save can also pack (Phase 8 doesn't
    # archive, but seeded_thread stays active anyway — double-check).
    await db_session.refresh(seeded_thread)
    seeded_thread.status = "active"
    await db_session.flush()

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{seeded_thread.id}/save-to-memory",
        headers=_auth(raw),
    )
    assert second.status_code == 200, second.text
    assert second.json()["bucket_id"] == first_bucket_id

    buckets = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.scope_kind == BucketScope.USER,
                KnowledgeBucket.user_id == user.id,
            )
        )
    ).scalars().all()
    assert len(buckets) == 1, "second save must reuse the first bucket"

    articles = (
        await db_session.execute(
            select(BucketArticle).where(
                BucketArticle.bucket_id == uuid.UUID(first_bucket_id)
            )
        )
    ).scalars().all()
    assert len(articles) == 2, "each save mirrors a fresh article"


@pytest.mark.asyncio
async def test_save_to_memory_empty_thread_is_400(
    v1_client, db_session, seed_workspace, stub_topic_pipeline
) -> None:
    user, raw, workspace = seed_workspace
    thread = ChatThread(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="empty",
        status="active",
    )
    db_session.add(thread)
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{thread.id}/save-to-memory",
        headers=_auth(raw),
    )
    assert resp.status_code == 400, resp.text
    assert "no messages" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_save_to_memory_unknown_thread_is_404(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    bogus = uuid.uuid4()
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{bogus}/save-to-memory",
        headers=_auth(raw),
    )
    assert resp.status_code == 404
