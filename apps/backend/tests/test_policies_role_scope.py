"""Pin the role-scoped filter on ``list_enabled_policies``.

After 0049, ``WorkspacePolicy.applies_to_roles`` is an optional
``VARCHAR(64)[]`` column. The render path treats NULL and the empty
array as "global" — every role sees these — and a non-empty array
as "scoped" — only the listed slugs see it. These tests pin all
three branches plus the legacy default (no role passed → globals
only).
"""

from __future__ import annotations

import pytest

from backend.app.db.models.policies import WorkspacePolicy
from backend.app.services.policies import (
    list_enabled_policies,
    render_policies_preamble,
)


@pytest.mark.asyncio
async def test_legacy_call_no_role_returns_globals_only(
    db_session, seed_workspace
) -> None:
    """Callers that don't pass ``role_slug`` (pre-refactor CLI, the
    Console policies-list endpoint, …) must keep seeing only the
    global policies. Role-scoped rows must not leak into a
    role-agnostic context."""
    _, _, workspace = seed_workspace
    db_session.add_all(
        [
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Global rule",
                body="Applies to everyone.",
                enabled=True,
                sort_order=0,
                applies_to_roles=None,
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Developer-only",
                body="Applies only to developer.",
                enabled=True,
                sort_order=1,
                applies_to_roles=["developer"],
            ),
        ]
    )
    await db_session.flush()

    rows = await list_enabled_policies(db_session, workspace.id)
    titles = [r.title for r in rows]
    assert titles == ["Global rule"]


@pytest.mark.asyncio
async def test_role_match_returns_globals_plus_scoped(
    db_session, seed_workspace
) -> None:
    """``role_slug='developer'`` returns globals ∪ developer-scoped.
    Other-role-scoped rows must stay invisible — that's the whole
    point of scoping."""
    _, _, workspace = seed_workspace
    db_session.add_all(
        [
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Global rule",
                body="Applies to everyone.",
                enabled=True,
                sort_order=0,
                applies_to_roles=None,
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Developer-only",
                body="Applies only to developer.",
                enabled=True,
                sort_order=1,
                applies_to_roles=["developer"],
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="BA-only",
                body="Applies only to ba.",
                enabled=True,
                sort_order=2,
                applies_to_roles=["ba"],
            ),
        ]
    )
    await db_session.flush()

    rows = await list_enabled_policies(
        db_session, workspace.id, role_slug="developer"
    )
    titles = [r.title for r in rows]
    assert titles == ["Global rule", "Developer-only"]


@pytest.mark.asyncio
async def test_role_in_multi_scope_array_matches(
    db_session, seed_workspace
) -> None:
    """A policy scoped to multiple roles (cross-role rules:
    reviewer-comment-only, evidence-grounded-findings, …) must
    surface for every listed slug."""
    _, _, workspace = seed_workspace
    db_session.add(
        WorkspacePolicy(
            workspace_id=workspace.id,
            title="Reviewer cross-rule",
            body="Applies to all reviewer roles.",
            enabled=True,
            sort_order=0,
            applies_to_roles=[
                "designer",
                "mobile-reviewer",
                "ml-reviewer",
            ],
        )
    )
    await db_session.flush()

    for slug in ("designer", "mobile-reviewer", "ml-reviewer"):
        rows = await list_enabled_policies(
            db_session, workspace.id, role_slug=slug
        )
        titles = [r.title for r in rows]
        assert titles == ["Reviewer cross-rule"], slug

    # A role outside the array sees no rows.
    rows = await list_enabled_policies(
        db_session, workspace.id, role_slug="developer"
    )
    assert rows == []


@pytest.mark.asyncio
async def test_empty_array_is_treated_as_global(
    db_session, seed_workspace
) -> None:
    """Admins editing the role list down to empty mustn't accidentally
    silence the rule. Both ``NULL`` and ``cardinality=0`` count as
    global so the renderer is forgiving of UI edits."""
    _, _, workspace = seed_workspace
    db_session.add(
        WorkspacePolicy(
            workspace_id=workspace.id,
            title="Empty-array rule",
            body="Should still render globally.",
            enabled=True,
            sort_order=0,
            applies_to_roles=[],
        )
    )
    await db_session.flush()

    # Visible without role.
    rows_no_role = await list_enabled_policies(db_session, workspace.id)
    assert [r.title for r in rows_no_role] == ["Empty-array rule"]

    # Visible with any role (treated as global).
    rows_with_role = await list_enabled_policies(
        db_session, workspace.id, role_slug="developer"
    )
    assert [r.title for r in rows_with_role] == ["Empty-array rule"]


@pytest.mark.asyncio
async def test_render_with_role_includes_scoped_rule(
    db_session, seed_workspace
) -> None:
    """End-to-end: ``render_policies_preamble`` honours ``role_slug``
    so the markdown returned to the agent contains the role-scoped
    rule alongside the globals."""
    _, _, workspace = seed_workspace
    db_session.add_all(
        [
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Global rule",
                body="Everyone.",
                enabled=True,
                sort_order=0,
                applies_to_roles=None,
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Developer gate",
                body="Run lint before PR.",
                enabled=True,
                sort_order=1,
                applies_to_roles=["developer"],
            ),
        ]
    )
    await db_session.flush()

    no_role = await render_policies_preamble(db_session, workspace.id)
    assert no_role is not None
    assert "Global rule" in no_role
    assert "Developer gate" not in no_role

    dev = await render_policies_preamble(
        db_session, workspace.id, role_slug="developer"
    )
    assert dev is not None
    assert "Global rule" in dev
    assert "Developer gate" in dev
    assert "Run lint before PR." in dev
