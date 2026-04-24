"""Navigator Phase-6 automations tools (Wave A read + Wave B mutate).

- ``automations_list`` — scope=all merges Pipelines + Lanes +
  FleetLanes; scope=fleet excludes per-repo rows; ``enabled_only``
  drops disabled rows.
- ``play_run_now`` — happy path dispatches the lane via the shared
  helper so the run lands ``status='running'`` (mirrors the HTTP
  "Run now" route); plays without a Pipeline → ``no_automation``;
  missing GitHub install → structured ``github_app_missing`` error;
  admin-gated.
- ``play_automate`` — creates ``Lane(origin='manual')`` for
  scope=repo and a FleetLane for scope=fleet; collisions return
  ``conflict``; admin-gated.
- ``automation_toggle`` — sets enabled, returns prior_enabled,
  admin-gated.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toolbox(session, *, workspace_id, user_id, settings=None):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=settings,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def _make_user(db_session, *, email: str | None = None):
    from backend.app.db.models.tenancy import User

    u = User(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        display_name="A",
    )
    db_session.add(u)
    await db_session.flush()
    return u


async def _make_member(db_session, *, workspace_id, user_id, role="member"):
    from backend.app.db.models.tenancy import WorkspaceMember

    db_session.add(
        WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
    )
    await db_session.flush()


async def _seed_repo(db_session, workspace, *, external_id: int, full_name: str):
    from backend.app.db.models.integrations import WorkspaceRepo

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=None,
        provider="github",
        external_id=external_id,
        full_name=full_name,
        default_branch="main",
        private=False,
        html_url=f"https://github.com/{full_name}",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def _seed_repo_with_install(
    db_session, workspace, *, external_id: int, full_name: str, install_external_id: int
):
    """Seed an activated repo *plus* a backing GitHub App install row.

    Mirrors the ``seed_repo_and_install`` fixture in
    ``test_v1_pipelines.py`` so dispatch-driven tests in this module
    don't need to mint that scaffolding by hand.
    """
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=install_external_id,
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
        external_id=external_id,
        full_name=full_name,
        default_branch="main",
        private=False,
        html_url=f"https://github.com/{full_name}",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return install, repo


async def _seed_pipeline(
    db_session,
    *,
    workspace_id,
    repo_id=None,
    lane_id: str,
    enabled: bool = True,
):
    from backend.app.db.models.pipelines import Pipeline

    p = Pipeline(
        workspace_id=workspace_id,
        repo_id=repo_id,
        lane_id=lane_id,
        name=lane_id,
        workflow_id="pr-and-ci-gate",
        enabled=enabled,
    )
    db_session.add(p)
    await db_session.flush()
    return p


async def _seed_lane_row(
    db_session, *, workspace_id, repo_id, lane_id: str, pattern: str, enabled: bool = True
):
    from backend.app.db.models.lanes import Lane

    lane = Lane(
        workspace_id=workspace_id,
        repo_id=repo_id,
        lane_id=lane_id,
        kind="event",
        pattern=pattern,
        config_blob={"pattern": pattern},
        enabled=enabled,
    )
    db_session.add(lane)
    await db_session.flush()
    return lane


async def _seed_fleet_lane(
    db_session, *, workspace_id, name: str, pattern_id: str, lane_id: str
):
    from backend.app.db.models.fleet_lanes import FleetLane

    fl = FleetLane(
        workspace_id=workspace_id,
        kind="mirror_lane",
        name=name,
        pattern_id=pattern_id,
        lane_id=lane_id,
        cadence="weekly",
        agent_slug=None,
        inputs={"source": "test"},
        enabled=True,
    )
    db_session.add(fl)
    await db_session.flush()
    return fl


# ---------------------------------------------------------------------------
# automations_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_automations_list_scope_all_merges_three_kinds(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=300_001, full_name="acme/auto-a"
    )
    p = await _seed_pipeline(
        db_session, workspace_id=ws.id, repo_id=repo.id, lane_id="pr_review"
    )
    lane = await _seed_lane_row(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="cov_scan",
        pattern="scan-test-coverage",
    )
    fl = await _seed_fleet_lane(
        db_session,
        workspace_id=ws.id,
        name="Daily fleet sweep",
        pattern_id="scan-security-deps",
        lane_id="fleet_sec_daily",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(await box.invoke("automations_list", {"scope": "all"}))
    by_id = {item["id"]: item for item in out["items"]}
    assert by_id[str(p.id)]["kind"] == "pipeline"
    assert by_id[str(lane.id)]["kind"] == "lane"
    assert by_id[str(fl.id)]["kind"] == "fleet_lane"


@pytest.mark.asyncio
async def test_automations_list_scope_fleet_excludes_per_repo(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=300_002, full_name="acme/auto-b"
    )
    p = await _seed_pipeline(
        db_session, workspace_id=ws.id, repo_id=repo.id, lane_id="pr_review"
    )
    lane = await _seed_lane_row(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="cov_scan",
        pattern="scan-test-coverage",
    )
    fl = await _seed_fleet_lane(
        db_session,
        workspace_id=ws.id,
        name="Fleet only",
        pattern_id="scan-security-deps",
        lane_id="fleet_sec_only",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("automations_list", {"scope": "fleet"})
    )
    ids = {item["id"] for item in out["items"]}
    assert str(fl.id) in ids
    assert str(p.id) not in ids
    assert str(lane.id) not in ids
    assert all(item["scope"] == "fleet" for item in out["items"])


@pytest.mark.asyncio
async def test_automations_list_enabled_only_filter(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=300_003, full_name="acme/auto-c"
    )
    enabled_p = await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="enabled_pipe",
        enabled=True,
    )
    disabled_p = await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="disabled_pipe",
        enabled=False,
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "automations_list", {"scope": "all", "enabled_only": True}
        )
    )
    ids = {item["id"] for item in out["items"]}
    assert str(enabled_p.id) in ids
    assert str(disabled_p.id) not in ids


@pytest.mark.asyncio
async def test_automations_list_invalid_scope(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("automations_list", {"scope": "bogus"})
    )
    assert out["error"] == "invalid_scope"


# ---------------------------------------------------------------------------
# play_run_now
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_run_now_dispatches_to_github(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Happy path: navigator-triggered run dispatches to GitHub Actions.

    Previously this tool only inserted a ``status='queued'`` row and
    handed off to a never-existed scheduler, so navigator-driven runs
    sat forever. The fix routes through the same ``dispatch_workflow``
    path the HTTP "Run now" endpoint uses, so the run lands ``running``
    in one shot. We monkeypatch the GitHub probe / dispatch the same
    way ``test_v1_pipelines.py`` does.
    """
    from sqlalchemy import select

    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.core.config import get_settings
    from backend.app.db.models.pipelines import PipelineRun

    user, _, ws = seed_workspace
    install, repo = await _seed_repo_with_install(
        db_session,
        ws,
        external_id=310_001,
        full_name="acme/run-now",
        install_external_id=999_201,
    )
    pipeline = await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="pr_review",
    )

    captured: dict[str, object] = {}

    async def _probe(repo_arg, install_arg, *, settings, **_):
        return frozenset({"pr-and-ci-gate.yml"})

    async def _dispatch(repo_arg, install_arg, workflow_file, *, inputs, settings, **_):
        captured["workflow_file"] = workflow_file
        captured["inputs"] = dict(inputs)
        captured["repo_full_name"] = repo_arg.full_name

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    box = _toolbox(
        db_session,
        workspace_id=ws.id,
        user_id=user.id,
        settings=get_settings(),
    )
    out = json.loads(
        await box.invoke(
            "play_run_now",
            {
                "play_key": "pr_review",
                "repo_id": str(repo.id),
            },
        )
    )
    assert out["status"] == "running", out
    assert out["pipeline_id"] == str(pipeline.id)

    run = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.id == uuid.UUID(out["run_id"]))
        )
    ).scalar_one()
    assert run.status == "running"
    assert run.trigger == "manual"
    assert run.payload.get("source") == "navigator"
    assert run.run_token_hash and len(run.run_token_hash) == 64

    assert captured["workflow_file"] == "pr-and-ci-gate.yml"
    assert captured["repo_full_name"] == "acme/run-now"
    inputs = captured["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["ship_run_id"] == out["run_id"]
    assert inputs["ship_callback_url"].endswith(
        f"/v1/pipelines/runs/{out['run_id']}/result"
    )
    assert isinstance(inputs["ship_run_token"], str)
    assert len(inputs["ship_run_token"]) > 20


@pytest.mark.asyncio
async def test_play_run_now_returns_error_when_app_install_missing(
    db_session, seed_workspace
) -> None:
    """Repo without a GitHub App install → structured ``github_app_missing``.

    Locks the new behaviour: instead of quietly inserting a queued row
    that nothing would ever dispatch, the tool now surfaces the same
    precondition codes the HTTP route raises so the LLM can suggest
    "reinstall the App".
    """
    from sqlalchemy import select

    from backend.app.core.config import get_settings
    from backend.app.db.models.pipelines import PipelineRun

    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session,
        ws,
        external_id=310_004,
        full_name="acme/run-now-no-install",
    )
    await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="pr_review",
    )

    box = _toolbox(
        db_session,
        workspace_id=ws.id,
        user_id=user.id,
        settings=get_settings(),
    )
    out = json.loads(
        await box.invoke(
            "play_run_now",
            {
                "play_key": "pr_review",
                "repo_id": str(repo.id),
            },
        )
    )
    assert out["error"] == "github_app_missing", out
    assert "message" in out

    runs = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.workspace_id == ws.id)
        )
    ).scalars().all()
    assert runs == []


