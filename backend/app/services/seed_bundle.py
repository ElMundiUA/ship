"""Unified wizard seed bundle (Wizard v2 iter 5).

Composes every file the Wizard v2 seed PR drops into a repo, so the
operator reviews + merges exactly **one** PR to get Ship wired:

* ``.github/workflows/ship-<lane>.yml`` — thin wrappers per enabled
  lane that delegate to the reusable ``ElMundiUA/ship/.github/workflows/run-agent.yml``.
  No shipctl version is copied inline; the reusable workflow installs
  ``@elmundi/ship-cli`` on the runner at ``latest`` dist-tag.
* ``.ship/config.yml`` — preset label + v2 ``lanes:`` block that the
  thin wrappers' lane ids resolve against.
* ``.ship/knowledge/*.md`` — optional starter buckets the operator
  ticked on the wizard (code-style / ui-runbook).
* ``.ship/tracker-fsm.md`` — the finite-state-machine doc Ship's agents
  consume when moving tickets across tracker statuses (iter 7 will add
  a console renderer for the same file).

The composer is pure: it takes plain inputs (preset id, knowledge
slugs, tracker kind) and returns ``(path, content)`` tuples. The HTTP
route in :mod:`backend.app.api.v1.routes.repos` wraps this with the
side-effecting bits (GitHub PR, Actions-secret mint for SHIP_RUN_TOKEN,
audit logs) so tests can exercise composition without touching the
network.

Invariants the caller relies on:

- Paths come back unique (tests assert it). Two presets declaring the
  same workflow get merged here; ``.ship/config.yml`` is rebuilt from
  the merged lane set so there's never duplicate keys.
- File list is stable-ordered: ``.github/workflows`` first, then
  ``.ship/config.yml``, then ``.ship/knowledge/*.md``, then the FSM.
  Keeps PR review diffs readable across re-runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from backend.app.services import catalog as catalog_service
from backend.app.services.tracker_fsm import (
    FSM_INSTALL_PATH,
    render_tracker_fsm,
)


# Bumping this marks every currently-seeded repo as "out of date"
# on the dashboard — drives the "Update available → Open wizard"
# CTA on the repo card. Bump when any of the following changes in
# a way that warrants re-seeding existing tenants:
#
# - a starter workflow YAML (trigger cadence, new step, pinned CLI
#   version drops the ``@latest`` resilience net, etc.)
# - ``.ship/config.yml`` schema or per-preset lane defaults
# - ``.ship/tracker-fsm.md`` material
# - knowledge starter content in ``catalog.knowledge_starter_files``
#
# Do NOT bump for changes that only affect freshly-seeded repos
# (e.g. adding a new preset) — existing repos are unaffected.
#
# ``1`` → baseline Wizard-v2 iter-5 bundle (config-schema v1 lanes
#         list, CLI pinned by exact semver)
# ``2`` → config-schema v2 ``lanes:`` mapping + CLI ``@latest``
BUNDLE_VERSION: int = 2


@dataclass(frozen=True, slots=True)
class SeedBundle:
    """Result of :func:`compose_seed_files`.

    ``files`` is the flat ``(path, content)`` list to hand to
    :func:`commit_bundle_pr`. The rest are metadata the route uses
    to populate the audit log and the response body.
    """

    files: list[tuple[str, str]] = field(default_factory=list)
    presets: list[str] = field(default_factory=list)
    knowledge_slugs: list[str] = field(default_factory=list)
    tracker_kind: str | None = None
    # True iff the caller asked to include the FSM file. Broken out
    # of ``tracker_kind`` so we can still seed "no tracker yet" docs
    # when the wizard skipped the tracker step.
    includes_fsm: bool = False


def compose_seed_files(
    *,
    presets: list[str],
    knowledge_slugs: list[str] | None,
    tracker_kind: str | None,
    workspace_default_tracker_kind: str | None = None,
    include_fsm: bool = True,
    repo_full_name: str | None = None,
) -> SeedBundle:
    """Build the unified wizard seed bundle.

    Arguments mirror the wizard's step-5 save payload:

    * ``presets`` — non-empty preset id list, already validated against
      :data:`catalog_service.KNOWN_PRESETS` at the HTTP layer.
    * ``knowledge_slugs`` — subset of :data:`catalog_service.KNOWLEDGE_STARTERS`
      to seed. ``None`` means "seed everything" (matches the
      existing ``knowledge_seed`` endpoint default). An empty list
      means "skip knowledge files entirely".
    * ``tracker_kind`` — ``linear`` / ``github`` / ``jira`` / ``None``.
      Only drives the FSM markdown header; no inline secrets.
    * ``workspace_default_tracker_kind`` — surfaced in the FSM doc
      when the repo overrides the workspace default.
    * ``include_fsm`` — on by default. Set to ``False`` to skip the
      FSM file entirely (internal-only; wizard always seeds it).
    * ``repo_full_name`` — ``owner/name``; echoed into
      ``.ship/config.yml`` and the FSM header. Optional.
    """

    if not presets:
        raise ValueError("compose_seed_files requires at least one preset")

    # ── Preset workflows + baseline .ship/config.yml ──────────────
    # Reuse the existing catalog surface so Wizard v1 installs and
    # Wizard v2 installs emit byte-identical files for the same
    # preset picks (operators comparing old/new PRs get clean diffs).
    seen_paths: set[str] = set()
    files: list[tuple[str, str]] = []
    for pid in presets:
        for path, content in catalog_service.preset_bundle_files(
            pid, repo_full_name=repo_full_name
        ):
            # ``.ship/config.yml`` is rewritten per preset; keep the
            # last one so the final file mentions every preset we
            # merged (the catalog helper already does this for each
            # individual call).
            if path == ".ship/config.yml":
                files = [(p, c) for (p, c) in files if p != ".ship/config.yml"]
                seen_paths.discard(path)
            if path in seen_paths:
                continue
            files.append((path, content))
            seen_paths.add(path)

    # ── Knowledge starters ────────────────────────────────────────
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
        knowledge_files = catalog_service.knowledge_starter_files(knowledge_slugs)
        resolved_knowledge = [
            p.removeprefix(".ship/knowledge/").removesuffix(".md")
            for p, _ in knowledge_files
        ]
    for path, content in knowledge_files:
        if path in seen_paths:
            # Preset bundles don't ship knowledge files today, but
            # guard anyway so future preset churn can't silently
            # clobber a starter.
            continue
        files.append((path, content))
        seen_paths.add(path)

    # ── Tracker FSM ───────────────────────────────────────────────
    if include_fsm:
        fsm_body = render_tracker_fsm(
            tracker_kind,
            workspace_default_kind=workspace_default_tracker_kind,
            repo_full_name=repo_full_name,
        )
        if FSM_INSTALL_PATH not in seen_paths:
            files.append((FSM_INSTALL_PATH, fsm_body))
            seen_paths.add(FSM_INSTALL_PATH)

    # Stable sort for readable PR diffs: workflows first, .ship next,
    # FSM last. We sort after composition so the above insertion
    # order only matters for dedupe.
    def _rank(path: str) -> tuple[int, str]:
        if path.startswith(".github/workflows/"):
            return (0, path)
        if path == ".ship/config.yml":
            return (1, path)
        if path.startswith(".ship/knowledge/"):
            return (2, path)
        if path == FSM_INSTALL_PATH:
            return (3, path)
        return (9, path)

    files.sort(key=lambda item: _rank(item[0]))

    return SeedBundle(
        files=files,
        presets=list(presets),
        knowledge_slugs=resolved_knowledge,
        tracker_kind=tracker_kind,
        includes_fsm=include_fsm,
    )


def _uniq(seq: Iterable[str]) -> list[str]:
    """Preserve-order uniquifier used in tests + the route."""

    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


__all__ = [
    "SeedBundle",
    "compose_seed_files",
]
