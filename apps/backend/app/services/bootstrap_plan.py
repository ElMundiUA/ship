"""BS3 — bootstrap epic generator.

Turns a repo's readiness gaps (:mod:`sdlc_readiness`) into a tracker
project + one infra child ticket per gap, bound to the target repo via
Variant A routing. The tickets are plain tracker issues with explicitly
infra bodies; the planning agent classifies them as infra and BS0.1's
FSM wiring routes them to ``devops_implementation`` (the DevOps role
scaffolds the folder structure first, then writes the CI/Docker/deploy
config). No hand-applied FSM stage labels — that would fight the picker
(``stage:planning`` alone is ambiguous between dev and devops); content
classification is the canonical infra-routing mechanism.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.integrations import (
    WorkspaceRepo,
    WorkspaceRepoRouting,
)
from backend.app.integrations.gateway.tracker import TrackerGateway
from backend.app.services.sdlc_readiness import ReadinessReport

logger = logging.getLogger(__name__)

# Linear project states that mean "this epic is still live" — a bootstrap
# project in any of these is reused on re-run instead of duplicated.
_OPEN_PROJECT_STATES: frozenset[str] = frozenset(
    {"backlog", "planned", "started", "paused"}
)


# capability → (ticket title, what the DevOps agent should scaffold).
# Covers the union of capabilities across the web/mobile/desktop
# blueprints; unknown capabilities fall back to a humanised name.
_CAPABILITY_SPECS: dict[str, tuple[str, str]] = {
    "unit_tests": (
        "Set up the unit-test harness + CI",
        "Add a unit-test runner for this stack and wire it to run on "
        "every PR in CI. Cover at least the core modules; make the run a "
        "required check.",
    ),
    "e2e_tests": (
        "Set up end-to-end tests + CI",
        "Add an e2e test runner (Playwright / Cypress / Detox / Maestro "
        "as fits the stack) with at least one smoke path, running in CI.",
    ),
    "containerization": (
        "Containerize the app (Dockerfile)",
        "Add a multi-stage, prod-lean Dockerfile + .dockerignore that "
        "builds a single runnable image in CI. This image is the one "
        "delivery artifact — built once per commit, promoted across "
        "environments unchanged.",
    ),
    "dev_env": (
        "Add a local/dev environment (docker-compose)",
        "Add a docker-compose.yml that brings the app + its datastore up "
        "locally so contributors get a one-command dev environment.",
    ),
    "prod_promotion": (
        "Add build-once deploy + dev→prod promotion",
        "Build a single sha-tagged image once, deploy it to the dev "
        "environment on merge, and promote the SAME image digest to prod "
        "behind the chosen gate (manual GitHub Environment approval by "
        "default; a rule job if configured). Never rebuild for prod.",
    ),
    "build_artifacts": (
        "Set up signed build artifacts (fastlane / EAS)",
        "Add a reproducible build pipeline (fastlane or EAS) that produces "
        "the platform artifacts (ipa / aab) in CI.",
    ),
    "internal_channel": (
        "Wire an internal distribution channel",
        "Add an internal-testing distribution lane (TestFlight internal / "
        "Play internal track / EAS internal) so builds reach testers "
        "before store submission.",
    ),
    "store_release": (
        "Add a store release pipeline",
        "Add a release workflow that submits a vetted build to the app "
        "store(s) behind an explicit approval gate.",
    ),
    "installers": (
        "Produce desktop installers",
        "Add packaging (electron-builder / tauri) that produces signed "
        "installers per OS in CI.",
    ),
    "code_signing": (
        "Wire code signing + notarization",
        "Wire code signing + notarization into the build using the "
        "configured signing secrets (CSC_LINK / APPLE_ID / etc.); the "
        "operator supplies the certs as repo secrets.",
    ),
    "release_channel": (
        "Add a release channel + auto-update feed",
        "Add a release channel (nightly/stable) and the auto-update feed "
        "(latest.yml / latest-mac.yml) so shipped apps can self-update.",
    ),
}


def _humanize(capability: str) -> str:
    return capability.replace("_", " ").capitalize()


def _spec(capability: str) -> tuple[str, str]:
    return _CAPABILITY_SPECS.get(
        capability,
        (
            _humanize(capability),
            f"Set up '{_humanize(capability)}' for this repo per the "
            "project's SDLC blueprint.",
        ),
    )


@dataclass(frozen=True, slots=True)
class BootstrapTicket:
    capability: str
    display_id: str
    url: str


@dataclass(frozen=True, slots=True)
class BootstrapPlanResult:
    project_id: str
    project_url: str
    project_native_id: str
    bound_repo_id: uuid.UUID
    tickets: tuple[BootstrapTicket, ...]


def _project_narrative(repo: WorkspaceRepo, report: ReadinessReport) -> str:
    envs = ", ".join(report.environments) or "—"
    gap_lines = "\n".join(
        f"- **{_spec(c)[0]}** ({c})" for c in report.gaps
    )
    checklist = "\n".join(f"- {item}" for item in report.external_checklist)
    parts = [
        f"# Bootstrap {report.project_type} SDLC — {repo.full_name}",
        "",
        f"Brings `{repo.full_name}` up to its recommended **{report.project_type}** "
        f"SDLC setup (delivery: **{report.delivery}**, environments: {envs}).",
        "",
        "## Scope",
        "Each child ticket below is an **infra** task — the DevOps role "
        "scaffolds the folder structure first if missing, then writes the "
        "config. All work targets the main branch.",
        "",
        gap_lines or "- (no gaps)",
    ]
    if checklist:
        parts += [
            "",
            "## What you set up outside Ship",
            "Ship can't hold your credentials. Do these by hand and add "
            "the secrets to the repo:",
            "",
            checklist,
        ]
    return "\n".join(parts)


def _ticket_body(repo: WorkspaceRepo, report: ReadinessReport, capability: str) -> str:
    _, what = _spec(capability)
    return "\n".join(
        [
            "**Type: infra** (DevOps / CI-CD / Docker / deploy — route to "
            "the DevOps role, not the feature developer).",
            "",
            f"Repo: `{repo.full_name}` · project type: **{report.project_type}** "
            f"· delivery: **{report.delivery}**.",
            "",
            "## Goal",
            what,
            "",
            "## Notes",
            "- If the repo isn't structurally ready, scaffold the correct "
            "folder structure first, then write the config.",
            "- Keep everything on the main branch; no separate deploy repo.",
        ]
    )


async def _upsert_routing(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_native_id: str,
    repo_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
) -> None:
    """Bind a tracker project to a repo (Variant A) so its tickets
    dispatch into ``repo_id``. Idempotent on (workspace, project)."""
    existing = (
        await session.execute(
            select(WorkspaceRepoRouting).where(
                WorkspaceRepoRouting.workspace_id == workspace_id,
                WorkspaceRepoRouting.project_native_id == project_native_id,
            )
        )
    ).scalars().first()
    if existing is not None:
        existing.repo_id = repo_id
        if actor_user_id is not None:
            existing.updated_by_user_id = actor_user_id
        return
    session.add(
        WorkspaceRepoRouting(
            workspace_id=workspace_id,
            project_native_id=project_native_id,
            repo_id=repo_id,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
    )


async def _find_open_bootstrap_project(
    tracker: TrackerGateway, *, name: str
) -> dict | None:
    """Return an existing OPEN bootstrap project with this exact name, or
    ``None``. Best-effort dedup so a re-run reuses the epic instead of
    minting a duplicate project + duplicate tickets. Tolerant of trackers
    that can't list / filter projects (returns None → caller creates)."""
    try:
        projects = await tracker.list_projects(query=name, limit=50)
    except Exception as exc:  # noqa: BLE001 — dedup is best-effort
        logger.warning("bootstrap dedup: list_projects failed: %s", exc)
        return None
    for proj in projects or []:
        if proj.get("name") != name:
            continue
        state = str(proj.get("state") or "").lower()
        # No state info → assume open (don't duplicate on uncertainty).
        if not state or state in _OPEN_PROJECT_STATES:
            return proj
    return None


