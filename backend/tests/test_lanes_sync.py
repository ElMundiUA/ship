"""Unit tests for :mod:`backend.app.services.lanes_sync`.

Cover the pure parsing / projection behaviour without touching GitHub
— we feed a canned YAML string and assert rows land correctly. The
``apply_workflow_run_completion`` branch is exercised through the
webhook test path in ``test_v1_webhooks.py`` (follow-up).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.services.lanes_sync import (
    apply_workflow_run_completion,
    extract_lane_id_from_path,
    map_conclusion_to_status,
    sync_lanes_for_repo,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeBlob:
    """Mimics :class:`BlobContent` for the gateway stub."""

    def __init__(self, content: str, sha: str = "deadbeef"):
        self.content = content
        self.encoding = "utf-8"
        self.sha = sha
        self.path = ".ship/config.yml"
        self.ref = "main"
        self.size = len(content)


class _FakeGateway:
    """Minimal stand-in for :class:`GitHubCodeHost`."""

    def __init__(self, content: str | None):
        self._content = content

    async def get_blob(self, ref: Any, *, path: str, ref_sha: str | None):
        if self._content is None:
            raise FileNotFoundError(path)
        return _FakeBlob(self._content)


@pytest.fixture
def patch_gateway(monkeypatch: pytest.MonkeyPatch):
    """Replace :class:`GitHubCodeHost` with a per-test stub."""

    def _factory(content: str | None):
        from backend.app.services import lanes_sync

        def _ctor(*args, **kwargs):
            return _FakeGateway(content)

        monkeypatch.setattr(lanes_sync, "GitHubCodeHost", _ctor)
        return _ctor

    return _factory


# ---------------------------------------------------------------------------
# Fixtures: workspace + repo + install
# ---------------------------------------------------------------------------


async def _seed(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, _raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=4242,
        account_id=1,
        account_login="acme",
        account_type="Organization",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=1001,
        full_name="acme/widgets",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/widgets",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()

    return workspace, install, repo


# ---------------------------------------------------------------------------
# sync_lanes_for_repo
# ---------------------------------------------------------------------------


_YAML_V2 = """
version: 2
lanes:
  pr_review:
    event: pull_request
    pattern: pr-and-ci-gate
  daily:
    schedule: "0 9 * * *"
    pattern: scheduled-sdlc-lane
  seed:
    once: install
    pattern: onboard-seed-knowledge
    idempotency_key: seed-v1
"""


@pytest.mark.asyncio
async def test_sync_creates_rows_for_v2_config(
    db_session, seed_workspace, patch_gateway
) -> None:
    from backend.app.db.models.lanes import Lane

    _workspace, install, repo = await _seed(db_session, seed_workspace)
    patch_gateway(_YAML_V2)

    report = await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )

    assert report.added == 3
    assert report.updated == 0
    assert report.removed == 0
    assert report.errors == []
    assert report.sync_source is not None

    rows = {
        row.lane_id: row
        for row in (
            await db_session.execute(
                Lane.__table__.select().where(Lane.repo_id == repo.id)
            )
        ).mappings()
    }
    assert set(rows) == {"pr_review", "daily", "seed"}
    assert rows["pr_review"]["kind"] == "event"
    assert rows["pr_review"]["pattern"] == "pr-and-ci-gate"
    assert rows["daily"]["kind"] == "schedule"
    assert rows["daily"]["cron"] == "0 9 * * *"
    assert rows["seed"]["kind"] == "once"
    assert rows["seed"]["idempotency_key"] == "seed-v1"


@pytest.mark.asyncio
async def test_sync_updates_and_removes(
    db_session, seed_workspace, patch_gateway
) -> None:
    from backend.app.db.models.lanes import Lane

    _workspace, install, repo = await _seed(db_session, seed_workspace)

    patch_gateway(_YAML_V2)
    first = await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )
    assert first.added == 3

    # Config v2 update: ``pr_review`` now pins a different pattern,
    # ``seed`` is dropped, ``daily`` unchanged. Should net as 1
    # updated + 1 removed + 1 unchanged.
    yaml_v2_updated = """
