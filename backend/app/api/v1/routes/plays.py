"""Plays — workspace-level aggregation surface (RFC-0010 P4-00).

The Console's Coverage tab needs one row per Play (= pattern) with
"how many activated repos in this workspace currently configure this
play?" answers, sortable by criticality and gap size, filterable by
category. Per the planning doc §1 the v1 view is a "list with progress
bars + critical-uncovered red badge"; the matrix view is deferred to
v2 and out of scope here.

Mounted at ``/v1/workspaces/{workspace_id}/plays/*``. Auth mirrors the
``lanes`` router — workspace-member read access via
:func:`backend.app.api.v1.routes.workspaces._require_membership`.

Implementation shape:

- The catalog of Plays comes from :mod:`backend.app.services.catalog`
  (``list_patterns()``); this is the same source the
  ``/v1/catalog/patterns`` endpoint reads, so coverage rows stay in
  lockstep with the picker UIs without re-parsing ARTIFACT.md files
  directly.
- ``inbox.profile == 'silent'`` patterns (``common-*``, ``op-*``)
  are excluded — they are system-internal helpers that never appear
  as user-facing Plays.
- ``assignments_count`` derives from :class:`Lane` rows scoped to the
  workspace. A lane is considered to "configure" a play when its
  ``pattern`` column matches ``play_key`` *or* the play_key appears in
  the lane's ``config_blob['patterns']`` list (the RFC-0008 C3.1
  multi-pattern shape). Performance-wise the endpoint pulls every
  lane for the workspace once, then groups in Python — the catalog
  is bounded (≤80 plays) and a typical workspace ≤100 activated
  repos, so the scan stays well under the 200 ms budget without per-
  play SQL fan-out.
- ``activated_repos_total`` mirrors what
  ``GET /v1/workspaces/{ws}/repos`` returns: every
  :class:`WorkspaceRepo` row for the workspace. Activation is a hard
  binary in the current model (the row exists iff the user picked
  the repo in the activation modal), so we don't second-guess it
  with an extra ``activated_at IS NOT NULL`` filter.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.lanes import Lane
from backend.app.db.session import get_session
from backend.app.services import catalog as catalog_service
from backend.app.services.catalog import CatalogArtifact


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["plays"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


# Sentinel used when frontmatter is missing fields that sibling tickets
# (P4-06 ``category``, P4-07 ``critical``) own. Keeping these constants
# named so downstream callers and tests can reference one source of
# truth instead of duplicating the literal.
DEFAULT_CATEGORY = "uncategorized"
DEFAULT_CRITICAL = False


class PlayCoverageRow(BaseModel):
    """One Play with its workspace-level coverage rollup."""

    play_key: str = Field(
        ...,
        description=(
            "Pattern id from the catalog (frontmatter ``id``), e.g. "
            "``flow-pr-self-review``. Stable across versions."
        ),
    )
    play_name: str = Field(
        ...,
        description=(
            "Human-readable name from the pattern frontmatter (``name``). "
            "Falls back to ``play_key`` when the catalog row has no "
            "explicit name."
        ),
    )
    category: str = Field(
        ...,
        description=(
            "Frontmatter ``spec.category`` (P4-06). Falls back to "
            "``'uncategorized'`` when the field is absent so the FE "
            "can group rows even before the sibling rename ships."
        ),
    )
    critical: bool = Field(
        ...,
        description=(
            "Frontmatter ``spec.critical`` (P4-07). Falls back to "
            "``False`` when the field is absent. Drives the red-badge "
            "treatment on the Coverage tab."
        ),
    )

    activated_repos_total: int = Field(
        ...,
        description=(
            "Workspace-wide count of activated repos (= candidate "
            "repos this play could be configured on). Repeated on "
            "the envelope for convenience."
        ),
    )
    assignments_count: int = Field(
        ...,
        description=(
            "Number of activated repos in this workspace whose "
            "``.ship/config.yml`` currently wires this play."
        ),
    )
    coverage_pct: float = Field(
        ...,
        description=(
            "``assignments_count / activated_repos_total``. ``0.0`` "
            "when ``activated_repos_total == 0`` so the FE never "
            "needs a guard."
        ),
    )

    repos_covered: list[uuid.UUID] = Field(
        default_factory=list,
        description=(
            "WorkspaceRepo ids that DO have a Lane wiring this play. "
            "Sorted by ``WorkspaceRepo.full_name`` so the drill-down "
            "list is stable."
        ),
    )
    repos_uncovered: list[uuid.UUID] = Field(
        default_factory=list,
        description=(
            "WorkspaceRepo ids that DO NOT have a Lane wiring this "
            "play. Sorted by ``WorkspaceRepo.full_name``."
        ),
    )


class PlayCoverageOut(BaseModel):
    """Envelope for ``GET /workspaces/{ws}/plays/coverage``."""

    activated_repos_total: int = Field(
        ...,
        description=(
            "Workspace-wide count of activated repos. Identical to "
            "the value carried on each row — surfaced here so the FE "
            "can render the page header without iterating the rows."
        ),
    )
    rows: list[PlayCoverageRow]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_user_facing_play(entry: CatalogArtifact) -> bool:
    """``True`` iff this catalog pattern should appear in Coverage.

    Plays with an inbox profile of ``silent`` are system-internal
    helpers (``common-*`` shared fragments, ``op-*`` housekeeping
    tasks) — they never show up in the user-facing Plays catalog and
    therefore are not part of coverage. We read the profile straight
    from frontmatter rather than going through
    :mod:`backend.app.services.inbox.profiles` so this endpoint
    doesn't depend on the inbox catalog YAML being loadable in every
    deployment.
    """
    inbox_cfg = entry.spec.get("inbox") if isinstance(entry.spec, dict) else None
    if not isinstance(inbox_cfg, dict):
        # No inbox config — treat as user-facing. The inbox profile
        # resolver itself defaults missing config to ``silent``, but
        # at the catalog layer we err on the side of *visibility*:
        # a freshly-authored pattern shows up in Coverage even if
        # the author hasn't picked an inbox profile yet.
        return True
    profile = inbox_cfg.get("profile")
    if not isinstance(profile, str):
        return True
    return profile != "silent"


def _frontmatter_category(entry: CatalogArtifact) -> str:
    """Return ``spec.category`` or :data:`DEFAULT_CATEGORY`.

    P4-06 (sibling B) renames the existing ``group`` axis onto a new
    ``category`` taxonomy. Until that ships, surface a stable
    fallback so the FE can group rows without crashing on ``None``.
    """
    raw = entry.category
    if isinstance(raw, str) and raw:
        return raw
    return DEFAULT_CATEGORY


def _frontmatter_critical(entry: CatalogArtifact) -> bool:
    """Return ``spec.critical`` or :data:`DEFAULT_CRITICAL`.

    P4-07 (sibling B) marks a curated subset of plays as critical so
    Coverage can render the red badge. The field doesn't exist yet on
    most artifacts; missing == not critical.
    """
    spec = entry.spec if isinstance(entry.spec, dict) else {}
    raw = spec.get("critical")
    return bool(raw) if isinstance(raw, bool) else False


def _lane_pattern_keys(lane: Lane) -> set[str]:
    """All play keys a single :class:`Lane` row contributes coverage for.

    Reads both the scalar ``Lane.pattern`` (back-compat single-pattern
    lanes) and the canonical ``config_blob['patterns']`` list
    (RFC-0008 C3.1 multi-pattern lanes). Either form proves the
    .ship/config.yml is wiring the pattern, so both should count.
    """
    out: set[str] = set()
    if lane.pattern:
        out.add(lane.pattern)
    blob = lane.config_blob or {}
    raw = blob.get("patterns") if isinstance(blob, dict) else None
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and entry:
                out.add(entry)
    return out


def _coverage_sort_key(row: PlayCoverageRow) -> tuple[int, float, str]:
    """Sort tuple matching the §1 spec ("uncovered DESC, critical-first").

    Three buckets, in this order:

    1. Critical with gaps (``critical=true and coverage_pct < 1``),
       lowest coverage first.
    2. Non-critical with gaps, lowest coverage first.
    3. Fully covered (``coverage_pct == 1``), alphabetical by name.

    The stable secondary sort by ``play_name`` keeps the order
    deterministic even when many rows share a coverage_pct (very
    common in fresh workspaces where every row is at 0.0).
    """
    has_gaps = row.coverage_pct < 1.0
    if has_gaps and row.critical:
        bucket = 0
    elif has_gaps:
        bucket = 1
    else:
        bucket = 2
    # Within "with gaps" buckets we want lowest coverage first;
    # within the "fully covered" bucket coverage_pct is always 1.0
    # so the secondary key (play_name) does the actual ordering.
    return (bucket, row.coverage_pct, row.play_name.lower())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/plays/coverage", response_model=PlayCoverageOut)
async def get_plays_coverage(
    workspace_id: uuid.UUID,
    category: str | None = Query(
        default=None,
        description=(
            "Filter rows to a single ``spec.category``. Matched as "
            "an exact string against the value the row would expose. "
            "Pass ``uncategorized`` to surface rows that have no "
            "frontmatter category yet (legacy / pre-P4-06)."
        ),
    ),
    critical_only: bool = Query(
        default=False,
        description="Return only rows where ``critical=true``.",
    ),
    has_gaps: bool = Query(
        default=False,
        description=(
            "Return only rows where ``coverage_pct < 1.0``. Useful "
            "for the Coverage tab's default 'show me what's broken' "
            "view."
        ),
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PlayCoverageOut:
    """Aggregate Play → activated-repo coverage for the workspace.

    Pre-sorted server-side per the §1 spec so the FE can render the
    list verbatim without a second pass. Filters are applied AFTER
    sorting / aggregation: the catalog is bounded (≤80 plays) so the
    bookkeeping cost is irrelevant compared to a SQL push-down, and
    keeping the filter logic in Python lets us share the exclusion
    rule (``inbox.profile=silent``) with future endpoints without
    duplicating it across routes.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    # 1. Activated repos for the workspace. Mirrors the read
    # surface of ``/v1/workspaces/{ws}/repos`` — every WorkspaceRepo
    # row is considered activated. Order alphabetical so the
    # repos_covered/uncovered lists are stable for snapshot tests.
    repo_rows = (
        (
            await session.execute(
                select(WorkspaceRepo)
                .where(WorkspaceRepo.workspace_id == workspace_id)
                .order_by(asc(WorkspaceRepo.full_name))
            )
        )
        .scalars()
        .all()
    )
    repo_ids: list[uuid.UUID] = [r.id for r in repo_rows]
    activated_total = len(repo_ids)

    # 2. Lanes for the workspace. One query — we group in Python
    # rather than fanning out per play to honour the performance
    # budget. ``Lane.workspace_id`` is indexed (see Lane.__table_args__).
    lanes_by_pattern: dict[str, set[uuid.UUID]] = {}
    if repo_ids:
        lane_rows = (
            (
                await session.execute(
                    select(Lane).where(Lane.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )
        for lane in lane_rows:
            # A lane referencing a repo that's no longer activated
            # (race window between deactivation and lane cleanup)
            # shouldn't inflate coverage. Pin to the live activated
            # set instead of trusting the stored ``repo_id`` blindly.
            if lane.repo_id not in repo_ids:
                continue
            for play_key in _lane_pattern_keys(lane):
                lanes_by_pattern.setdefault(play_key, set()).add(lane.repo_id)

    # 3. Catalog rows → Play rows. Filter out silent (system-internal)
    # patterns and de-dup by id (workspace-private overrides aren't
    # merged here — the Coverage view operates on the baked-in
    # catalog only; workspace-private plays land in v2 once their
    # frontmatter contract is settled).
    rows: list[PlayCoverageRow] = []
    repo_id_set: set[uuid.UUID] = set(repo_ids)
    for entry in catalog_service.list_patterns():
        if not _is_user_facing_play(entry):
            continue
        play_key = entry.id
        covered = lanes_by_pattern.get(play_key, set())
        # Constrain to activated repos so a stale lane on a
        # deactivated repo can't show up in ``repos_covered``.
        covered = covered & repo_id_set
        uncovered = repo_id_set - covered

        if activated_total == 0:
            coverage_pct = 0.0
        else:
            coverage_pct = len(covered) / activated_total

        rows.append(
            PlayCoverageRow(
                play_key=play_key,
                play_name=str(entry.name) if entry.name else play_key,
                category=_frontmatter_category(entry),
                critical=_frontmatter_critical(entry),
                activated_repos_total=activated_total,
                assignments_count=len(covered),
                coverage_pct=coverage_pct,
                # Sort by full_name (the activated set is already
                # ordered) so the drill-down split is deterministic.
                repos_covered=[r for r in repo_ids if r in covered],
                repos_uncovered=[r for r in repo_ids if r in uncovered],
            )
        )

    # 4. Sort BEFORE filtering so the post-filter list still reflects
    # the canonical "uncovered DESC, critical-first" order. Filtering
    # last is a deliberate ergonomic choice — the FE can swap query
    # params without the row order shifting under it.
    rows.sort(key=_coverage_sort_key)

    # 5. Apply filters in Python — ≤80 rows, no need for SQL.
    if category is not None:
        rows = [r for r in rows if r.category == category]
    if critical_only:
        rows = [r for r in rows if r.critical]
    if has_gaps:
        rows = [r for r in rows if r.coverage_pct < 1.0]

    return PlayCoverageOut(
        activated_repos_total=activated_total,
        rows=rows,
    )


__all__ = ["router"]
