"""v1 API — Distiller LLM classifier (Phase 6b).

Covers the ``classifier="llm"`` path through
``POST /v1/workspaces/{ws}/buckets/{slug}/distill`` with a fake
:class:`AgentClient` injected via
:func:`backend.app.api.v1.routes.distiller.set_distiller_agent_client`
so we never hit the network.

Test matrix:

1. **LLM verdict=update** — classifier returns ``update`` + target
   slug; write path supersedes the correct row and bumps version,
   and ``run.output_refs.classifier.reasoning`` is persisted for
   audit.
2. **LLM verdict=skip** — classifier picks ``skip`` with a reason;
   no article is written but the run row still records the reason
   + the classifier name.
3. **Malformed JSON → fallback to stub** — the LLM returns
   unparseable text; the Distiller downgrades to the stub, the
   run still lands a ``decision="new"`` article, and the
   classifier block records the fallback reason.
4. **classifier=auto with no client override** — matches the
   "stub" behaviour when the agent client override is cleared and
   no real credentials resolve (tested by monkeypatching
   ``pick_default_client`` to raise).
5. **classifier=llm with no client** — 503 when the caller forces
   the LLM path but no agent can be resolved.
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.api.v1.routes import distiller as distiller_route
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    DistillerRun,
    KnowledgeBucket,
)
from backend.app.services.agent.client import ChatMessage


# ---------------------------------------------------------------------------
# Fake AgentClient
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal :class:`AgentClient` double for the classifier tests.

    We only need :meth:`acomplete` — the LLM classifier never calls
    :meth:`astream`. Each ``responses`` entry is returned in order;
    additional calls replay the last one.
    """

    vendor = "fake"

    def __init__(self, *, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[Sequence[ChatMessage]] = []

    async def astream(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("astream should not be used by classifier")
        yield  # pragma: no cover — make it a generator for typing

    async def acomplete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("FakeClient ran out of canned responses")
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


@pytest.fixture
def use_fake_agent(request):
    """Pin a :class:`_FakeClient` on the distiller route for the test."""
    clients: list[_FakeClient] = []

    def _set(client: _FakeClient) -> _FakeClient:
        distiller_route.set_distiller_agent_client(client)
        clients.append(client)
        return client

    yield _set
    distiller_route.set_distiller_agent_client(None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def bucket_with_article(db_session, seed_workspace):
    """Seed one bucket + one published article so UPDATE paths exist."""
    _, _, workspace = seed_workspace
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="eng-runbook",
        name="Engineering runbook",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket)
    await db_session.flush()

    article = BucketArticle(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        slug="rollback-policy",
        title="Rollback policy",
        body_md="Old policy text.",
        content_sha="deadbeef" * 4,
        version=1,
        status=BucketArticleStatus.PUBLISHED,
        provenance={"source_kind": BucketSource.EXTERNAL_STATIC},
    )
    db_session.add(article)
    await db_session.flush()
    return workspace, bucket, article


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_classifier_update_supersedes_target(
    v1_client, seed_workspace, bucket_with_article, db_session, use_fake_agent
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket, article = bucket_with_article

    fake = use_fake_agent(
        _FakeClient(
            responses=[
                """
                {
                    "decision": "update",
                    "slug": "rollback-policy",
                    "title": "Rollback policy (rev 2)",
                    "target_slug": "rollback-policy",
                    "reason": null,
                    "reasoning": "Same topic, extended with on-call steps."
                }
                """
            ]
        )
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": "Updated rollback policy — adds on-call steps.",
            "source_kind": "external_static",
            "title_hint": "New notes",
            "slug_hint": "whatever",
            "classifier": "llm",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["decision"] == "update"
    assert payload["classifier"] == "llm"
    assert len(fake.calls) == 1

    # The new article reused the target slug + picked up the LLM title.
    new_id = uuid.UUID(payload["article_ids"][0])
    written = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.id == new_id)
        )
    ).scalars().one()
    assert written.slug == "rollback-policy"
    assert written.title == "Rollback policy (rev 2)"
    assert written.version == 2
    assert written.supersedes_id == article.id

    # The run row captured the classifier audit metadata.
    run_id = uuid.UUID(payload["run"]["id"])
    run = (
        await db_session.execute(
            select(DistillerRun).where(DistillerRun.id == run_id)
        )
    ).scalars().one()
    audit = run.output_refs.get("classifier") or {}
    assert audit.get("name") == "llm"
    assert "on-call" in (audit.get("reasoning") or "")


@pytest.mark.asyncio
async def test_llm_classifier_skip_with_reason(
    v1_client, seed_workspace, bucket_with_article, db_session, use_fake_agent
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket, _ = bucket_with_article

    use_fake_agent(
        _FakeClient(
            responses=[
                """
                {
                    "decision": "skip",
                    "slug": "rollback-policy",
                    "reason": "duplicate of existing rollback-policy article",
                    "reasoning": "No incremental info vs rollback-policy v1."
                }
                """
            ]
        )
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": "Old policy text.",
            "source_kind": "external_static",
            "classifier": "llm",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["decision"] == "skip"
    assert payload["article_ids"] == []
    assert "duplicate" in (payload["reason"] or "").lower()
    assert payload["classifier"] == "llm"

    # No new articles landed; the seeded one is still the only published row.
    published = (
        await db_session.execute(
            select(BucketArticle).where(
                BucketArticle.bucket_id == bucket.id,
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
    ).scalars().all()
    assert len(published) == 1


@pytest.mark.asyncio
async def test_llm_classifier_fallback_to_stub_on_garbage(
    v1_client, seed_workspace, bucket_with_article, db_session, use_fake_agent
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket, _ = bucket_with_article

    use_fake_agent(
        _FakeClient(responses=["this is not JSON at all, sorry"])
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": "Totally new topic: feature flag lifecycle.",
            "source_kind": "external_static",
            "slug_hint": "feature-flag-lifecycle",
            "classifier": "llm",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    # Fell back to stub → treated as new slug.
    assert payload["decision"] == "new"
    assert payload["classifier"] == "stub"

    # Audit trail records the fallback reason.
    run_id = uuid.UUID(payload["run"]["id"])
    run = (
        await db_session.execute(
            select(DistillerRun).where(DistillerRun.id == run_id)
        )
    ).scalars().one()
    audit = run.output_refs.get("classifier") or {}
    assert audit.get("name") == "stub"
    assert "fallback" in (audit.get("reasoning") or "").lower()


@pytest.mark.asyncio
async def test_classifier_llm_without_agent_returns_503(
    v1_client, seed_workspace, bucket_with_article, monkeypatch
) -> None:
    """Forcing ``classifier=llm`` without credentials is a hard error.

    The operator explicitly opted into the LLM path, so a silent
    fallback would be surprising. We prefer a 503 so the caller
    can retry with ``classifier=stub`` (or fix their config).
    """
    _, raw, workspace = seed_workspace
    _, bucket, _ = bucket_with_article

    # Force the override off *and* pick_default_client to raise.
    distiller_route.set_distiller_agent_client(None)
    monkeypatch.setattr(
        distiller_route,
        "pick_default_client",
        lambda: (_ for _ in ()).throw(RuntimeError("no key")),
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": "...",
            "source_kind": "external_static",
            "classifier": "llm",
        },
    )
    assert resp.status_code == 503, resp.text
    assert "LLM classifier unavailable" in resp.text


@pytest.mark.asyncio
async def test_classifier_auto_falls_back_when_no_agent(
    v1_client, seed_workspace, bucket_with_article, monkeypatch
) -> None:
    """``classifier=auto`` without credentials silently picks the stub."""
    _, raw, workspace = seed_workspace
    _, bucket, _ = bucket_with_article

    distiller_route.set_distiller_agent_client(None)
    monkeypatch.setattr(
        distiller_route,
        "pick_default_client",
        lambda: (_ for _ in ()).throw(RuntimeError("no key")),
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": "Brand new topic: incident postmortem template.",
            "source_kind": "external_static",
            "slug_hint": "postmortem-template",
            "classifier": "auto",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["classifier"] == "stub"
    assert payload["decision"] == "new"
