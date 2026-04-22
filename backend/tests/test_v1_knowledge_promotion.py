"""HTTP tests for workspace knowledge promotion (PR-7B).

Covers the four new endpoints:

- ``GET  /v1/workspaces/{ws}/knowledge/candidates`` — cache hit vs
  recompute, cross-repo-only cluster detection, similarity threshold,
  single-repo clusters skipped.
- ``POST /v1/workspaces/{ws}/knowledge/candidates/refresh`` — forces
  recompute even when the cache is fresh.
- ``POST /v1/workspaces/{ws}/knowledge/candidates/{id}/draft`` — LLM
  happy path with a fake client, 412 when unconfigured.
- ``POST /v1/workspaces/{ws}/knowledge/promote`` — creates workspace
  bucket + article, marks sources as overrides, respects pre-existing
  overrides, invalidates the candidate cache.

Embeddings are fabricated as weighted sums of basis vectors so cosine
similarity is exactly predictable — the dedup threshold can be pinned
without any vector-quality evaluation sneaking into unit tests.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import pytest
import pytest_asyncio


EMBED_DIM = 1536


def _unit_vec(index: int) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[index % EMBED_DIM] = 1.0
    return v


def _mix_vec(index_a: int, index_b: int, weight_a: float) -> list[float]:
    """Normalised mix of two basis vectors.

    Cosine similarity between ``_mix_vec(a, b, w)`` and ``_unit_vec(a)``
    equals ``w`` (to numerical precision). Handy for crafting near-
    duplicates with an exact target similarity score.
    """
    wb = math.sqrt(max(0.0, 1.0 - weight_a * weight_a))
    v = [0.0] * EMBED_DIM
    v[index_a % EMBED_DIM] = weight_a
    v[index_b % EMBED_DIM] = wb
    return v


class _FakeAgentClient:
    """Minimal stand-in for :class:`AgentClient` — returns a fixed JSON payload."""

    vendor = "fake"

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def acomplete(
        self,
        messages: Sequence[Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": [getattr(m, "content", "") for m in messages],
                "response_format": response_format,
            }
        )
        return json.dumps(self._payload)


@pytest_asyncio.fixture
async def seed_promotion_workspace(db_session, seed_workspace):
    """Seed a workspace with two activated repos + per-repo buckets.

    The caller fills in articles/embeddings per-test so each assertion
    can control exactly which clusters should surface.
    """
    from backend.app.db.models.agent_memory import (
        BucketScope,
        BucketSource,
        KnowledgeBucket,
    )
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=9_800_001,
        account_login="ks",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo_a = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=60001,
        full_name="ks/api",
        default_branch="main",
        private=False,
        html_url="https://github.com/ks/api",
        activated_at=datetime.now(timezone.utc),
    )
    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=60002,
        full_name="ks/web",
        default_branch="main",
        private=False,
        html_url="https://github.com/ks/web",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

    bucket_a = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="auth-runbook",
        name="Auth (api)",
        description="Repo A auth runbook.",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_a.id,
    )
    bucket_b = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="auth-runbook",
        name="Auth (web)",
        description="Repo B auth runbook.",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_b.id,
    )
    db_session.add_all([bucket_a, bucket_b])
    await db_session.flush()

    return {
        "raw": raw,
        "workspace": workspace,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "bucket_a": bucket_a,
        "bucket_b": bucket_b,
    }


async def _mk_article(
    db_session, bucket_id, slug: str, title: str, body: str, vec: list[float]
):
    from backend.app.db.models.agent_memory import BucketArticle

    article = BucketArticle(
        bucket_id=bucket_id,
        slug=slug,
        title=title,
        body_md=body,
        content_sha=("%040x" % hash(title))[:40] + "0" * 24,
        embedding=vec,
    )
    db_session.add(article)
    await db_session.flush()
    return article


@pytest.mark.asyncio
async def test_candidates_detects_cross_repo_duplicates(
    v1_client, seed_promotion_workspace, db_session
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    # Both articles point at basis vector #0 → similarity == 1.0.
    art_a = await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "Auth runbook (api)",
        "Rotate the OAuth creds every 90 days.",
        _unit_vec(0),
    )
    art_b = await _mk_article(
        db_session, ctx["bucket_b"].id,
        "main", "Auth runbook (web)",
        "Rotate the OAuth creds every 90 days — web edition.",
        _unit_vec(0),
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_fresh"] is False
    assert len(body["candidates"]) == 1
    cand = body["candidates"][0]
    assert cand["member_count"] == 2
    assert cand["repo_count"] == 2
    ids = {m["article_id"] for m in cand["members"]}
    assert ids == {str(art_a.id), str(art_b.id)}
    assert cand["centroid_score"] >= 0.99


@pytest.mark.asyncio
async def test_candidates_ignores_single_repo_duplicates(
    v1_client, seed_promotion_workspace, db_session
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    # Two near-identical articles BOTH in repo A → not a promotion
    # candidate (single-repo duplicates are a bucket-hygiene issue).
    await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "A1", "Body A1", _unit_vec(0),
    )
    await _mk_article(
        db_session, ctx["bucket_a"].id,
        "deep-dive", "A2", "Body A2", _unit_vec(0),
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates"] == []


@pytest.mark.asyncio
async def test_candidates_respects_similarity_threshold(
    v1_client, seed_promotion_workspace, db_session
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    # Cosine similarity ~0.6 — below the default 0.85 threshold.
    await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "A1", "Body A1", _unit_vec(0),
    )
    await _mk_article(
        db_session, ctx["bucket_b"].id,
        "main", "B1", "Body B1", _mix_vec(0, 1, 0.6),
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates"] == []


@pytest.mark.asyncio
async def test_candidates_cache_hit_is_fresh(
    v1_client, seed_promotion_workspace, db_session
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "A", "Body A", _unit_vec(0),
    )
    await _mk_article(
        db_session, ctx["bucket_b"].id,
        "main", "B", "Body B", _unit_vec(0),
    )

    first = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["is_fresh"] is False
    first_ids = {c["id"] for c in first.json()["candidates"]}

    second = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["is_fresh"] is True
    assert {c["id"] for c in second.json()["candidates"]} == first_ids


@pytest.mark.asyncio
async def test_refresh_forces_recompute(
    v1_client, seed_promotion_workspace, db_session
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    art_a = await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "A", "Body A", _unit_vec(0),
    )
    art_b = await _mk_article(
        db_session, ctx["bucket_b"].id,
        "main", "B", "Body B", _unit_vec(0),
    )

    # Prime the cache.
    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert resp.status_code == 200
    cached_fp = resp.json()["candidates"][0]["fingerprint"]

    # Break the cluster — repo B article drifts far away.
    art_b.embedding = _unit_vec(42)
    await db_session.flush()

    refresh = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates/refresh",
        headers=headers,
    )
    assert refresh.status_code == 200, refresh.text
    body = refresh.json()
    assert body["is_fresh"] is False
    # Cluster should have dissolved.
    assert body["candidates"] == []
    _ = cached_fp  # value intentionally unused beyond shape assertion


@pytest.mark.asyncio
async def test_draft_happy_path(
    v1_client, seed_promotion_workspace, db_session, monkeypatch
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "A", "Repo-A auth guidance.", _unit_vec(0),
    )
    await _mk_article(
        db_session, ctx["bucket_b"].id,
        "main", "B", "Repo-B auth guidance.", _unit_vec(0),
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert resp.status_code == 200
    candidate_id = resp.json()["candidates"][0]["id"]

    fake = _FakeAgentClient(
        {
            "slug": "auth-runbook",
            "title": "Auth runbook",
            "body": "Rotate OAuth creds every 90 days.",
            "summary": "Workspace-wide auth rotation policy.",
            "notes": "",
        }
    )

    import backend.app.api.v1.routes.knowledge as mod

    monkeypatch.setattr(mod, "pick_default_client", lambda settings: fake)

    draft_resp = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}"
        f"/knowledge/candidates/{candidate_id}/draft",
        headers=headers,
        json={},
    )
    assert draft_resp.status_code == 200, draft_resp.text
    draft = draft_resp.json()
    assert draft["slug"] == "auth-runbook"
    assert draft["title"] == "Auth runbook"
    assert len(fake.calls) == 1
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_draft_412_when_llm_unconfigured(
    v1_client, seed_promotion_workspace, db_session, monkeypatch
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    await _mk_article(
        db_session, ctx["bucket_a"].id, "main", "A", "Body A", _unit_vec(0),
    )
    await _mk_article(
        db_session, ctx["bucket_b"].id, "main", "B", "Body B", _unit_vec(0),
    )
    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    candidate_id = resp.json()["candidates"][0]["id"]

    import backend.app.api.v1.routes.knowledge as mod

    def _raise(_settings):
        raise RuntimeError("No LLM API key configured.")

    monkeypatch.setattr(mod, "pick_default_client", _raise)

    draft_resp = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}"
        f"/knowledge/candidates/{candidate_id}/draft",
        headers=headers,
        json={},
    )
    assert draft_resp.status_code == 412, draft_resp.text
    assert draft_resp.json()["detail"]["code"] == "llm_unconfigured"


@pytest.mark.asyncio
async def test_promote_creates_workspace_bucket_and_marks_overrides(
    v1_client, seed_promotion_workspace, db_session, monkeypatch
) -> None:
    from backend.app.db.models.agent_memory import (
        BucketArticle,
        BucketScope,
        BucketSource,
        KnowledgeBucket,
    )

    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    art_a = await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "A", "Body A", _unit_vec(0),
    )
    art_b = await _mk_article(
        db_session, ctx["bucket_b"].id,
        "main", "B", "Body B", _unit_vec(0),
    )

    # Skip real embedding — promotion endpoint swallows RuntimeError
    # and still creates the article.
    import backend.app.api.v1.routes.knowledge as mod

    async def _no_embed(_text, *, settings=None):
        raise RuntimeError("embeddings off for tests")

    monkeypatch.setattr(mod, "embed_text", _no_embed)

    resp = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/promote",
        headers=headers,
        json={
            "slug": "auth-runbook",
            "title": "Auth runbook",
            "body": "Canonical auth runbook body.",
            "summary": "Canonical auth summary.",
            "source_article_ids": [str(art_a.id), str(art_b.id)],
            "mark_sources_as_overrides": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["overridden_article_ids"]) == {str(art_a.id), str(art_b.id)}

    ws_bucket = await db_session.get(KnowledgeBucket, body["workspace_bucket_id"])
    assert ws_bucket is not None
    assert ws_bucket.scope_kind == BucketScope.WORKSPACE
    assert ws_bucket.slug == "auth-runbook"
    assert ws_bucket.source_kind == BucketSource.PROMOTED

    ws_article = await db_session.get(
        BucketArticle, body["workspace_article_id"]
    )
    assert ws_article is not None
    assert ws_article.body_md == "Canonical auth runbook body."

    await db_session.refresh(art_a)
    await db_session.refresh(art_b)
    assert art_a.overrides_workspace_article_id == ws_article.id
    assert art_b.overrides_workspace_article_id == ws_article.id


@pytest.mark.asyncio
async def test_promote_skips_already_overridden_sources(
    v1_client, seed_promotion_workspace, db_session, monkeypatch
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    art_a = await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "A", "Body A", _unit_vec(0),
    )
    art_b = await _mk_article(
        db_session, ctx["bucket_b"].id,
        "main", "B", "Body B", _unit_vec(0),
    )

    # Plant an existing, unrelated override on art_a.
    from backend.app.db.models.agent_memory import (
        BucketArticle,
        BucketScope,
        BucketSource,
        KnowledgeBucket,
    )

    existing_ws = KnowledgeBucket(
        workspace_id=ctx["workspace"].id,
        slug="legacy-auth",
        name="Legacy",
        description=None,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(existing_ws)
    await db_session.flush()

    existing_article = BucketArticle(
        bucket_id=existing_ws.id,
        slug="main",
        title="Legacy",
        body_md="old canonical",
        content_sha="legacy" + "0" * 58,
    )
    db_session.add(existing_article)
    await db_session.flush()

    art_a.overrides_workspace_article_id = existing_article.id
    await db_session.flush()

    import backend.app.api.v1.routes.knowledge as mod

    async def _no_embed(_text, *, settings=None):
        raise RuntimeError("off")

    monkeypatch.setattr(mod, "embed_text", _no_embed)

    resp = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/promote",
        headers=headers,
        json={
            "slug": "auth-runbook",
            "title": "Auth runbook",
            "body": "Canonical body.",
            "source_article_ids": [str(art_a.id), str(art_b.id)],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only art_b actually got relinked — art_a retained its prior override.
    assert body["overridden_article_ids"] == [str(art_b.id)]

    await db_session.refresh(art_a)
    assert art_a.overrides_workspace_article_id == existing_article.id


@pytest.mark.asyncio
async def test_promote_invalidates_candidate_cache(
    v1_client, seed_promotion_workspace, db_session, monkeypatch
) -> None:
    ctx = seed_promotion_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    art_a = await _mk_article(
        db_session, ctx["bucket_a"].id,
        "main", "A", "Body A", _unit_vec(0),
    )
    art_b = await _mk_article(
        db_session, ctx["bucket_b"].id,
        "main", "B", "Body B", _unit_vec(0),
    )

    # Prime cache.
    first = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["is_fresh"] is False
    assert len(first.json()["candidates"]) == 1

    import backend.app.api.v1.routes.knowledge as mod

    async def _no_embed(_text, *, settings=None):
        raise RuntimeError("off")

    monkeypatch.setattr(mod, "embed_text", _no_embed)

    promote_resp = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/promote",
        headers=headers,
        json={
            "slug": "auth-runbook",
            "title": "Auth runbook",
            "body": "Canonical.",
            "source_article_ids": [str(art_a.id), str(art_b.id)],
        },
    )
    assert promote_resp.status_code == 200, promote_resp.text

    after = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/candidates",
        headers=headers,
    )
    assert after.status_code == 200, after.text
    # Cache invalidated → recomputed. Promoted article is now
    # workspace-scope so it's excluded from clustering; the sources
    # now point at the workspace canonical but remain repo-scope —
    # still a valid cluster unless the body changed. We assert the
    # recompute ran (is_fresh=False) which is the contract.
    assert after.json()["is_fresh"] is False
