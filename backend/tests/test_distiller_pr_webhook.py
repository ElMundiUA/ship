"""Webhook → Distiller integration (Phase 6c).

Confirms the ``pull_request`` webhook handler in
``backend/app/api/v1/routes/github_app.py`` calls
:func:`ingest_pr_merge` on a merged-transition delivery and the
resulting :class:`BucketArticle` lands under a repo-scoped bucket.

We don't patch the default classifier path — since the test env
may or may not have an OPENAI_API_KEY, we only assert that *some*
article lands and that the bucket is scoped to the repo. The
deterministic behaviour (slug / version / provenance) is covered
by ``test_distiller_sources.py`` against the stub classifier.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketScope,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)


WEBHOOK_SECRET = "wh_phase6c_secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


@pytest.fixture
def github_app_env(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_install_and_repo(db_session, workspace_id):
    install = GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=55_123,
        account_id=1,
        account_login="acme",
        account_type="Organization",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace_id,
        installation_id=install.id,
        provider="github",
        external_id=9_500_001,
        full_name="acme/webhook-ingest",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/webhook-ingest",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return install, repo


def _merged_payload(install, repo, *, pr_number: int = 777) -> bytes:
    payload = {
        "action": "closed",
        "installation": {"id": install.installation_id},
        "repository": {
            "id": repo.external_id,
            "full_name": repo.full_name,
        },
        "pull_request": {
            "id": 10_000_000 + pr_number,
            "number": pr_number,
            "title": "Add circuit breaker",
            "body": "Wraps the downstream call in a half-open breaker.",
            "state": "closed",
            "merged": True,
            "draft": False,
            "user": {"login": "octo"},
            "merged_by": {"login": "reviewer"},
            "merged_at": "2026-04-21T12:00:00Z",
            "html_url": (
                f"https://github.com/{repo.full_name}/pull/{pr_number}"
            ),
            "created_at": "2026-04-21T11:00:00Z",
            "updated_at": "2026-04-21T11:55:00Z",
            "closed_at": "2026-04-21T12:00:00Z",
            "head": {"ref": "feature/breaker", "sha": "cafebabe"},
            "base": {"ref": "main"},
        },
    }
    return json.dumps(payload).encode("utf-8")


@pytest.mark.asyncio
async def test_pull_request_merged_webhook_triggers_distiller(
    v1_client, db_session, seed_workspace, github_app_env, monkeypatch
):
    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)

    # Force the stub classifier path regardless of local env — this
    # test guards the wiring between webhook and adapter, not the
    # model's taste in taxonomy. The webhook imports
    # ``ingest_pr_merge`` lazily from ``distiller_sources``, so we
    # swap the attribute on that module before the request fires.
    import backend.app.services.distiller_sources as sources_mod

    _orig_ingest = sources_mod.ingest_pr_merge

    async def _patched_ingest(
        session,
        *,
        workspace_id,
        repo,
        payload,
        actor_user_id=None,
        classifier=None,
    ):
        from backend.app.services.distiller import classify_stub

        return await _orig_ingest(
            session,
            workspace_id=workspace_id,
            repo=repo,
            payload=payload,
            actor_user_id=actor_user_id,
            classifier=classify_stub,
        )

    monkeypatch.setattr(sources_mod, "ingest_pr_merge", _patched_ingest)

    body = _merged_payload(install, repo)
    resp = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 200, resp.text

    bucket = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.slug == "pr-summaries",
                KnowledgeBucket.repo_id == repo.id,
            )
        )
    ).scalars().first()
    assert bucket is not None
    assert bucket.scope_kind == BucketScope.REPO

    articles = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.bucket_id == bucket.id)
        )
    ).scalars().all()
    assert len(articles) == 1
    art = articles[0]
    assert art.slug == "pr-777"
    prov = art.provenance or {}
    assert prov.get("kind") == "pr_merged"
    assert prov.get("pr_number") == 777


@pytest.mark.asyncio
async def test_pull_request_unmerged_webhook_skips_distiller(
    v1_client, db_session, seed_workspace, github_app_env
):
    """Opened/reopened PRs must not mint a knowledge bucket."""
    _, _, workspace = seed_workspace
    install, repo = await _seed_install_and_repo(db_session, workspace.id)

    body = _merged_payload(install, repo, pr_number=999)
    payload = json.loads(body)
    payload["action"] = "opened"
    payload["pull_request"]["merged"] = False
    payload["pull_request"]["state"] = "open"
    body = json.dumps(payload).encode("utf-8")

    resp = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 200

    # No knowledge bucket was created.
    buckets = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert buckets == []
