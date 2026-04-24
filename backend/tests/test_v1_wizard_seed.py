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


# ---------------------------------------------------------------------------
# P5-06 / P5-07 coverage — DEFAULT_BUNDLE collapse, CODEOWNERS routing,
# intel harvest dispatch, synthetic Lane sync.
# ---------------------------------------------------------------------------


def _patch_codeowners_missing(monkeypatch):
    """Stub :func:`resolve_codeowners` to a CODEOWNERS-not-found result.

    Default fixture for tests that don't care about the routing
    pre-seed step — keeps them from trying to talk to GitHub.
    """
    from backend.app.services import codeowners as codeowners_module
    from backend.app.services import wizard_seed_routing
    from backend.app.services.codeowners import CodeownersResolution

    async def _resolve(**_kwargs):
        return CodeownersResolution(
            rules=(),
            handles_by_path={},
            unresolved=(),
            fetched_from="missing",
            sha=None,
        )

    monkeypatch.setattr(codeowners_module, "resolve_codeowners", _resolve)
    monkeypatch.setattr(wizard_seed_routing, "resolve_codeowners", _resolve)


def _patch_intel_inline_skip(monkeypatch):
    """Stub the inline harvest path so wizard tests don't hit GitHub.

    The default for every test below — when a test wants the real
    inline path / a redis pool, it overrides this with its own
    monkeypatch on ``request.app.state.redis_pool`` or on the
    harvester directly.
    """
    import uuid as _uuid

    from backend.app.services import repo_intel as repo_intel_module
    from backend.app.services.repo_intel import HarvestReport

    async def _harvest(**_kwargs):
        return HarvestReport(
            intel_id=_uuid.uuid4(),
            version=1,
            duration_ms=0,
            files_examined=0,
            languages_detected=0,
            knowledge_articles_written=0,
        )

    monkeypatch.setattr(repo_intel_module, "harvest_repo_intel", _harvest)