@pytest.mark.asyncio
async def test_play_run_now_returns_error_when_workflow_not_installed(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Workflow YAML not on default branch → ``workflow_not_installed`` error.

    The error payload carries enough metadata (``workflow_file``,
    ``repo_full_name``, ``install_endpoint``) for the chat surface to
    render an actionable "Open install PR" CTA.
    """
    from backend.app.api.v1.routes import pipelines as pipelines_route
    from backend.app.core.config import get_settings

    user, _, ws = seed_workspace
    install, repo = await _seed_repo_with_install(
        db_session,
        ws,
        external_id=310_005,
        full_name="acme/run-now-needs-install",
        install_external_id=999_202,
    )
    await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="pr_review",
    )

    async def _probe(repo_arg, install_arg, *, settings, **_):
        return frozenset()  # nothing installed yet

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)

    box = _toolbox(
        db_session,
        workspace_id=ws.id,
        user_id=user.id,
        settings=get_settings(),
    )
    out = json.loads(
        await box.invoke(
            "play_run_now",
            {
                "play_key": "pr_review",
                "repo_id": str(repo.id),
            },
        )
    )
    assert out["error"] == "workflow_not_installed", out
    assert out["workflow_file"] == "pr-and-ci-gate.yml"
    assert out["repo_full_name"] == "acme/run-now-needs-install"
    assert out["install_endpoint"].endswith("/install")


@pytest.mark.asyncio
async def test_play_run_now_no_automation(db_session, seed_workspace) -> None:
    """A play that doesn't yet have a Pipeline returns ``no_automation``."""
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=310_002, full_name="acme/run-now-na"
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "play_run_now",
            {
                "play_key": "flow-nothing-here",
                "repo_id": str(repo.id),
            },
        )
    )
    assert out["error"] == "no_automation"


