"""Strangler regression gate (ELS-240).

Pins the Phase-4 teardown contract: ONLY console-render routes were
removed; nothing in the EXCLUDED set (engine/CLI boundary, webhooks,
OAuth, Inbox store, config registry, dashboard_priorities) regressed.
Computed from the live FastAPI route table — the OpenAPI diff as an
executable assertion.
"""

from __future__ import annotations


def _route_paths() -> set[str]:
    from backend.app.main import app

    # Some FastAPI/Starlette versions leave a path-less ``_IncludedRouter``
    # proxy in ``app.routes`` per ``include_router`` call (seen in CI once
    # the MCP edge + OAuth broker routers were mounted). Those carry no
    # path to gate on — skip anything without a ``.path``.
    return {r.path for r in app.routes if hasattr(r, "path")}


def test_deleted_render_routes_are_gone() -> None:
    paths = _route_paths()
    gone_prefixes = (
        "/v1/workspaces/{workspace_id}/analytics/dora",
        "/v1/workspaces/{workspace_id}/dashboard",
        "/v1/workspaces/{workspace_id}/live-system",
    )
    for p in paths:
        for g in gone_prefixes:
            # dashboard_priorities lives under /dashboard/priorities —
            # explicitly EXCLUDED from the teardown.
            if p.startswith(g) and "priorities" not in p:
                raise AssertionError(f"console-render route survived: {p}")
    assert not any("/repos/{repo_id}/home" in p for p in paths), "repo_home survived"


def test_excluded_set_untouched() -> None:
    """The engine/CLI boundary + control-plane-adjacent routes must
    all still be registered."""
    paths = _route_paths()

    def present(fragment: str) -> bool:
        return any(fragment in p for p in paths)

    # CLI boundary (shipctl run picker + finish)
    assert present("/agent-runs"), "agent_runs boundary missing"
    # Inbox store API
    assert present("/inbox"), "inbox routes missing"
    # Config registry (the pivot's config spine)
    assert present("/config/{scope}"), "config scope routes missing"
    # Engine health residue (ELS-230)
    assert present("/engine-health"), "engine-health missing"
    # Webhook / OAuth ingress
    assert present("/github"), "github app ingress missing"
    assert present("/linear"), "linear ingress missing"
    # MUST-FIX: dashboard_priorities is EXCLUDED (control-plane-adjacent)
    assert present("priorities"), "dashboard_priorities routes missing"


def test_no_double_v1_prefix() -> None:
    """config.py + planning.py used to self-prefix ``/v1`` under
    api_router's ``/v1`` — every client (ConfigScopeCard, the settings
    renderer, mass-planning-preview) calls single-``/v1`` paths, so the
    doubled routes 404'd for all of them. Pin the fixed mount points."""
    paths = _route_paths()
    doubled = sorted(p for p in paths if p.startswith("/v1/v1/"))
    assert not doubled, f"double-prefixed routes: {doubled}"
    assert "/v1/workspaces/{workspace_id}/config/{scope}" in paths
    assert any(
        p.startswith("/v1/workspaces/{workspace_id}/planning/") for p in paths
    ), "planning routes not mounted under /v1/workspaces"


def test_dashboard_priorities_model_consumers_intact() -> None:
    """WorkspaceProjectPriority feeds project_state_sync,
    project_completion and the Navigator picker — the reason the module
    was pre-tagged EXCLUDED. Pin the imports."""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    for rel in (
        "services/agent/project_state_sync.py",
        "services/project_completion.py",
        "services/agent/tools.py",
    ):
        src = (app_dir / rel).read_text()
        assert "WorkspaceProjectPriority" in src, (
            f"{rel} no longer consumes WorkspaceProjectPriority — "
            "EXCLUDED invariant broken"
        )


def test_inbox_tables_not_dropped_by_phase4() -> None:
    """No migration in this phase may touch the Inbox store."""
    from pathlib import Path

    versions = (
        Path(__file__).resolve().parents[1] / "migrations" / "versions"
    )
    phase_migrations = [
        p for p in versions.glob("00*.py")
        if p.name.startswith(("0086", "0087"))
    ]
    assert phase_migrations, "phase migrations missing"
    for p in phase_migrations:
        src = p.read_text()
        assert "inbox" not in src.lower(), f"{p.name} touches the Inbox store"


def test_inbox_model_importable_and_complete() -> None:
    from backend.app.db.models.inbox import (  # noqa: F401
        InboxItem,
        InboxItemEvent,
        RunEscalation,
    )

    cols = {c.name for c in InboxItem.__table__.columns}
    # The approval/escalation engine's load-bearing columns.
    for col in ("intake_handle", "intake_reason", "category", "priority",
                "resolution", "snoozed_until", "stale_after"):
        assert col in cols, f"InboxItem.{col} missing"
