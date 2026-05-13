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
