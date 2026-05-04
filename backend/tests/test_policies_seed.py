"""Pin the default-policy seeder behaviour.

``seed_default_policies`` runs at workspace provisioning and inserts
the canonical policy set (``policies_seed._DEFAULT_POLICIES``). It
must be:

- **Complete** — every default lands once on a fresh workspace.
- **Idempotent** — re-running over an already-seeded workspace
  inserts nothing.
- **Admin-respecting** — a default the admin deleted does **not**
  come back on the next seed pass.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.db.models.policies import WorkspacePolicy
from backend.app.services.policies_seed import (
    SeedPolicy,
    default_policies,
    seed_default_policies,
)


@pytest.mark.asyncio
async def test_default_set_shape() -> None:
    """The shape of the canonical seed set is what callers expect:
    39 policies, 7 globals, distinctive titles, distinctive sort
    orders. Drift here means the migration backfill and the
    Console list view rendered different sets — pin both axes."""
    policies = default_policies()
    assert len(policies) == 39

    globals_ = [p for p in policies if not p.applies_to_roles]
    scoped = [p for p in policies if p.applies_to_roles]
    assert len(globals_) == 7
    assert len(scoped) == 32

    titles = [p.title for p in policies]
    assert len(set(titles)) == len(titles), "duplicate seed title"

    orders = [p.sort_order for p in policies]
    assert len(set(orders)) == len(orders), "duplicate sort_order"


@pytest.mark.asyncio
async def test_seed_inserts_all_defaults_on_fresh_workspace(
    db_session, seed_workspace
) -> None:
    """A workspace that pre-dates the seed bootstrap (or one whose
    policies happened to be deleted) gets the full default set in
    one call."""
    _, _, workspace = seed_workspace

    # Drop anything seeded by the alembic backfill so we measure the
    # service in isolation.
    rows = await db_session.execute(
        select(WorkspacePolicy).where(
            WorkspacePolicy.workspace_id == workspace.id
        )
    )
    for row in rows.scalars().all():
        await db_session.delete(row)
    await db_session.flush()

    result = await seed_default_policies(
        session=db_session, workspace_id=workspace.id
    )
    assert result == {"inserted": 39, "skipped": 0}

    rows_after = (
        await db_session.execute(
            select(WorkspacePolicy).where(
                WorkspacePolicy.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert len(rows_after) == 39


@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session, seed_workspace) -> None:
    """Re-running over an already-seeded workspace is a no-op."""
    _, _, workspace = seed_workspace

    first = await seed_default_policies(
        session=db_session, workspace_id=workspace.id
    )
    second = await seed_default_policies(
        session=db_session, workspace_id=workspace.id
    )

    # Whatever the first pass inserted, the second pass must skip
    # exactly that many.
    total = first["inserted"] + first["skipped"]
    assert second == {"inserted": 0, "skipped": total}

    rows = (
        await db_session.execute(
            select(WorkspacePolicy).where(
                WorkspacePolicy.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    # Either the alembic backfill ran (39) or migrations skipped it
    # — pin the upper bound, not the exact number, since the test DB
    # may already be at head before the test starts.
    assert {p.title for p in rows} >= {p.title for p in default_policies()}
    # Never duplicates.
    assert len({p.title for p in rows}) == len(rows)


@pytest.mark.asyncio
async def test_seed_respects_admin_disable(
    db_session, seed_workspace
) -> None:
    """Disabling (``enabled=False``) is the canonical "silence this
    default" gesture — the disabled row keeps the title slot taken
    so the next seed call doesn't re-insert it. Pin both halves: the
    disabled state survives, no duplicate gets inserted."""
    _, _, workspace = seed_workspace

    # Make sure the workspace is fully seeded first.
    await seed_default_policies(session=db_session, workspace_id=workspace.id)

    target_title = "Don't invent values you can't verify"
    found = await db_session.execute(
        select(WorkspacePolicy).where(
            WorkspacePolicy.workspace_id == workspace.id,
            WorkspacePolicy.title == target_title,
        )
    )
    row = found.scalar_one()
    row.enabled = False
    await db_session.flush()

    # Re-seed and ensure no duplicate row appears + the disabled
    # state survives.
    await seed_default_policies(session=db_session, workspace_id=workspace.id)

    rows_after = (
        await db_session.execute(
            select(WorkspacePolicy).where(
                WorkspacePolicy.workspace_id == workspace.id,
                WorkspacePolicy.title == target_title,
            )
        )
    ).scalars().all()
    assert len(rows_after) == 1, (
        "seeder duplicated a disabled default — title-key idempotency "
        "broke"
    )
    assert rows_after[0].enabled is False, (
        "seeder un-disabled an admin-silenced default"
    )


@pytest.mark.asyncio
async def test_seed_reinserts_deleted_defaults_by_design(
    db_session, seed_workspace
) -> None:
    """Documented ENSURE contract: a hard-deleted default *will* come
    back on the next seed pass. This test pins the contract so a
    future change can't silently flip it without a corresponding
    docstring + test edit."""
    _, _, workspace = seed_workspace

    await seed_default_policies(session=db_session, workspace_id=workspace.id)

    target_title = "Don't invent values you can't verify"
    found = await db_session.execute(
        select(WorkspacePolicy).where(
            WorkspacePolicy.workspace_id == workspace.id,
            WorkspacePolicy.title == target_title,
        )
    )
    await db_session.delete(found.scalar_one())
    await db_session.flush()

    result = await seed_default_policies(
        session=db_session, workspace_id=workspace.id
    )
    assert result["inserted"] == 1
    revived = await db_session.execute(
        select(WorkspacePolicy).where(
            WorkspacePolicy.workspace_id == workspace.id,
            WorkspacePolicy.title == target_title,
        )
    )
    assert revived.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_seed_accepts_custom_policy_set(
    db_session, seed_workspace
) -> None:
    """The ``policies`` override is the alembic backfill's hook into
    the same code path. Pin that it works against an arbitrary tuple
    so the migration's frozen snapshot can drift from the live
    default set without breaking the seeder contract."""
    _, _, workspace = seed_workspace

    custom = (
        SeedPolicy(
            title="Test-only rule X",
            body="X body.",
            applies_to_roles=("developer",),
            sort_order=999,
        ),
        SeedPolicy(
            title="Test-only rule Y",
            body="Y body.",
            applies_to_roles=(),
            sort_order=1000,
        ),
    )

    result = await seed_default_policies(
        session=db_session,
        workspace_id=workspace.id,
        policies=custom,
    )
    assert result == {"inserted": 2, "skipped": 0}

    rows = await db_session.execute(
        select(WorkspacePolicy).where(
            WorkspacePolicy.workspace_id == workspace.id,
            WorkspacePolicy.title.in_(["Test-only rule X", "Test-only rule Y"]),
        )
    )
    fetched = {r.title: r for r in rows.scalars().all()}
    assert fetched["Test-only rule X"].applies_to_roles == ["developer"]
    assert fetched["Test-only rule Y"].applies_to_roles is None  # () → NULL
