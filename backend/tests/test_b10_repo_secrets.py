"""B10 — Per-repo Ship-managed secrets (encrypted storage + GitHub sync).

Covers the core contract:

- Name validation accepts the GitHub grammar and rejects ``GITHUB_*``.
- Plaintext is encrypted at rest (``ciphertext`` bytes decrypt round
  trip, no plaintext column anywhere).
- ``POST`` creates a row + syncs to GitHub; the list endpoint never
  returns plaintext; ``masked_hint`` surfaces the last 4 characters.
- ``POST`` with an existing name rotates (same row, new ciphertext,
  different ``updated_at``).
- ``DELETE`` removes from GitHub first, then DB.
- Admin-only gate: non-admin members get 403.
- Sealed-box encryption uses the repo's public key — we verify by
  round-tripping through the matching private key in-test.

GitHub HTTP is mocked via :class:`httpx.MockTransport`; none of the
tests actually reach github.com.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import pytest_asyncio
from nacl import encoding, public
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_repo(db_session, seed_workspace):
    """Workspace + GitHub installation + one activated repo."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, _raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=777_001,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_001_001,
        full_name="acme/prod",
        default_branch="main",
        private=True,
        html_url="https://github.com/acme/prod",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return workspace, install, repo


@pytest.fixture
def github_secrets_env(monkeypatch):
    """Populate the GitHub App auth env + provide a valid ENCRYPTION_KEY."""
    from backend.app.core.config import get_settings

    # cryptography's Fernet wants a 32-byte urlsafe base64 key. Any
    # deterministic value works — we're exercising encrypt/decrypt,
    # not key security.
    monkeypatch.setenv(
        "ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"B" * 32).decode("ascii"),
    )
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv(
        "GITHUB_APP_PRIVATE_KEY",
        # A fake PEM — we monkeypatch ``fetch_installation_token`` so
        # no signing actually happens.
        "-----BEGIN RSA PRIVATE KEY-----\nX\n-----END RSA PRIVATE KEY-----",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def mock_github(monkeypatch):
    """Intercept ``httpx`` traffic to ``api.github.com`` for Actions-secrets.

    Exposes ``state["public_key"]`` (Curve25519 private key so we can
    decrypt the sealed box round-trip) and ``state["writes"]`` /
    ``state["deletes"]`` so tests can assert exactly what the sync
    pushed upstream.
    """
    private_key = public.PrivateKey.generate()
    public_key = private_key.public_key
    key_b64 = public_key.encode(encoder=encoding.Base64Encoder).decode("ascii")
    state: dict[str, Any] = {
        "public_key": private_key,  # used to decrypt in assertions
        "key_id": "test-key-42",
        "writes": [],  # list of (owner/repo, name, decrypted_value)
        "deletes": [],  # list of (owner/repo, name)
        "public_key_status": 200,
        "put_status": 201,
        "delete_status": 204,
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # /repos/{owner}/{repo}/actions/secrets/public-key
        if path.endswith("/actions/secrets/public-key"):
            if state["public_key_status"] != 200:
                return httpx.Response(state["public_key_status"], json={"message": "forbidden"})
            return httpx.Response(
                200,
                json={"key_id": state["key_id"], "key": key_b64},
            )
        # /repos/{owner}/{repo}/actions/secrets/{name}
        if "/actions/secrets/" in path:
            full = path.split("/repos/", 1)[1]
            owner_repo, _, name = full.partition("/actions/secrets/")
            if request.method == "PUT":
                body = json.loads(request.content or b"{}")
                encrypted = base64.b64decode(body["encrypted_value"])
                plaintext = public.SealedBox(private_key).decrypt(encrypted)
                state["writes"].append(
                    (owner_repo, name, plaintext.decode("utf-8"))
                )
                return httpx.Response(state["put_status"])
            if request.method == "DELETE":
                state["deletes"].append((owner_repo, name))
                return httpx.Response(state["delete_status"])
        # Installation token: mint a fake bearer so we don't need JWTs.
        if path.endswith("/access_tokens"):
            return httpx.Response(
                201,
                json={
                    "token": "ghs_mocked_installation_token",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        return httpx.Response(404, json={"message": "unmocked", "path": path})

    transport = httpx.MockTransport(_handler)

    # Patch fetch_installation_token to bypass the real JWT signing
    # path (our fake PEM won't load with cryptography).
    async def _fake_token(*_a, **_kw):
        return "ghs_mocked_installation_token"

    monkeypatch.setattr(
        "backend.app.integrations.github.app_auth.fetch_installation_token",
        _fake_token,
    )
    monkeypatch.setattr(
        "backend.app.integrations.github.workflows.fetch_installation_token",
        _fake_token,
    )
    monkeypatch.setattr(
        "backend.app.integrations.github.actions_secrets.fetch_installation_token",
        _fake_token,
    )

    # Redirect httpx.AsyncClient to our MockTransport. This covers
    # the common "client=None → create a client" branch inside
    # ``_request``.
    orig_async_client = httpx.AsyncClient

    def _patched_async_client(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched_async_client)

    return state


# ---------------------------------------------------------------------------
# Unit: name validation
# ---------------------------------------------------------------------------


def test_validate_secret_name_accepts_canonical_form() -> None:
    from backend.app.services.repo_secrets import validate_secret_name

    assert validate_secret_name("anthropic_api_key") == "ANTHROPIC_API_KEY"
    assert validate_secret_name("FOO_BAR_42") == "FOO_BAR_42"
    assert validate_secret_name(" spaced ") == "SPACED"


def test_validate_secret_name_rejects_bad_shapes() -> None:
    import pytest

    from backend.app.services.repo_secrets import validate_secret_name

    with pytest.raises(ValueError):
        validate_secret_name("")
    with pytest.raises(ValueError):
        validate_secret_name("1STARTS_WITH_DIGIT")
    with pytest.raises(ValueError):
        validate_secret_name("has-hyphen")
    with pytest.raises(ValueError):
        validate_secret_name("GITHUB_RESERVED_PREFIX")
    with pytest.raises(ValueError):
        validate_secret_name("A" * 300)


# ---------------------------------------------------------------------------
# Integration: upsert through the service layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_stores_ciphertext_and_syncs_to_github(
    db_session, seed_repo, github_secrets_env, mock_github
) -> None:
    """Happy path: encrypt at rest, push plaintext to GitHub via sealed box."""
    from backend.app.core.config import get_settings
    from backend.app.db.models.repo_secrets import (
        SYNC_STATUS_SYNCED,
        RepoSecret,
    )
    from backend.app.security.encryption import decrypt
    from backend.app.services.repo_secrets import upsert_repo_secret

    _ws, install, repo = seed_repo

    row = await upsert_repo_secret(
        db_session,
        repo,
        install,
        settings=get_settings(),
        name="anthropic_api_key",
        plaintext="sk-ant-super-secret-value-abcd",
        description="Claude key",
        actor_user_id=None,
    )
    assert row.name == "ANTHROPIC_API_KEY"
    assert row.sync_status == SYNC_STATUS_SYNCED
    assert row.github_key_id == mock_github["key_id"]
    assert row.masked_hint == "abcd"

    # Stored value round-trips. ``ciphertext`` is bytes; decrypting
    # via the same Fernet machinery reproduces the plaintext.
    plaintext = decrypt(bytes(row.ciphertext))
    assert plaintext == "sk-ant-super-secret-value-abcd"

    # GitHub received the correctly-decrypted plaintext (sealed box
    # through our in-test private key).
    assert mock_github["writes"] == [
        ("acme/prod", "ANTHROPIC_API_KEY", "sk-ant-super-secret-value-abcd")
    ]

    # The DB row persisted.
    stored = (
        await db_session.execute(
            select(RepoSecret).where(RepoSecret.id == row.id)
        )
    ).scalar_one()
    assert stored.sync_status == SYNC_STATUS_SYNCED
    assert stored.last_synced_at is not None


@pytest.mark.asyncio
async def test_upsert_rotates_existing_secret(
    db_session, seed_repo, github_secrets_env, mock_github
) -> None:
    """Second upsert keeps the same row and overwrites the ciphertext."""
    from backend.app.core.config import get_settings
    from backend.app.security.encryption import decrypt
    from backend.app.services.repo_secrets import upsert_repo_secret

    _ws, install, repo = seed_repo
    first = await upsert_repo_secret(
        db_session,
        repo,
        install,
        settings=get_settings(),
        name="LINEAR_API_KEY",
        plaintext="lin_api_11111111",
        description=None,
        actor_user_id=None,
    )
    second = await upsert_repo_secret(
        db_session,
        repo,
        install,
        settings=get_settings(),
        name="LINEAR_API_KEY",
        plaintext="lin_api_22222222",
        description="rotated",
        actor_user_id=None,
    )
    assert first.id == second.id
    assert decrypt(bytes(second.ciphertext)) == "lin_api_22222222"
    assert second.description == "rotated"

    # Both GitHub writes landed in order — upsert calls are
    # idempotent on name at both layers (DB unique + GitHub PUT).
    assert [w[2] for w in mock_github["writes"]] == [
        "lin_api_11111111",
        "lin_api_22222222",
    ]


@pytest.mark.asyncio
async def test_upsert_marks_sync_error_when_github_rejects(
    db_session, seed_repo, github_secrets_env, mock_github
) -> None:
    """Row survives even when GitHub rejects; sync_status flips to error."""
    from backend.app.core.config import get_settings
    from backend.app.db.models.repo_secrets import SYNC_STATUS_ERROR
    from backend.app.services.repo_secrets import (
        SecretSyncError,
        upsert_repo_secret,
    )

    _ws, install, repo = seed_repo
    mock_github["put_status"] = 422  # GitHub "unprocessable entity"

    with pytest.raises(SecretSyncError) as excinfo:
        await upsert_repo_secret(
            db_session,
            repo,
            install,
            settings=get_settings(),
            name="SENTRY_AUTH_TOKEN",
            plaintext="sntrys_example_token_xyz9",
            description=None,
            actor_user_id=None,
        )
    row = excinfo.value.secret
    assert row.sync_status == SYNC_STATUS_ERROR
    assert row.sync_error is not None
    assert "GitHub" in row.sync_error


@pytest.mark.asyncio
async def test_delete_removes_row_and_calls_github(
    db_session, seed_repo, github_secrets_env, mock_github
) -> None:
    """Delete sends DELETE to GitHub, then removes the DB row."""
    from backend.app.core.config import get_settings
    from backend.app.db.models.repo_secrets import RepoSecret
    from backend.app.services.repo_secrets import (
        delete_repo_secret_row,
        upsert_repo_secret,
    )

    _ws, install, repo = seed_repo
    row = await upsert_repo_secret(
        db_session,
        repo,
        install,
        settings=get_settings(),
        name="FOO_BAR",
        plaintext="plain-foo-bar-1234",
        description=None,
        actor_user_id=None,
    )
    await delete_repo_secret_row(
        db_session, repo, install, settings=get_settings(), secret=row
    )

    assert mock_github["deletes"] == [("acme/prod", "FOO_BAR")]
    gone = (
        await db_session.execute(
            select(RepoSecret).where(RepoSecret.id == row.id)
        )
    ).scalar_one_or_none()
    assert gone is None


# ---------------------------------------------------------------------------
# API: auth + audit + plaintext never leaks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_plaintext_never_returned_from_list(
    v1_client, db_session, seed_repo, seed_workspace, github_secrets_env, mock_github
) -> None:
    """The list endpoint returns masked hints + status, never plaintext."""
    user, raw_token, workspace = seed_workspace
    _ws, _install, repo = seed_repo

    # Create a secret via the API so we exercise the route.
    r = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/secrets",
        json={
            "name": "API_TOKEN",
            "value": "super-secret-plaintext-12345",
            "description": "test",
        },
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "value" not in body
    assert "plaintext" not in body
    assert "ciphertext" not in body
    assert body["masked_hint"] == "2345"
    assert body["sync_status"] == "synced"

    listed = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/secrets",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    row = items[0]
    # No plaintext surface anywhere.
    for forbidden in ("value", "plaintext", "ciphertext"):
        assert forbidden not in row, (
            f"plaintext field {forbidden!r} leaked in list response"
        )
    assert row["name"] == "API_TOKEN"
    assert row["masked_hint"] == "2345"


@pytest.mark.asyncio
async def test_api_rejects_non_admin(
    v1_client, db_session, seed_repo, seed_workspace, github_secrets_env, mock_github
) -> None:
    """Only admins/owners can create secrets — viewers get 403."""
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        OrgMember,
        User,
        WorkspaceMember,
    )

    user, raw_token, workspace = seed_workspace
    _ws, _install, repo = seed_repo

    # Add a second user as a viewer (same org for fixture simplicity).
    viewer = User(email=f"viewer-{uuid.uuid4().hex[:6]}@example.com", display_name="V")
    db_session.add(viewer)
    await db_session.flush()
    org_member_stmt = select(OrgMember).where(OrgMember.user_id == user.id)
    existing = (await db_session.execute(org_member_stmt)).scalar_one()
    db_session.add(
        OrgMember(org_id=existing.org_id, user_id=viewer.id, role="org_member")
    )
    db_session.add(
        WorkspaceMember(
            workspace_id=workspace.id, user_id=viewer.id, role="viewer"
        )
    )
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=viewer.id,
            name="viewer",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=["workspace:read"],
        )
    )
    await db_session.flush()

    r = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/secrets",
        json={"name": "NOPE", "value": "x"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_api_creates_audit_log_without_plaintext(
    v1_client, db_session, seed_repo, seed_workspace, github_secrets_env, mock_github
) -> None:
    """Audit row captures hint + status, never the raw value."""
    from backend.app.db.models.tenancy import AuditLog

    user, raw_token, workspace = seed_workspace
    _ws, _install, repo = seed_repo

    r = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/secrets",
        json={"name": "AUDIT_CHECK", "value": "the-quick-brown-fox-jumps"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert r.status_code == 200

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.workspace_id == workspace.id)
        )
    ).scalars().all()
    audit = [r for r in rows if r.target_kind == "repo_secret"]
    assert len(audit) == 1
    entry = audit[0]
    assert entry.action == "repo_secret.created"
    # Plaintext must not appear anywhere in the audit payload.
    serialised = json.dumps(entry.payload)
    assert "the-quick-brown-fox-jumps" not in serialised
    assert "jumps" not in serialised  # no accidental tail leak either
    assert entry.payload["masked_hint"] == "umps"
    assert entry.payload["sync_status"] == "synced"