@pytest.mark.asyncio
async def test_wizard_seed_uses_default_bundle_regardless_of_payload_presets(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """P5-06 collapse: payload's ``presets`` list is silently ignored.

    The composed file list MUST reflect the canonical
    ``DEFAULT_BUNDLE`` shape (config + at least one workflow + ad-hoc
    runner + repo-intel placeholder + v2 marker) regardless of what
    the legacy FE/CLI sends in the body.
    """
    raw, workspace, _install, repo = seeded_wizard_repo
    captured = _patch_github(monkeypatch)
    _patch_codeowners_missing(monkeypatch)
    _patch_intel_inline_skip(monkeypatch)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={
            # Caller asks for a tiny single-pattern bundle. We must
            # ignore this and seed the canonical default.
            "presets": ["scan-security-deps"],
            "knowledge_slugs": [],
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text

    files = captured["files"]
    assert ".ship/config.yml" in files
    assert ".ship/state/wizard-seed.v2.json" in files
    assert ".ship/knowledge/repo-intel.md" in files
    assert any(p.endswith("/adhoc-agent-run.yml") for p in files)


@pytest.mark.asyncio
async def test_wizard_seed_creates_routing_rules_from_codeowners(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """One resolvable CODEOWNERS owner → one ``code_owner`` routing
    rule with ``target_type='user'`` and the user's id."""
    from backend.app.db.models.inbox import InboxRoutingRule
    from backend.app.services import codeowners as codeowners_module
    from backend.app.services import wizard_seed_routing
    from backend.app.services.codeowners import (
        CodeownersResolution,
        CodeownersRule,
        ResolvedOwner,
    )

    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)
    _patch_intel_inline_skip(monkeypatch)

    # Resolve to the workspace owner so the routing rule has a real
    # user_id to bind to (the one ``seed_workspace`` creates).
    from backend.app.db.models.tenancy import WorkspaceMember

    owner_member = (
        await db_session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.role == "owner",
            )
        )
    ).scalar_one()
    owner_uid = owner_member.user_id

    rule = CodeownersRule(path_pattern="*", owners=("@owner",))
    owner = ResolvedOwner(
        raw="@owner", kind="user", user_id=owner_uid, label="Owner"
    )
    resolution = CodeownersResolution(
        rules=(rule,),
        handles_by_path={"*": (owner,)},
        unresolved=(),
        fetched_from="default_branch",
        sha="cafebabe",
    )

    async def _resolve(**_kwargs):
        return resolution

    monkeypatch.setattr(codeowners_module, "resolve_codeowners", _resolve)
    monkeypatch.setattr(wizard_seed_routing, "resolve_codeowners", _resolve)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["codeowners"]["file_found"] is True
    assert body["codeowners"]["routing_rules_created"] == 1
    assert body["codeowners"]["unresolved_owners"] == []

    rows = (
        await db_session.execute(
            select(InboxRoutingRule).where(
                InboxRoutingRule.workspace_id == workspace.id,
                InboxRoutingRule.handle_key == "code_owner",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.target_type == "user"
    assert row.target_value == str(owner_uid)
    assert row.is_enabled is True


@pytest.mark.asyncio
async def test_wizard_seed_skips_team_only_owners_in_routing(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """Team-only / unresolvable CODEOWNERS lines yield zero rules but
    DO surface in ``unresolved_owners`` so the FE can nudge the admin
    to invite the missing teammate."""
    from backend.app.db.models.inbox import InboxRoutingRule
    from backend.app.services import codeowners as codeowners_module
    from backend.app.services import wizard_seed_routing
    from backend.app.services.codeowners import (
        CodeownersResolution,
        CodeownersRule,
        ResolvedOwner,
    )

    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)
    _patch_intel_inline_skip(monkeypatch)

    rule = CodeownersRule(path_pattern="*", owners=("@acme/devs",))
    team_owner = ResolvedOwner(
        raw="@acme/devs", kind="team", user_id=None, label="@acme/devs"
    )
    resolution = CodeownersResolution(
        rules=(rule,),
        handles_by_path={"*": (team_owner,)},
        unresolved=("@acme/devs",),
        fetched_from="default_branch",
        sha="bee",
    )

    async def _resolve(**_kwargs):
        return resolution

    monkeypatch.setattr(codeowners_module, "resolve_codeowners", _resolve)
    monkeypatch.setattr(wizard_seed_routing, "resolve_codeowners", _resolve)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["codeowners"]["file_found"] is True
    assert body["codeowners"]["rules_count"] == 0
    assert body["codeowners"]["routing_rules_created"] == 0
    assert "@acme/devs" in body["codeowners"]["unresolved_owners"]

    rows = (
        await db_session.execute(
            select(InboxRoutingRule).where(
                InboxRoutingRule.workspace_id == workspace.id,
                InboxRoutingRule.handle_key == "code_owner",
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_wizard_seed_idempotent_routing(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """A second wizard call MUST NOT create duplicate routing rules.

    We assert on both the response counter and the row count — the
    counter alone could mask a duplicate-row bug if the unique
    constraint silently swallowed the second insert."""
    from backend.app.db.models.inbox import InboxRoutingRule
    from backend.app.db.models.tenancy import WorkspaceMember
    from backend.app.services import codeowners as codeowners_module
    from backend.app.services import wizard_seed_routing
    from backend.app.services.codeowners import (
        CodeownersResolution,
        CodeownersRule,
        ResolvedOwner,
    )

    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)
    _patch_intel_inline_skip(monkeypatch)

    owner_member = (
        await db_session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.role == "owner",
            )
        )
    ).scalar_one()
    owner_uid = owner_member.user_id

    rule = CodeownersRule(path_pattern="*", owners=("@owner",))
    owner = ResolvedOwner(
        raw="@owner", kind="user", user_id=owner_uid, label="Owner"
    )
    resolution = CodeownersResolution(
        rules=(rule,),
        handles_by_path={"*": (owner,)},
        unresolved=(),
        fetched_from="default_branch",
        sha="dup",
    )

    async def _resolve(**_kwargs):
        return resolution

    monkeypatch.setattr(codeowners_module, "resolve_codeowners", _resolve)
    monkeypatch.setattr(wizard_seed_routing, "resolve_codeowners", _resolve)

    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["codeowners"]["routing_rules_created"] == 1

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert second.status_code == 200, second.text
    # Idempotent — found the file again, but didn't write a new row.
    assert second.json()["codeowners"]["routing_rules_created"] == 0
    assert second.json()["codeowners"]["file_found"] is True

    rows = (
        await db_session.execute(
            select(InboxRoutingRule).where(
                InboxRoutingRule.workspace_id == workspace.id,
                InboxRoutingRule.handle_key == "code_owner",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_wizard_seed_dispatches_intel_harvest_inline_in_dev(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """No redis pool on ``app.state`` → harvest runs inline and the
    response carries ``enqueued=False`` + a real ``intel_id``."""
    import uuid as _uuid

    from backend.app.services import repo_intel as repo_intel_module
    from backend.app.services.repo_intel import HarvestReport

    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)
    _patch_codeowners_missing(monkeypatch)

    fake_intel_id = _uuid.uuid4()
    calls: dict[str, int] = {"count": 0}

    async def _harvest(**_kwargs):
        calls["count"] += 1
        return HarvestReport(
            intel_id=fake_intel_id,
            version=1,
            duration_ms=12,
            files_examined=42,
            languages_detected=2,
            knowledge_articles_written=1,
        )

    monkeypatch.setattr(repo_intel_module, "harvest_repo_intel", _harvest)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    intel = resp.json()["intel"]
    assert intel is not None
    assert intel["enqueued"] is False
    assert intel["job_id"] is None
    assert intel["intel_id"] == str(fake_intel_id)
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_wizard_seed_dispatches_intel_harvest_to_redis_in_prod(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """A redis pool on ``app.state`` → enqueue path: ``enqueued=True``,
    ``job_id`` populated, ``intel_id`` ``None`` (the worker hasn't
    written the row yet)."""
    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)
    _patch_codeowners_missing(monkeypatch)

    enqueued: dict[str, object] = {}

    class _FakePool:
        async def enqueue_job(self, *args, **kwargs):
            enqueued["args"] = args
            enqueued["kwargs"] = kwargs

    # The route reads ``request.app.state.redis_pool``; v1_client
    # binds against the FastAPI app fixture so we set the attribute
    # there.
    from backend.app.main import app as _app

    _app.state.redis_pool = _FakePool()
    try:
        resp = await v1_client.post(
            f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
            json={"knowledge_slugs": []},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200, resp.text
        intel = resp.json()["intel"]
        assert intel is not None
        assert intel["enqueued"] is True
        assert isinstance(intel["job_id"], str) and intel["job_id"]
        assert intel["intel_id"] is None
        # Sanity: the queue actually saw the call.
        assert enqueued["args"][0] == "harvest_repo_intel_job"
        assert enqueued["args"][1] == str(workspace.id)
        assert enqueued["args"][2] == str(repo.id)
    finally:
        # Tests share the FastAPI app instance; leaking app state
        # into the next test would make the inline-mode test above
        # flake. Reset deterministically.
        if hasattr(_app.state, "redis_pool"):
            del _app.state.redis_pool


@pytest.mark.asyncio
async def test_wizard_seed_creates_synthetic_lanes_immediately(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """P5-07: Lane rows are written BEFORE the seed PR merges so the
    new Inbox / Coverage / Automations surfaces light up immediately."""
    from backend.app.db.models.lanes import Lane

    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)
    _patch_codeowners_missing(monkeypatch)
    _patch_intel_inline_skip(monkeypatch)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["synthetic_lanes_created"] >= 1

    rows = (
        await db_session.execute(
            select(Lane).where(Lane.repo_id == repo.id)
        )
    ).scalars().all()
    assert len(rows) == body["synthetic_lanes_created"]
    # Every fresh row carries the synthetic origin so the post-merge
    # syncer can promote them in place (P5-07).
    assert all(r.origin == "wizard_seed_synthetic" for r in rows)


@pytest.mark.asyncio
async def test_wizard_seed_codeowners_404_yields_summary_with_file_found_false(
    monkeypatch, v1_client, db_session, seeded_wizard_repo
) -> None:
    """Repo without a CODEOWNERS file → routing summary still returned
    with ``file_found=False`` so the FE can render "no CODEOWNERS yet,
    add one to enable code_owner routing"."""
    from backend.app.db.models.inbox import InboxRoutingRule

    raw, workspace, _install, repo = seeded_wizard_repo
    _patch_github(monkeypatch)
    _patch_codeowners_missing(monkeypatch)
    _patch_intel_inline_skip(monkeypatch)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed",
        json={"knowledge_slugs": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["codeowners"] is not None
    assert body["codeowners"]["file_found"] is False
    assert body["codeowners"]["routing_rules_created"] == 0

    rows = (
        await db_session.execute(
            select(InboxRoutingRule).where(
                InboxRoutingRule.workspace_id == workspace.id,
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_synthetic_lane_sync_idempotent(
    db_session, seeded_wizard_repo
) -> None:
    """Running the synthetic sync twice is a no-op the second time —
    the wizard's re-run path relies on this."""
    from backend.app.db.models.lanes import Lane
    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.synthetic_lane_sync import synthetic_lane_sync

    _raw, workspace, _install, repo = seeded_wizard_repo

    first = await synthetic_lane_sync(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        bundle=DEFAULT_BUNDLE,
    )
    assert first >= 1

    second = await synthetic_lane_sync(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        bundle=DEFAULT_BUNDLE,
    )
    assert second == 0

    rows = (
        await db_session.execute(
            select(Lane).where(Lane.repo_id == repo.id)
        )
    ).scalars().all()
    assert len(rows) == first


def _patch_lanes_gateway(monkeypatch, *, content: str | None):
    """Stub :class:`GitHubCodeHost` for ``sync_lanes_for_repo``.

    Mirrors the ``patch_gateway`` fixture in ``test_lanes_sync.py``
    — duplicated here rather than imported so the wizard-suite
    keeps its own dependency footprint.
    """

    class _FakeBlob:
        def __init__(self, body: str, sha: str = "deadbeef"):
            self.content = body
            self.encoding = "utf-8"
            self.sha = sha
            self.path = ".ship/config.yml"
            self.ref = "main"
            self.size = len(body)

    class _FakeGateway:
        def __init__(self, body: str | None):
            self._body = body

        async def get_blob(self, _ref, *, path, ref_sha=None):
            if self._body is None:
                raise FileNotFoundError(path)
            return _FakeBlob(self._body)

    from backend.app.services import lanes_sync as lanes_sync_module

    def _ctor(*_args, **_kwargs):
        return _FakeGateway(content)

    monkeypatch.setattr(lanes_sync_module, "GitHubCodeHost", _ctor)


@pytest.mark.asyncio
async def test_real_merge_sync_promotes_synthetic_origin_to_merged(
    monkeypatch, db_session, seeded_wizard_repo
) -> None:
    """A post-merge ``sync_lanes_for_repo`` whose merged config still
    references a wizard-seeded lane MUST flip ``origin`` in place
    (preserves ``last_run_at`` / ``last_run_status``)."""
    from datetime import datetime, timezone

    from backend.app.db.models.lanes import Lane
    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.lanes_sync import sync_lanes_for_repo
    from backend.app.services.synthetic_lane_sync import synthetic_lane_sync

    _raw, workspace, install, repo = seeded_wizard_repo

    await synthetic_lane_sync(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        bundle=DEFAULT_BUNDLE,
    )
    await db_session.flush()

    # Pick a synthetic row whose lane shape round-trips through YAML
    # cleanly. ``pr_review`` carries ``pattern: **`` which YAML
    # rejects as a bare scalar; we want a lane whose pattern is a
    # plain identifier so the reconciler can match on
    # ``(lane_id, kind)`` rather than crashing on parse.
    synth = (
        await db_session.execute(
            select(Lane).where(
                Lane.repo_id == repo.id,
                Lane.lane_id == "scan-security-deps",
            )
        )
    ).scalar_one()
    pinned_run = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
    synth.last_run_at = pinned_run
    synth.last_run_status = "success"
    await db_session.flush()
    synth_lane_id = synth.lane_id
    synth_kind = synth.kind
    synth_pattern = synth.pattern

    cron = synth.cron or "0 7 * * *"
    yaml_lanes_block = (
        f"  {synth_lane_id}:\n"
        f"    schedule: \"{cron}\"\n"
        f"    pattern: {synth_pattern}\n"
    )
    assert synth_kind == "schedule", (
        "test fixture changed: expected scan-security-deps to be a "
        "schedule lane"
    )
    config_yaml = (
        "version: 2\n"
        "lanes:\n"
        f"{yaml_lanes_block}"
    )
    _patch_lanes_gateway(monkeypatch, content=config_yaml)

    await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )
    await db_session.flush()

    promoted = (
        await db_session.execute(
            select(Lane).where(
                Lane.repo_id == repo.id,
                Lane.lane_id == synth_lane_id,
            )
        )
    ).scalar_one()
    assert promoted.origin == "merged"
    # In-place promotion must NOT clobber run history.
    assert promoted.last_run_at == pinned_run
    assert promoted.last_run_status == "success"


@pytest.mark.asyncio
async def test_real_merge_sync_reconciles_when_config_diverges(
    monkeypatch, db_session, seeded_wizard_repo
) -> None:
    """Synthetic rows for lanes the merged config no longer references
    fall through to the standard remove pass — no zombies."""
    from backend.app.db.models.lanes import Lane
    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.lanes_sync import sync_lanes_for_repo
    from backend.app.services.synthetic_lane_sync import synthetic_lane_sync

    _raw, workspace, install, repo = seeded_wizard_repo

    await synthetic_lane_sync(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        bundle=DEFAULT_BUNDLE,
    )
    await db_session.flush()

    # A merged config declares a brand-new lane, dropping ALL of the
    # synthetic ones — the operator-edited-the-PR case.
    config_yaml = (
        "version: 2\n"
        "lanes:\n"
        "  custom_daily:\n"
        "    schedule: \"0 8 * * 1-5\"\n"
        "    pattern: scan-tech-debt\n"
    )
    _patch_lanes_gateway(monkeypatch, content=config_yaml)

    await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(Lane).where(Lane.repo_id == repo.id)
        )
    ).scalars().all()
    # Synthetic rows are gone; only the operator-declared lane remains.
    assert len(rows) == 1
    assert rows[0].lane_id == "custom_daily"
    assert rows[0].origin == "merged"
