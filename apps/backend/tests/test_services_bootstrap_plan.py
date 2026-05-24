"""Tests for the bootstrap epic generator (BS3).

``generate_bootstrap_plan`` is exercised against a fake TrackerGateway +
a seeded repo: it must create the project, bind it to the repo (Variant A
routing row), create one infra ticket per gap (attached to the project),
and render the Tasks section. The ticket bodies must read as infra so the
planning agent classifies them to the DevOps path (BS0.1).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
    WorkspaceRepoRouting,
)
from backend.app.db.models.repo_intel import RepoIntel
from backend.app.integrations.gateway.tracker import CreatedTicket, TicketRef
from backend.app.services.bootstrap_plan import (
    generate_bootstrap_plan,
    run_bootstrap_for_repo,
)
from backend.app.services.sdlc_readiness import ReadinessReport


class _FakeTracker:
    def __init__(self, existing_projects: list[dict] | None = None) -> None:
        self.project: dict | None = None
        self.tickets: list[dict] = []
        self.sections: dict[str, str] = {}
        self.existing_projects = existing_projects or []
        self._n = 0

    async def list_projects(self, *, limit=50, state=None, query=None):
        return list(self.existing_projects)

    async def create_project(self, *, name, body, description=None):
        self.project = {"name": name, "body": body}
        return {
            "id": "proj-uuid-1",
            "url": "https://linear.app/acme/project/boot",
            "name": name,
            "slug": "boot",
        }

    async def create_ticket(
        self,
        *,
        title,
        body,
        labels=None,
        project_hint=None,
        project_id=None,
        ticket_type=None,
    ):
        self._n += 1
        self.tickets.append(
            {
                "title": title,
                "body": body,
                "labels": labels,
                "project_id": project_id,
                "ticket_type": ticket_type,
            }
        )
        ref = TicketRef(kind="linear", workspace_hint="team-1", id=f"uuid-{self._n}")
        return CreatedTicket(
            ref=ref, url=f"https://linear.app/t/{self._n}", display_id=f"ELS-{self._n}"
        )

    async def upsert_project_section(self, project_id, *, section, body):
        self.sections[section] = body


def _report(gaps: list[str]) -> ReadinessReport:
    return ReadinessReport(
        project_type="web",
        delivery="docker",
        environments=("dev", "prod"),
        capabilities=(),
        gaps=tuple(gaps),
        secrets=(),
        missing_required_secrets=(),
        external_checklist=("Add a registry token.",),
        ready=False,
    )


@pytest.fixture
async def seed_repo(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=66_001,
        account_id=6601,
        account_login="acme",
        account_type="Organization",
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=8801,
        full_name="acme/bootstrap-target",
        default_branch="main",
        html_url="https://github.com/acme/bootstrap-target",
        preset="default",
    )
    db_session.add(repo)
    await db_session.flush()
    return workspace, repo


@pytest.mark.asyncio
async def test_generates_project_tickets_and_routing(db_session, seed_repo) -> None:
    workspace, repo = seed_repo
    tracker = _FakeTracker()
    report = _report(["containerization", "e2e_tests", "prod_promotion"])

    result = await generate_bootstrap_plan(
        session=db_session, tracker=tracker, repo=repo, report=report
    )

    # Project created with a narrative body.
    assert tracker.project is not None
    assert "Bootstrap web SDLC" in tracker.project["name"]
    assert result.project_native_id == "proj-uuid-1"

    # One ticket per gap, attached to the project, typed task, infra body.
    assert len(result.tickets) == 3
    assert len(tracker.tickets) == 3
    caps = {t.capability for t in result.tickets}
    assert caps == {"containerization", "e2e_tests", "prod_promotion"}
    for t in tracker.tickets:
        assert t["project_id"] == "proj-uuid-1"
        assert t["ticket_type"] == "task"
        assert "**Type: infra**" in t["body"]

    # Tasks section rendered.
    assert "Tasks" in tracker.sections
    assert "ELS-1" in tracker.sections["Tasks"]

    # Variant A routing row binds the project to the repo.
    routing = (
        await db_session.execute(
            select(WorkspaceRepoRouting).where(
                WorkspaceRepoRouting.workspace_id == workspace.id,
                WorkspaceRepoRouting.project_native_id == "proj-uuid-1",
            )
        )
    ).scalars().first()
    assert routing is not None
    assert routing.repo_id == repo.id


@pytest.mark.asyncio
async def test_unknown_capability_falls_back_to_humanized(
    db_session, seed_repo
) -> None:
    _, repo = seed_repo
    tracker = _FakeTracker()
    report = _report(["some_new_capability"])

    result = await generate_bootstrap_plan(
        session=db_session, tracker=tracker, repo=repo, report=report
    )
    assert len(result.tickets) == 1
    assert tracker.tickets[0]["title"].startswith("Some new capability")


@pytest.mark.asyncio
async def test_no_gaps_refuses_to_mint_empty_epic(db_session, seed_repo) -> None:
    # A repo that's only not-ready because of a missing required secret
    # has gaps=() — there's nothing to scaffold. The service must refuse
    # rather than create an empty project + a dangling routing row.
    _, repo = seed_repo
    tracker = _FakeTracker()
    report = _report([])

    with pytest.raises(ValueError, match="capability gaps"):
        await generate_bootstrap_plan(
            session=db_session, tracker=tracker, repo=repo, report=report
        )
    assert tracker.project is None
    assert tracker.tickets == []


@pytest.mark.asyncio
async def test_reuses_existing_open_bootstrap_project(db_session, seed_repo) -> None:
    workspace, repo = seed_repo
    name = f"Bootstrap web SDLC — {repo.full_name}"
    tracker = _FakeTracker(
        existing_projects=[
            {
                "id": "proj-existing",
                "name": name,
                "state": "started",
                "url": "https://linear.app/acme/project/existing",
            }
        ]
    )
    report = _report(["containerization", "e2e_tests"])

    result = await generate_bootstrap_plan(
        session=db_session, tracker=tracker, repo=repo, report=report
    )
    # Reused the existing epic — no new project, no duplicate tickets.
    assert tracker.project is None
    assert tracker.tickets == []
    assert result.project_native_id == "proj-existing"
    assert result.tickets == ()
    # Routing still (re-)bound to the existing project.
    routing = (
        await db_session.execute(
            select(WorkspaceRepoRouting).where(
                WorkspaceRepoRouting.project_native_id == "proj-existing",
            )
        )
    ).scalars().first()
    assert routing is not None
    assert routing.repo_id == repo.id


@pytest.mark.asyncio
async def test_completed_bootstrap_project_does_not_block_new_one(
    db_session, seed_repo
) -> None:
    workspace, repo = seed_repo
    name = f"Bootstrap web SDLC — {repo.full_name}"
    # A completed same-name project must NOT be reused — create a fresh one.
    tracker = _FakeTracker(
        existing_projects=[
            {"id": "proj-old", "name": name, "state": "completed", "url": "x"}
        ]
    )
    report = _report(["containerization"])

    result = await generate_bootstrap_plan(
        session=db_session, tracker=tracker, repo=repo, report=report
    )
    assert tracker.project is not None  # a new project was created
    assert result.project_native_id == "proj-uuid-1"
    assert len(result.tickets) == 1


@pytest.mark.asyncio
async def test_list_projects_failure_falls_through_to_create(
    db_session, seed_repo
) -> None:
    # Best-effort dedup: if list_projects raises, we create (fail-open)
    # rather than block a legitimate first-time bootstrap.
    workspace, repo = seed_repo

    class _RaisingTracker(_FakeTracker):
        async def list_projects(self, *, limit=50, state=None, query=None):
            raise RuntimeError("tracker down")

    tracker = _RaisingTracker()
    result = await generate_bootstrap_plan(
        session=db_session, tracker=tracker, repo=repo, report=_report(["containerization"])
    )
    assert tracker.project is not None
    assert len(result.tickets) == 1


@pytest.mark.asyncio
async def test_project_without_state_is_treated_as_open(
    db_session, seed_repo
) -> None:
    # An adapter that omits state → assume open (don't risk duplication).
    workspace, repo = seed_repo
    name = f"Bootstrap web SDLC — {repo.full_name}"
    tracker = _FakeTracker(
        existing_projects=[{"id": "proj-nostate", "name": name, "url": "x"}]
    )
    result = await generate_bootstrap_plan(
        session=db_session, tracker=tracker, repo=repo, report=_report(["containerization"])
    )
    assert tracker.project is None  # reused, not created
    assert result.project_native_id == "proj-nostate"


@pytest.mark.asyncio
async def test_routing_records_actor_provenance(
    db_session, seed_repo, seed_workspace
) -> None:
    workspace, repo = seed_repo
    tracker = _FakeTracker()
    report = _report(["containerization"])
    # Real seeded user — created_by_user_id is an FK to users.id.
    actor = seed_workspace[0].id

    await generate_bootstrap_plan(
        session=db_session,
        tracker=tracker,
        repo=repo,
        report=report,
        actor_user_id=actor,
    )
    routing = (
        await db_session.execute(
            select(WorkspaceRepoRouting).where(
                WorkspaceRepoRouting.project_native_id == "proj-uuid-1",
            )
        )
    ).scalars().first()
    assert routing is not None
    assert routing.created_by_user_id == actor
    assert routing.updated_by_user_id == actor


@pytest.mark.asyncio
async def test_routing_upsert_is_idempotent(db_session, seed_repo) -> None:
    workspace, repo = seed_repo
    tracker = _FakeTracker()
    report = _report(["containerization"])

    await generate_bootstrap_plan(
        session=db_session, tracker=tracker, repo=repo, report=report
    )
    # Second run (same project id from the fake) must not create a dup row.
    await generate_bootstrap_plan(
        session=db_session, tracker=tracker, repo=repo, report=report
    )

    rows = (
        await db_session.execute(
            select(WorkspaceRepoRouting).where(
                WorkspaceRepoRouting.project_native_id == "proj-uuid-1",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# run_bootstrap_for_repo — shared orchestrator (readiness → generate)
# ---------------------------------------------------------------------------


class _FakeCodeGateway:
    def __init__(self, paths: list[str]) -> None:
        self._paths = paths

    async def list_files(self, ref, *, ref_sha=None) -> list[str]:
        return self._paths


async def _seed_intel(db_session, workspace, repo, **kw) -> RepoIntel:
    row = RepoIntel(
        id=__import__("uuid").uuid4(),
        workspace_id=workspace.id,
        repo_id=repo.id,
        version=1,
        is_current=True,
        languages={},
        frameworks=kw.get("frameworks", []),
        package_managers=kw.get("package_managers", []),
        entry_points=[],
        structure={},
        commit_style={},
        visual_tokens={},
        project_type=kw.get("project_type"),
        sdlc_maturity={},
        harvested_by="wizard",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _secrets_none() -> list[str]:
    return []


@pytest.mark.asyncio
async def test_run_bootstrap_for_repo_generates_when_not_ready(
    db_session, seed_repo
) -> None:
    workspace, repo = seed_repo
    await _seed_intel(
        db_session, workspace, repo, project_type="web", frameworks=["react"]
    )
    tracker = _FakeTracker()
    res = await run_bootstrap_for_repo(
        session=db_session,
        repo=repo,
        tracker=tracker,
        gateway=_FakeCodeGateway(["package.json", "src/index.tsx"]),
        secret_lister=_secrets_none,
    )
    assert res["result"] == "bootstrap_generated"
    assert res["ticket_count"] >= 1
    assert tracker.project is not None


@pytest.mark.asyncio
async def test_run_bootstrap_for_repo_skips_when_no_intel(
    db_session, seed_repo
) -> None:
    workspace, repo = seed_repo
    tracker = _FakeTracker()
    res = await run_bootstrap_for_repo(
        session=db_session,
        repo=repo,
        tracker=tracker,
        gateway=_FakeCodeGateway([]),
        secret_lister=_secrets_none,
    )
    assert res["result"] == "skipped_no_blueprint"
    assert tracker.project is None


@pytest.mark.asyncio
async def test_run_bootstrap_for_repo_skips_when_ready(
    db_session, seed_repo
) -> None:
    workspace, repo = seed_repo
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

    gw = _FakeCodeGateway(
        [
            "package.json",
            "src/app/page.test.tsx",
            "playwright.config.ts",
            "Dockerfile",
            "docker-compose.yml",
            ".github/workflows/deploy.yml",
        ]
    )
    tracker = _FakeTracker()
    res = await run_bootstrap_for_repo(
        session=db_session,
        repo=repo,
        tracker=tracker,
        gateway=gw,
        secret_lister=_secrets,
    )
    assert res["result"] == "skipped_already_ready"
    assert tracker.project is None