async def generate_bootstrap_plan(
    *,
    session: AsyncSession,
    tracker: TrackerGateway,
    repo: WorkspaceRepo,
    report: ReadinessReport,
    actor_user_id: uuid.UUID | None = None,
) -> BootstrapPlanResult:
    """Create the bootstrap project + one infra ticket per gap, bound to
    ``repo``. Requires ``report`` to have capability gaps — a repo that's
    only ``ready=False`` because of a missing required secret has nothing
    to scaffold, so we refuse rather than mint an empty epic + a dangling
    routing row. The endpoint 409s on this before calling; the guard here
    is the backstop for any other caller (e.g. BS5 wizard).

    Idempotent on re-run: if an OPEN bootstrap project with the same
    deterministic name already exists, reuse it (re-assert the routing
    binding) instead of minting a duplicate project + duplicate tickets."""
    if not report.gaps:
        raise ValueError(
            "generate_bootstrap_plan requires capability gaps; none present"
        )
    name = f"Bootstrap {report.project_type} SDLC — {repo.full_name}"[:255]

    # Per-repo isolation rests entirely on ``repo.full_name`` being in the
    # name — the dedup matches by name only, so two different repos can't
    # collide unless their full_names match (impossible within a workspace).
    existing = await _find_open_bootstrap_project(tracker, name=name)
    if existing is not None:
        project_id = str(existing.get("id") or "")
        await _upsert_routing(
            session,
            workspace_id=repo.workspace_id,
            project_native_id=project_id,
            repo_id=repo.id,
            actor_user_id=actor_user_id,
        )
        await session.flush()
        return BootstrapPlanResult(
            project_id=project_id,
            project_url=str(existing.get("url") or ""),
            project_native_id=project_id,
            bound_repo_id=repo.id,
            tickets=(),  # epic already exists — don't duplicate its tickets
        )

    project = await tracker.create_project(
        name=name, body=_project_narrative(repo, report)
    )
    project_id = str(project.get("id") or "")

    await _upsert_routing(
        session,
        workspace_id=repo.workspace_id,
        project_native_id=project_id,
        repo_id=repo.id,
        actor_user_id=actor_user_id,
    )

    tickets: list[BootstrapTicket] = []
    for capability in report.gaps:
        title, _ = _spec(capability)
        created = await tracker.create_ticket(
            title=f"{title} — {repo.full_name}"[:300],
            body=_ticket_body(repo, report, capability),
            project_id=project_id,
            ticket_type="task",
        )
        tickets.append(
            BootstrapTicket(
                capability=capability,
                display_id=created.display_id,
                url=created.url,
            )
        )

    if tickets:
        section = "\n".join(
            f"- **{t.display_id}** — {_spec(t.capability)[0]}" for t in tickets
        )
        await tracker.upsert_project_section(
            project_id, section="Tasks", body=section
        )

    await session.flush()
    return BootstrapPlanResult(
        project_id=project_id,
        project_url=str(project.get("url") or ""),
        project_native_id=project_id,
        bound_repo_id=repo.id,
        tickets=tuple(tickets),
    )


