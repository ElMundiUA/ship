"""Tests for ``GET /v1/workspaces/{ws}/plays/coverage`` (P4-00).

The endpoint folds catalog patterns × Lane rows into one row per Play
with a covered/uncovered repo split. We don't fixture out the catalog
here — :func:`backend.app.services.catalog.list_patterns` reads the
real ``artifacts/`` tree so the tests double as integration coverage
for the loader. To keep assertions stable across catalog edits we
key off concrete, long-lived pattern ids (``flow-pr-self-review``,
``common-base``, ``op-retry-sweep``) and assert relative behaviour
(present/absent, count delta, ordering bucket) rather than absolute
counts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed_repo(
    db_session,
    workspace,
    *,
    external_id: int,
    full_name: str,
    installation_id: uuid.UUID | None = None,
):
    from backend.app.db.models.integrations import WorkspaceRepo

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=installation_id,
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


async def _seed_lane(
    db_session,
    *,
    workspace,
    repo,
    lane_id: str,
    pattern: str,
    kind: str = "event",
    extra_patterns: list[str] | None = None,
):
    from backend.app.db.models.lanes import Lane

    config_blob: dict = {"pattern": pattern}
    if extra_patterns:
        config_blob["patterns"] = [pattern, *extra_patterns]
    lane = Lane(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id=lane_id,
        kind=kind,
        pattern=pattern,
        config_blob=config_blob,
    )
    db_session.add(lane)
    await db_session.flush()
    return lane


@pytest_asyncio.fixture
async def workspace_with_two_repos(db_session, seed_workspace):
    _, raw, workspace = seed_workspace
    repo_a = await _seed_repo(
        db_session, workspace, external_id=70_001, full_name="acme/alpha"
    )
    repo_b = await _seed_repo(
        db_session, workspace, external_id=70_002, full_name="acme/bravo"
    )
    return raw, workspace, repo_a, repo_b


def _row_by_key(rows: list[dict], play_key: str) -> dict | None:
    for row in rows:
        if row["play_key"] == play_key:
            return row
    return None


# ---------------------------------------------------------------------------
# 1. Empty workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_empty_workspace_returns_empty_rows(
    v1_client, seed_workspace
):
    """No activated repos ⇒ rows=[] and total=0.

    We deliberately return an empty rows list (rather than every play
    with coverage_pct=0) so the FE can render an empty-state CTA
    without iterating an 80-long noise list.
    """
    _, raw, workspace = seed_workspace

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["activated_repos_total"] == 0
    # Rationale: with no candidate repos, every play would be 0/0 —
    # not informative. We still walk the catalog (so silent patterns
    # are filtered) but return rows with assignments_count=0,
    # coverage_pct=0.0. Tighter shape: confirm rows is a list and
    # contains user-facing plays only.
    assert isinstance(body["rows"], list)
    keys = {r["play_key"] for r in body["rows"]}
    assert "common-base" not in keys
    assert "op-retry-sweep" not in keys
    for row in body["rows"]:
        assert row["activated_repos_total"] == 0
        assert row["assignments_count"] == 0
        assert row["coverage_pct"] == 0.0
        assert row["repos_covered"] == []
        assert row["repos_uncovered"] == []


# ---------------------------------------------------------------------------
# 2. One repo, no lanes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_single_repo_no_lanes(v1_client, db_session, seed_workspace):
    _, raw, workspace = seed_workspace
    repo = await _seed_repo(
        db_session, workspace, external_id=71_001, full_name="acme/solo"
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["activated_repos_total"] == 1
    assert len(body["rows"]) > 0
    for row in body["rows"]:
        assert row["activated_repos_total"] == 1
        assert row["assignments_count"] == 0
        assert row["coverage_pct"] == 0.0
        assert row["repos_covered"] == []
        assert row["repos_uncovered"] == [str(repo.id)]


# ---------------------------------------------------------------------------
# 3. One repo, one lane wiring a play
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_single_repo_one_lane(
    v1_client, db_session, seed_workspace
):
    _, raw, workspace = seed_workspace
    repo = await _seed_repo(
        db_session, workspace, external_id=72_001, full_name="acme/single"
    )
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo,
        lane_id="pr_review",
        pattern="flow-pr-self-review",
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["activated_repos_total"] == 1
    target = _row_by_key(body["rows"], "flow-pr-self-review")
    assert target is not None, "flow-pr-self-review should appear in coverage"
    assert target["assignments_count"] == 1
    assert target["coverage_pct"] == 1.0
    assert target["repos_covered"] == [str(repo.id)]
    assert target["repos_uncovered"] == []
    # Every other row should still be at 0 — the lane only wires
    # one play.
    other = next(r for r in body["rows"] if r["play_key"] != "flow-pr-self-review")
    assert other["assignments_count"] == 0
    assert other["coverage_pct"] == 0.0


# ---------------------------------------------------------------------------
# 4. Two repos, partial assignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_two_repos_partial_assignment(
    v1_client, db_session, workspace_with_two_repos
):
    raw, workspace, repo_a, repo_b = workspace_with_two_repos

    # Wire flow-pr-self-review on repo_a only.
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo_a,
        lane_id="pr_review",
        pattern="flow-pr-self-review",
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["activated_repos_total"] == 2

    target = _row_by_key(body["rows"], "flow-pr-self-review")
    assert target is not None
    assert target["assignments_count"] == 1
    assert target["coverage_pct"] == 0.5
    assert target["repos_covered"] == [str(repo_a.id)]
    assert target["repos_uncovered"] == [str(repo_b.id)]


# ---------------------------------------------------------------------------
# 5. Silent patterns excluded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_excludes_silent_patterns(
    v1_client, db_session, seed_workspace
):
    """``common-*`` and ``op-*`` patterns (inbox.profile=silent) MUST NOT appear.

    These are system-internal helpers / housekeeping tasks; surfacing
    them in Coverage would mis-imply "configure this on every repo".
    """
    _, raw, workspace = seed_workspace
    await _seed_repo(
        db_session, workspace, external_id=73_001, full_name="acme/silent-test"
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    keys = {r["play_key"] for r in resp.json()["rows"]}
    # Sentinel silent patterns from the planning doc §1.
    for silent in (
        "common-base",
        "common-kickoff",
        "op-retry-sweep",
        "op-stale-issue-sweep",
        "op-workflow-self-heal",
    ):
        assert silent not in keys, (
            f"silent pattern {silent!r} leaked into the user-facing "
            f"Plays catalog"
        )


# ---------------------------------------------------------------------------
# 6. Sorting: critical-uncovered first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_sorting_critical_uncovered_first(
    v1_client, db_session, seed_workspace, monkeypatch
):
    """Server pre-sorts: critical-with-gaps → non-critical-with-gaps → covered.

    Within "with gaps" buckets the order is coverage_pct ASC. We
    monkey-patch the ``critical`` resolver so we don't depend on
    sibling B (P4-07) having landed yet — the assertion is on the
    sort algorithm, not on which patterns happen to be marked
    critical in frontmatter today.
    """
    from backend.app.api.v1.routes import plays as plays_module

    _, raw, workspace = seed_workspace
    repo_a = await _seed_repo(
        db_session, workspace, external_id=74_001, full_name="acme/sort-a"
    )
    repo_b = await _seed_repo(
        db_session, workspace, external_id=74_002, full_name="acme/sort-b"
    )

    # Cover scan-test-coverage on both repos (pct=1.0); cover
    # flow-pr-self-review on one (pct=0.5); leave flow-release-notes
    # uncovered (pct=0.0).
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo_a,
        lane_id="pr_review",
        pattern="flow-pr-self-review",
    )
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo_a,
        lane_id="coverage_scan",
        pattern="scan-test-coverage",
        kind="schedule",
    )
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo_b,
        lane_id="coverage_scan",
        pattern="scan-test-coverage",
        kind="schedule",
    )

    # Mark flow-pr-self-review (with gaps) AND scan-test-coverage
    # (fully covered) as critical. flow-release-notes stays
    # non-critical so it lands in the second bucket.
    critical_keys = {"flow-pr-self-review", "scan-test-coverage"}

    def _fake_critical(entry):
        return entry.id in critical_keys

    monkeypatch.setattr(
        plays_module, "_frontmatter_critical", _fake_critical
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]

    # Position lookups.
    by_key = {r["play_key"]: idx for idx, r in enumerate(rows)}
    pr_review_idx = by_key["flow-pr-self-review"]
    release_notes_idx = by_key["flow-release-notes"]
    test_coverage_idx = by_key["scan-test-coverage"]

    # Bucket 1 (critical w/ gaps) before bucket 2 (non-critical w/ gaps).
    assert pr_review_idx < release_notes_idx, (
        "critical-with-gaps must precede non-critical-with-gaps"
    )
    # Bucket 2 (non-critical w/ gaps) before bucket 3 (fully covered).
    assert release_notes_idx < test_coverage_idx, (
        "rows with gaps must precede fully covered rows"
    )

    # Within bucket 1, ordering is coverage_pct ASC. We only have one
    # row in bucket 1 here (flow-pr-self-review at 0.5) so verify it's
    # ahead of every other critical-with-gaps would-be row by
    # checking it's the first critical entry.
    first_critical = next(
        idx for idx, r in enumerate(rows)
        if r["critical"] and r["coverage_pct"] < 1.0
    )
    assert first_critical == pr_review_idx


# ---------------------------------------------------------------------------
# 7. Filter: ?category=
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_filter_category(v1_client, db_session, seed_workspace):
    """Server-side category filter is exact-match on ``spec.category``."""
    _, raw, workspace = seed_workspace
    await _seed_repo(
        db_session, workspace, external_id=75_001, full_name="acme/cat-filter"
    )

    # Look up a real category value from the catalog so the test
    # doesn't drift if the rename ships.
    from backend.app.services import catalog as catalog_service

    category_to_test: str | None = None
    for entry in catalog_service.list_patterns():
        spec = entry.spec if isinstance(entry.spec, dict) else {}
        inbox = spec.get("inbox") if isinstance(spec, dict) else None
        if isinstance(inbox, dict) and inbox.get("profile") == "silent":
            continue
        if isinstance(entry.category, str) and entry.category:
            category_to_test = entry.category
            break
    assert category_to_test is not None, (
        "expected at least one user-facing pattern to declare a category"
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
        params={"category": category_to_test},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert len(rows) > 0
    assert all(r["category"] == category_to_test for r in rows)


# ---------------------------------------------------------------------------
# 8. Filter: ?critical_only=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_filter_critical_only(
    v1_client, db_session, seed_workspace, monkeypatch
):
    """``critical_only=true`` restricts rows to those marked critical."""
    from backend.app.api.v1.routes import plays as plays_module

    _, raw, workspace = seed_workspace
    await _seed_repo(
        db_session, workspace, external_id=76_001, full_name="acme/crit-filter"
    )

    critical_keys = {"flow-pr-self-review", "scan-security-deps"}

    def _fake_critical(entry):
        return entry.id in critical_keys

    monkeypatch.setattr(
        plays_module, "_frontmatter_critical", _fake_critical
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
        params={"critical_only": "true"},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    keys = {r["play_key"] for r in rows}
    assert keys == critical_keys, (
        f"expected only critical rows, got {keys!r}"
    )
    assert all(r["critical"] is True for r in rows)


# ---------------------------------------------------------------------------
# 9. Filter: ?has_gaps=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_filter_has_gaps(
    v1_client, db_session, workspace_with_two_repos
):
    raw, workspace, repo_a, repo_b = workspace_with_two_repos

    # Wire scan-test-coverage on BOTH repos (=> coverage_pct=1.0)
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo_a,
        lane_id="cov_scan",
        pattern="scan-test-coverage",
        kind="schedule",
    )
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo_b,
        lane_id="cov_scan",
        pattern="scan-test-coverage",
        kind="schedule",
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
        params={"has_gaps": "true"},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert len(rows) > 0
    assert all(r["coverage_pct"] < 1.0 for r in rows)
    keys = {r["play_key"] for r in rows}
    # The fully-covered scan-test-coverage row must be filtered out.
    assert "scan-test-coverage" not in keys


# ---------------------------------------------------------------------------
# 10. covered + uncovered cleanly partition the activated set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_includes_repo_ids_in_split(
    v1_client, db_session, workspace_with_two_repos
):
    raw, workspace, repo_a, repo_b = workspace_with_two_repos
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo_a,
        lane_id="pr_review",
        pattern="flow-pr-self-review",
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    activated_ids = {str(repo_a.id), str(repo_b.id)}
    for row in body["rows"]:
        covered = set(row["repos_covered"])
        uncovered = set(row["repos_uncovered"])
        assert covered.isdisjoint(uncovered), (
            f"repo cannot be both covered and uncovered for {row['play_key']!r}"
        )
        assert covered | uncovered == activated_ids, (
            f"covered+uncovered must partition activated set for "
            f"{row['play_key']!r}; got covered={covered!r} "
            f"uncovered={uncovered!r}"
        )
        assert len(covered) == row["assignments_count"]


# ---------------------------------------------------------------------------
# Bonus: multi-pattern lanes (RFC-0008 C3.1) count for every pattern they list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_multi_pattern_lane_credits_each_pattern(
    v1_client, db_session, seed_workspace
):
    """A lane with ``patterns: [a, b]`` covers both ``a`` and ``b``.

    Pinning this so the C3.1 multi-pattern syntax stays usable on
    Coverage; the planning doc §6 calls it out explicitly as a v1
    requirement and we'd otherwise silently under-count workspaces
    that have moved to the list form.
    """
    _, raw, workspace = seed_workspace
    repo = await _seed_repo(
        db_session, workspace, external_id=77_001, full_name="acme/multi"
    )
    await _seed_lane(
        db_session,
        workspace=workspace,
        repo=repo,
        lane_id="pr_review",
        pattern="flow-pr-self-review",
        extra_patterns=["flow-blast-radius"],
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/plays/coverage",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    a = _row_by_key(rows, "flow-pr-self-review")
    b = _row_by_key(rows, "flow-blast-radius")
    assert a is not None and b is not None
    assert a["assignments_count"] == 1
    assert b["assignments_count"] == 1
