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
from backend.app.integrations.gateway.tracker import CreatedTicket, TicketRef
from backend.app.services.bootstrap_plan import generate_bootstrap_plan
from backend.app.services.sdlc_readiness import ReadinessReport


class _FakeTracker:
    def __init__(self) -> None:
        self.project: dict | None = None
        self.tickets: list[dict] = []
        self.sections: dict[str, str] = {}
        self._n = 0

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
