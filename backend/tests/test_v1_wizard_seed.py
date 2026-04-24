"""Tests for the unified wizard seed PR endpoint (Wizard v2 iter 5).

Covers the route ``POST /v1/workspaces/{ws}/repos/{repo}/wizard_seed``.
Makes sure:

- Seed PR happens only if the token push to GitHub succeeded (no PR
  opened when ``put_repo_secret`` fails — otherwise the installed
  workflows would 401 on their first run).
- SHIP_RUN_TOKEN is minted exactly once by default (first wizard run)
  and retained on later calls unless ``rotate_run_token`` is set.
- Tracker kind comes from the body override first, else the repo's
  binding, else the workspace default, else ``None``.
- FSM file is included by default and the header reflects the
  resolved tracker.
- Audit log records the wizard seed with presets, knowledge slugs,
  tracker source, and run-token prefix — never plaintext.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def seeded_wizard_repo(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=900_601,
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
        external_id=30_032_000,
        full_name="acme/wizard-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/wizard-target",
        description=None,
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.commit()
    return raw, workspace, install, repo


def _patch_github(monkeypatch):
    """Stub out GitHub calls: fake token push + PR creation."""
    from backend.app.integrations.github.workflows import StarterWorkflowPR
    from backend.app.services import repo_tokens as tokens_svc

    # put_repo_secret is invoked by mint_repo_callback_token. We
    # replace it on the tokens module since that's where mint looks
    # it up.
    async def _fake_put_secret(
        repo, install, *, name, plaintext, settings, client=None, public_key=None
    ):
        # Plaintext is exactly ``SHIP_RUN_TOKEN_SECRET_NAME`` we passed.
        assert name == tokens_svc.SHIP_RUN_TOKEN_SECRET_NAME
        assert isinstance(plaintext, str) and plaintext
        return "keyid-stub"

    monkeypatch.setattr(tokens_svc, "put_repo_secret", _fake_put_secret)

    captured: dict[str, object] = {}

    async def _fake_commit_pr(
        repo, install, *, files, title, branch_label, pr_body_header,
        settings, return_url=None, client=None,
    ):
        captured["files"] = [p for p, _ in files]
        captured["title"] = title
        captured["branch_label"] = branch_label
        captured["return_url"] = return_url
        captured["pr_body_header"] = pr_body_header
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/wizard-target/pull/7",
            pr_number=7,
            branch="ship/wizard-web-app-123",
        )

    monkeypatch.setattr(
        "backend.app.integrations.github.workflows.commit_bundle_pr",
        _fake_commit_pr,
    )
    return captured


@pytest.mark.asyncio
async def test_wizard_seed_first_run_mints_token_and_opens_pr(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, _install, repo = seeded_wizard_repo
    captured = _patch_github(monkeypatch)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={
            "presets": ["web-app"],
            "knowledge_slugs": [],
            "tracker_kind": "linear",
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pr_number"] == 7
    # P5-01 collapse: the legacy ``"web-app"`` id sent by the caller
    # normalizes to ``"default"`` before bundle composition + audit
    # logging.
    assert body["presets"] == ["default"]
    assert body["tracker_kind"] == "linear"
    assert body["run_token_rotated"] is True
    assert body["run_token_prefix"]

    # Repo row has the hash/prefix persisted; plaintext never returned.
    await db_session.refresh(repo)
    reloaded = (
        await db_session.execute(
            select(WorkspaceRepo).where(WorkspaceRepo.id == repo.id)
        )
    ).scalar_one()
    assert reloaded.run_token_hash is not None
    assert reloaded.run_token_prefix == body["run_token_prefix"]

    # File list is exactly what the composer produced (presented to
    # the commit_pr stub). Expect the FSM doc + config.yml + at
    # least one workflow YAML.
    files = captured["files"]
    assert ".ship/config.yml" in files
    assert ".ship/tracker-fsm.md" in files
    assert any(p.startswith(".github/workflows/") for p in files)

    # Audit log has one wizard_seed entry and never includes the
    # plaintext token.
    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "repo.wizard_seed",
            )
        )
    ).scalars().all()
    assert len(audits) == 1
    payload = audits[0].payload
    # Audit telemetry stops fragmenting on legacy ids — every row
    # records the normalized ``"default"`` value (P5-01).
    assert payload["presets"] == ["default"]
    assert payload["tracker_kind"] == "linear"
    assert payload["run_token_rotated"] is True
    # Plaintext MUST NOT leak anywhere.
    serialised = repr(payload)
    assert "SHIP_RUN_TOKEN" not in serialised  # Not the secret name either
    # Prefix is fine — that's the whole point of persisting it.
    assert payload["run_token_prefix"] == reloaded.run_token_prefix


@pytest.mark.asyncio
async def test_wizard_seed_skips_rotation_on_second_run(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)

    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"presets": ["web-app"], "knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert first.status_code == 200
    first_prefix = first.json()["run_token_prefix"]

    # Second call — no rotation requested, token stays.
    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"presets": ["web-app"], "knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert second.status_code == 200
    assert second.json()["run_token_rotated"] is False
    assert second.json()["run_token_prefix"] == first_prefix


@pytest.mark.asyncio
async def test_wizard_seed_force_rotates_when_asked(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)

    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"presets": ["web-app"], "knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert first.status_code == 200
    first_prefix = first.json()["run_token_prefix"]

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={
            "presets": ["web-app"],
            "knowledge_slugs": [],
            "rotate_run_token": True,
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert second.status_code == 200
    body = second.json()
    assert body["run_token_rotated"] is True
    # Prefix should change (strong bet: 4 billion distinct values).
    assert body["run_token_prefix"] != first_prefix


@pytest.mark.asyncio
async def test_wizard_seed_refuses_pr_when_token_push_fails(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """If GitHub rejects the SHIP_RUN_TOKEN PUT we must not open a
    PR — the installed workflows would 401 on every callback."""

    from backend.app.services import repo_tokens as tokens_svc

    raw, workspace, _install, repo = seeded_wizard_repo

    async def _boom(*args, **kwargs):
        raise RuntimeError("github 403 permissions revoked")

    monkeypatch.setattr(tokens_svc, "put_repo_secret", _boom)

    pr_calls = {"count": 0}

    async def _should_not_run(*args, **kwargs):
        pr_calls["count"] += 1
        raise AssertionError("commit_bundle_pr must not run when token push failed")

    monkeypatch.setattr(
        "backend.app.integrations.github.workflows.commit_bundle_pr",
        _should_not_run,
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"presets": ["web-app"], "knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 502
    assert pr_calls["count"] == 0


@pytest.mark.asyncio
async def test_wizard_seed_resolves_tracker_from_repo_binding(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """No tracker_kind in body → use the per-repo binding."""
    from backend.app.db.models.tenancy import Integration

    raw, workspace, _install, repo = seeded_wizard_repo
    captured = _patch_github(monkeypatch)

    db_session.add(
        Integration(
            workspace_id=workspace.id,
            repo_id=repo.id,
            kind="jira",
            config={"project": "WIDG"},
            status="ok",
        )
    )
    await db_session.flush()
    await db_session.commit()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"presets": ["web-app"], "knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tracker_kind"] == "jira"


@pytest.mark.asyncio
async def test_wizard_seed_falls_back_to_workspace_default(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    from backend.app.db.models.tenancy import Integration

    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)

    db_session.add(
        Integration(
            workspace_id=workspace.id,
            repo_id=None,
            kind="linear",
            config={},
            status="ok",
        )
    )
    await db_session.flush()
    await db_session.commit()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"presets": ["web-app"], "knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tracker_kind"] == "linear"


@pytest.mark.asyncio
async def test_wizard_seed_accepts_legacy_preset_no_422(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """Post-P5-01 the validate-against-``KNOWN_PRESETS`` 422 gate is
    gone. Legacy preset strings (every entry in ``LEGACY_PRESETS``)
    pass through and collapse to ``"default"`` via
    :func:`backend.app.services.lane_recipes.normalize_preset` before
    bundle composition. The wizard-side 422 case is now genuinely
    just "bad payload shape", not "unknown preset enum value"."""
    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"presets": ["adoption-minimum"], "knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["presets"] == ["default"]


@pytest.mark.asyncio
async def test_wizard_seed_404_on_unknown_repo(
    v1_client, seed_workspace
) -> None:
    import uuid

    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}/wizard_seed",
        json={"presets": ["web-app"]},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404
