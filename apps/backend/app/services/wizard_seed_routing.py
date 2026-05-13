"""CODEOWNERS → Inbox routing rule projection (Wave 8b P5-06).

Bridges :mod:`backend.app.services.codeowners` (parses + resolves
``CODEOWNERS``) with :class:`backend.app.db.models.inbox.InboxRoutingRule`
so the wizard can pre-seed an actionable ``code_owner`` mapping in the
same transaction that opens the seed PR. Without this, a freshly
activated repo would intake a ``code_owner``-handled item and fall
straight through the resolver's built-in chain to the workspace owner —
which is correct but invisible to the admin who already declared
ownership in CODEOWNERS.

Schema reality vs. spec language
--------------------------------

The P5-06 spec describes "for each parsed rule with at least 1
resolved user owner, create ONE :class:`InboxRoutingRule` … with
``targets=[user_id]`` and ``assignment_strategy='broadcast'``". The
DB model in :mod:`backend.app.db.models.inbox` has hard constraints
that the spec language papers over:

- ``UNIQUE (workspace_id, handle_key)`` — at most one rule per
  ``(workspace_id, handle)``. Multiple rows per handle are not
  representable.
- ``target_value`` is a single ``VARCHAR`` (not a list), keyed by
  ``target_type`` (``user`` → UUID string, ``group`` → group key,
  ``strategy`` → strategy name).
- ``assignment_strategy`` is constrained at the API surface to
  ``round_robin | oncall | first | NULL`` (``broadcast`` is not in
  :data:`backend.app.api.v1.routes.inbox_routing.VALID_ASSIGNMENT_STRATEGIES`).

Reconciliation: we project the resolved CODEOWNERS into a single
strategy-typed rule per workspace::

    handle_key:          'code_owner'
    target_type:         'user' (when exactly one resolved user)
                       | 'strategy' (when 2+ resolved users)
    target_value:        '<user_id>' | 'code_owner_broadcast'
    assignment_strategy: NULL
    strategy_config:     {'user_ids': [...], 'rules': [...]}

The ``rules`` array inside ``strategy_config`` records the per-line
breakdown (path pattern + resolved user_ids) so the Wave-8c admin UI
can render "this rule covers /backend/ → @alice; /frontend/ → @bob"
even though the rule itself collapses to one row. The ``user_ids``
top-level field is what a future ``code_owner_broadcast`` strategy
implementation in :mod:`backend.app.services.inbox.routing` will read
when it lands.

Idempotency
-----------

The wizard re-run path is the design target — operators retry the
button when CI is flaky. ``seed_routing_from_codeowners`` is therefore
a pure upsert: it's safe to call repeatedly, and the second call
produces no audit churn. Two flavours of "already there":

- An existing rule with ``handle_key='code_owner'`` whose
  ``strategy_config['user_ids']`` matches the freshly-resolved set →
  ``routing_rules_created=0`` and the row is left untouched.
- An existing rule with a different user set → also left untouched.
  Re-deriving ownership from CODEOWNERS on every wizard click would
  silently overwrite admin edits, which is worse than a stale rule
  the operator can see and fix.

Tests rely on the ``routing_rules_created`` counter to assert the
no-op path on the second call.
"""

from __future__ import annotations

import logging
import uuid

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.inbox import InboxRoutingRule
from backend.app.services.codeowners import (
    CodeownersResolution,
    resolve_codeowners,
)


logger = logging.getLogger(__name__)


# Handle every Play emit rule references when it wants the routing
# layer to deliver to "the people responsible for this code path"
# (see :mod:`backend.app.services.inbox.routing` ``_builtin_strategy``
# fallback for the same handle).
CODE_OWNER_HANDLE: str = "code_owner"

# Sentinel strategy name we stash into ``target_value`` when the
# resolved owner set is multi-user. Surfaces as a string in the admin
# UI's "configured target" column so operators can tell aggregated
# CODEOWNERS rules apart from manually-targeted strategies.
CODE_OWNER_BROADCAST_STRATEGY: str = "code_owner_broadcast"