version: 2
lanes:
  pr_review:
    event: pull_request
    pattern: pr-and-ci-gate-v2
  daily:
    schedule: "0 9 * * *"
    pattern: scheduled-sdlc-lane
"""
    patch_gateway(yaml_v2_updated)
    report = await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )
    assert report.added == 0
    assert report.updated == 1
    assert report.removed == 1
    assert report.unchanged == 1

    rows = (
        await db_session.execute(
            Lane.__table__.select().where(Lane.repo_id == repo.id)
        )
    ).mappings().all()
    ids = {row["lane_id"] for row in rows}
    assert ids == {"pr_review", "daily"}
    pr_row = next(row for row in rows if row["lane_id"] == "pr_review")
    assert pr_row["pattern"] == "pr-and-ci-gate-v2"


@pytest.mark.asyncio
async def test_sync_skips_invalid_entries(
    db_session, seed_workspace, patch_gateway
) -> None:
    _workspace, install, repo = await _seed(db_session, seed_workspace)

    patch_gateway(
        """
lanes:
  GOOD:           # uppercase -> invalid id
    event: push
  missing_trigger:
    pattern: oops
  ok_one:
    once: install
    pattern: seed
"""
    )
    report = await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )
    assert report.added == 1
    assert len(report.errors) == 2
    assert any("GOOD" in err for err in report.errors)
    assert any("missing_trigger" in err for err in report.errors)


@pytest.mark.asyncio
async def test_sync_missing_config_raises(
    db_session, seed_workspace, patch_gateway
) -> None:
    _workspace, install, repo = await _seed(db_session, seed_workspace)
    patch_gateway(None)

    with pytest.raises(FileNotFoundError):
        await sync_lanes_for_repo(
            session=db_session, repo=repo, install=install
        )


# ---------------------------------------------------------------------------
# Path + conclusion helpers
# ---------------------------------------------------------------------------


def test_extract_lane_id_matches_ship_wrapper():
    assert (
        extract_lane_id_from_path(".github/workflows/ship-pr_review.yml")
        == "pr_review"
    )
    assert (
        extract_lane_id_from_path(".github/workflows/ship-daily.yaml")
        == "daily"
    )


def test_extract_lane_id_rejects_non_lane_paths():
    assert extract_lane_id_from_path(None) is None
    assert extract_lane_id_from_path("") is None
    assert (
        extract_lane_id_from_path(".github/workflows/ci.yml") is None
    )
    assert (
        extract_lane_id_from_path(
            ".github/workflows/ship-BadCase.yml"
        )
        is None
    )


def test_map_conclusion_normalises_github_values():
    assert map_conclusion_to_status("success") == "succeeded"
    assert map_conclusion_to_status("failure") == "failed"
    assert map_conclusion_to_status("cancelled") == "cancelled"
    assert map_conclusion_to_status(None) == "running"


# ---------------------------------------------------------------------------
# RFC-0008 C3.1 — multi-pattern lanes (``patterns: [ids]``)
# ---------------------------------------------------------------------------


_YAML_V2_MULTI = """
version: 2
lanes:
  tech_debt_audit:
    schedule: "0 6 * * 1"
    patterns: [role-tech-architect, role-qa-architect, role-security-officer]
  pr_review:
    event: pull_request
    pattern: flow-pr-self-review
