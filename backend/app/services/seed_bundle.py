"""Unified wizard seed bundle (Plays/Inbox redesign — P5-05).

Composes every file the Wave-8 wizard's seed PR drops into a repo so the
operator reviews + merges exactly **one** PR to get Ship wired:

* ``.ship/config.yml`` — single rendered YAML covering every routine in
  the canonical :data:`~backend.app.services.lane_recipes.DEFAULT_BUNDLE`.
  Rendered as ``process.routines`` so shipctl owns local scheduling and
  Ship only claims schedule windows.
* ``.github/workflows/ship-trigger-schedule.yml`` — the **only**
  workflow file the seed installs. Cron + ``workflow_dispatch``.
  Per the closed-beta architecture lock (E03 walk plan §"Customer
  repo gets exactly ONE workflow file"), no event-triggered second
  file ships in the bundle: knowledge ingestion, repo analysis,
  and self-healing all run server-side or as scheduled routines
  on the same cron, not as separate ``on: push`` workflows.
* ``.ship/state/wizard-seed.v2.json`` — idempotency marker that
  records the bundle hash + knowledge versions so the wizard can
  detect drift on re-run without re-walking the catalog.
* ``.ship/tracker-fsm.md`` — gated by ``include_fsm`` + the resolved
  ``tracker_kind``; renders the finite-state-machine doc the agent
  consults when transitioning tracker tickets.

The composer is pure: it takes plain inputs (bundle, knowledge slugs,
tracker kind) and returns ``(path, content)`` tuples. The route layer
in :mod:`backend.app.api.v1.routes.repos` wraps this with GitHub PR,
``SHIP_RUN_TOKEN`` mint, CI secrets, and audit logs. Repo analysis and
knowledge ingestion run server-side after the seed PR merges.

Invariants the caller relies on:

- Paths come back unique. Two patterns that share a starter workflow
  emit it once.
- File list is stable-ordered: workflows first, then ``.ship/config.yml``,
  compatibility knowledge if explicitly requested, the v2 marker, the FSM.
  Keeps PR review diffs readable across re-runs.
- :func:`compose_seed_files` is idempotent: same ``(bundle,
  knowledge_slugs, tracker_kind)`` → byte-identical output (modulo
  the ISO timestamp embedded in the v2 marker / repo-intel placeholder
  when the caller doesn't pin ``seeded_at``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.app.services import catalog as catalog_service
from backend.app.services.catalog import KNOWLEDGE_STARTERS
from backend.app.services.lane_recipes import DEFAULT_BUNDLE
from backend.app.services.lane_recipes import default_seed_lanes
from backend.app.services.tracker_fsm import (
    FSM_INSTALL_PATH,
    render_tracker_fsm,
)


# Bumping this marks every currently-seeded repo as "out of date" on
# the dashboard — drives the "Update available → Open wizard" CTA on
# the repo card. Bump when any of the following changes in a way that
# warrants re-seeding existing tenants:
#
# - a starter workflow YAML (trigger cadence, new step, pinned CLI
#   version drops the ``@latest`` resilience net, etc.)
# - ``.ship/config.yml`` schema or canonical bundle composition
# - ``.ship/tracker-fsm.md`` material
# - bootstrap workflow or compatibility knowledge starter content
# - the ``.ship/state/wizard-seed.v2.json`` marker shape
#
# Do NOT bump for additive changes that only affect freshly-seeded
# repos (e.g. tightening the bundle picker copy).
#
# ``1`` → baseline Wizard-v2 iter-5 bundle (config-schema v1 lanes
#         list, CLI pinned by exact semver)
# ``2`` → config-schema v2 ``lanes:`` mapping + CLI ``@latest``
# ``3`` → Phase 3 "Requests" — bundle shipped ``adhoc-agent-run.yml``
# ``4`` → P5-05 collapse: bundle-based composer, repo-intel placeholder,
#         ``.ship/state/wizard-seed.v2.json`` idempotency marker.
# ``5`` → post-merge knowledge bootstrap workflow; PR 1 is infra-only.
# ``0.6`` → process.routines runtime: generated config drops top-level
#         ``lanes:`` and schedule workflow uses local due calculation.
# ``0.7`` → seed_default_knowledge: workspace gets a `product-knowledge`
#         bucket + a starter article on first repo activation.
# ``0.8`` → seed installs exactly one workflow file
#         (``ship-trigger-schedule.yml``); legacy ``ship-bootstrap.yml``
#         push-event workflow dropped — knowledge ingestion moved
#         server-side. ``seed_default_knowledge`` finally wired into the
#         JIT + explicit workspace-create paths (was dead code through
#         0.7).
# ``0.9`` → daily review crons spread every 3h across 24h UTC instead
#         of stacking at 08:00 / 09:00 / 17:00. Order: security 06:00,
#         digest 09:00, tech-debt 12:00, test-coverage 15:00, retro
#         18:00.
# ``0.10`` → Agent exit protocol moved from committing
#         ``.ship/run-state.json`` on a branch to a
#         ``POST /v1/workspaces/{ws}/agent-runs/finish`` HTTP call from
#         inside the agent. Branchless agents (intake, BA, planner)
#         no longer need to push anything. ``common-base`` pattern
#         body rewritten to match. Linear provisioner now creates a
#         ``needs:clarification`` signal label and ``LinearTracker``
#         excludes tickets carrying it from FSM picks.
# ``0.11`` → ``shipctl agent-run`` collapsed into ``shipctl run``: a
#         single command both prints (``--dry-run``) and launches.
#         ``agent-run`` stays as a dispatch alias so already-seeded
#         trigger workflows keep working.
# ``0.12`` → "no work" is no longer a blocker. ``ready_next_step``
#         now accepts a null ``ticket_ref`` as a tracker no-op
#         (audit-only, no inbox row) so context-free routines
#         (daily audits without findings, FSM stages with empty
#         queues, reviews with nothing to review) finish silently
#         instead of being forced into ``blocked``. Exit-protocol
#         in ``run.mjs`` rewrites ``blocked`` to mean ONLY a broken
#         environment (missing secret / dead adapter / conflicting
#         branch). Inbox blocker noise should drop near-zero.
# ``0.13`` → Inbox clarifications/blockers now snapshot the source
#         ticket (title/description/labels/state/url) into the
#         payload at finish time, so the operator can read the
#         original ask without flipping to Linear. New
#         ``LinearTracker.get_ticket_snapshot`` + best-effort
#         hook in ``finish_agent_run``; missing snapshot is
#         tolerated for older rows / non-Linear adapters.
# ``0.14`` → Phase 2.4 Step C-2: seeded routines now emit
#         ``specialist: <slug>`` instead of ``pattern: role-<slug>``.
#         ``shipctl run`` resolves the slug through the new
#         ``GET /v1/.../agent-roles/{slug}/resolve`` endpoint
#         (workspace override → Ship default file). Three legacy
#         routine-prompt patterns (flow-daily-retro,
#         flow-learning-capture, op-workflow-self-heal) moved into
#         ``backend/app/resources/agent_roles/`` as ``daily-retro``,
#         ``learning-capture``, and ``workflow-self-heal``.
#         ``cli/lib/runtime/routines.mjs:pickSpecialistSlug`` keeps
#         legacy ``pattern: role-X`` configs working until repos
#         re-seed onto v0.14.
# ``0.15`` → Phase 2.5: agent rule files (CLAUDE.md, AGENTS.md,
#         .cursor/rules/ship-artifacts-protocol.mdc, …) baked
#         straight into the seed PR. The wizard's selected
#         ``stack.agents`` slugs drive the include list; bodies
#         live under ``backend/app/resources/agent_rule_files/``.
#         Replaces the legacy ``shipctl init --copy-rules`` path
#         that pulled bodies through ``GET /collections`` after
#         the seed PR merged. The ``shipctl init`` / ``sync`` /
#         ``--copy-rules`` flow + the ``/collections`` + ``/fetch``
#         endpoints are gone in this bundle.
BUNDLE_VERSION: str = "0.27"

# Default knowledge starters for PR 1. Empty by design: generated knowledge is
# analyzed post-merge and proposed in a second PR. Historical callers can still
# pass explicit slugs for compatibility.
DEFAULT_KNOWLEDGE: tuple[str, ...] = ()


# Path constants. Pulled out so tests + downstream consumers
# (``synthetic_lane_sync``, codeowners audit) can reference one source
# of truth instead of hand-typed string literals.
CONFIG_PATH: str = ".ship/config.yml"
REPO_INTEL_PLACEHOLDER_PATH: str = ".ship/knowledge/repo-intel.md"
WIZARD_SEED_MARKER_PATH: str = ".ship/state/wizard-seed.v2.json"


# Body of the repo-intel placeholder. ``{seeded_at}`` is interpolated
# at compose time; everything else is fixed so the diff against a
# previous run is the timestamp line only.
_REPO_INTEL_TEMPLATE: str = (
    "# Repository Intel\n"
    "\n"
    "_This file is automatically populated by Ship's intel harvester._\n"
    "\n"
    "Status: harvesting. Triggered at {seeded_at}.\n"
    "\n"
    "When complete, this file will summarise the repo's languages,\n"
    "frameworks, entry points, structure, and commit-message conventions\n"
    "so that automated reviews don't have to re-scan the project on\n"
    "every run.\n"
    "\n"
    "Re-trigger from the workspace console (Repo → Refresh intel).\n"
)


@dataclass(frozen=True, slots=True)
class SeedBundle:
    """Result of :func:`compose_seed_files`.

    ``files`` is the flat ``(path, content)`` list to hand to
    :func:`commit_bundle_pr`. The rest are metadata the route uses
    to populate the audit log + the response body.
    """

    files: list[tuple[str, str]] = field(default_factory=list)
    bundle: tuple[str, ...] = field(default_factory=tuple)
    knowledge_slugs: list[str] = field(default_factory=list)
    tracker_kind: str | None = None
    # True iff the caller asked to include the FSM file. Broken out
    # of ``tracker_kind`` so we can still seed "no tracker yet" docs
    # when the wizard skipped the tracker step.
    includes_fsm: bool = False
    # SHA-256 prefix of the bundle, embedded in the v2 marker. Exposed
    # here so the route can audit-log the same value the marker file
    # carries (drift detection on re-run).
    bundle_hash: str = ""
    # The ISO-8601 timestamp baked into the v2 marker + repo-intel
    # placeholder. Useful for tests that want to assert on the body
    # without re-deriving the format.
    seeded_at_iso: str = ""

    # --- backward-compat shims ----------------------------------------
    # Pre-P5-05 callers (route, audit log payload) referenced
    # ``bundle.presets`` even though the bundle is now preset-free.
    # Surfacing the canonical ``["default"]`` keeps those payloads
    # stable until Wave 8c rips the field out of the FE contract.

    @property
    def presets(self) -> list[str]:
        """Compat shim: pre-P5-05 callers expect a list of preset ids.

        Always returns ``["default"]`` post-P5-01 because the wizard
        no longer branches on preset choice. Drop once Wave 8c lands
        the FE contract change that removes ``presets`` from
        ``WizardSeedOut``.
        """
        return ["default"]


def compose_seed_files(
    *,
    bundle: tuple[str, ...] = DEFAULT_BUNDLE,
    knowledge_slugs: tuple[str, ...] | list[str] | None = DEFAULT_KNOWLEDGE,
    tracker_kind: str | None = None,
    workspace_default_tracker_kind: str | None = None,
    include_fsm: bool = True,
    repo_intel_placeholder: bool = False,
    repo_full_name: str | None = None,
    seeded_at: datetime | None = None,
    ship_version: str | None = None,
    # ----- Phase 2.5 — agent rule files baked into the seed PR. -----
    # ``agents`` is a tuple of slugs from
    # :data:`backend.app.services.agent_rule_files.SUPPORTED_AGENTS`
    # (``cursor``, ``claude-md``, ``codex``, …). The seed bundle drops
    # the corresponding rule file at each agent's repo-relative
    # install path with a marker-fenced Ship-owned block. Empty / None
    # = no rule files in this seed.
    agents: tuple[str, ...] | list[str] | None = None,
    # ----- legacy kwargs (P5-05 deprecation; ignored, kept for ABI) -----
    presets: list[str] | None = None,  # noqa: ARG001 — see deprecation note
) -> SeedBundle:
    """Build the unified wizard seed bundle (P5-05 contract).

    Arguments:

    * ``bundle`` — canonical pattern-key tuple. Defaults to
      :data:`~backend.app.services.lane_recipes.DEFAULT_BUNDLE`. The
      route always passes ``DEFAULT_BUNDLE`` post-P5-05 collapse;
      tests inject smaller bundles to exercise edge cases.
    * ``knowledge_slugs`` — compatibility-only subset of
      :data:`catalog_service.KNOWLEDGE_STARTERS`. The wizard passes an
      empty list because analyzed knowledge is ingested server-side
      after merge.
    * ``tracker_kind`` — ``linear`` / ``github`` / ``jira`` / ``None``.
      Drives the FSM markdown header + the v2 marker; no inline
      secrets.
    * ``workspace_default_tracker_kind`` — surfaced in the FSM doc
      when the repo overrides the workspace default.
    * ``include_fsm`` — on by default. Set ``False`` to skip the
      FSM file entirely (internal-only; the wizard always seeds it).
    * ``repo_intel_placeholder`` — off by default. Historical callers can
      still opt in, but the wizard no longer drops a misleading intel stub
      into PR 1.
    * ``repo_full_name`` — ``owner/name``; echoed into
      ``.ship/config.yml`` and the FSM header. Optional.
    * ``seeded_at`` — pinned timestamp for tests / re-runs. Defaults
      to ``datetime.now(timezone.utc)``.
    * ``ship_version`` — recorded in the v2 marker. Defaults to the
      installed package version (best-effort) so the marker is
      diagnostic without the route having to plumb the version.
    * ``presets`` — **deprecated, ignored.** The pre-P5-01 14-preset
      menu collapsed to a single bundle; this kwarg stays in the
      signature so legacy in-flight callers (e.g. older test
      fixtures) don't 5xx the moment they pass it. New code should
      pass ``bundle=`` instead.
    """
    if not bundle:
        raise ValueError("compose_seed_files requires a non-empty bundle")

    # Lazy import keeps seed_bundle importable without pulling the
    # starter_workflows table on catalog-only paths.
    from backend.app.services import starter_workflows

    seeded_at = seeded_at or datetime.now(timezone.utc)
    seeded_iso = _isoformat(seeded_at)
    bundle_hash = _bundle_hash(bundle)

    # Normalise knowledge selection up-front so we can stamp the v2
    # marker without a second resolution pass.
    resolved_knowledge: list[str]
    if knowledge_slugs is None:
        knowledge_files = catalog_service.knowledge_starter_files(None)
        resolved_knowledge = [
            p.removeprefix(".ship/knowledge/").removesuffix(".md")
            for p, _ in knowledge_files
        ]
    elif len(knowledge_slugs) == 0:
        knowledge_files = []
        resolved_knowledge = []
    else:
        knowledge_files = catalog_service.knowledge_starter_files(
            list(knowledge_slugs)
        )
        resolved_knowledge = [
            p.removeprefix(".ship/knowledge/").removesuffix(".md")
            for p, _ in knowledge_files
        ]

    seen_paths: set[str] = set()
    files: list[tuple[str, str]] = []

    def _add(path: str, content: str) -> None:
        if path in seen_paths:
            return
        files.append((path, content))
        seen_paths.add(path)

    # ── .ship/config.yml ─────────────────────────────────────────
    # Single render against the *whole* bundle. Patterns that don't
    # contribute a routine (request-only templates, missing trigger)
    # silently fall out of the runtime mapping; they still belong in
    # the bundle accounting (v2 marker) so the wizard can show them.
    #
    # Decomposition routines (ELS-79) are merged into the same
    # ``process.routines`` map so customer-side ``shipctl run
    # --routine wbs`` resolves them like any SDLC routine. Each
    # decomposition routine carries an explicit ``fsm_stage`` that
    # overrides the role's default — that's how one role (``ba``)
    # serves both ``ba_requirements`` (SDLC) and ``wbs``
    # (decomposition) without per-process role clones.
    routine_entries = catalog_service.bundle_routine_entries(default_seed_lanes())
    decomp_routines = catalog_service.default_planning_process_config().get(
        "routines", {}
    )
    if isinstance(decomp_routines, dict):
        for routine_id, routine in decomp_routines.items():
            if routine_id not in routine_entries:
                routine_entries[routine_id] = dict(routine)
    config_yaml = catalog_service.emit_config_yaml(
        preset_id="default",
        repo_full_name=repo_full_name,
        lanes={},
        process=catalog_service.default_development_process_config(routines=routine_entries),
    )
    _add(CONFIG_PATH, config_yaml)

    # ── Trigger adapter ──────────────────────────────────────────
    # v5 makes GitHub Actions a thin runner. shipctl reads .ship/config.yml,
    # computes due routines locally, then asks Ship only to claim the window.
    # Per the closed-beta architecture lock, this is the **only** workflow
    # the seed bundle installs — knowledge ingestion (formerly the
    # ``ship-bootstrap.yml`` push-event workflow) runs server-side or as a
    # scheduled routine on the same cron, not as a separate event trigger.
    trigger_entry = starter_workflows.get("ship-trigger-schedule")
    if trigger_entry is not None:
        trigger_body = trigger_entry.read_yaml()
        if trigger_body:
            _add(trigger_entry.install_target, trigger_body)

    # ── Knowledge starters ───────────────────────────────────────
    for path, content in knowledge_files:
        _add(path, content)

    # ── Agent rule files (Phase 2.5) ─────────────────────────────
    # Drop one Markdown rule file per selected agent at the agent's
    # repo-relative install path (CLAUDE.md, AGENTS.md, .cursor/...).
    # Replaces the legacy ``shipctl init --copy-rules`` flow that
    # had the customer's CLI tug the bodies through ``/collections``
    # + ``/fetch`` after the seed PR merged. Now the seed PR itself
    # contains the rule files so adoption is one merge instead of
    # two — and the bodies live under
    # ``backend/app/resources/agent_rule_files/<slug>.md`` instead
    # of ``artifacts/collections/agent-rules-<slug>/``.
    if agents:
        from backend.app.services import agent_rule_files

        for path, content in agent_rule_files.render_rule_files(list(agents)):
            _add(path, content)

    # ── Repo-intel placeholder (gated) ───────────────────────────
    # Lives under ``.ship/knowledge/`` so the knowledge_lister sees
    # it on day one; the harvest job overwrites it once the real
    # summary is ready.
    if repo_intel_placeholder:
        _add(
            REPO_INTEL_PLACEHOLDER_PATH,
            _REPO_INTEL_TEMPLATE.format(seeded_at=seeded_iso),
        )

    # ── Tracker FSM (gated) ──────────────────────────────────────
    if include_fsm:
        fsm_body = render_tracker_fsm(
            tracker_kind,
            workspace_default_kind=workspace_default_tracker_kind,
            repo_full_name=repo_full_name,
        )
        _add(FSM_INSTALL_PATH, fsm_body)

    # ── v2 idempotency marker ────────────────────────────────────
    # Last so the marker reflects the actual content set composed
    # above. Any future addition (e.g. a per-pattern checksum) just
    # needs to land here and the dashboard will pick up "drift" on
    # the next render.
    marker_body = _render_v2_marker(
        bundle=bundle,
        bundle_hash=bundle_hash,
        knowledge=resolved_knowledge,
        tracker_kind=tracker_kind,
        seeded_at_iso=seeded_iso,
        ship_version=ship_version or _detect_ship_version(),
    )
    _add(WIZARD_SEED_MARKER_PATH, marker_body)

    files.sort(key=lambda item: _rank(item[0]))

    return SeedBundle(
        files=files,
        bundle=tuple(bundle),
        knowledge_slugs=resolved_knowledge,
        tracker_kind=tracker_kind,
        includes_fsm=include_fsm,
        bundle_hash=bundle_hash,
        seeded_at_iso=seeded_iso,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rank(path: str) -> tuple[int, str]:
    """Sort key giving reviewers a stable PR-diff order."""
    if path.startswith(".github/workflows/"):
        return (0, path)
    if path == CONFIG_PATH:
        return (1, path)
    if path.startswith(".ship/knowledge/"):
        return (2, path)
    if path == WIZARD_SEED_MARKER_PATH:
        return (3, path)
    if path == FSM_INSTALL_PATH:
        return (4, path)
    return (9, path)


def _bundle_hash(bundle: Iterable[str]) -> str:
    """Stable 12-char prefix of ``sha256(",".join(sorted(bundle)))``.

    Sorted so reordering the bundle (recommended-display vs
    alphabetical) doesn't churn the hash. 12 chars is enough surface
    for human-eyeball drift detection while staying short enough to
    embed in dashboard tooltips and audit log payloads.
    """
    canonical = ",".join(sorted(bundle)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:12]


def _isoformat(dt: datetime) -> str:
    """Stable ISO-8601 (UTC, ``...Z``) for the marker + placeholder."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _render_v2_marker(
    *,
    bundle: tuple[str, ...],
    bundle_hash: str,
    knowledge: list[str],
    tracker_kind: str | None,
    seeded_at_iso: str,
    ship_version: str,
) -> str:
    """Pretty-print the v2 marker as deterministic JSON.

    Sort keys / fixed indent so the file is byte-stable across
    runs with the same inputs (the wizard / dashboard diff against
    the prior version to detect drift; ``json.dumps`` defaults
    don't sort keys).
    """
    payload = {
        "bundle_version": bundle_hash,
        "bundle": list(bundle),
        "knowledge": list(knowledge),
        "tracker_kind": tracker_kind,
        "seeded_at": seeded_at_iso,
        "ship_version": ship_version,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _detect_ship_version() -> str:
    """Best-effort reader for the installed Ship backend version.

    Tries ``importlib.metadata`` for the ``ship-backend`` distribution;
    falls back to ``"unknown"`` so the marker stays well-formed
    even in dev installs that haven't been ``pip install``'d.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        for dist in ("ship-backend", "ship", "shipctl"):
            try:
                return version(dist)
            except PackageNotFoundError:
                continue
    except Exception:  # noqa: BLE001 — best-effort, never raise
        pass
    return "unknown"


async def seed_default_knowledge(
    *,
    session,
    workspace_id,
    workspace_name: str,
    repo_names: list[str] | None = None,
    tracker_provider: str | None = None,
) -> dict:
    """Seed a default knowledge bucket for a newly-created workspace.

    Creates one workspace-scoped ``KnowledgeBucket`` row with slug
    ``product-knowledge`` plus one ``BucketArticle`` describing what
    just happened during onboarding.

    Returns a summary dict with ``bucket_created`` (bool) and ``article_created``
    (bool) flags for audit logging.

    Idempotent: if a bucket with slug ``product-knowledge`` already exists
    for the workspace, this is a no-op.
    """
    from datetime import datetime, timezone
    from hashlib import sha256

    from backend.app.db.models.agent_memory import (
        BucketArticle,
        BucketArticleStatus,
        BucketScope,
        BucketSource,
        KnowledgeBucket,
    )
    from sqlalchemy import select

    # Check if default bucket exists
    stmt = select(KnowledgeBucket).where(
        KnowledgeBucket.workspace_id == workspace_id,
        KnowledgeBucket.slug == "product-knowledge",
    )
    existing = (await session.execute(stmt)).scalars().first()

    if existing is not None:
        return {"bucket_created": False, "article_created": False}

    # Create the default bucket
    bucket = KnowledgeBucket(
        workspace_id=workspace_id,
        slug="product-knowledge",
        name="Product knowledge",
        description="Default workspace knowledge bucket seeded on creation",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
    )
    session.add(bucket)
    await session.flush()

    # Generate article body describing the workspace setup
    repo_list = (
        "\n".join(f"  - {name}" for name in (repo_names or []))
        if repo_names
        else "  (No repos selected yet)"
    )
    tracker_note = (
        f"\n- **Issue Tracker**: {tracker_provider}"
        if tracker_provider
        else ""
    )

    article_body = (
        f"# How this workspace was set up\n\n"
        f"This workspace **{workspace_name}** was created on "
        f"{datetime.now(timezone.utc).isoformat(timespec='minutes')}Z.\n\n"
        f"## Repositories\n\n"
        f"The following repositories are now tracked by Ship:\n\n"
        f"{repo_list}\n\n"
        f"## Configuration\n\n"
        f"- **GitHub App**: Installed\n"
        f"{tracker_note}\n"
    )

    # Compute content SHA for idempotency
    content_sha = sha256(article_body.encode()).hexdigest()

    # Create the starter article
    article = BucketArticle(
        bucket_id=bucket.id,
        slug="workspace-setup",
        title="How this workspace was set up",
        body_md=article_body,
        content_sha=content_sha,
        version=1,
        status=BucketArticleStatus.PUBLISHED,
    )
    session.add(article)
    await session.flush()

    return {"bucket_created": True, "article_created": True}


__all__ = [
    "BUNDLE_VERSION",
    "CONFIG_PATH",
    "DEFAULT_KNOWLEDGE",
    "REPO_INTEL_PLACEHOLDER_PATH",
    "SeedBundle",
    "WIZARD_SEED_MARKER_PATH",
    "compose_seed_files",
    "seed_default_knowledge",
]