class WizardSeedCodeownersSummary(BaseModel):
    """Wizard-facing audit of one ``seed_routing_from_codeowners`` pass.

    All four fields are stable post-P5-06 (Wave-8c FE binds against
    them). ``rules_count`` counts the *parsed* CODEOWNERS lines that
    contributed at least one resolved user — which is how operators
    naturally count "rules" in a CODEOWNERS file. The DB-side row count
    sits in ``routing_rules_created`` because the schema collapses
    everything into one row per workspace.
    """

    file_found: bool
    rules_count: int
    routing_rules_created: int
    unresolved_owners: list[str]


async def seed_routing_from_codeowners(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    pr_branch: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> WizardSeedCodeownersSummary:
    """Resolve CODEOWNERS for ``repo_id`` and upsert a ``code_owner`` rule.

    Pipeline:

    1. ``resolve_codeowners`` — fetches CODEOWNERS (preferring
       ``pr_branch`` when set so we see the file the seed PR itself
       just authored) and resolves owner tokens against workspace
       members.
    2. Aggregate the resolved user_ids in CODEOWNERS file order
       (``last-match-wins`` is the GitHub semantic, so the trailing
       owner of any path wins; we de-duplicate while preserving
       first-seen order so the broadcast list is deterministic).
    3. Idempotency probe: a pre-existing ``code_owner`` rule for the
       workspace short-circuits the write — re-runs are no-ops. The
       summary still reports ``rules_count`` so the admin UI shows
       "what we found in CODEOWNERS this time" alongside "what we
       wrote last time".
    4. Insert a single :class:`InboxRoutingRule`. Whether the row
       targets a single user (``target_type='user'``) or a broadcast
       strategy (``target_type='strategy'``) is decided by the
       resolved user count.

    Returns a :class:`WizardSeedCodeownersSummary` for the route's
    response body and the audit log entry. Never raises on a missing
    CODEOWNERS — that path returns ``file_found=False`` so the FE can
    nudge the operator to add one.
    """

    try:
        resolution = await resolve_codeowners(
            session=session,
            workspace_id=workspace_id,
            repo_id=repo_id,
            client=client,
            branch=pr_branch,
        )
    except httpx.HTTPStatusError:
        # An auth/5xx fault from GitHub is the wizard's problem, not
        # the routing-seed step's — re-raise so the route can decide
        # whether to abort the whole call or degrade gracefully. The
        # seed PR has *already* been opened by this point; bubbling
        # up means the caller writes a partial audit and the FE
        # surfaces "PR opened, codeowners step failed".
        raise

    file_found = resolution.fetched_from != "missing"
    contributing_rules, ordered_user_ids, per_rule_breakdown = (
        _aggregate_resolved(resolution)
    )

    if not file_found or not ordered_user_ids:
        # Nothing to wire — leave the resolver fallback chain to do
        # its job at intake time.
        return WizardSeedCodeownersSummary(
            file_found=file_found,
            rules_count=contributing_rules,
            routing_rules_created=0,
            unresolved_owners=list(resolution.unresolved),
        )

    existing = await _find_existing_rule(
        session=session, workspace_id=workspace_id
    )
    if existing is not None:
        # Idempotent: re-runs of the wizard never overwrite an
        # operator's manual customisation, AND never thrash the
        # audit log with redundant updates when CODEOWNERS hasn't
        # actually changed.
        return WizardSeedCodeownersSummary(
            file_found=file_found,
            rules_count=contributing_rules,
            routing_rules_created=0,
            unresolved_owners=list(resolution.unresolved),
        )

    rule = _build_routing_rule(
        workspace_id=workspace_id,
        ordered_user_ids=ordered_user_ids,
        per_rule_breakdown=per_rule_breakdown,
        sha=resolution.sha,
        branch=pr_branch,
    )
    session.add(rule)
    await session.flush()

    return WizardSeedCodeownersSummary(
        file_found=True,
        rules_count=contributing_rules,
        routing_rules_created=1,
        unresolved_owners=list(resolution.unresolved),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aggregate_resolved(
    resolution: CodeownersResolution,
) -> tuple[int, list[uuid.UUID], list[dict[str, object]]]:
    """Walk a :class:`CodeownersResolution` for routing-row inputs.

    Returns ``(contributing_rules, ordered_user_ids,
    per_rule_breakdown)``:

    - ``contributing_rules`` — count of parsed CODEOWNERS lines that
      contributed *at least one* resolved user (matches how operators
      think about "rules" in their CODEOWNERS file).
    - ``ordered_user_ids`` — de-duplicated user_ids in
      first-seen order. The order is stable across reruns so audit
      log diffs stay meaningful.
    - ``per_rule_breakdown`` — one entry per contributing rule with
      ``{path_pattern, user_ids}`` so the Wave-8c admin UI can render
      "/backend/ → @alice, @bob" without re-resolving.
    """
    contributing_rules: int = 0
    ordered_user_ids: list[uuid.UUID] = []
    seen_user_ids: set[uuid.UUID] = set()
    per_rule_breakdown: list[dict[str, object]] = []

    for rule in resolution.rules:
        owners = resolution.handles_by_path.get(rule.path_pattern, ())
        rule_user_ids: list[uuid.UUID] = []
        for owner in owners:
            if owner.user_id is None:
                continue
            if owner.user_id in seen_user_ids:
                # Still record it under this rule's breakdown so the
                # UI can show the path-level mapping accurately.
                rule_user_ids.append(owner.user_id)
                continue
            seen_user_ids.add(owner.user_id)
            ordered_user_ids.append(owner.user_id)
            rule_user_ids.append(owner.user_id)
        if rule_user_ids:
            contributing_rules += 1
            per_rule_breakdown.append(
                {
                    "path_pattern": rule.path_pattern,
                    "user_ids": [str(uid) for uid in rule_user_ids],
                }
            )

    return contributing_rules, ordered_user_ids, per_rule_breakdown


def _build_routing_rule(
    *,
    workspace_id: uuid.UUID,
    ordered_user_ids: list[uuid.UUID],
    per_rule_breakdown: list[dict[str, object]],
    sha: str | None,
    branch: str | None,
) -> InboxRoutingRule:
    """Assemble the single :class:`InboxRoutingRule` row to persist.

    Two shapes:

    - One resolved user → ``target_type='user'`` so the admin UI's
      target chip resolves to a real account without a strategy
      indirection.
    - Multiple resolved users → ``target_type='strategy'`` with a
      sentinel ``target_value`` and the user list in
      ``strategy_config``. Until a real ``code_owner_broadcast``
      strategy lands in the resolver, intake-time fallback still
      reaches the right human via the resolver's built-in
      ``code_owner`` reader.

    The ``strategy_config`` carries ``codeowners_sha`` (the blob sha
    we read) + ``branch`` so a later sweep can detect "the file moved"
    without a full re-fetch.
    """
    if len(ordered_user_ids) == 1:
        target_type = "user"
        target_value = str(ordered_user_ids[0])
    else:
        target_type = "strategy"
        target_value = CODE_OWNER_BROADCAST_STRATEGY

    strategy_config: dict[str, object] = {
        "user_ids": [str(uid) for uid in ordered_user_ids],
        "rules": per_rule_breakdown,
        "source": "wizard_seed_codeowners",
    }
    if sha:
        strategy_config["codeowners_sha"] = sha
    if branch:
        strategy_config["codeowners_branch"] = branch

    return InboxRoutingRule(
        workspace_id=workspace_id,
        handle_key=CODE_OWNER_HANDLE,
        target_type=target_type,
        target_value=target_value,
        assignment_strategy=None,
        strategy_config=strategy_config,
        is_enabled=True,
    )


async def _find_existing_rule(
    *, session: AsyncSession, workspace_id: uuid.UUID
) -> InboxRoutingRule | None:
    """Look for the workspace's existing ``code_owner`` rule, if any.

    The schema's ``UNIQUE (workspace_id, handle_key)`` means a hit is
    always at most one row, so a ``scalar_one_or_none`` is correct
    even on a hot table.
    """
    return (
        await session.execute(
            select(InboxRoutingRule).where(
                InboxRoutingRule.workspace_id == workspace_id,
                InboxRoutingRule.handle_key == CODE_OWNER_HANDLE,
            )
        )
    ).scalar_one_or_none()


__all__ = [
    "CODE_OWNER_BROADCAST_STRATEGY",
    "CODE_OWNER_HANDLE",
    "WizardSeedCodeownersSummary",
    "seed_routing_from_codeowners",
]
