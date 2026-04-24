"""Navigator Phase-6 plays tools (Wave A read-side).

These tools wrap the catalog pattern loader. We don't fixture out
the catalog — same approach as ``test_v1_plays_coverage.py`` —
because the loader IS the contract, and over-mocking would let
catalog edits silently break the LLM tools. The assertions key
off long-lived pattern ids (``flow-pr-self-review``,
``scan-security-deps``, ``scan-test-coverage``) and verify
relative behavior (subset, ordering, presence/absence) rather
than absolute counts.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toolbox(session, *, workspace_id, user_id):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
    )


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


async def _seed_lane(db_session, *, workspace, repo, lane_id: str, pattern: str):
    from backend.app.db.models.lanes import Lane

    lane = Lane(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id=lane_id,
        kind="event",
        pattern=pattern,
        config_blob={"pattern": pattern},
    )
    db_session.add(lane)
    await db_session.flush()
    return lane


# ---------------------------------------------------------------------------
# plays_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plays_list_category_filter(db_session, seed_workspace) -> None:
    """``category`` is exact-match — every row must carry it."""
    from backend.app.services import catalog as catalog_service

    user, _, ws = seed_workspace

    # Pick a category that's present in the live catalog so the test
    # follows real data without hard-coding a string that may rename.
    sample_category: str | None = None
    for entry in catalog_service.list_patterns():
        if isinstance(entry.category, str) and entry.category:
            spec = entry.spec if isinstance(entry.spec, dict) else {}
            inbox = spec.get("inbox") if isinstance(spec, dict) else None
            if isinstance(inbox, dict) and inbox.get("profile") == "silent":
                continue
            sample_category = entry.category
            break
    assert sample_category is not None

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("plays_list", {"category": sample_category})
    )
    assert out["items"], "expected at least one item for chosen category"
    for row in out["items"]:
        assert row["category"] == sample_category


def _patch_critical_keys(monkeypatch, critical_keys: set[str]) -> None:
    """Force a chosen set of catalog ids to look ``critical: true``.

    The navigator tool (mirroring the v1 ``plays_coverage`` route)
    reads ``entry.spec.get("critical")``, but today all artifacts
    declare ``critical`` at the TOP level of the frontmatter (next
    to ``id``), NOT under ``spec:`` — so ``entry.spec["critical"]``
    is always ``None`` and every row evaluates to ``critical=False``.

    The v1 route's tests (``test_v1_plays_coverage.py``) patch a
    sibling ``_frontmatter_critical`` resolver to work around this.
    The agent tool inlines the read, so we have to patch the
    catalog loader itself: wrap ``list_patterns`` and stamp
    ``spec["critical"] = True`` on the chosen ids before handing
    the entries to the tool.
    """
    from backend.app.services import catalog as catalog_service
    from backend.app.services.agent import tools as tools_module

    real = catalog_service.list_patterns

    def _patched(*args, **kwargs):
        entries = real(*args, **kwargs)
        for e in entries:
            if e.id in critical_keys:
                spec = dict(e.spec) if isinstance(e.spec, dict) else {}
                spec["critical"] = True
                e.spec = spec
        return entries

    # Patch BOTH the canonical module attribute and the alias the
    # agent tool imports as ``catalog_service`` so the override
    # holds however the tool reaches it.
    monkeypatch.setattr(catalog_service, "list_patterns", _patched)
    monkeypatch.setattr(
        tools_module.catalog_service, "list_patterns", _patched
    )


@pytest.mark.asyncio
async def test_plays_list_critical_only(
    db_session, seed_workspace, monkeypatch
) -> None:
    """``critical_only=true`` returns only ``critical: true`` rows."""
    user, _, ws = seed_workspace
    critical_keys = {"flow-pr-self-review", "scan-security-deps"}
    _patch_critical_keys(monkeypatch, critical_keys)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("plays_list", {"critical_only": True})
    )
    assert out["items"], "expected at least one critical pattern in catalog"
    for row in out["items"]:
        assert row["critical"] is True
    keys = {r["play_key"] for r in out["items"]}
    assert keys == critical_keys


@pytest.mark.asyncio
async def test_plays_list_q_substring_match(
    db_session, seed_workspace
) -> None:
    """``q`` matches title or play_key, case-insensitive."""
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(await box.invoke("plays_list", {"q": "security"}))
    assert out["items"]
    keys_or_titles = [
        (r["play_key"].lower(), (r["title"] or "").lower())
        for r in out["items"]
    ]
    for key, title in keys_or_titles:
        assert "security" in key or "security" in title


@pytest.mark.asyncio
async def test_plays_list_invalid_category_argument(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("plays_list", {"category": 42})
    )
    assert out["error"] == "invalid_category"


# ---------------------------------------------------------------------------
# plays_get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plays_get_known_returns_full_body(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "plays_get", {"play_key": "flow-pr-self-review"}
        )
    )
    assert out["play_key"] == "flow-pr-self-review"
    assert isinstance(out["title"], str) and out["title"]
    # Body is the artifact prose — must not be empty for a long-lived play.
    assert isinstance(out["body"], str) and len(out["body"]) > 0
    assert isinstance(out.get("modes"), list)


@pytest.mark.asyncio
async def test_plays_get_unknown_returns_not_found(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "plays_get",
            {"play_key": f"definitely-missing-{uuid.uuid4().hex[:6]}"},
        )
    )
    assert out["error"] == "not_found"


@pytest.mark.asyncio
async def test_plays_get_missing_play_key_validation(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(await box.invoke("plays_get", {}))
    assert out["error"] == "invalid_play_key"


# ---------------------------------------------------------------------------
# plays_coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plays_coverage_has_gaps_filter_excludes_full(
    db_session, seed_workspace
) -> None:
    """A play covered on every activated repo drops out of has_gaps view."""
    user, _, ws = seed_workspace
    repo_a = await _seed_repo(
        db_session, ws, external_id=110_001, full_name="acme/cov-a"
    )
    repo_b = await _seed_repo(
        db_session, ws, external_id=110_002, full_name="acme/cov-b"
    )
    # Wire scan-test-coverage on BOTH repos => coverage_pct = 1.0 for it.
    await _seed_lane(
        db_session,
        workspace=ws,
        repo=repo_a,
        lane_id="scan_cov",
        pattern="scan-test-coverage",
    )
    await _seed_lane(
        db_session,
        workspace=ws,
        repo=repo_b,
        lane_id="scan_cov",
        pattern="scan-test-coverage",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("plays_coverage", {"has_gaps": True})
    )
    assert out["activated_repos_total"] == 2
    keys = {r["play_key"] for r in out["rows"]}
    assert "scan-test-coverage" not in keys
    for row in out["rows"]:
        assert row["coverage_pct"] < 1.0


@pytest.mark.asyncio
async def test_plays_coverage_critical_only_filter(
    db_session, seed_workspace, monkeypatch
) -> None:
    user, _, ws = seed_workspace
    await _seed_repo(
        db_session, ws, external_id=110_010, full_name="acme/crit"
    )
    critical_keys = {"flow-pr-self-review", "scan-security-deps"}
    _patch_critical_keys(monkeypatch, critical_keys)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("plays_coverage", {"critical_only": True})
    )
    assert out["rows"], "expected ≥1 critical play surface in the coverage view"
    for row in out["rows"]:
        assert row["critical"] is True
    assert {r["play_key"] for r in out["rows"]} == critical_keys


@pytest.mark.asyncio
async def test_plays_coverage_sort_critical_with_gaps_first(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Critical-with-gaps must precede non-critical-with-gaps and full-cov."""
    user, _, ws = seed_workspace
    # ``flow-pr-self-review`` is the critical-with-gaps anchor; mark
    # it critical via the same shim the v1 plays-coverage tests use
    # (see ``_patch_critical_keys`` for why).
    _patch_critical_keys(monkeypatch, {"flow-pr-self-review"})
    repo_a = await _seed_repo(
        db_session, ws, external_id=110_020, full_name="acme/sort-a"
    )
    repo_b = await _seed_repo(
        db_session, ws, external_id=110_021, full_name="acme/sort-b"
    )
    # scan-test-coverage on BOTH (=> covered, non-critical-by-frontmatter
    # depending on artifact, but it should be in bucket 3 if non-critical
    # OR bucket 1 if critical). Either way, it must NOT precede a
    # critical-with-gaps row.
    await _seed_lane(
        db_session,
        workspace=ws,
        repo=repo_a,
        lane_id="scan_cov",
        pattern="scan-test-coverage",
    )
    await _seed_lane(
        db_session,
        workspace=ws,
        repo=repo_b,
        lane_id="scan_cov",
        pattern="scan-test-coverage",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(await box.invoke("plays_coverage", {}))
    rows = out["rows"]
    by_key = {r["play_key"]: idx for idx, r in enumerate(rows)}

    # ``flow-pr-self-review`` is critical-with-gaps (no lane wired
    # for it in this test); it must appear before any non-critical
    # row that has gaps.
    assert "flow-pr-self-review" in by_key
    crit_gaps_idx = by_key["flow-pr-self-review"]

    # Find the first NON-critical with gaps (bucket 2) — it must
    # follow our critical-with-gaps row.
    first_noncrit_gaps = next(
        (
            idx
            for idx, r in enumerate(rows)
            if not r["critical"] and r["coverage_pct"] < 1.0
        ),
        None,
    )
    assert first_noncrit_gaps is not None
    assert crit_gaps_idx < first_noncrit_gaps

    # And every fully-covered row sorts AFTER every critical-with-gaps row.
    full_cov_indices = [
        idx for idx, r in enumerate(rows) if r["coverage_pct"] >= 1.0
    ]
    if full_cov_indices:
        assert min(full_cov_indices) > crit_gaps_idx


@pytest.mark.asyncio
async def test_plays_coverage_invalid_category_argument(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("plays_coverage", {"category": 99})
    )
    assert out["error"] == "invalid_category"
