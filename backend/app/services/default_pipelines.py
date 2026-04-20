"""Default-pipeline seeding for the WOW-onboarding flow.

The pilot ships five baked-in pipelines. They map 1:1 onto entries in
the workflow catalog under ``artifacts/workflows/`` (workflow IDs are
the slugs the wizard's "Install workflows" step already understands),
plus one pipeline kind that has no catalog backing yet (``code_map``)
because it's pure infra.

Day-4 Phase-2 ties the seeding to a **catalog preset** — one of
``web-app`` / ``api-backend`` / ``mobile-app`` / ``cli`` / ``monorepo``
/ ``adoption-minimum``. The preset only decides which of the five
lanes ship *enabled*; every lane is still materialised so the
dashboard's "Pipelines" page can show the full catalogue with a clear
"disabled — flip me on" CTA. That keeps the seeding idempotent
(re-activating a repo never clobbers a tenant override) and means the
preset is a hint about the default shape, not a gate on what's
possible later.

Idempotent on ``(workspace_id, kind)`` so re-activating a repo (or
running the migration on existing tenants) is safe to call repeatedly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final, Iterable

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
# self-heal card last (the "advanced" one). ``enabled`` on the spec is
# the fallback default; per-preset overrides in :data:`PRESET_ENABLED_KINDS`
# are authoritative when a preset is provided.
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
        # Fallback off unless the preset explicitly opts in.
        enabled=False,
    ),
)


# ---------------------------------------------------------------------------
# Preset → enabled-kinds mapping
# ---------------------------------------------------------------------------

# Known preset IDs. Kept in sync with the ``spec.preset_id`` values in
# ``artifacts/collections/preset-*/ARTIFACT.md``. Centralised so API
# validation + tests + console picker all read from the same list.
KNOWN_PRESETS: Final[tuple[str, ...]] = (
    "web-app",
    "api-backend",
    "mobile-app",
    "cli",
    "monorepo",
    "adoption-minimum",
)

# ``adoption-minimum`` is deliberately tiny — the WOW flow promise is
# "sign up and Ship reviews your next PR"; anything beyond that needs
# the tenant to flip it on. ``monorepo`` opts into self-heal because
# the drift-detection story is most useful on big repos with many
# workflows. Web-app / api-backend / mobile-app share the same
# "operational baseline" (PR gate + standup + audit) — presets that
# need more lanes (e.g. hosted E2E) add them later via the catalog's
# "available workflows" dashboard, not here.
PRESET_ENABLED_KINDS: Final[dict[str, frozenset[str]]] = {
    "web-app": frozenset({"pr_review", "daily_standup", "tech_debt", "code_map"}),
    "api-backend": frozenset(
        {"pr_review", "daily_standup", "tech_debt", "code_map"}
    ),
    "mobile-app": frozenset(
        {"pr_review", "daily_standup", "tech_debt", "code_map"}
    ),
    "cli": frozenset({"pr_review", "tech_debt", "code_map"}),
    "monorepo": frozenset(
        {"pr_review", "daily_standup", "tech_debt", "self_heal", "code_map"}
    ),
    "adoption-minimum": frozenset({"pr_review", "code_map"}),
}

# Fallback when neither a preset nor a seed override are provided —
# matches the Day-3 behaviour (everything on except self-heal).
_FALLBACK_ENABLED: Final[frozenset[str]] = frozenset(
    spec.kind for spec in DEFAULT_PIPELINES if spec.enabled
)


def resolve_enabled_kinds(preset: str | None) -> frozenset[str]:
    """Return the set of pipeline kinds a preset enables by default.

    Unknown or missing presets fall back to the Day-3 defaults so the
    seeding path never 500s on a stale preset label left over from a
    future schema bump.
    """
    if preset is None:
        return _FALLBACK_ENABLED
    return PRESET_ENABLED_KINDS.get(preset, _FALLBACK_ENABLED)


async def seed_default_pipelines(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    specs: Iterable[DefaultPipelineSpec] = DEFAULT_PIPELINES,
    default_repo_id: uuid.UUID | None = None,
    preset: str | None = None,
) -> list[Pipeline]:
    """Insert any of ``specs`` that don't already exist for the workspace.

    Returns the *full* current set of pipelines for the workspace
    (existing + newly inserted) so callers can audit what changed
    without a second SELECT.

    The function is intentionally *additive only*: we never disable or
    rename rows the user may have customised. If a tenant turned off
    PR review, re-activating a repo won't re-enable it.

    ``default_repo_id`` binds newly seeded pipelines (and backfills
    legacy stub rows that still lack a binding) so the Day-4
    dispatcher can resolve the workflow file without prompting the
    user.

    ``preset`` (Phase 2) is one of the catalog preset ids (e.g.
    ``web-app`` / ``api-backend`` / ``monorepo``). It decides which
    lanes ship *enabled* on first seeding — see
    :data:`PRESET_ENABLED_KINDS`. Unknown presets fall back to the
    Day-3 defaults; a ``NULL`` preset (legacy call site or
    ``adoption-minimum`` not supplied) also falls back.
    """
    existing_stmt = select(Pipeline).where(Pipeline.workspace_id == workspace_id)
    existing_rows = (await session.execute(existing_stmt)).scalars().all()
    existing_kinds: set[str] = {row.kind for row in existing_rows}

    if default_repo_id is not None:
        for row in existing_rows:
            if row.repo_id is None:
                row.repo_id = default_repo_id

    enabled_kinds = resolve_enabled_kinds(preset)
    new_rows: list[Pipeline] = []
    for spec in specs:
        if spec.kind in existing_kinds:
            continue
        enabled = spec.kind in enabled_kinds if preset else spec.enabled
        row = Pipeline(
            workspace_id=workspace_id,
            kind=spec.kind,
            name=spec.name,
            workflow_id=spec.workflow_id,
            enabled=enabled,
            config={},
            repo_id=default_repo_id,
        )
        session.add(row)
        new_rows.append(row)

    if new_rows or default_repo_id is not None:
        await session.flush()

    return [*existing_rows, *new_rows]


__all__ = [
    "DEFAULT_PIPELINES",
    "DefaultPipelineSpec",
    "KNOWN_PRESETS",
    "PRESET_ENABLED_KINDS",
    "resolve_enabled_kinds",
    "seed_default_pipelines",
]