async def run_bootstrap_for_repo(
    *,
    session: AsyncSession,
    repo: WorkspaceRepo,
    tracker: TrackerGateway,
    settings=None,
    gateway=None,
    secret_lister=None,
    actor_user_id: uuid.UUID | None = None,
) -> dict:
    """Assess readiness then generate the bootstrap epic in one call.

    The shared path behind both the BS3 endpoint and the BS2 Inbox
    "generate tickets" action. Returns a result dict (never raises on the
    nothing-to-do cases) so the Inbox /decide handler can record it as a
    side-effect: ``{"result": "bootstrap_generated", "project_url", ...}``
    or a ``skipped_*`` reason. ``gateway`` / ``secret_lister`` are the
    code-host hooks for ``build_readiness`` (injectable for tests)."""
    from backend.app.services.sdlc_readiness import build_readiness

    rdy = await build_readiness(
        session=session,
        repo=repo,
        settings=settings,
        gateway=gateway,
        secret_lister=secret_lister,
    )
    if not rdy.has_blueprint or rdy.report is None:
        return {"result": "skipped_no_blueprint", "detail": rdy.detail}
    if rdy.report.ready:
        return {"result": "skipped_already_ready"}
    if not rdy.report.gaps:
        return {"result": "skipped_no_gaps"}

    plan = await generate_bootstrap_plan(
        session=session,
        tracker=tracker,
        repo=repo,
        report=rdy.report,
        actor_user_id=actor_user_id,
    )
    return {
        "result": "bootstrap_generated",
        "project_url": plan.project_url,
        "project_native_id": plan.project_native_id,
        "ticket_count": len(plan.tickets),
    }


__all__ = [
    "BootstrapPlanResult",
    "BootstrapTicket",
    "generate_bootstrap_plan",
    "run_bootstrap_for_repo",
]
