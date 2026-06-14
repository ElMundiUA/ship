"""Tests for the SDLC bootstrap blueprint loader + readiness assessor (BS1).

Three layers:
- the blueprint loader parses the shipped web/mobile/desktop .md files
  (frontmatter capabilities/required/secrets + body checklist),
- ``assess_readiness`` is a pure function over (blueprint, paths,
  frameworks, secrets) — the bulk of the matrix lives here,
- ``build_readiness`` orchestrates intel + blueprint + a (faked) gateway
  + secret lister against a seeded DB row.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.repo_intel import RepoIntel
from backend.app.services.sdlc_blueprint import (
    Blueprint,
    BlueprintError,
    _split_frontmatter,
    load_all_blueprints,
    load_blueprint,
    parse_checklist,
)
from backend.app.db.models.inbox import InboxItem
from backend.app.services.sdlc_readiness import (
    assess_readiness,
    build_readiness,
    file_bootstrap_recommendation,
)
from sqlalchemy import select


# ---------------------------------------------------------------------------
# 1. Blueprint loader
# ---------------------------------------------------------------------------


def test_loads_shipped_blueprints() -> None:
    blueprints = load_all_blueprints()
    assert set(blueprints) >= {"web", "mobile", "desktop", "backend"}


def test_web_blueprint_shape() -> None:
    bp = load_blueprint("web")
    assert bp is not None
    assert bp.project_type == "web"
    assert bp.delivery == "docker"
    assert bp.environments == ("dev", "prod")
    assert "containerization" in bp.capabilities
    assert "Dockerfile" in bp.capabilities["containerization"]
    assert "containerization" in bp.required
    assert "CURSOR_API_KEY" in bp.secrets_required
    assert "REGISTRY_TOKEN" in bp.secrets_optional
    # Every required capability must be declared under capabilities.
    assert set(bp.required) <= set(bp.capabilities)


def test_all_required_capabilities_are_declared() -> None:
    for bp in load_all_blueprints().values():
        assert set(bp.required) <= set(bp.capabilities), bp.project_type


def test_unknown_project_type_returns_none() -> None:
    assert load_blueprint("embedded") is None
    assert load_blueprint(None) is None
    assert load_blueprint("library") is None


def test_shipped_blueprints_carry_version() -> None:
    for bp in load_all_blueprints().values():
        assert bp.version >= 1


def test_split_frontmatter_rejects_missing_fence() -> None:
    from pathlib import Path

    with pytest.raises(BlueprintError):
        _split_frontmatter("no frontmatter here\n", Path("x.md"))


def test_blueprint_checklist_parsed_with_actors() -> None:
    bp = load_blueprint("web")
    assert bp is not None
    assert bp.checklist, "web blueprint should have a checklist"
    actors = {item.actor for item in bp.checklist}
    assert "user" in actors
    assert "ship" in actors
    # The (you) marker is stripped from the visible text.
    for item in bp.checklist:
        assert "(you)" not in item.text.lower()


def test_parse_checklist_strips_markers_and_bold() -> None:
    body = (
        "## Checklist\n"
        "- [ ] **(you)** Registry token added (or `ghcr.io`).\n"
        "- [ ] `Dockerfile` builds a runnable image in CI.\n"
        "- not a checklist line\n"
        "- [x] Already-done item.\n"
    )
    items = parse_checklist(body)
    assert len(items) == 3
    assert items[0].actor == "user"
    assert items[0].text.startswith("Registry token added")
    assert items[1].actor == "ship"


# ---------------------------------------------------------------------------
# 2. assess_readiness (pure)
# ---------------------------------------------------------------------------


def _web_bp() -> Blueprint:
    bp = load_blueprint("web")
    assert bp is not None
    return bp


def test_assess_web_fully_ready() -> None:
    bp = _web_bp()
    paths = [
        "package.json",
        "src/app/page.tsx",
        "src/app/page.test.tsx",  # unit_tests glob
        "e2e/login.spec.ts",  # e2e via spec glob
        "playwright.config.ts",  # e2e config
        "Dockerfile",  # containerization
        "docker-compose.yml",  # dev_env
        ".github/workflows/deploy.yml",  # prod_promotion
    ]
    report = assess_readiness(
        blueprint=bp,
        paths=paths,
        frameworks=["next.js", "react"],
        package_managers=["npm"],
        secret_names=["CURSOR_API_KEY"],
    )
    assert report.gaps == ()
    assert report.missing_required_secrets == ()
    assert report.ready is True
    # every required capability satisfied
    satisfied = {c.capability for c in report.capabilities if c.satisfied}
    assert set(bp.required) <= satisfied


def test_assess_web_with_capability_gaps() -> None:
    bp = _web_bp()
    paths = [
        "package.json",
        "src/app/page.tsx",
        "src/app/page.test.tsx",  # only unit tests present
    ]
    report = assess_readiness(
        blueprint=bp,
        paths=paths,
        frameworks=["next.js"],
        secret_names=["CURSOR_API_KEY"],
    )
    assert "containerization" in report.gaps
    assert "e2e_tests" in report.gaps
    assert "dev_env" in report.gaps
    assert "prod_promotion" in report.gaps
    assert "unit_tests" not in report.gaps
    assert report.ready is False


def test_assess_missing_required_secret_blocks_ready() -> None:
    bp = _web_bp()
    paths = [
        "package.json",
        "src/app/page.test.tsx",
        "playwright.config.ts",  # e2e via config probe
        "Dockerfile",
        "docker-compose.yml",
        ".github/workflows/release.yml",
    ]
    # All capabilities satisfied but CURSOR_API_KEY absent.
    report = assess_readiness(
        blueprint=bp,
        paths=paths,
        secret_names=["REGISTRY_TOKEN"],
    )
    assert report.gaps == ()
    assert "CURSOR_API_KEY" in report.missing_required_secrets
    assert report.ready is False


def test_assess_external_checklist_is_user_items_only() -> None:
    bp = _web_bp()
    report = assess_readiness(blueprint=bp, paths=[])
    user_items = {i.text for i in bp.checklist if i.actor == "user"}
    assert set(report.external_checklist) == user_items
    assert report.external_checklist  # non-empty


def test_assess_secret_present_optional_reported() -> None:
    bp = _web_bp()
    report = assess_readiness(
        blueprint=bp,
        paths=[],
        secret_names=["CURSOR_API_KEY", "REGISTRY_TOKEN"],
    )
    by_name = {s.name: s for s in report.secrets}
    assert by_name["REGISTRY_TOKEN"].present is True
    assert by_name["REGISTRY_TOKEN"].required is False
    assert by_name["CURSOR_API_KEY"].present is True


def test_assess_mobile_e2e_via_detox_config_file() -> None:
    bp = load_blueprint("mobile")
    assert bp is not None
    # detox is detected by its config file (``.detoxrc*`` glob), not by a
    # dep token — the harvester's framework table doesn't carry detox.
    report = assess_readiness(
        blueprint=bp,
        paths=["src/App.tsx", ".detoxrc.js"],
        frameworks=["react-native"],
        secret_names=["CURSOR_API_KEY"],
    )
    e2e = next(c for c in report.capabilities if c.capability == "e2e_tests")
    assert e2e.satisfied is True
    # Matched either as the "detox" token (substring of the path) or the
    # ".detoxrc*" glob — both are correct; capability satisfaction is the
    # contract, the specific winning probe is not.
    assert e2e.matched_by in {"detox", ".detoxrc*"}


def test_assess_mobile_ready_file_based() -> None:
    bp = load_blueprint("mobile")
    assert bp is not None
    report = assess_readiness(
        blueprint=bp,
        paths=[
            "package.json",
            "__tests__/app.test.tsx",  # unit_tests **/*.test.*
            ".maestro/flow.maestro.yaml",  # e2e_tests *.maestro.yaml
            "eas.json",  # build_artifacts + internal_channel
            ".github/workflows/release.yml",  # store_release
        ],
        frameworks=["react-native"],
        secret_names=["CURSOR_API_KEY"],
    )
    assert report.gaps == ()
    assert report.ready is True


def test_assess_desktop_code_signing_satisfied_by_secret() -> None:
    bp = load_blueprint("desktop")
    assert bp is not None
    paths = [
        "package.json",
        "src/main.test.ts",  # unit_tests
        "test/app.e2e.ts",  # e2e_tests *.e2e.*
        "electron-builder.yml",  # installers **/electron-builder.*
        ".github/workflows/release.yml",  # release_channel
    ]
    report = assess_readiness(
        blueprint=bp,
        paths=paths,
        frameworks=["electron"],
        secret_names=["CURSOR_API_KEY", "CSC_LINK"],
    )
    cs = next(c for c in report.capabilities if c.capability == "code_signing")
    assert cs.satisfied is True
    assert cs.matched_by == "CSC_LINK"
    assert report.ready is True


def test_assess_desktop_code_signing_gap_without_secret() -> None:
    bp = load_blueprint("desktop")
    assert bp is not None
    paths = [
        "package.json",
        "src/main.test.ts",
        "test/app.e2e.ts",
        "electron-builder.yml",
        ".github/workflows/release.yml",
    ]
    # No signing secret/file → code_signing (required) is the only gap.
    report = assess_readiness(
        blueprint=bp,
        paths=paths,
        frameworks=["electron"],
        secret_names=["CURSOR_API_KEY"],
    )
    assert "code_signing" in report.gaps
    assert report.ready is False


def test_probe_glob_matches_root_and_nested_test_files() -> None:
    bp = _web_bp()
    # root-level test file must satisfy unit_tests (**/*.test.* glob).
    report = assess_readiness(blueprint=bp, paths=["a.test.ts"])
    unit = next(c for c in report.capabilities if c.capability == "unit_tests")
    assert unit.satisfied is True


# ---------------------------------------------------------------------------
# 3. build_readiness (DB-backed, faked gateway + secret lister)
# ---------------------------------------------------------------------------


class _FakeGateway:
    def __init__(self, paths: list[str]) -> None:
        self._paths = paths

    async def list_files(self, ref, *, ref_sha=None) -> list[str]:
        return self._paths


@pytest.fixture
async def seed_repo(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=55_001,
        account_id=5501,
        account_login="acme",
        account_type="Organization",
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=7701,
        full_name="acme/readiness-target",
        default_branch="main",
        html_url="https://github.com/acme/readiness-target",
        preset="default",
    )
    db_session.add(repo)
    await db_session.flush()
    return workspace, repo, install


async def _seed_intel(db_session, workspace, repo, **kw) -> RepoIntel:
    row = RepoIntel(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        repo_id=repo.id,
        version=1,
        is_current=True,
        languages=kw.get("languages", {}),
        frameworks=kw.get("frameworks", []),
        package_managers=kw.get("package_managers", []),
        entry_points=[],
        structure={},
        commit_style={},
        visual_tokens={},
        project_type=kw.get("project_type"),
        sdlc_maturity=kw.get("sdlc_maturity", {}),
        harvested_by="wizard",
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
async def test_build_readiness_no_intel(db_session, seed_repo) -> None:
    workspace, repo, _ = seed_repo
    result = await build_readiness(session=db_session, repo=repo)
    assert result.has_blueprint is False
    assert result.report is None
    assert "harvested" in (result.detail or "").lower()


@pytest.mark.asyncio
async def test_build_readiness_no_blueprint_for_type(
    db_session, seed_repo
) -> None:
    workspace, repo, _ = seed_repo
    await _seed_intel(db_session, workspace, repo, project_type="embedded")
    result = await build_readiness(session=db_session, repo=repo)
    assert result.has_blueprint is False
    assert result.project_type == "embedded"
    assert "blueprint" in (result.detail or "").lower()


@pytest.mark.asyncio
async def test_build_readiness_web_full(db_session, seed_repo) -> None:
    workspace, repo, _ = seed_repo
    await _seed_intel(
        db_session,
        workspace,
        repo,
        project_type="web",
        frameworks=["next.js", "react"],
        package_managers=["npm"],
    )

    async def _secrets() -> list[str]:
        return ["CURSOR_API_KEY"]

    gw = _FakeGateway(
        [
            "package.json",
            "src/app/page.tsx",
            "src/app/page.test.tsx",
            "e2e/login.spec.ts",
            "playwright.config.ts",
            "Dockerfile",
            "docker-compose.yml",
            ".github/workflows/deploy.yml",
        ]
    )
    result = await build_readiness(
        session=db_session, repo=repo, gateway=gw, secret_lister=_secrets
    )
    assert result.has_blueprint is True
    assert result.project_type == "web"
    assert result.report is not None
    assert result.report.ready is True
    assert result.report.gaps == ()


@pytest.mark.asyncio
async def test_build_readiness_degrades_without_installation(
    db_session, seed_repo
) -> None:
    workspace, repo, _ = seed_repo
    await _seed_intel(
        db_session, workspace, repo, project_type="web", frameworks=["react"]
    )
    # No installation → can't read the tree → degrade, don't emit a
    # confident all-gaps report.
    repo.installation_id = None
    await db_session.flush()
    result = await build_readiness(session=db_session, repo=repo)
    assert result.has_blueprint is True
    assert result.report is None
    assert "installation" in (result.detail or "").lower()


@pytest.mark.asyncio
async def test_build_readiness_web_with_gaps(db_session, seed_repo) -> None:
    workspace, repo, _ = seed_repo
    await _seed_intel(
        db_session, workspace, repo, project_type="web", frameworks=["react"]
    )

    async def _secrets() -> list[str]:
        return []  # CURSOR_API_KEY missing too

    gw = _FakeGateway(["package.json", "src/index.tsx"])
    result = await build_readiness(
        session=db_session, repo=repo, gateway=gw, secret_lister=_secrets
    )
    assert result.report is not None
    assert result.report.ready is False
    assert "containerization" in result.report.gaps
    assert "CURSOR_API_KEY" in result.report.missing_required_secrets


# ---------------------------------------------------------------------------
# 4. file_bootstrap_recommendation (inbox letter on harvest completion)
# ---------------------------------------------------------------------------


async def _bare_web_repo(db_session, workspace, repo):
    await _seed_intel(
        db_session, workspace, repo, project_type="web", frameworks=["react"]
    )

    async def _secrets() -> list[str]:
        return []

    return _FakeGateway(["package.json", "src/index.tsx"]), _secrets


@pytest.mark.asyncio
async def test_files_letter_when_not_ready(db_session, seed_repo) -> None:
    workspace, repo, _ = seed_repo
    gw, secrets = await _bare_web_repo(db_session, workspace, repo)

    item = await file_bootstrap_recommendation(
        session=db_session, repo=repo, gateway=gw, secret_lister=secrets
    )
    assert item is not None
    assert item.type == "improvement"
    assert item.repo_id == repo.id
    assert item.intake_reason == "bootstrap_readiness"
    assert item.payload["kind"] == "bootstrap_readiness"
    assert "containerization" in item.payload["gaps"]
    # Actionable: "start-bootstrap" runs the epic generator via /decide.
    ids = {a["id"] for a in item.payload["action_items"]}
    assert ids == {"start-bootstrap", "not-now"}
    assert all(a["kind"] == "choice" for a in item.payload["action_items"])
    assert item.payload["resolution_mode"] == "single_choice"


@pytest.mark.asyncio
async def test_letter_deduped_on_repeat_harvest(db_session, seed_repo) -> None:
    workspace, repo, _ = seed_repo
    gw, secrets = await _bare_web_repo(db_session, workspace, repo)

    first = await file_bootstrap_recommendation(
        session=db_session, repo=repo, gateway=gw, secret_lister=secrets
    )
    second = await file_bootstrap_recommendation(
        session=db_session, repo=repo, gateway=gw, secret_lister=secrets
    )
    assert first is not None
    assert second is None  # deduped while the first is still 'new'

    rows = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.repo_id == repo.id,
                InboxItem.intake_reason == "bootstrap_readiness",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_no_letter_when_ready(db_session, seed_repo) -> None:
    workspace, repo, _ = seed_repo
    await _seed_intel(
        db_session,
        workspace,
        repo,
        project_type="web",
        frameworks=["next.js", "react"],
        package_managers=["npm"],
    )

    async def _secrets() -> list[str]:
        return ["CURSOR_API_KEY"]

    gw = _FakeGateway(
        [
            "package.json",
            "src/app/page.test.tsx",
            "playwright.config.ts",
            "Dockerfile",
            "docker-compose.yml",
            ".github/workflows/deploy.yml",
        ]
    )
    item = await file_bootstrap_recommendation(
        session=db_session, repo=repo, gateway=gw, secret_lister=_secrets
    )
    assert item is None


@pytest.mark.asyncio
async def test_no_letter_without_blueprint(db_session, seed_repo) -> None:
    workspace, repo, _ = seed_repo
    await _seed_intel(db_session, workspace, repo, project_type="embedded")
    item = await file_bootstrap_recommendation(
        session=db_session, repo=repo, gateway=_FakeGateway([])
    )
    assert item is None