# ---------------------------------------------------------------------------
# Webhook: lazy PipelineRun registration for cron-triggered Ship workflows
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_ship_pipeline_without_run(db_session, seed_workspace):
    """Install + repo + enabled Ship pipeline that has *no* PipelineRun yet."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.pipelines import Pipeline

    _, _raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=555_002,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_002_001,
        full_name="acme/scheduled",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/scheduled",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    # The scheduled SDLC lane is cron-triggered by design — exactly
    # the shape we need to exercise lazy PipelineRun creation.
    pipeline = Pipeline(
        workspace_id=workspace.id,
        repo_id=repo.id,
        kind="daily_standup",
        name="Scheduled SDLC lane",
        workflow_id="scheduled-sdlc-lane",
        enabled=True,
        config={},
    )
    db_session.add(pipeline)
    await db_session.flush()
    return workspace, install, repo, pipeline


WEBHOOK_SECRET = "wh_b10_secret"


def _sign(body: bytes) -> str:
    import hashlib
    import hmac

    return "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


@pytest.fixture
def github_app_webhook_env(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_SLUG", "ship-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_cron_workflow_run_creates_pipeline_run_lazily(
    v1_client, db_session, seed_ship_pipeline_without_run, github_app_webhook_env
) -> None:
    """Schedule-triggered Ship workflow → webhook lazily registers PipelineRun."""
    from backend.app.db.models.pipelines import PipelineRun
    from backend.app.services.catalog import workflow_install_filename

    workspace, install, repo, pipeline = seed_ship_pipeline_without_run

    # Snapshot every attribute we need before the webhook call — the
    # handler ``expire_all()``s mid-flight and accessing a relationship
    # proxy afterwards triggers a lazy SELECT from a sync context which
    # SQLAlchemy refuses (MissingGreenlet).
    workspace_id = workspace.id
    pipeline_id = pipeline.id
    install_id = install.installation_id
    repo_external_id = repo.external_id
    repo_full_name = repo.full_name

    workflow_path = f".github/workflows/{workflow_install_filename(pipeline.workflow_id)}"
    payload = {
        "action": "completed",
        "installation": {"id": install_id},
        "repository": {"id": repo_external_id, "full_name": repo_full_name},
        "workflow_run": {
            "id": 909_909,
            "name": "Ship · Scheduled SDLC lane",
            "path": workflow_path,
            "event": "schedule",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": "cafe1234",
            "actor": {"login": "github-actions[bot]"},
            "html_url": f"https://github.com/{repo_full_name}/actions/runs/909909",
            "run_started_at": "2026-04-20T03:00:00Z",
            "updated_at": "2026-04-20T03:05:00Z",
        },
    }
    body = json.dumps(payload).encode("utf-8")

    r = await v1_client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert r.status_code == 200, r.text

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.workspace_id == workspace_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    created = rows[0]
    assert created.pipeline_id == pipeline_id
    assert created.trigger == "cron"
    # Webhook reconciliation should also close out the row on a
    # "completed + success" delivery — the lazy-create + reconcile
    # path is meant to land a terminal PipelineRun in one shot.
    assert created.status in {"succeeded", "completed"}
    metrics = (created.payload or {}).get("metrics") or {}
    assert metrics.get("gh_workflow_run_id") == 909_909
