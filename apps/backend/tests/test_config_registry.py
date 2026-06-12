"""Phase 1d ``config_registry`` — scope discovery + scalar helpers.

Two surfaces consume the registry: the ``config_help`` / ``config_put``
agent tools and the console settings UI. Both treat scopes as
opaque slugs + JSONSchema, so the unit boundary worth pinning is
"every built-in scope is consistent and round-trips".

Concretely:

* ``list_scopes()`` lists every slug from :data:`SCOPES` in order.
* Each scope's schema is a well-formed dict carrying ``type``.
* ``put_scope`` validation rejects shapes the writer would otherwise
  pass through — e.g. ``agent.provider`` accepts only the supported
  trio.

DB-bound paths (audit-log row inserted, partial catalog merge) get
asserted in :mod:`test_navigator_mutating_tools` via the tool surface
once a real session is wired in CI — that route doubles as the
end-to-end smoke for the registry.
"""

from __future__ import annotations

import pytest

from backend.app.services.config_registry import (
    SCOPES,
    list_scopes,
)


def test_list_scopes_returns_every_registered_slug() -> None:
    rows = list_scopes()
    slugs = {r["slug"] for r in rows}
    # Pin the v1 set; new scopes should land deliberately, not by
    # surprise. Update this assertion alongside :data:`SCOPES`.
    assert slugs == {
        "agent.provider",
        "agent.default_profile",
        "catalog.sources",
        "console.surface",
        "autonomy.profile",
        "local_executor.enabled",
    }
    # Every row carries a non-empty description (consumed by the
    # help-without-scope path).
    for row in rows:
        assert row["description"], f"empty description for {row['slug']}"


def test_every_scope_carries_jsonschema_with_type() -> None:
    """Every scope's schema must declare a type so JSONSchema-driven
    renderers (FE form generator, JSON validators) don't crash on an
    empty-schema row. ``string``-typed scopes carry an ``enum`` to
    keep the LLM honest about valid values."""
    for slug, scope in SCOPES.items():
        assert "type" in scope.schema, f"{slug}: schema missing 'type'"


@pytest.mark.parametrize(
    "scope_slug",
    sorted(SCOPES.keys()),
)
def test_every_scope_has_audit_event(scope_slug: str) -> None:
    """Audit-log queries that already index on the per-setting
    action name (``workspace.agent_provider.set`` etc.) must keep
    matching after the registry takes over. Every scope must declare
    a non-empty audit event."""
    scope = SCOPES[scope_slug]
    assert scope.audit_event, f"{scope_slug}: empty audit_event"
    # Convention: dotted path matching the existing workspace.* /
    # repo.* / inbox.* namespacing used elsewhere in audit_log.
    assert "." in scope.audit_event


# ---------------------------------------------------------------------------
# Headless-pivot config spine (ELS-218): console.surface + autonomy.profile
# ---------------------------------------------------------------------------


class _FakeWorkspace:
    """Just enough Workspace shape for the scalar scope writers."""

    def __init__(self) -> None:
        self.settings: dict = {}
        self.autonomy = "balanced"


@pytest.mark.asyncio
async def test_console_surface_defaults_to_full() -> None:
    scope = SCOPES["console.surface"]
    ws = _FakeWorkspace()
    assert await scope.read(None, ws) == "full"


@pytest.mark.asyncio
async def test_console_surface_round_trips_and_merges_settings() -> None:
    scope = SCOPES["console.surface"]
    ws = _FakeWorkspace()
    ws.settings = {"notifications": {"email_to": "ops@example.com"}}
    await scope.write(None, ws, "residual")
    # Existing settings keys survive the merge; reassignment (not
    # in-place mutation) is what makes JSONB change detection fire.
    assert ws.settings["notifications"] == {"email_to": "ops@example.com"}
    assert ws.settings["console"] == {"surface": "residual"}
    assert await scope.read(None, ws) == "residual"


@pytest.mark.asyncio
async def test_console_surface_rejects_out_of_enum() -> None:
    scope = SCOPES["console.surface"]
    ws = _FakeWorkspace()
    with pytest.raises(ValueError):
        await scope.write(None, ws, "hidden")
    assert "console" not in ws.settings


@pytest.mark.asyncio
async def test_autonomy_profile_round_trips() -> None:
    scope = SCOPES["autonomy.profile"]
    ws = _FakeWorkspace()
    assert await scope.read(None, ws) == "balanced"
    await scope.write(None, ws, "high")
    assert ws.autonomy == "high"
    assert await scope.read(None, ws) == "high"


@pytest.mark.asyncio
async def test_autonomy_profile_rejects_out_of_enum() -> None:
    scope = SCOPES["autonomy.profile"]
    ws = _FakeWorkspace()
    with pytest.raises(ValueError):
        await scope.write(None, ws, "yolo")
    assert ws.autonomy == "balanced"


@pytest.mark.asyncio
async def test_autonomy_profile_read_falls_back_on_garbage_column() -> None:
    """Legacy / mid-migration rows with an unexpected value resolve to
    the safe default instead of leaking garbage to the LLM/console."""
    scope = SCOPES["autonomy.profile"]
    ws = _FakeWorkspace()
    ws.autonomy = "wat"
    assert await scope.read(None, ws) == "balanced"


@pytest.mark.asyncio
async def test_local_executor_enabled_defaults_off() -> None:
    """ELS-247 FOUNDER DECISION: default-OFF for every workspace."""
    scope = SCOPES["local_executor.enabled"]
    ws = _FakeWorkspace()
    assert await scope.read(None, ws) is False


@pytest.mark.asyncio
async def test_local_executor_enabled_round_trips_and_merges() -> None:
    scope = SCOPES["local_executor.enabled"]
    ws = _FakeWorkspace()
    ws.settings = {"console": {"surface": "residual"}}
    await scope.write(None, ws, True)
    assert ws.settings["console"] == {"surface": "residual"}
    assert ws.settings["local_executor"] == {"enabled": True}
    assert await scope.read(None, ws) is True
    await scope.write(None, ws, False)
    assert await scope.read(None, ws) is False


@pytest.mark.asyncio
async def test_local_executor_enabled_rejects_non_bool() -> None:
    scope = SCOPES["local_executor.enabled"]
    ws = _FakeWorkspace()
    with pytest.raises(ValueError):
        await scope.write(None, ws, "yes")
    assert "local_executor" not in ws.settings
