"""Default-pipeline seeding for the WOW-onboarding flow (pilot Day 3).

The pilot ships five baked-in pipelines. They map 1:1 onto entries in
the workflow catalog under ``artifacts/workflows/`` (workflow IDs are
the slugs the wizard's "Install workflows" step already understands),
plus one pipeline kind that has no catalog backing yet (``code_map``)
because it's pure infra.

The list is intentionally small and hard-coded — for the pilot we
treat "5 lanes" as the product offering. When a tenant wants to add a
sixth pipeline post-pilot, they'll either install another workflow
artifact + use the (Day-4) "create custom pipeline" form, or we add
a new entry here and run a backfill migration.

Idempotent on ``(workspace_id, kind)`` so re-activating a repo (or
running the migration on existing tenants) is safe to call repeatedly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.pipelines import Pipeline


@dataclass(frozen=True, slots=True)
class DefaultPipelineSpec:
    """Static spec for one of the five baked-in pipelines."""

    kind: str
    name: str
    workflow_id: str
    enabled: bool = True


# Order matters — the dashboard renders cards in this order so the
# user's eye lands on PR review first (the demo-most card) and on the
# self-heal card last (the "advanced" one).
DEFAULT_PIPELINES: tuple[DefaultPipelineSpec, ...] = (
    DefaultPipelineSpec(
        kind="pr_review",
        name="PR review",
        workflow_id="pr-and-ci-gate",
    ),
    DefaultPipelineSpec(
        kind="daily_standup",
        name="Daily standup",
        workflow_id="scheduled-sdlc-lane",
    ),
    DefaultPipelineSpec(
        kind="code_map",
        # No catalog artifact today — the resolver lives at
        # ``GET /v1/workspaces/{ws}/repos/{id}/code-map`` and is
        # invoked directly by the dashboard "refresh" button.
        name="Code map refresh",
        workflow_id="code-map-refresh",
    ),
    DefaultPipelineSpec(
        kind="tech_debt",
        name="Tech-debt scan",
        workflow_id="parallel-audit-lanes",
    ),
    DefaultPipelineSpec(
        kind="self_heal",
        name="Pipeline self-heal",
        workflow_id="pipeline-self-heal",
        # Default off — tenants opt in once they trust the agent to
        # touch their CI; opt-in matches the catalog's framing.
        enabled=False,
    ),
)


async def seed_default_pipelines(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    specs: Iterable[DefaultPipelineSpec] = DEFAULT_PIPELINES,
    default_repo_id: uuid.UUID | None = None,
) -> list[Pipeline]:
    """Insert any of ``specs`` that don't already exist for the workspace.

    Returns the *full* current set of pipelines for the workspace
    (existing + newly inserted) so callers can audit what changed
    without a second SELECT.

    The function is intentionally *additive only*: we never disable or
    rename rows the user may have customised. If a tenant turned off
    PR review, re-activating a repo won't re-enable it.

    When ``default_repo_id`` is provided, newly seeded pipelines are
    bound to that repo via ``Pipeline.repo_id`` so the Day-4
    dispatcher can resolve the workflow file without prompting the
    user. Pre-existing rows that still lack a binding are *also*
    backfilled to the same repo so re-activating a repo upgrades
    legacy stub pipelines without forcing a manual remap.
    """
    existing_stmt = select(Pipeline).where(Pipeline.workspace_id == workspace_id)
    existing_rows = (await session.execute(existing_stmt)).scalars().all()
    existing_kinds: set[str] = {row.kind for row in existing_rows}

    if default_repo_id is not None:
        for row in existing_rows:
            if row.repo_id is None:
                row.repo_id = default_repo_id

    new_rows: list[Pipeline] = []
    for spec in specs:
        if spec.kind in existing_kinds:
            continue
        row = Pipeline(
            workspace_id=workspace_id,
            kind=spec.kind,
            name=spec.name,
            workflow_id=spec.workflow_id,
            enabled=spec.enabled,
            config={},
            repo_id=default_repo_id,
        )
        session.add(row)
        new_rows.append(row)

    if new_rows or default_repo_id is not None:
        await session.flush()

    return [*existing_rows, *new_rows]


__all__ = ["DEFAULT_PIPELINES", "DefaultPipelineSpec", "seed_default_pipelines"]
