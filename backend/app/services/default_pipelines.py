"""Default-pipeline seeding for the WOW-onboarding flow.

The pilot ships five baked-in pipelines. Four of them map 1:1 onto
starter workflow YAMLs in
:mod:`backend.app.services.starter_workflows` (the ``.github/workflows/``
templates the Pipeline install flow commits into customer repos); the
fifth kind (``code_map``) is resolver-only and has no YAML.

Day-4 Phase-2 introduced the **catalog preset** layer — one of
``web-app`` / ``api-backend`` / ``mobile-app`` / ``cli`` / ``monorepo``
/ ``adoption-minimum`` — which only decides which of these lanes ship
*enabled*. Every lane is still materialised so the dashboard's
"Pipelines" page can show the full catalogue with a clear
"disabled — flip me on" CTA.

The seeding stays idempotent on ``(workspace_id, kind)`` so
re-activating a repo (or running the migration on existing tenants) is
safe to call repeatedly.
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
    # Config-v2 lane trigger. ``lanes_sync`` parses ``.ship/config.yml``
    # as a mapping where every lane declares exactly one of
    # ``once`` / ``event`` / ``schedule`` — this is the payload the
    # seed bundle emits per kind. ``None`` means "don't surface this
    # kind as a lane" (e.g. ``code_map``, which is resolver-only and
    # has no YAML). Extra keys (``pattern`` / ``idempotency_key``)
    # ride along in the mapping so the CLI sees the same config the
    # console does.
    lane_trigger: dict[str, str] | None = None


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
        # Triggered on every PR open/update — the starter workflow's
        # ``workflow_dispatch`` path is for dashboard "Run now"; the
        # reviewer lane semantically fires on ``pull_request``.
        lane_trigger={
            "event": "pull_request",
            "pattern": "**",
            "idempotency_key": "{{pr}}",
        },
    ),
    DefaultPipelineSpec(
        kind="daily_standup",
        name="Daily standup",
        workflow_id="scheduled-sdlc-lane",
        lane_trigger={"schedule": "0 9 * * 1-5"},
    ),
    DefaultPipelineSpec(
        kind="code_map",
        # No catalog artifact today — the resolver lives at
        # ``GET /v1/workspaces/{ws}/repos/{id}/code-map`` and is
        # invoked directly by the dashboard "refresh" button.
        name="Code map refresh",
        workflow_id="code-map-refresh",
        # ``code_map`` is resolver-only with no YAML and no cron —
        # leave it out of ``.ship/config.yml`` so ``lanes_sync``
        # doesn't trip over a triggerless lane.
        lane_trigger=None,
    ),
    DefaultPipelineSpec(
        kind="tech_debt",
        name="Tech-debt scan",
        workflow_id="parallel-audit-lanes",
        lane_trigger={"schedule": "0 6 * * 1"},
    ),
    DefaultPipelineSpec(
        kind="self_heal",
        name="Pipeline self-heal",
        workflow_id="pipeline-self-heal",
        # Fallback off unless the preset explicitly opts in.
        enabled=False,
        lane_trigger={"schedule": "0 4 * * *"},
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
    "marketing",
    "adoption-minimum",
)

# ``adoption-minimum`` is deliberately tiny — the WOW flow promise is
# "sign up and Ship reviews your next PR"; anything beyond that needs
# the tenant to flip it on. ``web-app`` is the flagship "Elmundi-grade
# SDLC" preset: the whole grid on, including self-heal, so a brand-new
# tenant starts with the same lane coverage the reference implementation
# runs in production. ``monorepo`` matches ``web-app`` (self-heal is
# most useful there anyway). ``api-backend`` / ``mobile-app`` share the
# "operational baseline" (PR gate + standup + audit) without self-heal,
# because service / device-lab repos generally have fewer workflows to
# babysit — presets that need more add them later via the catalog's
# "available workflows" dashboard, not here.
PRESET_ENABLED_KINDS: Final[dict[str, frozenset[str]]] = {
    "web-app": frozenset(
        {"pr_review", "daily_standup", "tech_debt", "self_heal", "code_map"}
    ),
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
    # Marketing sites: copy-and-cadence flavour — PR gate for copy
    # reviews, standup to track publishing cadence, code_map so Ship
    # can answer "where does this claim live?". Tech-debt and
    # self-heal are intentionally off; see
    # ``artifacts/collections/preset-marketing/ARTIFACT.md``.
    "marketing": frozenset({"pr_review", "daily_standup", "code_map"}),
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