"""


@pytest.mark.asyncio
async def test_sync_parses_patterns_list_and_keeps_primary_in_column(
    db_session, seed_workspace, patch_gateway
) -> None:
    """RFC-0008 C3.1 — ``patterns: [ids]`` is the canonical shape.

    The DB column ``Lane.pattern`` stays populated with the first id
    (back-compat with consumers that only read the primary); the full
    list survives round-trip in ``config_blob['patterns']`` so
    downstream readers (Console Active calendar, seed derivation)
    never have to re-parse YAML.
    """
    from backend.app.db.models.lanes import Lane

    _workspace, install, repo = await _seed(db_session, seed_workspace)
    patch_gateway(_YAML_V2_MULTI)

    report = await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )
    assert report.added == 2
    assert report.errors == []

    rows = {
        row.lane_id: row
        for row in (
            await db_session.execute(
                Lane.__table__.select().where(Lane.repo_id == repo.id)
            )
        ).mappings()
    }

    # Multi-pattern lane: primary pinned in ``pattern`` column, full
    # list survives in ``config_blob``.
    audit = rows["tech_debt_audit"]
    assert audit["pattern"] == "role-tech-architect"
    assert audit["config_blob"]["patterns"] == [
        "role-tech-architect",
        "role-qa-architect",
        "role-security-officer",
    ]
    # Single-pattern lane: ``patterns`` list is a one-element
    # normalisation of the legacy ``pattern:`` scalar.
    pr_row = rows["pr_review"]
    assert pr_row["pattern"] == "flow-pr-self-review"
    assert pr_row["config_blob"]["patterns"] == ["flow-pr-self-review"]
    # Default fanout lands in the blob so downstream consumers don't
    # have to branch on "is this key missing?".
    assert audit["config_blob"]["fanout"] == "matrix"
    assert pr_row["config_blob"]["fanout"] == "matrix"
    assert map_conclusion_to_status("timed_out") == "timed_out"


_YAML_V2_MULTI_SEQ = """
version: 2
lanes:
  tech_debt_audit:
    schedule: "0 6 * * 1"
    patterns: [role-tech-architect, role-qa-architect]
    fanout: sequential
"""


@pytest.mark.asyncio
async def test_sync_preserves_fanout_field(
    db_session, seed_workspace, patch_gateway
) -> None:
    """RFC-0008 C3.2 — `fanout` survives round-trip into ``config_blob``.

    Unknown values fall back to the default so a stale config doesn't
    break sync; the writer endpoint is the authority on validation.
    """
    from backend.app.db.models.lanes import Lane

    _workspace, install, repo = await _seed(db_session, seed_workspace)
    patch_gateway(_YAML_V2_MULTI_SEQ)
    report = await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )
    assert report.added == 1
    row = (
        await db_session.execute(
            Lane.__table__.select().where(Lane.repo_id == repo.id)
        )
    ).mappings().first()
    assert row["config_blob"]["fanout"] == "sequential"
    assert row["config_blob"]["patterns"] == [
        "role-tech-architect",
        "role-qa-architect",
    ]


@pytest.mark.asyncio
async def test_apply_workflow_run_completion_pins_last_run(
    db_session, seed_workspace, patch_gateway
) -> None:
    from backend.app.db.models.lanes import Lane

    _workspace, install, repo = await _seed(db_session, seed_workspace)
    patch_gateway(_YAML_V2)
    await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )

    finished = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    row = await apply_workflow_run_completion(
        session=db_session,
        repo=repo,
        workflow_path=".github/workflows/ship-pr_review.yml",
        conclusion="success",
        finished_at=finished,
    )
    assert row is not None
    assert row.lane_id == "pr_review"
    assert row.last_run_status == "succeeded"
    assert row.last_run_at == finished


@pytest.mark.asyncio
async def test_apply_workflow_run_completion_skips_unmatched_path(
    db_session, seed_workspace, patch_gateway
) -> None:
    _workspace, install, repo = await _seed(db_session, seed_workspace)
    patch_gateway(_YAML_V2)
    await sync_lanes_for_repo(
        session=db_session, repo=repo, install=install
    )

    row = await apply_workflow_run_completion(
        session=db_session,
        repo=repo,
        workflow_path=".github/workflows/ci.yml",
        conclusion="success",
        finished_at=None,
    )
    assert row is None