@pytest.mark.asyncio
async def test_play_run_now_non_admin_forbidden(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )
    repo = await _seed_repo(
        db_session, ws, external_id=310_003, full_name="acme/run-now-forbidden"
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "play_run_now",
            {
                "play_key": "flow-pr-self-review",
                "repo_id": str(repo.id),
            },
        )
    )
    assert out["error"] == "forbidden"


# ---------------------------------------------------------------------------
# play_automate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_automate_repo_scope_creates_lane(
    db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.lanes import Lane

    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=320_001, full_name="acme/auto-repo"
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "play_automate",
            {
                "play_key": "flow-pr-self-review",
                "scope": "repo",
                "repo_id": str(repo.id),
                "cadence": "on_pr",
            },
        )
    )
    assert out["scope"] == "repo"
    assert out["status"] == "synthetic"

    lane = (
        await db_session.execute(
            select(Lane).where(Lane.id == uuid.UUID(out["lane_id"]))
        )
    ).scalar_one()
    assert lane.origin == "manual"
    assert lane.pattern == "flow-pr-self-review"
    assert lane.workspace_id == ws.id
    assert lane.repo_id == repo.id


@pytest.mark.asyncio
async def test_play_automate_fleet_scope_creates_fleet_lane(
    db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.fleet_lanes import FleetLane

    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "play_automate",
            {
                "play_key": "flow-pr-self-review",
                "scope": "fleet",
                "cadence": "weekly",
            },
        )
    )
    assert out["scope"] == "fleet"
    assert out["repo_id"] is None

    fl = (
        await db_session.execute(
            select(FleetLane).where(FleetLane.id == uuid.UUID(out["lane_id"]))
        )
    ).scalar_one()
    assert fl.workspace_id == ws.id
    assert fl.pattern_id == "flow-pr-self-review"
    assert fl.cadence == "weekly"


@pytest.mark.asyncio
async def test_play_automate_conflict_on_duplicate(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=320_002, full_name="acme/auto-dup"
    )
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)

    payload = {
        "play_key": "flow-pr-self-review",
        "scope": "repo",
        "repo_id": str(repo.id),
        "cadence": "on_pr",
    }
    first = json.loads(await box.invoke("play_automate", payload))
    assert first.get("status") == "synthetic"

    second = json.loads(await box.invoke("play_automate", payload))
    assert second["error"] == "conflict"
    assert second["existing_lane_id"] == first["lane_id"]


@pytest.mark.asyncio
async def test_play_automate_non_admin_forbidden(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "play_automate",
            {
                "play_key": "flow-pr-self-review",
                "scope": "fleet",
                "cadence": "weekly",
            },
        )
    )
    assert out["error"] == "forbidden"


# ---------------------------------------------------------------------------
# automation_toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_automation_toggle_sets_enabled_and_returns_prior(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=330_001, full_name="acme/toggle"
    )
    pipeline = await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="pipe_toggle",
        enabled=True,
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "automation_toggle",
            {"pipeline_id": str(pipeline.id), "enabled": False},
        )
    )
    assert out["enabled"] is False
    assert out["prior_enabled"] is True

    await db_session.refresh(pipeline)
    assert pipeline.enabled is False


@pytest.mark.asyncio
async def test_automation_toggle_pipeline_not_found(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "automation_toggle",
            {"pipeline_id": str(uuid.uuid4()), "enabled": True},
        )
    )
    assert out["error"] == "not_found"


@pytest.mark.asyncio
async def test_automation_toggle_non_admin_forbidden(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )
    repo = await _seed_repo(
        db_session, ws, external_id=330_002, full_name="acme/toggle-fb"
    )
    pipeline = await _seed_pipeline(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="pipe_toggle_fb",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "automation_toggle",
            {"pipeline_id": str(pipeline.id), "enabled": False},
        )
    )
    assert out["error"] == "forbidden"
