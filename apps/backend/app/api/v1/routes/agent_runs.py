"""Agent-run helpers: the read-and-write surface ``shipctl`` calls
during a routine run.

E14 architecture (locked 2026-04-30):

- A workspace **is** a project. There is exactly **one** tracker
  per workspace (Linear team / Jira project / etc.); the workspace
  may host several repos but they all share the same backlog. The
  endpoints in this module are therefore workspace-scoped — repo
  context is only needed at the agent-runtime level (which checkout
  to spawn), not for tracker resolution.

- Customer's GitHub Actions cron fires ``shipctl run --routine X``.
- ``shipctl`` reads the routine's pattern, asks Ship server for a
  task (a single ticket in the routine's FSM stage, if applicable),
  hands the prompt to a Cursor Cloud agent, polls until the agent
  finishes. Branchless agents (intake, BA, planner) do not commit
  anything to a branch — they call ``POST /agent-runs/finish``
  directly from inside the Cursor runtime to report their outcome.
  Branchful agents (developer, qa) push a code branch *and* call
  ``/agent-runs/finish``; the finish call is the canonical signal,
  the branch is just where the code lives.

  Outcomes the finish endpoint accepts:

  - ``ready_next_step``    → transition ticket to ``stage_next``
                             (and optionally leave a comment).
  - ``needs_clarification`` → tag with ``needs:clarification`` so
                             intake stops re-picking until the
                             human answers; optional comment.
  - ``blocked``             → audit only (no inbox row); ticket
                             unchanged.
  - ``out_of_scope``        → close the ticket with optional
                             comment.

``shipctl`` runs in the customer's runner with a workspace API
token; the agent runtime gets the same token in its prompt so it
can call ``/agent-runs/finish``. The server uses the workspace's
Linear OAuth integration to do the actual mutation — the CLI and
the agent never hold the tracker credential.
"""

from __future__ import annotations

import hashlib
import logging
import re
import struct
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from sqlalchemy import select as sa_select

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.dashboard_priorities import WorkspaceProjectPriority
from backend.app.core.sentry import record_inbox_exception_breadcrumb
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.pipelines import PullRequest
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.services.inbox.headline import derive_headline
from backend.app.services.inbox.sweep import sweep_auto_resolvable
from backend.app.services.dispatcher import (
    ENV_SEPARATION_ACK_KEY,
    ENV_SEPARATION_PENDING_KEY,
    env_separation_handle,
    normalize_routine_id,
)
from backend.app.db.session import get_session
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.services.linear_provisioner import (
    OVERLAY_FREEZE_LABEL_PREFIXES,
)
from backend.app.services.file_overlap import (
    build_file_coordination_warning,
    load_file_coordination_warning_from_audit,
)
from backend.app.services.file_overlap_telemetry import evaluate_file_overlap_honour
from backend.app.services.tracker_resolver import resolve_for_workspace


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["agent-runs"],
)
logger = logging.getLogger(__name__)


# Planning-anchor identification. Set by the tracker adapter when the
# anchor is minted (Linear: ``LinearTracker.PLANNING_ANCHOR_LABEL``).
# The picker uses it to bypass the project-priority gate for anchors
# whose project is still in Drafts (``state='planning'``) — without
# this exempt the decomposition routines (wbs / architecture /
# qa_plan / planning_done) can never run, because their anchor lives
# in a Drafts project by definition and ``planning_done`` is what
# graduates Drafts → Parked.
_PLANNING_ANCHOR_LABEL: str = "planning:anchor"

# Auto-filed-ticket escape hatch. When reviewer-shaped routines
# (qa-reviewer, security-reviewer, retro, learning-capture, …) open
# coverage-gap tickets, they tag them ``needs:intake`` so the next
# ``task_intake`` tick takes them regardless of the parent project's
# priority state. Without this label they'd sit in Drafts/Parked
# projects forever and just generate ``priority_skipped`` audit noise
# every cron tick — observed today as 9 ticks × 6 tickets = 54 skips
# from one reviewer pass. The label is dropped at the first
# task_intake transition so the ticket inherits its parent project's
# normal priority gate from then on.
_NEEDS_INTAKE_LABEL: str = "needs:intake"

# Audit-dedup window — a workspace's tracker is degraded when its
# adapter starts erroring; subsequent stage picks within the window
# all see the same failure mode and re-emitting one audit row per
# stage (× ~13 stages × hourly cron) drowns the operator inbox. We
# emit one audit row + one blocker letter per outage and short-
# circuit the rest until the window elapses or the next call
# succeeds.
_TRACKER_FAILURE_DEDUP_WINDOW = timedelta(hours=1)
# priority_skipped dedup window — same project priority state will
# refuse a given ticket every tick. Keep one audit breadcrumb per
# (ticket, hour); operator-facing breadcrumb is unchanged but the
# audit volume drops by ~9× on workspaces with active reviewer
# pipelines.
_PRIORITY_SKIPPED_DEDUP_WINDOW = timedelta(hours=1)

# ELS-120 safety net: stages whose ``ready_next_step`` finish must
# carry a PR URL in ``comment``. ``run.mjs`` splices the URL in after
# ``gh pr create`` succeeds (sidecar flow); a finish without one means
# the agent bypassed the sidecar and called /finish directly before
# the runner could push — the exact bug class ELS-120 closes. Reject
# with 422 instead of advancing the ticket to a stage that has no PR.
_PR_AUTHORING_STAGES: frozenset[str] = frozenset(
    {
        "dev_implementation",
        "qa_automation",
        "workflow_self_heal",
    }
)
_PR_URL_RE = re.compile(
    r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+", re.IGNORECASE
)

# Picker refire cap — universal loop guard.
#
# A correctly-flowing ticket fires each FSM stage exactly once: the
# routine picks, the agent finishes, the server transitions the ticket
# forward by adding a ``stage:<X>`` breadcrumb, and the next picker
# call for that stage excludes the ticket via its own-label filter.
# When ANY layer between "agent finished" and "breadcrumb present"
# silently fails — Linear adapter dropping its filter clause because
# ``label_id_by_stage[X]`` is None (the ELS team's bug_triage label was
# never provisioned, observed in production as ~\$200 of agent-token
# burn over 24h), a transition mutation rejected by the tracker, the
# breadcrumb existing but the picker filter changing shape after a
# label rename, etc. — the same ticket re-picks every cron tick.
#
# The cap is universal: regardless of root cause, if the same
# ``(workspace, fsm_stage, ticket_ref)`` triple has fired ``finish``
# more than this many times in the dedup window, the picker refuses
# to hand the ticket back to the routine until the cap window
# elapses, emits one ``agent_run.refire_capped`` audit + one inbox
# blocker letter, and stops the burn. Operator sees the letter,
# decides whether to bump the cap or fix the underlying breadcrumb
# bug; agent tokens are conserved either way.
_REFIRE_CAP_LIMIT: int = 3
_REFIRE_CAP_WINDOW = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TaskTicketOut(BaseModel):
    """A single task the agent will work on, shaped for prompt rendering."""

    ticket_ref: str  # vendor-agnostic id the CLI passes back on writes
    kind: str  # tracker provider — informational, not load-bearing
    title: str
    body: str | None = None
    url: str | None = None
    labels: list[str] = Field(default_factory=list)
    state: str | None = None
    fsm_stage: str | None = None  # echo of the requested stage
    # ELS-86 — markdown excerpt of the parent project body (Brief /
    # WBS / Architecture / Test architecture / Tasks sections),
    # capped at 8KB. ``None`` when the ticket isn't associated with a
    # project, when the tracker can't surface project bodies, or when
    # the fetch failed (best-effort: a runaway 5xx must not block the
    # agent run, the per-ticket ``body`` already carries the
    # immediate task brief).
    project_context: str | None = None
    # ELS-154 — blockquote prepended by the CLI when sibling open PRs
    # touch high-risk paths (migrations / hard path overlap).
    file_coordination_warning: str | None = None


class TaskResponseOut(BaseModel):
    """``GET /tracker/next`` response. ``ticket=None`` → no eligible task."""

    ticket: TaskTicketOut | None = None
    fsm_stage: str
    tracker_kind: str | None = None


class TransitionIn(BaseModel):
    ticket_ref: str = Field(min_length=1, max_length=512)
    to_state: str = Field(min_length=1, max_length=64)
    # Optional sanity check: refuse the call if the ticket isn't in this
    # stage on Ship's side. CLI passes its own knowledge of "I just
    # picked this from <stage>" — server uses it to short-circuit a
    # double-fire.
    from_state: str | None = None
    comment: str | None = None  # leave a trail before/with the transition


class CommentIn(BaseModel):
    ticket_ref: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=8000)


class InboxItemIn(BaseModel):
    """Payload an agent posts (via ``shipctl inbox create``) to file an
    item in the operator's inbox.

    ``body`` carries the full markdown content the operator reads in
    the mailbox preview pane — it lands under ``payload.body`` because
    the InboxItem schema reserves ``summary`` for the short list-row
    blurb (≤2KB). Reports / digests want a long body without
    truncation; the preview pane reads ``payload.body`` first, falling
    through to ``summary`` for legacy items.
    """

    type: Literal[
        "clarification",
        "improvement",
        "blocker",
        "approval",
        "exception",
        "report",
    ] = "improvement"
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    # Markdown body for the preview pane. ≤32KB so a long retro
    # digest fits without paginating.
    body: str | None = Field(default=None, max_length=32 * 1024)
    payload: dict[str, Any] = Field(default_factory=dict)
    ticket_ref: str | None = None


class WriteOut(BaseModel):
    ok: bool = True
    tracker_kind: str | None = None
    note: str | None = None


class ChildTicketCreate(BaseModel):
    """One child ticket the decomposition developer is creating from
    a WBS slice.

    The developer's role at the ``tasks`` stage is to slice the WBS
    into one tracker ticket per slice. The ``create_ticket`` adapter
    method exists on Linear, but exposing it as a separate HTTP call
    asks each agent to chain N round-trips and collect identifiers
    before the finish call. Instead the agent declares the children
    here and the server creates them as part of finish processing,
    then auto-emits a ``## Tasks`` section with their identifiers so
    the project body matches reality without the agent needing to
    pre-substitute the IDs it cannot know.

    ``project_id`` is derived server-side from the anchor ticket;
    the agent only declares title/body for each slice. Labels and
    priority are optional pass-throughs to the tracker adapter.
    """

    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=32_000)
    labels: list[str] = Field(default_factory=list, max_length=20)
    priority: int | None = Field(default=None, ge=0, le=4)


class ProjectSectionPatch(BaseModel):
    """One section the decomposition agent owns and is patching this run.

    Each role in the decomposition chain owns exactly one section of
    the project body (BA → ``WBS``, tech-arch → ``Architecture``,
    qa-arch → ``Test architecture``, qa-eng → ``QA scenarios``,
    developer → ``Tasks``). The role prompts told agents to "patch via
    ``upsert_project_section(...)``" — but that was a fictional tool;
    the server now reads this list from ``FinishIn`` and applies each
    patch via ``LinearTracker.upsert_project_section`` (existing
    adapter method that replace-or-appends a ``## <section>`` block).

    ``project_id`` is derived server-side from the anchor ticket the
    run was working against — agents only need to declare what
    section + body they produced, not where to write it.
    """

    section: str = Field(min_length=1, max_length=64)
    body: str = Field(max_length=50_000)


class FinishIn(BaseModel):
    """Payload the Cursor agent posts to ``/agent-runs/finish`` from
    inside its runtime once it's done with the work.

    A workspace API token is rendered into the agent's prompt, so the
    agent can call this endpoint without holding any tracker credential.
    The server resolves the workspace's Linear OAuth row and applies
    the side-effects implied by ``outcome``.
    """

    run_id: str = Field(min_length=1, max_length=128)
    outcome: Literal[
        "ready_next_step",
        "needs_clarification",
        "blocked",
        "out_of_scope",
        # Workspace bundles (self-heal / daily-digest) report
        # ``noop`` when a tick found nothing actionable. Treated like
        # ``ready_next_step`` with no ticket: only the audit row
        # lands, no transition, no inbox.
        "noop",
    ]
    fsm_stage: str | None = Field(default=None, max_length=64)
    stage_next: str | None = Field(default=None, max_length=64)
    ticket_ref: str | None = Field(default=None, max_length=512)
    comment: str | None = Field(default=None, max_length=8000)
    summary: str | None = Field(default=None, max_length=2000)
    # Shape-the-ticket-itself surface for stages that should rewrite
    # the issue body (intake, BA, planner). When set on a
    # ``ready_next_step`` finish, the server replaces the tracker
    # description with this markdown — comments are reserved for
    # auditable narration ("what I did and why"), the description
    # carries the structured shape (Problem / Goal / AC / Scope /
    # Risks / etc.). Linear keeps prior bodies in the issue activity
    # feed so the operator can always see what changed.
    # 100KB cap — Linear's own description cap is ~256KB so we stay
    # well under that, but we lift the original 20KB ceiling because
    # the planning bundle's combined Brief + Architecture + Test plan
    # on a real ticket easily runs 25-40KB (askslayer/PAC-23 wrote
    # 26KB on its second clean planning run and got rejected with
    # 422 by this validator on 2026-05-15, stalling the entire
    # chain three weeks past the E16 cutover).
    description: str | None = Field(default=None, max_length=100_000)
    # Decomposition stage artefacts: the role's section of the project
    # body. Persisted via the tracker's ``upsert_project_section``
    # adapter (replace-or-append the ``## <section>`` block). Empty
    # for SDLC / non-decomposition runs.
    project_sections: list[ProjectSectionPatch] = Field(default_factory=list)
    # Decomposition `tasks` stage: child tickets the developer carved
    # out of the WBS. Server creates each under the anchor's project
    # via the tracker's ``create_ticket`` adapter, then auto-renders a
    # ``## Tasks`` section listing the freshly-created identifiers.
    # Empty for every other role.
    child_tickets: list[ChildTicketCreate] = Field(default_factory=list)
    # Which process the run executed under. Defaults to the per-ticket
    # SDLC (``development``); ``decomposition`` (ELS-75) is the
    # project-anchor pipeline. The finish hook reads this to know
    # whether ``stage_next='planning_done'`` should flip the project's
    # dashboard row from Drafts → Parked (the PO promotes Parked →
    # Active manually when ready to ship; ELS-81).
    # Loose accept: ``development`` (SDLC default), ``decomposition``
    # (anchor pipeline), or any ``workspace_*`` flavour (self-heal /
    # daily-digest / weekly-audit). Workspace bundles' role prompts
    # don't constrain ``process``, and Cursor sometimes synthesises a
    # bundle-specific label here — we accept that rather than 422
    # the agent's audit row over a free-text field that the handler
    # only reads as a coarse hint (``process == 'decomposition'`` is
    # the only branch that actually keys on it).
    process: str = Field(default="development", max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class FinishOut(BaseModel):
    ok: bool = True
    outcome: str
    run_id: str
    actions: list[str] = Field(default_factory=list)
    tracker_kind: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pickup_lock_key(
    workspace_id: uuid.UUID, fsm_stage: str
) -> tuple[int, int]:
    """Stable (int4, int4) key for the picker's advisory lock (ELS-85).

    Postgres ``pg_try_advisory_xact_lock`` accepts a single 64-bit int
    or a pair of 32-bit ints. The pair form lines up nicely with our
    natural lock key — ``(workspace_id, fsm_stage)`` — so we hash each
    side to a signed int32 and pass them separately. Collisions across
    different ``(ws, stage)`` pairs are theoretically possible
    (``2^-32`` per pair) but the cost of a false positive is just
    "the second caller noops and retries on the next cron tick" —
    well within tolerance.

    BLAKE2b is used over Python's built-in ``hash`` because the latter
    is randomised per-process: two replicas would compute different
    keys for the same workspace and the lock would stop working
    altogether.
    """
    ws_digest = hashlib.blake2b(workspace_id.bytes, digest_size=4).digest()
    stage_digest = hashlib.blake2b(
        fsm_stage.encode("utf-8"), digest_size=4
    ).digest()
    ws_key = struct.unpack("<i", ws_digest)[0]
    stage_key = struct.unpack("<i", stage_digest)[0]
    return ws_key, stage_key


def _vendor_kind_to_ticket_kind(vendor_kind: str) -> Literal[
    "github_issues", "linear", "notion", "jira"
]:
    if vendor_kind == "linear":
        return "linear"
    if vendor_kind == "github_issues":
        return "github_issues"
    if vendor_kind == "jira":
        return "jira"
    return "github_issues"  # safe default for the pilot


async def _try_ticket_snapshot(gateway: Any, ref: TicketRef) -> dict[str, Any] | None:
    """Best-effort source-ticket fetch for inbox payloads.

    Adapter is allowed to either expose ``get_ticket_snapshot`` (Linear
    today) or not — and the call itself can fail (Linear timeout,
    deleted ticket). Either way we just return ``None``; the inbox row
    still gets created without a snapshot so the operator at least
    sees the agent's question.
    """
    fn = getattr(gateway, "get_ticket_snapshot", None)
    if fn is None:
        return None
    try:
        return await fn(ref)
    except Exception:
        return None


def _ticket_ref_from(vendor_kind: str, raw: str) -> TicketRef:
    """Hydrate a vendor-agnostic ``ticket_ref`` string into a typed
    :class:`TicketRef` for adapter calls.

    The string format we standardise on:

    - ``linear``        → display id, e.g. ``ENG-42`` (the adapter
      resolves to UUID at call time).
    - ``github_issues`` → ``owner/repo#42``.
    """
    return TicketRef(
        kind=_vendor_kind_to_ticket_kind(vendor_kind),
        workspace_hint=None,
        id=raw,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/tracker/next",
    response_model=TaskResponseOut,
)
async def get_next_task(
    workspace_id: uuid.UUID,
    state: str = Query(..., min_length=1, max_length=64, alias="state"),
    ticket_ref: str | None = Query(
        default=None,
        min_length=1,
        max_length=128,
        alias="ticket_ref",
        description=(
            "Pin the picker to one ticket (E16/ELS-124). When set, the "
            "candidate list is filtered to rows whose identifier equals "
            "this ref before the orphan/overlay/priority gates run. "
            "Used by the dispatcher-driven flow where the backend has "
            "already chosen which ticket the agent should work on."
        ),
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TaskResponseOut:
    """Return the next ticket the agent should work on for ``state``.

    Picker filters (applied in this canonical order):

    * **ELS-83 — orphans.** Tickets without a tracker ``project_id``
      are skipped. A one-shot inbox item and ``agent_run.orphan_skipped``
      audit rows land so an operator can re-home or close them.
    * **ELS-84 — overlay-frozen.** Tickets carrying a label whose
      prefix is in :data:`OVERLAY_FREEZE_LABEL_PREFIXES`
      (``needs:clarification`` / ``blocked`` / ``blocked-on-…``) are
      skipped silently with an ``agent_run.overlay_frozen_skipped``
      audit row — the operator already owes a reply on these.
    * **ELS-80 — project priority.** Only tickets whose project has
      ``WorkspaceProjectPriority.state == 'active'`` come through.
      Projects in ``planning`` (Drafts) or ``parked`` are gated by
      the PO; tickets there get an ``agent_run.priority_skipped``
      audit row but no inbox spam (operator's choice to hold them).
      Projects with no priority row are treated as not-yet-onboarded
      and skipped too.
    * **Planning-anchor exempt.** Tickets carrying the
      ``planning:anchor`` label bypass the priority gate. Anchors
      live in Drafts projects (``state='planning'``) by design, and
      the decomposition routines (wbs / architecture / qa_plan /
      planning_done) running against them are what *graduates* the
      project to Parked. Without the exempt, decomposition would
      deadlock on its own gate.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    # ELS-85: pick-race protection. Two parallel ``shipctl run
    # --routine X`` calls on the same FSM stage would otherwise both
    # fetch the same head-of-list ticket and start two Cursor runs.
    # ``pg_try_advisory_xact_lock(int4, int4)`` keyed on
    # ``(workspace_id, fsm_stage)`` serialises them — second caller
    # gets ``ticket=null`` and noops cleanly. Lock is transactional
    # so it auto-releases at commit / rollback; no manual unlock.
    ws_key, stage_key = _pickup_lock_key(workspace_id, state)
    got = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:k1, :k2)"),
            {"k1": ws_key, "k2": stage_key},
        )
    ).scalar_one()
    if not got:
        return TaskResponseOut(ticket=None, fsm_stage=state, tracker_kind=None)

    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    if resolved is None:
        return TaskResponseOut(ticket=None, fsm_stage=state, tracker_kind=None)

    # Pull extra rows so a head-of-list orphan / parked-project row
    # doesn't starve the picker — we drop the skips and pick the first
    # eligible row that remains.
    #
    # Defensive degradation: a Linear hiccup (rotated state ID, deleted
    # label, transient API 5xx) raises RuntimeError out of the
    # GraphQL helper. The cron tick must NOT fail the workflow over
    # that — the next tick will retry. Surface the error in the audit
    # log so an operator can debug, then return ``ticket=None`` so the
    # cron noops cleanly.
    try:
        # ELS-124 pin: when the backend dispatcher already picked a
        # ticket (Linear state transition triggered the run, dispatcher
        # resolved fsm_stage from labels, fired ``workflow_dispatch``
        # with that ticket_ref), trust it. ``list_tickets`` runs the
        # full FSM-stage gate filter — "ticket doesn't carry the
        # ``stage:<own>`` label yet" — which is the right semantics
        # for cron-mode picking but the wrong semantics for an
        # explicit pin: a ticket that already carries
        # ``stage:task_intake`` (legacy from the pre-E16 picker chain)
        # is exactly the one the post-cutover dispatcher wants the
        # planning bundle to re-run, and rejecting it strands the
        # whole flow before any agent gets to invoke /finish.
        # ``get_ticket_snapshot`` reads the ticket by id, skips every
        # gate, and returns the same row shape ``list_tickets`` does
        # so downstream orphan / overlay / priority checks still
        # apply uniformly. Caught on askslayer/PAC-23 2026-05-15
        # after E16/ELS-124 + bundle 0.36 reseed.
        if ticket_ref:
            # Cross-stage cascade hands the next agent its prompt from
            # the tracker's description, which the previous stage just
            # finished writing. Linear's read-replica lag (40-90s in
            # practice on askslayer/PAC-{21,22,23} 2026-05-15) is
            # longer than any reasonable cascade settle, so we read
            # Ship's own ``agent_run.finish`` audit as the
            # authoritative source for the freshly-written description
            # and overlay it on top of whatever the tracker returned.
            # The agent's sidecar always lands in our DB before the
            # tracker mutation, so this read is always at least as
            # fresh as the tracker.
            from sqlalchemy import desc as _sa_desc
            last_finish = (
                await session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.workspace_id == workspace_id,
                        AuditLog.action == "agent_run.finish",
                        AuditLog.payload["ticket_ref"].astext == ticket_ref,
                    )
                    .order_by(_sa_desc(AuditLog.created_at))
                    .limit(1)
                )
            ).scalars().first()
            audit_description = None
            audit_title = None
            if last_finish is not None and isinstance(last_finish.payload, dict):
                audit_description = last_finish.payload.get("description")
                if not isinstance(audit_description, str) or len(audit_description) < 50:
                    audit_description = None
            snapshot_fn = getattr(resolved.gateway, "get_ticket_snapshot", None)
            snapshot = None
            if snapshot_fn is not None:
                # Single snapshot attempt — the audit-overlay below
                # is the authoritative path for "did the prior stage
                # write a description?", so we don't burn 15s of
                # sleep waiting for the tracker replica. The runner's
                # /tracker/next is on a tight latency budget (one
                # cron tick may time out if we sleep too long).
                try:
                    snapshot = await snapshot_fn(
                        TicketRef(
                            kind=resolved.kind,
                            workspace_hint=None,
                            id=ticket_ref,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — defensive
                    logger.warning(
                        "tracker/next pin: snapshot failed ws=%s "
                        "ticket=%s err=%s",
                        workspace_id, ticket_ref, exc,
                    )
                    snapshot = None
            # Fallback synthesis: when the tracker replica returned
            # NULL or an empty issue but we have a recent audit
            # finish for the same ticket_ref, synthesise the row
            # from the audit description + a placeholder title.
            # Without this fallback the cascade dies on stale
            # replicas — caught on PAC-{19,20,21,22} 2026-05-15.
            if snapshot is None or not (snapshot.get("title") or ""):
                if audit_description:
                    # Pull a reasonable title from the previous
                    # tracker.event.received audit (we recorded the
                    # ticket's title at the moment dispatch fired)
                    # or fall back to the ticket_ref itself.
                    audit_title_row = (
                        await session.execute(
                            select(AuditLog)
                            .where(
                                AuditLog.workspace_id == workspace_id,
                                AuditLog.action == "tracker.event.received",
                                AuditLog.target_id == ticket_ref,
                            )
                            .order_by(_sa_desc(AuditLog.created_at))
                            .limit(1)
                        )
                    ).scalars().first()
                    audit_title = None
                    if audit_title_row is not None and isinstance(audit_title_row.payload, dict):
                        audit_title = audit_title_row.payload.get("title")
                    # Best-effort project_id recovery: prefer the
                    # snapshot's value, fall back to whichever
                    # ``agent_run.dispatch`` audit fired most recently
                    # for this ticket (the dispatcher writes
                    # project_id into its payload via the same
                    # tracker snapshot, so we benefit from however
                    # fresh THAT lookup was).
                    snap_project_id = (snapshot or {}).get("project_id")
                    synth_project_id: str | None = snap_project_id
                    if synth_project_id is None:
                        last_dispatch_row = (
                            await session.execute(
                                select(AuditLog)
                                .where(
                                    AuditLog.workspace_id == workspace_id,
                                    AuditLog.action == "agent_run.dispatch",
                                    AuditLog.payload["ticket_ref"].astext == ticket_ref,
                                )
                                .order_by(_sa_desc(AuditLog.created_at))
                                .limit(1)
                            )
                        ).scalars().first()
                        if last_dispatch_row and isinstance(last_dispatch_row.payload, dict):
                            synth_project_id = last_dispatch_row.payload.get("project_id")
                    row = {
                        "id": ticket_ref,
                        "title": audit_title or ticket_ref,
                        "body": audit_description,
                        "url": (snapshot or {}).get("url"),
                        "labels": (snapshot or {}).get("labels") or [],
                        "state": (snapshot or {}).get("state"),
                    }
                    # Only include project_id when we actually have one
                    # — otherwise the orphan gate sees a present-but-
                    # NULL key and filters the row out.
                    if synth_project_id:
                        row["project_id"] = synth_project_id
                    rows = [row]
                elif snapshot is None:
                    rows = []
                else:
                    rows = []
            else:
                snap_body = snapshot.get("description") or ""
                # Overlay Ship's DB description if it's longer than
                # what the tracker returned (i.e. the tracker's
                # replica hasn't caught up to our last
                # ``set_description`` mutation yet).
                if audit_description and len(audit_description) > len(snap_body):
                    snap_body = audit_description
                # project_id: prefer snapshot's, fall back to the last
                # dispatch's payload (the cascade dispatcher recorded
                # it from a fresher tracker read). Omit the key
                # entirely when both are None so the orphan gate
                # (which trips on present-but-null project_id) lets
                # the row through. Without this fallback Linear's
                # replica lag on the project ref drops the row even
                # when our last dispatch knew the project.
                snap_pid = snapshot.get("project_id")
                if not snap_pid:
                    last_dispatch_row = (
                        await session.execute(
                            select(AuditLog)
                            .where(
                                AuditLog.workspace_id == workspace_id,
                                AuditLog.action == "agent_run.dispatch",
                                AuditLog.payload["ticket_ref"].astext == ticket_ref,
                            )
                            .order_by(_sa_desc(AuditLog.created_at))
                            .limit(1)
                        )
                    ).scalars().first()
                    if last_dispatch_row and isinstance(last_dispatch_row.payload, dict):
                        snap_pid = last_dispatch_row.payload.get("project_id")
                row = {
                    "id": snapshot.get("ticket_ref") or ticket_ref,
                    "title": snapshot.get("title"),
                    "body": snap_body,
                    "url": snapshot.get("url"),
                    "labels": snapshot.get("labels") or [],
                    "state": snapshot.get("state"),
                }
                if snap_pid:
                    row["project_id"] = snap_pid
                rows = [row]
        else:
            rows = await resolved.gateway.list_tickets(state=state, limit=10)
    except RuntimeError as exc:
        logger.exception(
            "tracker/next: list_tickets failed ws=%s state=%s err=%s",
            workspace_id,
            state,
            exc,
        )
        # Outage dedup: when the tracker adapter starts erroring the
        # next ~13 stage picks in the same hour will all hit the same
        # error mode. Emit one ``tracker_next_failed`` audit row +
        # one operator inbox letter on the first detection, short-
        # circuit to None on every subsequent stage pick within the
        # window. Keeps the audit log readable and prevents the inbox
        # from drowning the *one* signal the operator actually needs.
        try:
            already_logged = await _recent_audit_exists(
                session,
                workspace_id=workspace_id,
                action="agent_run.tracker_next_failed",
                window=_TRACKER_FAILURE_DEDUP_WINDOW,
            )
            if not already_logged:
                session.add(
                    AuditLog(
                        workspace_id=workspace_id,
                        actor_user_id=auth.user.id,
                        actor_token_id=auth.token.id if auth.token else None,
                        action="agent_run.tracker_next_failed",
                        target_kind="fsm_stage",
                        target_id=state,
                        payload={
                            "tracker_kind": resolved.kind,
                            "fsm_stage": state,
                            "error": str(exc)[:500],
                        },
                    )
                )
                # First detection in this window → drop one blocker
                # row in the operator inbox. The audit-row dedup
                # gates this same-window check, so we don't re-issue
                # blocker letters either.
                session.add(
                    InboxItem(
                        workspace_id=workspace_id,
                        repo_id=None,
                        type="blocker",
                        title=(
                            f"Tracker {resolved.kind} unreachable — "
                            f"agents stalled"
                        )[:300],
                        summary=(
                            "The bound tracker adapter is rejecting calls "
                            "(likely an OAuth token expiry / revocation). "
                            "Re-authorize the workspace integration to "
                            "restore agent picks. Subsequent stage picks "
                            "in this hour are short-circuited to keep the "
                            "audit log readable.\n\n"
                            f"First error: {str(exc)[:400]}"
                        )[:2000],
                        payload={
                            "tracker_kind": resolved.kind,
                            "fsm_stage": state,
                            "error": str(exc)[:500],
                        },
                        status="new",
                        intake_handle=None,
                        intake_reason="tracker_outage",
                    )
                )
                await session.flush()
        except Exception as audit_exc:  # noqa: BLE001 — audit failure must not sink the response
            logger.warning(
                "tracker/next: audit write failed ws=%s err=%s",
                workspace_id,
                audit_exc,
            )
        return TaskResponseOut(
            ticket=None, fsm_stage=state, tracker_kind=resolved.kind
        )
    if not rows:
        return TaskResponseOut(
            ticket=None, fsm_stage=state, tracker_kind=resolved.kind
        )

    pick: dict[str, Any] | None = None
    skipped_orphans: list[dict[str, Any]] = []
    skipped_overlay: list[tuple[dict[str, Any], list[str]]] = []
    skipped_priority: list[tuple[dict[str, Any], str | None]] = []
    for row in rows:
        # ``project_id`` is surfaced by adapters that can populate it
        # (Linear today). Adapters that can't (Notion / Jira / GitHub
        # Issues) return ``None``; we accept those rows for backward
        # compatibility — orphan filtering only kicks in when the
        # adapter actually distinguishes "no project" from "unknown".
        project_id = row.get("project_id")
        if "project_id" in row and project_id is None:
            skipped_orphans.append(row)
            continue
        # ELS-84: overlay-frozen tickets — anything carrying a
        # ``needs:clarification`` or ``blocked*`` label means the
        # operator owes a reply. Skip silently; the next tick re-
        # picks once the label is cleared.
        matched_overlays = _matched_overlay_labels(row.get("labels") or [])
        if matched_overlays:
            skipped_overlay.append((row, matched_overlays))
            continue
        # Priority-gate exemptions:
        # 1. *Planning anchors* — anchors live in Drafts projects
        #    (``state='planning'``) by construction. Decomposition
        #    routines (wbs / architecture / qa_plan / planning_done)
        #    moving the chain forward is what *graduates* the project
        #    out of Drafts. Gating those on ``priority_state='active'``
        #    would deadlock the funnel.
        # 2. *needs:intake escape hatch* — reviewer-shaped routines
        #    (qa-reviewer / security-reviewer / retro / learning-
        #    capture) auto-file coverage tickets that need to enter
        #    the SDLC chain via ``task_intake`` regardless of where
        #    their parent project sits. Without this label they'd
        #    rot in the project's priority bucket and emit
        #    ``priority_skipped`` audit noise every cron tick.
        # The auto-onboard + the priority gate below still apply to
        # regular tickets.
        labels = row.get("labels") or []
        is_anchor = _is_planning_anchor(labels)
        needs_intake = _has_intake_label(labels)
        priority_exempt = is_anchor or needs_intake
        # ELS-80: WorkspaceProjectPriority gate — only ``active``
        # projects feed the picker.
        # ELS-92: when a ticket has a ``project_id`` but no priorities
        # row exists, **auto-onboard** the project as ``active`` and
        # let the picker take the ticket. The ticket is already
        # flowing through FSM stages — that's evidence enough of
        # operator intent (they made the project in Linear, made the
        # ticket, and the ticket reached an FSM stage). Forcing the
        # operator to also click "promote" in Ship's dashboard before
        # the agent picks up would defeat the "wraps your existing
        # tracker" promise. Navigator's drafting flow stays separate:
        # ``_tool_create_project`` writes the row explicitly with
        # ``state='planning'``, so this auto-onboard never overrides
        # an in-progress draft.
        if project_id is not None and not priority_exempt:
            priority_state = await _project_priority_state(
                session, workspace_id=workspace_id, project_id=str(project_id)
            )
            if priority_state is None:
                await _auto_onboard_linear_native_project(
                    session,
                    workspace_id=workspace_id,
                    auth=auth,
                    tracker_kind=resolved.kind,
                    fsm_stage=state,
                    project_id=str(project_id),
                    ticket_ref=str(row.get("id") or ""),
                )
                priority_state = "active"
            if priority_state != "active":
                skipped_priority.append((row, priority_state))
                continue
        pick = row
        break

    if skipped_orphans:
        await _record_orphan_skips(
            session,
            workspace_id=workspace_id,
            auth=auth,
            tracker_kind=resolved.kind,
            fsm_stage=state,
            orphans=skipped_orphans,
        )
    if skipped_overlay:
        await _record_overlay_skips(
            session,
            workspace_id=workspace_id,
            auth=auth,
            tracker_kind=resolved.kind,
            fsm_stage=state,
            skipped=skipped_overlay,
        )
    if skipped_priority:
        await _record_priority_skips(
            session,
            workspace_id=workspace_id,
            auth=auth,
            tracker_kind=resolved.kind,
            fsm_stage=state,
            skipped=skipped_priority,
        )

    if pick is None:
        # ELS-148 / A1: every picker filter that returns null here
        # leaves the `maybe_dispatch`-acquired project_lock dangling.
        # The agent CLI exits via EXIT_NO_TASK without calling
        # /finish, so the lock survives until its 24h TTL — blocking
        # every sibling ticket in the project. Release here based on
        # which filter caught the pinned ticket. Best-effort: noop
        # when the route was called without a pinned `ticket_ref`
        # (legacy non-pinned cron path; no fresh project_lock to
        # release in that path either).
        if ticket_ref:
            skipped_kind = None
            if any(str(r.get("id") or "") == ticket_ref for r in skipped_orphans):
                skipped_kind = "orphan_skipped"
            elif any(
                str(r.get("id") or "") == ticket_ref
                for r, _ in skipped_overlay
            ):
                skipped_kind = "overlay_frozen"
            elif any(
                str(r.get("id") or "") == ticket_ref
                for r, _ in skipped_priority
            ):
                skipped_kind = "priority_skipped"
            if skipped_kind is not None:
                await _release_project_lock_for_ticket(
                    session,
                    workspace_id=workspace_id,
                    resolved=resolved,
                    ticket_ref=ticket_ref,
                    reason=f"picker_{skipped_kind}",
                )
        return TaskResponseOut(
            ticket=None, fsm_stage=state, tracker_kind=resolved.kind
        )

    ticket_ref = str(pick.get("id") or "")

    # Refire cap (universal loop guard). When the same
    # ``(workspace, fsm_stage, ticket)`` triple has fired ``finish``
    # more than ``_REFIRE_CAP_LIMIT`` times in the cap window, the
    # breadcrumb idempotency that's supposed to prevent re-picks has
    # silently broken somewhere downstream (label not provisioned,
    # transition no-op, label rename, …). Stop handing the ticket
    # back to the routine, emit one ``agent_run.refire_capped`` audit
    # row + one ``InboxItem(type='blocker', intake_reason='refire_capped')``
    # letter (deduped against re-emission), and return ``ticket=None``
    # so the routine noops cleanly. Operator sees the letter, decides
    # whether to bump the cap or fix the breadcrumb root cause.
    if ticket_ref:
        fire_count = await _recent_finish_count_for_stage(
            session,
            workspace_id=workspace_id,
            fsm_stage=state,
            ticket_ref=ticket_ref,
            window=_REFIRE_CAP_WINDOW,
        )
        if fire_count >= _REFIRE_CAP_LIMIT:
            already_capped = await _recent_audit_exists(
                session,
                workspace_id=workspace_id,
                action="agent_run.refire_capped",
                target_kind="ticket",
                target_id=ticket_ref,
                window=_REFIRE_CAP_WINDOW,
            )
            if not already_capped:
                session.add(
                    AuditLog(
                        workspace_id=workspace_id,
                        actor_user_id=auth.user.id,
                        actor_token_id=auth.token.id if auth.token else None,
                        action="agent_run.refire_capped",
                        target_kind="ticket",
                        target_id=ticket_ref,
                        payload={
                            "tracker_kind": resolved.kind,
                            "fsm_stage": state,
                            "fire_count": fire_count,
                            "limit": _REFIRE_CAP_LIMIT,
                            "window_hours": int(
                                _REFIRE_CAP_WINDOW.total_seconds() // 3600
                            ),
                        },
                    )
                )
                session.add(
                    InboxItem(
                        workspace_id=workspace_id,
                        repo_id=None,
                        type="blocker",
                        title=(
                            f"Refire cap hit on {ticket_ref} at "
                            f"{state} — routine paused"
                        )[:300],
                        summary=(
                            f"The {state} routine has finished against "
                            f"{ticket_ref} {fire_count} times in the last "
                            f"{int(_REFIRE_CAP_WINDOW.total_seconds() // 3600)}h. "
                            "Agent tokens are being burnt without the "
                            "ticket advancing — usually because the "
                            "breadcrumb label that's supposed to exclude "
                            "the ticket on the next pick isn't being "
                            "added (provisioner gap, transition no-op, "
                            "label rename). The picker is now refusing "
                            "to hand this ticket back to the routine "
                            f"until the {int(_REFIRE_CAP_WINDOW.total_seconds() // 3600)}h "
                            "window elapses. Investigate the missing "
                            "breadcrumb or move the ticket past this "
                            "stage manually."
                        )[:2000],
                        payload={
                            "tracker_kind": resolved.kind,
                            "fsm_stage": state,
                            "ticket_ref": ticket_ref,
                            "fire_count": fire_count,
                            "url": str(pick.get("url") or "") or None,
                            # ELS-163 — recovery options as one-click
                            # pills in the Inbox Decision UI. The
                            # /decide endpoint posts each label as a
                            # comment on the source ticket; the
                            # operator is signalling intent — Ship
                            # doesn't auto-execute these yet (P2-2
                            # follow-up will wire side-effects). For
                            # now the comment is the contract.
                            "action_items": [
                                {
                                    "id": "retry-stage",
                                    "kind": "choice",
                                    "label": "Retry this stage",
                                    "hint": (
                                        "Clear refire cap and re-dispatch "
                                        f"{state}. Use only if you fixed "
                                        "whatever was making it bounce."
                                    ),
                                },
                                {
                                    "id": "send-back-to-dev",
                                    "kind": "choice",
                                    "label": "Send back to dev_implementation",
                                    "hint": (
                                        "Dev rewrites against fresh main. "
                                        "Use if the bounces are about "
                                        "broken code, not broken pipeline."
                                    ),
                                },
                                {
                                    "id": "pause-routine",
                                    "kind": "choice",
                                    "label": "Pause routine — I'll handle manually",
                                    "hint": (
                                        "Mark ack'd; ticket stays where it "
                                        "is. You move it past this stage "
                                        "by hand."
                                    ),
                                },
                            ],
                            "resolution_mode": "single_choice",
                        },
                        status="new",
                        category="failure",
                        priority=10,
                        intake_handle=None,
                        intake_reason="refire_capped",
                    )
                )
                await session.flush()
            # ELS-148 / A1: same leak as the picker null paths above —
            # refire-cap returns null without /finish, so the dispatcher's
            # project_lock dangles. Release it before exiting.
            await _release_project_lock_for_ticket(
                session,
                workspace_id=workspace_id,
                resolved=resolved,
                ticket_ref=ticket_ref,
                reason="picker_refire_capped",
            )
            return TaskResponseOut(
                ticket=None, fsm_stage=state, tracker_kind=resolved.kind
            )

    # ELS-86: stitch the parent project's body sections onto the task
    # so the SDLC agent sees the surrounding plan (Brief / WBS /
    # Architecture / Test architecture / Tasks). Best-effort —
    # tracker errors / projects without a body / tickets without a
    # project all degrade silently to ``project_context=None``; the
    # immediate ticket body in ``body`` already carries the
    # per-task brief. We pass ``project_id`` directly from the
    # ``list_tickets`` row (ELS-83 already projected it) so we don't
    # round-trip Linear a second time per pick.
    #
    # Decomposition exception: when the picked ticket IS the planning
    # anchor itself, the role's *job* is to read upstream sections in
    # full and produce the next one — a child-ticket-style 2KB-per-
    # section cap starves the developer stage in particular (it has
    # to enumerate every WBS slice into one child ticket per slice).
    # An anchor read returns the full canonical excerpt without per-
    # section caps; the overall body is bounded by Linear's project
    # body size in practice, and clipping that mid-WBS is exactly the
    # blocker we just observed.
    is_anchor_pick = _is_planning_anchor(pick.get("labels") or [])
    project_context = await _build_project_context_for_ticket(
        resolved=resolved,
        project_id=pick.get("project_id"),
        full=is_anchor_pick,
    )
    file_coordination_warning = await load_file_coordination_warning_from_audit(
        session,
        workspace_id=workspace_id,
        ticket_ref=ticket_ref,
    )
    # Open sibling-PR / file-coordination context is useful to every
    # role that touches the diff, not just the dev: the reviewer and
    # auto-merger weigh merge-order against overlapping open PRs too.
    # Surface it for any non-anchor pick.
    if file_coordination_warning is None and not is_anchor_pick:
        settings = get_settings()
        pick_project_id = pick.get("project_id")
        if settings.enable_file_overlap_warnings and pick_project_id:
            snapshot_fn = getattr(resolved.gateway, "get_ticket_snapshot", None)
            overlap = await build_file_coordination_warning(
                session,
                workspace_id=workspace_id,
                ticket_ref=ticket_ref,
                project_id=str(pick_project_id),
                tracker_kind=resolved.kind,
                snapshot_fn=snapshot_fn,
                settings=settings,
            )
            file_coordination_warning = overlap.warning_markdown

    body = pick.get("body") if isinstance(pick.get("body"), str) else None
    # Conversation context for EVERY role (not just the dev): the recent
    # SDLC verdict thread + operator hints. The dev gets it framed as
    # "feedback to address" (the dev_not_converging fix); reviewer /
    # validation / auto-merger get it as neutral "recent activity" so
    # they honour operator decisions and prior verdicts. Best-effort.
    if ticket_ref:
        feedback = await _fetch_reviewer_feedback_section(
            resolved=resolved,
            ticket_ref=ticket_ref,
            for_dev=(state == "dev_implementation"),
        )
        if feedback:
            body = f"{body}\n\n{feedback}" if body else feedback

    return TaskResponseOut(
        fsm_stage=state,
        tracker_kind=resolved.kind,
        ticket=TaskTicketOut(
            ticket_ref=ticket_ref,
            kind=resolved.kind,
            title=str(pick.get("title") or ""),
            body=body,
            url=str(pick["url"]) if pick.get("url") else None,
            labels=list(pick.get("labels") or []),
            state=str(pick.get("status") or "") or None,
            fsm_stage=state,
            project_context=project_context,
            file_coordination_warning=file_coordination_warning,
        ),
    )


def _collect_project_sections(payload: "FinishIn") -> list["ProjectSectionPatch"]:
    """Pull decomposition section patches from the finish payload.

    Agents can land the list in two equivalent places:

    1. ``project_sections`` as a top-level field on the finish JSON
       — the canonical wire shape Pydantic validates into typed
       ``ProjectSectionPatch`` instances at ingestion.
    2. Inside the catch-all ``payload`` dict as
       ``payload.project_sections`` — a tolerant fallback for agents
       that read ``payload`` in the role prompt as "the JSON body's
       payload" and nested it there. The wording in earlier role
       prompts was ambiguous and Claude (correctly, by its reading)
       sometimes places it under the dict.

    The fallback re-validates each entry through
    ``ProjectSectionPatch`` so the same length/shape rules apply
    regardless of where the agent wrote it; malformed entries are
    silently dropped (the audit trail will show the missing
    ``tracker:project_section:<X>`` action so the operator can spot
    it).
    """
    if payload.project_sections:
        return list(payload.project_sections)
    raw = (payload.payload or {}).get("project_sections")
    if not isinstance(raw, list) or not raw:
        return []
    out: list[ProjectSectionPatch] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(ProjectSectionPatch(**entry))
        except Exception:  # noqa: BLE001 — drop malformed silently
            continue
    return out


async def _release_project_lock_for_ticket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    resolved: Any,
    ticket_ref: str,
    reason: str,
) -> None:
    """Free the ``project:<linear_project_id>`` lock when a ticket's
    chain reaches a terminal-non-merge state.

    The dispatcher's per-project WIP cap holds the lock for 24h
    (``PROJECT_LOCK_TTL_S``) on the assumption that work continues
    until PR merge releases it. That assumption breaks when the
    chain stalls without a merge — outcome=blocked, =needs_clarification,
    =out_of_scope — and the lock then blocks every other ticket in
    the project for the remainder of its 24h TTL. Caught on
    Ship-on-Ship/ELS-142 2026-05-18: ELS-142 dev_implementation
    finished blocked at 00:35 (gh pr create failed on empty
    commits), lock held until next day; for 6h the backstop
    scan looped ``dispatch.project_busy`` for every other ticket
    in QA Debt. Release the lock on terminal-non-merge outcomes
    so siblings can dispatch.

    Best-effort: missing project_id, missing snapshot fn, tracker
    hiccup — all log and return without raising.
    """
    if not ticket_ref or resolved is None:
        return
    snap_fn = getattr(resolved.gateway, "get_ticket_snapshot", None)
    if snap_fn is None:
        return
    try:
        snap = await snap_fn(
            TicketRef(kind=resolved.kind, workspace_hint=None, id=ticket_ref)
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "project_lock release: snapshot failed ws=%s ticket=%s err=%s",
            workspace_id, ticket_ref, exc,
        )
        return
    project_id = (snap or {}).get("project_id")
    if not project_id:
        return
    deleted = (
        await session.execute(
            text(
                "DELETE FROM agent_dispatch_locks "
                "WHERE workspace_id = :ws AND key = :k RETURNING 1"
            ),
            {"ws": workspace_id, "k": f"project:{project_id}"},
        )
    ).all()
    if deleted:
        logger.info(
            "agent_run.finish (%s) → released project lock "
            "ws=%s ticket=%s project=%s",
            reason, workspace_id, ticket_ref, project_id,
        )
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="dispatch.project_lock_released",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "via": reason,
                    "project_id": str(project_id),
                },
            )
        )


async def _recent_finish_count_for_stage(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    fsm_stage: str,
    ticket_ref: str,
    window: timedelta,
) -> int:
    """Count CONSECUTIVE blocked ``agent_run.finish`` rows for one
    ``(workspace, fsm_stage, ticket_ref)`` triple within ``window``.

    The refire cap reads this before letting the picker hand a ticket
    back to its routine. Original v0 counted ALL finishes — but a real
    "fix → re-validate" iteration spends budget alongside the
    ``ready_next_step`` finish that already moved the ticket forward.
    Caught on askslayer/PAC-11 2026-05-17: 1 success + 2 obsolete
    blockeds = cap=3 hit on the very first legitimate retry. Wrong
    semantics.

    Correct semantics: count finishes **since the last
    ``ready_next_step``**. Three blocked finishes in a row genuinely
    means "this role can't get past whatever it's hitting — call a
    human". A mix with a success in the middle means the ticket
    progressed and we're on a fresh iteration.

    **Cross-stage reset (ELS-FSM 2026-05-19):** the pre-fix query
    filtered the audit pull by ``fsm_stage`` for ALL rows, so a
    ``validation`` finish with ``outcome=ready_next_step`` (which
    cascaded into ``code_review``) was invisible to the code_review
    cap counter — the cap stayed armed across the cross-stage
    success. Caught on Ship-on-Ship/ELS-7 2026-05-18: an
    auto_merge bounce chain hit cap=3 even though intervening
    validation+code_review finishes had `ready_next_step`. Fix:
    same-stage filter for blocked rows (cap is per-stage), but
    accept ANY-stage ``ready_next_step`` for the same ticket as a
    reset signal.

    Counts rows with both ``target_id == ticket_ref`` (canonical) and
    ``payload.ticket_ref == ticket_ref`` (legacy) — keeps the cap
    honest across a deploy boundary.
    """
    cutoff = datetime.now(timezone.utc) - window
    # Pull ANY-stage finish + clarification-resolved markers for this
    # (workspace, ticket) within the window, newest first. The walk
    # below decides per-row:
    #   - ``clarification_resolved`` → reset (operator answered)
    #   - any-stage ``ready_next_step`` → reset (chain advanced)
    #   - same-stage non-success → increment counter
    #   - cross-stage non-success → skip (different cap bucket)
    stmt = (
        select(
            AuditLog.action,
            AuditLog.payload["outcome"].astext,
            AuditLog.payload["fsm_stage"].astext,
            AuditLog.id,
        )
        .where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.action.in_(
                ("agent_run.finish", "agent_run.clarification_resolved")
            ),
            AuditLog.created_at >= cutoff,
            (
                (AuditLog.target_id == ticket_ref)
                | (AuditLog.payload["ticket_ref"].astext == ticket_ref)
            ),
        )
        .order_by(AuditLog.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    consecutive_blocked = 0
    for action, outcome, row_stage, _ in rows:
        # Either a successful finish (any stage — the chain moved
        # forward) or an operator-answered clarification resets the
        # counter. The pre-fix per-stage filter missed cross-stage
        # successes and over-counted the cap.
        if action == "agent_run.clarification_resolved":
            break
        if outcome == "ready_next_step":
            break
        if row_stage != fsm_stage:
            # Non-success finish on a different stage doesn't add to
            # this stage's cap; skip it without resetting either.
            continue
        consecutive_blocked += 1
    return consecutive_blocked


async def _recent_audit_exists(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    action: str,
    target_kind: str | None = None,
    target_id: str | None = None,
    window: timedelta,
) -> bool:
    """True iff any ``audit_log`` row matching the (workspace, action,
    target) filter was written within ``window``.

    Used by the picker's outage / priority-skip dedup paths so the
    first event in an outage records the breadcrumb and the subsequent
    cron-tick fan-out short-circuits without re-emitting. The query
    hits the ``(workspace_id, action, created_at)`` covering index so
    cost is bounded even on workspaces with bulky audit history.
    """
    cutoff = datetime.now(timezone.utc) - window
    stmt = select(AuditLog.id).where(
        AuditLog.workspace_id == workspace_id,
        AuditLog.action == action,
        AuditLog.created_at >= cutoff,
    )
    if target_kind is not None:
        stmt = stmt.where(AuditLog.target_kind == target_kind)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)
    return (await session.execute(stmt.limit(1))).first() is not None


def _has_intake_label(labels: list[Any]) -> bool:
    """True iff ``labels`` carries the auto-intake escape-hatch label.

    Reviewer-shaped routines (qa-reviewer, security-reviewer, retro,
    learning-capture) tag freshly opened tickets with ``needs:intake``
    so ``task_intake`` claims them next tick regardless of the parent
    project's priority state — otherwise auto-filed coverage tickets
    sit in Drafts/Parked projects and just generate ``priority_skipped``
    audit noise. Match is exact + case-sensitive: the literal
    namespaced label is the contract; arbitrary near-misses
    (``needs-intake``, ``intake``) shouldn't accidentally bypass the
    priority gate.
    """
    return any(
        isinstance(lbl, str) and lbl == _NEEDS_INTAKE_LABEL
        for lbl in labels
    )


def _is_planning_anchor(labels: list[Any]) -> bool:
    """Return True if ``labels`` contains the planning-anchor marker.

    Match is exact + case-sensitive — Linear adapter mints the label
    as the literal string :data:`_PLANNING_ANCHOR_LABEL`, and any
    other casing or near-match is the operator's hand-edit, not the
    minted anchor. Non-string entries skip silently so tracker rows
    with weird label shapes never crash the picker.
    """
    return any(
        isinstance(lbl, str) and lbl == _PLANNING_ANCHOR_LABEL
        for lbl in labels
    )


def _matched_overlay_labels(labels: list[str]) -> list[str]:
    """Return labels matching an overlay-freeze prefix (ELS-84).

    Match is case-insensitive — for each prefix ``p`` in
    :data:`OVERLAY_FREEZE_LABEL_PREFIXES`, a label ``l`` is a hit if
    ``l == p`` (exact), ``l.startswith(p + "-")`` (operator-friendly
    suffix like ``blocked-on-acme``), or ``l.startswith(p + ":")``
    (label-namespace suffix like ``blocked:foo``). Returns the
    *original* label strings so the audit row surfaces what the
    operator actually sees in Linear.
    """
    matched: list[str] = []
    for raw in labels:
        if not isinstance(raw, str):
            continue
        candidate = raw.strip().lower()
        if not candidate:
            continue
        for prefix in OVERLAY_FREEZE_LABEL_PREFIXES:
            if (
                candidate == prefix
                or candidate.startswith(prefix + "-")
                or candidate.startswith(prefix + ":")
            ):
                matched.append(raw)
                break
    return matched


async def _record_overlay_skips(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    auth: AuthContext,
    tracker_kind: str,
    fsm_stage: str,
    skipped: list[tuple[dict[str, Any], list[str]]],
) -> None:
    """Audit rows for tickets the overlay-label gate dropped (ELS-84).

    No inbox spam — the operator already owes a reply on these
    (``needs:clarification`` is *their* TODO, ``blocked`` is theirs to
    unblock). The audit log keeps the breadcrumb so debugging "why
    didn't the agent pick X" stays tractable.
    """
    for row, matched in skipped:
        ticket_ref = str(row.get("id") or "")
        if not ticket_ref:
            continue
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="agent_run.overlay_frozen_skipped",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "tracker_kind": tracker_kind,
                    "fsm_stage": fsm_stage,
                    "matched_labels": matched,
                    "title": str(row.get("title") or "")[:200],
                    "url": str(row.get("url") or "") or None,
                },
            )
        )
    await session.flush()


async def _auto_onboard_linear_native_project(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    auth: AuthContext,
    tracker_kind: str,
    fsm_stage: str,
    project_id: str,
    ticket_ref: str,
) -> None:
    """Insert a ``WorkspaceProjectPriority`` row for a project the
    picker has just encountered (ELS-92).

    Default state is ``'active'`` — the auto-onboard is a separate
    code path from ``_tool_create_project`` (which sets ``planning``
    for Drafts). Justification: the ticket is already flowing through
    FSM stages, which is evidence the operator intended this project
    to be live work; forcing them to click promote in Ship's
    dashboard before the agent picks up would defeat the
    "wraps your existing tracker" promise.

    Ordinal goes to MAX+1 so the auto-onboarded project sorts after
    everything saved without disturbing existing positions.

    Idempotent: callers must check ``_project_priority_state`` first
    and only invoke this when the lookup returned ``None``. If a row
    appears between the check and this insert (concurrent writer),
    the unique constraint
    ``uq_workspace_project_priorities_ws_native`` will fire — we let
    it propagate (the picker call returns 5xx and the cron retries).
    """
    max_ord = (
        await session.execute(
            sa_select(WorkspaceProjectPriority.ordinal)
            .where(WorkspaceProjectPriority.workspace_id == workspace_id)
            .order_by(WorkspaceProjectPriority.ordinal.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    next_ord = 0 if max_ord is None else int(max_ord) + 1
    session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace_id,
            project_native_id=project_id,
            ordinal=next_ord,
            state="active",
        )
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.project_auto_onboarded",
            target_kind="workspace_project_priority",
            target_id=project_id,
            payload={
                "tracker_kind": tracker_kind,
                "fsm_stage": fsm_stage,
                "project_id": project_id,
                "ticket_ref": ticket_ref,
                "ordinal": next_ord,
                "state": "active",
                "reason": "linear_native_first_encounter",
            },
        )
    )
    await session.flush()


async def _project_priority_state(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: str,
) -> str | None:
    """Return the ``state`` of the workspace's priority row for
    ``project_id``, or ``None`` when no row exists (project hasn't
    been onboarded through ``_tool_create_project``).

    The picker reads this to gate which projects feed agents — only
    ``'active'`` projects do; ``'planning'`` (Drafts) and ``'parked'``
    are operator holds.
    """
    row = (
        await session.execute(
            sa_select(WorkspaceProjectPriority.state).where(
                WorkspaceProjectPriority.workspace_id == workspace_id,
                WorkspaceProjectPriority.project_native_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return str(row)


async def _record_priority_skips(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    auth: AuthContext,
    tracker_kind: str,
    fsm_stage: str,
    skipped: list[tuple[dict[str, Any], str | None]],
) -> None:
    """Audit rows for tickets the priority gate dropped.

    No inbox spam here — ``planning`` / ``parked`` are deliberate
    operator holds, and a one-row-per-pick inbox would flood when a
    workspace has dozens of held projects. The audit log keeps the
    why-was-this-skipped breadcrumb so debugging is still tractable.

    Per-ticket dedup: the same ticket in the same priority bucket
    will fail the gate every cron tick × every routine (~9 ticks ×
    13 stages = >100 audit rows for a single held ticket per day).
    Drop the audit row when an identical (workspace, ticket) skip
    landed within :data:`_PRIORITY_SKIPPED_DEDUP_WINDOW`; first skip
    of the hour still records so the breadcrumb is fresh.
    """
    for row, priority_state in skipped:
        ticket_ref = str(row.get("id") or "")
        if not ticket_ref:
            continue
        already_logged = await _recent_audit_exists(
            session,
            workspace_id=workspace_id,
            action="agent_run.priority_skipped",
            target_kind="ticket",
            target_id=ticket_ref,
            window=_PRIORITY_SKIPPED_DEDUP_WINDOW,
        )
        if already_logged:
            continue
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="agent_run.priority_skipped",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "tracker_kind": tracker_kind,
                    "fsm_stage": fsm_stage,
                    "project_id": str(row.get("project_id") or ""),
                    "priority_state": priority_state,
                    "title": str(row.get("title") or "")[:200],
                    "url": str(row.get("url") or "") or None,
                    "reason": (
                        "no_priority_row"
                        if priority_state is None
                        else f"priority_state={priority_state}"
                    ),
                },
            )
        )
    await session.flush()


async def _record_orphan_skips(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    auth: AuthContext,
    tracker_kind: str,
    fsm_stage: str,
    orphans: list[dict[str, Any]],
) -> None:
    """Audit + inbox notification for tickets the picker dropped.

    One inbox row total per pick (not per orphan) so a single bad day
    doesn't flood the operator's inbox; the row's payload carries the
    list of skipped ticket refs so they can re-home them in bulk. The
    audit log gets one row per orphan for traceability.
    """
    refs = [
        str(row.get("id") or "")
        for row in orphans
        if row.get("id")
    ]
    refs = [r for r in refs if r]
    if not refs:
        return
    for row in orphans:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="agent_run.orphan_skipped",
                target_kind="ticket",
                target_id=str(row.get("id") or ""),
                payload={
                    "tracker_kind": tracker_kind,
                    "fsm_stage": fsm_stage,
                    "title": str(row.get("title") or "")[:200],
                    "url": str(row.get("url") or "") or None,
                    "reason": "no_project_id",
                },
            )
        )
    session.add(
        InboxItem(
            workspace_id=workspace_id,
            type="improvement",
            title=f"Orphan tickets skipped at stage {fsm_stage}"[:300],
            summary=(
                f"{len(refs)} ticket(s) at FSM stage {fsm_stage!r} have no "
                "tracker project attached and were skipped by the agent "
                "picker. Re-home them under a project (or close)."
            )[:2000],
            payload={
                "kind": "orphan_skipped",
                "tracker_kind": tracker_kind,
                "fsm_stage": fsm_stage,
                "ticket_refs": refs,
            },
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Parent-project context (ELS-86)
# ---------------------------------------------------------------------------


# Project body sections the SDLC startup context lifts from the
# planning anchor. Order matches the decomposition chain
# (BA → Tech-arch → QA-arch → Developer); rendering preserves it
# even when sections appear out of order in the body. ``Tasks`` is
# the WBS-children list — useful for sibling awareness.
_PROJECT_CONTEXT_SECTIONS: tuple[str, ...] = (
    "Brief",
    "WBS",
    "Architecture",
    "Test architecture",
    "Tasks",
)

# Per-section caps (architect review): a global 8KB cap clipped the
# last canonical section first (``Tasks`` — sibling awareness, the
# most useful for a child-ticket agent). Per-section caps make the
# truncation hit each block uniformly so no single section is
# starved when the body is large.
_PROJECT_CONTEXT_SECTION_CAPS: dict[str, int] = {
    "Brief": 2048,
    "WBS": 2048,
    "Architecture": 2048,
    "Test architecture": 1024,
    "Tasks": 2048,
}

# Hard outer cap as a defensive belt + suspenders. The sum of the
# per-section caps above is 9216; we round up to 10KB so a slightly-
# over-budget section doesn't push the total past the cap.
_PROJECT_CONTEXT_CAP_BYTES: int = 10 * 1024


def _truncate_section(lines: list[str], cap_bytes: int) -> list[str]:
    """Truncate a section body to ``cap_bytes`` UTF-8 bytes, line-aligned.

    We prefer dropping whole trailing lines rather than mid-line
    cuts so the markdown stays valid. If the *first* line alone
    exceeds the cap we hard-cut it and append a marker. Returns the
    possibly-shorter list of lines, with a ``…(truncated)`` line
    appended when truncation actually happened.
    """
    if cap_bytes <= 0:
        return lines
    out: list[str] = []
    used = 0
    for line in lines:
        line_bytes = len(line.encode("utf-8")) + 1  # +1 for the join newline
        if used + line_bytes > cap_bytes:
            break
        out.append(line)
        used += line_bytes
    if len(out) < len(lines):
        out.append("…(truncated)")
    return out


def _extract_project_sections(
    content: str,
    *,
    section_caps: dict[str, int] | None = None,
    overall_cap_bytes: int = _PROJECT_CONTEXT_CAP_BYTES,
) -> str | None:
    """Pull the canonical sections out of a project body.

    ``content`` is the project's markdown ``content`` field as
    returned by ``LinearTracker.get_project``. We pick lines that
    start with ``## <name>`` and emit each named block in canonical
    order. Heading match is **case-insensitive** so a role file that
    drifts to ``## brief`` (lowercase) doesn't silently drop the
    section — the canonical names in :data:`_PROJECT_CONTEXT_SECTIONS`
    are the recovery target.

    Falls back to a generic ``## Project description`` block when no
    canonical heading is present — projects filed by humans with
    free-form descriptions (e.g., "QA Debt — holding pen for test-
    coverage gaps") would otherwise hand the agent ``None`` and lose
    all parent-project context. Better to surface the operator's
    own words than to render the prompt without any project anchor.

    Each section is independently capped per :data:`_PROJECT_CONTEXT_SECTION_CAPS`
    so a runaway WBS doesn't starve the Tasks list (which is what a
    sibling-aware child-ticket agent most needs). The whole
    response is then capped at ``overall_cap_bytes`` as a defensive
    belt-and-suspenders limit.
    """
    if not content:
        return None
    caps = section_caps or _PROJECT_CONTEXT_SECTION_CAPS
    lines = content.splitlines()
    # Map case-insensitive heading → canonical name so a drifted
    # ``## wbs`` recovers to the canonical ``WBS`` slot.
    canon_by_lower = {
        s.lower(): s for s in _PROJECT_CONTEXT_SECTIONS
    }
    found: dict[str, list[str]] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip()
            if current is not None and buffer:
                found[current] = buffer
            buffer = []
            current = canon_by_lower.get(heading.lower())
            continue
        if current is not None:
            buffer.append(line)
    if current is not None and buffer:
        found[current] = buffer
    if not found:
        # No canonical sections — fall back to surfacing the
        # operator-written project description as-is under a generic
        # heading, truncated to the overall cap. This is the path
        # for non-decomposition projects (QA Debt, Tech Debt, ad-hoc
        # buckets) whose ``content`` is one paragraph rather than the
        # Brief / WBS / Architecture / Test architecture / Tasks
        # canon. The agent then has at least one sentence about what
        # this parent project is about, instead of None.
        stripped = content.strip()
        if not stripped:
            return None
        body_lines = stripped.splitlines()
        body_lines = _truncate_section(body_lines, overall_cap_bytes)
        return (
            "## Project description\n\n" + "\n".join(body_lines).rstrip()
        )

    parts: list[str] = []
    for section in _PROJECT_CONTEXT_SECTIONS:
        if section not in found:
            continue
        body_lines = found[section]
        # Drop trailing whitespace-only lines.
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        if not body_lines:
            continue
        body_lines = _truncate_section(body_lines, caps.get(section, 2048))
        parts.append(f"## {section}")
        parts.append("")
        parts.extend(body_lines)
        parts.append("")
    text = "\n".join(parts).rstrip()
    if not text:
        return None

    encoded = text.encode("utf-8")
    if len(encoded) <= overall_cap_bytes:
        return text
    truncated = encoded[:overall_cap_bytes].decode("utf-8", errors="ignore").rstrip()
    return truncated + "\n\n…(truncated)"


# ---------------------------------------------------------------------------
# Reviewer-feedback channel for re-dispatched dev runs.
#
# When a ticket cascades ``code_review (blocked) → dev_implementation``,
# the dev agent used to see only the original ticket body — never the
# reviewer's blocking comment. So it re-implemented the same brief, the
# reviewer re-blocked with the identical finding, and the ticket looped
# forever: the ``dev_not_converging`` failure mode. ``/tracker/next``
# now stitches the latest non-dev SDLC verdict (reviewer / validation /
# auto-merger) plus any operator hints posted after it onto the dev's
# task body so the agent knows exactly what to fix this run.
# ---------------------------------------------------------------------------

_OPERATOR_HINT_MARKERS: tuple[str, ...] = ("[Operator hint", "[Operator]")
_REVIEWER_FEEDBACK_CAP_BYTES = 6 * 1024


async def _fetch_reviewer_feedback_section(
    *, resolved, ticket_ref: str, for_dev: bool = True
) -> str | None:
    """Markdown ticket-conversation block for an agent pick, or ``None``
    when there's nothing to surface.

    Every SDLC role benefits from the recent verdict thread, not just
    the dev: the reviewer should honour an operator's decision and not
    re-flag a resolved finding, the auto-merger reads the reviewer's
    verdict, validation sees what the reviewer cared about. ``for_dev``
    only switches the framing — an imperative "fix this" block for the
    developer re-run vs a neutral "recent activity" context block for
    everyone else.

    Best-effort: one ``list_comments`` call. Any tracker error degrades
    to ``None`` — a missing context block must never block the run.

    A comment counts as a verdict when it carries a ``[Ship SDLC:role-…]``
    marker for any role **other than** ``role-developer`` (the dev's own
    finish comments are low-signal "Done." notes). We take the most
    recent such verdict, then append any operator hints posted after it
    (the operator is steering this specific re-run, so those win)."""
    list_fn = getattr(resolved.gateway, "list_comments", None)
    if list_fn is None:
        return None
    try:
        comments = await list_fn(
            TicketRef(kind=resolved.kind, workspace_hint=None, id=ticket_ref)
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never block the run
        logger.debug(
            "reviewer_feedback: list_comments failed ticket=%s err=%s",
            ticket_ref, exc,
        )
        return None
    if not comments:
        return None

    def _is_feedback(body: str) -> bool:
        return "[Ship SDLC:role-" in body and "role-developer" not in body

    # comments arrive oldest-first; scan back for the latest verdict.
    last_idx: int | None = None
    for i in range(len(comments) - 1, -1, -1):
        if _is_feedback(comments[i].body or ""):
            last_idx = i
            break
    if last_idx is None:
        return None

    parts: list[str] = [(comments[last_idx].body or "").strip()]
    for c in comments[last_idx + 1:]:
        body = (c.body or "").strip()
        if any(m in body for m in _OPERATOR_HINT_MARKERS):
            parts.append(body)

    joined = "\n\n---\n\n".join(p for p in parts if p)
    encoded = joined.encode("utf-8")
    if len(encoded) > _REVIEWER_FEEDBACK_CAP_BYTES:
        joined = encoded[:_REVIEWER_FEEDBACK_CAP_BYTES].decode(
            "utf-8", "ignore"
        ) + "\n\n…(truncated)"
    if for_dev:
        return (
            "## Reviewer feedback to address\n\n"
            "A previous review **blocked** this PR and sent it back to "
            "you. Fix every point below in this run — re-implementing "
            "the original brief without addressing these will just get "
            "blocked again.\n\n"
            f"{joined}"
        )
    return (
        "## Recent ticket activity\n\n"
        "Prior SDLC verdicts and operator notes on this ticket. Take "
        "them into account — don't re-flag a finding a previous pass "
        "already resolved, and honour any operator decision recorded "
        "below.\n\n"
        f"{joined}"
    )


async def _build_project_context_for_ticket(
    *,
    resolved,  # ResolvedTracker — avoid circular import
    project_id: str | None,
    overall_cap_bytes: int = _PROJECT_CONTEXT_CAP_BYTES,
    full: bool = False,
) -> str | None:
    """Fetch the project body and return its canonical-section excerpt.

    ``project_id`` comes straight from the ``list_tickets`` row
    (ELS-83 already projects it), so we do **not** round-trip Linear
    a second time via ``get_ticket_snapshot`` — that was the
    architect's blocker on the original ELS-86 design. ``None``
    here (orphan ticket / non-Linear adapter that doesn't surface
    project_id) skips the lookup cleanly.

    ``full=True`` is the anchor read path: caps are lifted both per-
    section and overall, so the role that owns slicing the WBS into
    children sees every slice rather than the first ~5 (the tasks
    routine's developer was hitting that exact cliff). For SDLC
    child-ticket picks ``full=False`` is the safe default — the
    section caps preserve sibling-Tasks visibility against a runaway
    upstream section.

    All steps are best-effort: any failure (tracker that can't model
    projects, unauthenticated, network 5xx, project deleted) returns
    ``None`` so the picker can still hand the agent a task. Errors
    are debug-logged so an operator chasing a missing context block
    can find the trace.
    """
    if not project_id:
        return None
    get_project_fn = getattr(resolved.gateway, "get_project", None)
    if get_project_fn is None:
        return None
    pid = str(project_id)
    try:
        project = await get_project_fn(pid, issues_limit=10)
    except TypeError:
        # Adapters whose ``get_project`` doesn't take ``issues_limit``
        # — fall back to a positional-only call.
        try:
            project = await get_project_fn(pid)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "project_context: get_project failed pid=%s err=%s",
                pid,
                exc,
            )
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "project_context: get_project failed pid=%s err=%s",
            pid,
            exc,
        )
        return None
    content = project.get("content") if isinstance(project, dict) else None
    if not isinstance(content, str):
        return None
    if full:
        # Anchor read: skip the per-section caps. Pass an open-ended
        # overall cap (max signed int range from the validator's view
        # is more than enough) and per-section caps that all clear the
        # body's actual length so ``_truncate_section`` becomes a
        # no-op. The canonical extraction still happens — heading
        # ordering + non-canonical-section drop — so the agent doesn't
        # have to scan a 50KB body to find ``## WBS``.
        big_caps = {s: 1 << 20 for s in _PROJECT_CONTEXT_SECTIONS}
        return _extract_project_sections(
            content,
            section_caps=big_caps,
            overall_cap_bytes=1 << 20,
        )
    return _extract_project_sections(
        content, overall_cap_bytes=overall_cap_bytes
    )


@router.post("/tracker/transition", response_model=WriteOut)
async def transition_ticket(
    workspace_id: uuid.UUID,
    payload: TransitionIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WriteOut:
    """Move ``payload.ticket_ref`` to ``payload.to_state`` (FSM stage).

    The vendor adapter knows how to map the abstract Ship FSM stage
    (``ba_requirements`` etc.) to its native state — Linear status
    name, etc. This is the only place that mapping happens, so CLI
    doesn't need to grow per-vendor logic.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "no_tracker_bound"},
        )

    ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
    if payload.comment:
        await resolved.gateway.comment(ref, body=payload.comment)
    await resolved.gateway.transition(ref, to_state=payload.to_state)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.transition",
            target_kind="ticket",
            target_id=payload.ticket_ref,
            payload={
                "tracker_kind": resolved.kind,
                "from_state": payload.from_state,
                "to_state": payload.to_state,
                "had_comment": bool(payload.comment),
            },
        )
    )
    await session.flush()
    return WriteOut(ok=True, tracker_kind=resolved.kind)


@router.post("/tracker/comment", response_model=WriteOut)
async def comment_ticket(
    workspace_id: uuid.UUID,
    payload: CommentIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WriteOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "no_tracker_bound"},
        )
    ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
    await resolved.gateway.comment(ref, body=payload.body)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.comment",
            target_kind="ticket",
            target_id=payload.ticket_ref,
            payload={
                "tracker_kind": resolved.kind,
                "body_chars": len(payload.body),
            },
        )
    )
    await session.flush()
    return WriteOut(ok=True, tracker_kind=resolved.kind)


@router.post("/inbox/items", response_model=WriteOut)
async def post_inbox_item(
    workspace_id: uuid.UUID,
    payload: InboxItemIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> WriteOut:
    """Drop a free-form item into the workspace inbox.

    Used by:
      - context-free routines (daily digest, learning capture) that
        don't transition tickets — they leave their output as an
        operator-facing inbox row.
      - ``shipctl run`` when the agent's state is ``blocked`` or
        ``human_validation`` — captures the question or the missing
        prerequisite as an inbox item the operator can act on.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    if payload.type == "exception":
        record_inbox_exception_breadcrumb(
            source="agent_run.inbox_item",
            title=payload.title,
            ticket_ref=payload.ticket_ref,
        )
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="agent_run.inbox_item",
                target_kind="inbox_item",
                target_id=None,
                payload={
                    "type": payload.type,
                    "title": payload.title[:300],
                    "ticket_ref": payload.ticket_ref,
                    "breadcrumb_only": True,
                },
            )
        )
        await session.flush()
        return WriteOut(ok=True, note="exception recorded (no inbox row)")
    # Build the row's payload bag. ``body`` (markdown for the preview
    # pane) lives here so the InboxItem schema can keep ``summary``
    # capped at 2KB for the list view. When the agent didn't pass an
    # explicit summary, derive a short one from the body so the list
    # row isn't blank.
    item_payload: dict[str, Any] = {
        **payload.payload,
        "ticket_ref": payload.ticket_ref,
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }
    body_text = (payload.body or "").strip() or None
    if body_text:
        item_payload["body"] = body_text
    summary_text = payload.summary
    if summary_text is None and body_text:
        summary_text = body_text[:200]
    item = InboxItem(
        workspace_id=workspace_id,
        repo_id=None,
        type=payload.type,
        title=payload.title[:300],
        summary=(summary_text or "")[:2000] or None,
        payload=item_payload,
        status="new",
        intake_handle=None,
        intake_reason="agent_run",
    )
    session.add(item)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.inbox_item",
            target_kind="inbox_item",
            target_id=None,
            payload={
                "type": payload.type,
                "title": payload.title[:300],
                "ticket_ref": payload.ticket_ref,
            },
        )
    )
    await session.flush()
    return WriteOut(ok=True, note=f"inbox item created (type={payload.type})")


class EnvSeparationWarningOut(BaseModel):
    handle: str
    project_id: str
    project_name: str


class EnvSeparationAckIn(BaseModel):
    handle: str = Field(min_length=8, max_length=64)


@router.get(
    "/agent-runs/env-separation-warnings",
    response_model=list[EnvSeparationWarningOut],
)
async def list_env_separation_warnings(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[EnvSeparationWarningOut]:
    """Pending first-run env-separation modals for this workspace."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found")
    pending = list((workspace.settings or {}).get(ENV_SEPARATION_PENDING_KEY) or [])
    return [
        EnvSeparationWarningOut(
            handle=str(entry.get("handle") or ""),
            project_id=str(entry.get("project_id") or ""),
            project_name=str(entry.get("project_name") or ""),
        )
        for entry in pending
        if entry.get("handle")
    ]


@router.post("/agent-runs/env-separation-warnings/ack", response_model=WriteOut)
async def ack_env_separation_warning(
    workspace_id: uuid.UUID,
    payload: EnvSeparationAckIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> WriteOut:
    """Mark a project env-separation warning as acknowledged."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found")

    settings = dict(workspace.settings or {})
    pending = [
        e
        for e in (settings.get(ENV_SEPARATION_PENDING_KEY) or [])
        if e.get("handle") != payload.handle
    ]
    acknowledged = list(settings.get(ENV_SEPARATION_ACK_KEY) or [])
    if payload.handle not in acknowledged:
        acknowledged.append(payload.handle)
    settings[ENV_SEPARATION_PENDING_KEY] = pending
    settings[ENV_SEPARATION_ACK_KEY] = acknowledged
    workspace.settings = settings
    flag_modified(workspace, "settings")
    await session.flush()
    return WriteOut(ok=True, note="env separation warning acknowledged")


# ---------------------------------------------------------------------------
# PR cache reconciliation — admin-only sync against GitHub
# ---------------------------------------------------------------------------


class PrCacheSyncOut(BaseModel):
    """Summary of a one-shot PR-cache reconciliation against GitHub.

    Webhook-driven caches occasionally drift — a missed merge event,
    a redelivery the App didn't process during a deploy, etc. Run this
    when the dashboard's stuck-work count looks suspicious; it walks
    every cached PR currently in ``state='open'`` and re-fetches the
    truth from GitHub. PRs that are actually merged (or closed) get
    their cache row updated, which lets the next dashboard refresh
    auto-dismiss the stale ``stuck`` inbox items.
    """

    ok: bool = True
    checked: int
    updated: int
    skipped_no_token: int
    sample_updates: list[str] = Field(default_factory=list)


@router.post("/admin/sync-pull-requests", response_model=PrCacheSyncOut)
async def post_sync_pull_requests(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PrCacheSyncOut:
    """Reconcile cached PullRequest rows against GitHub.

    For each cached PR in ``state='open'`` we round-trip
    ``GET /repos/{owner}/{name}/pulls/{number}`` with the App's
    installation token and overwrite the cache columns
    (``state`` / ``merged`` / ``merged_at`` / ``closed_at`` /
    ``updated_at_external``). The dashboard endpoint's stuck-PR
    reconciliation then dismisses any ``stuck`` inbox items whose
    underlying PR turned out to be merged.

    Best-effort per-row: a transient 404 / 5xx is logged but doesn't
    fail the whole sync. Workspaces without a GitHub App installation
    skip the call cleanly (``skipped_no_token``).

    Admin-only. The route returns a summary so the operator running
    it from a script can see how many rows were touched.
    """
    import httpx

    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.integrations.github.app_auth import (
        fetch_installation_token,
    )

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    open_prs = (
        await session.execute(
            select(PullRequest).where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.state == "open",
            )
        )
    ).scalars().all()
    if not open_prs:
        return PrCacheSyncOut(ok=True, checked=0, updated=0, skipped_no_token=0)

    # Resolve installation_id per repo_id once. Same workspace can have
    # multiple repos under different installations (org + personal).
    repo_ids = {pr.repo_id for pr in open_prs if pr.repo_id is not None}
    install_by_repo: dict[uuid.UUID, int] = {}
    if repo_ids:
        rows = (
            await session.execute(
                select(WorkspaceRepo.id, GitHubInstallation.installation_id)
                .join(
                    GitHubInstallation,
                    WorkspaceRepo.installation_id == GitHubInstallation.id,
                )
                .where(WorkspaceRepo.id.in_(repo_ids))
            )
        ).all()
        for row in rows:
            install_by_repo[row[0]] = int(row[1])

    updated = 0
    skipped_no_token = 0
    sample: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        for pr in open_prs:
            installation_id = install_by_repo.get(pr.repo_id) if pr.repo_id else None
            if installation_id is None:
                skipped_no_token += 1
                continue
            try:
                token = await fetch_installation_token(
                    installation_id, settings=settings, client=client
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pr-sync: token mint failed installation=%s err=%s",
                    installation_id,
                    exc,
                )
                skipped_no_token += 1
                continue

            url = (
                f"https://api.github.com/repos/{pr.repo_full_name}/pulls/"
                f"{pr.number}"
            )
            try:
                response = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pr-sync: GET failed url=%s err=%s",
                    url,
                    exc,
                )
                continue
            if response.status_code != 200:
                logger.warning(
                    "pr-sync: %s %s on %s",
                    response.status_code,
                    response.reason_phrase,
                    url,
                )
                continue
            data = response.json()
            new_state = (data.get("state") or "open").lower()
            merged = bool(data.get("merged"))
            if merged and new_state == "closed":
                new_state = "merged"
            merged_at = _parse_iso_or_none(data.get("merged_at"))
            closed_at = _parse_iso_or_none(data.get("closed_at"))
            updated_at_external = _parse_iso_or_none(data.get("updated_at"))

            drifted = (
                pr.state != new_state
                or pr.merged != merged
                or (merged_at is not None and pr.merged_at != merged_at)
            )
            if drifted:
                pr.state = new_state
                pr.merged = merged
                if merged_at is not None:
                    pr.merged_at = merged_at
                if closed_at is not None:
                    pr.closed_at = closed_at
                if updated_at_external is not None:
                    pr.updated_at_external = updated_at_external
                updated += 1
                if len(sample) < 10:
                    sample.append(
                        f"#{pr.number} ({pr.repo_full_name}) "
                        f"open → {new_state}"
                    )

    if updated:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="agent_run.pr_cache_synced",
                target_kind="workspace",
                target_id=str(workspace_id),
                payload={
                    "checked": len(open_prs),
                    "updated": updated,
                    "skipped_no_token": skipped_no_token,
                    "sample": sample,
                },
            )
        )
        await session.flush()

    return PrCacheSyncOut(
        ok=True,
        checked=len(open_prs),
        updated=updated,
        skipped_no_token=skipped_no_token,
        sample_updates=sample,
    )


class OrphanTicketRow(BaseModel):
    ticket_ref: str
    title: str
    state: str | None
    labels: list[str] = Field(default_factory=list)
    description: str | None = None
    url: str | None = None
    fsm_stage_seen: str | None = None  # which stage's picker dropped it


class TrackerProjectRow(BaseModel):
    id: str
    name: str
    state: str | None = None
    url: str | None = None


class OrphanAuditOut(BaseModel):
    """Side-channel admin view for the orphan-ticket cleanup pass."""

    tickets: list[OrphanTicketRow]
    projects: list[TrackerProjectRow]


@router.get("/admin/orphan-tickets", response_model=OrphanAuditOut)
async def get_orphan_tickets(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> OrphanAuditOut:
    """List every open ticket the workspace's tracker has without a
    project association, plus the existing project list.

    The earlier audit-log walk only surfaced tickets the picker had
    actively tried (limited by which FSM stages the cron rotated
    through), so the operator could see e.g. ELS-17/18/.../27 but
    not orphans the picker hadn't reached yet. This now queries
    Linear directly via ``list_orphan_tickets`` (filter
    ``project: {null: true}``) so the cleanup view is comprehensive
    rather than picker-driven.
    """
    from sqlalchemy import desc as sa_desc

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id
    )
    if resolved is None:
        return OrphanAuditOut(tickets=[], projects=[])

    # Map ticket_ref → most-recent fsm_stage seen by the picker, so
    # the operator can see "where the agent last saw it" alongside
    # the row. Best-effort — empty audit log just means no annotation.
    rows = (
        await session.execute(
            sa_select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.orphan_skipped",
            )
            .order_by(sa_desc(AuditLog.created_at))
            .limit(500)
        )
    ).scalars().all()
    fsm_stage_by_ref: dict[str, str] = {}
    for r in rows:
        ticket_ref = (r.target_id or "").strip()
        if ticket_ref and ticket_ref not in fsm_stage_by_ref:
            stage = (r.payload or {}).get("fsm_stage")
            if stage:
                fsm_stage_by_ref[ticket_ref] = stage

    tickets_out: list[OrphanTicketRow] = []
    list_orphans_fn = getattr(resolved.gateway, "list_orphan_tickets", None)
    if list_orphans_fn is not None:
        try:
            orphans = await list_orphans_fn(limit=200)
        except Exception as exc:  # noqa: BLE001 — surface partial result
            logger.warning(
                "orphan-tickets: list_orphan_tickets failed ws=%s err=%s",
                workspace_id,
                exc,
            )
            orphans = []
        for row in orphans:
            ref = row.get("ticket_ref") or ""
            tickets_out.append(
                OrphanTicketRow(
                    ticket_ref=ref,
                    title=row.get("title") or "",
                    state=row.get("state"),
                    labels=list(row.get("labels") or []),
                    description=(row.get("description") or "")[:1500] or None,
                    url=row.get("url"),
                    fsm_stage_seen=fsm_stage_by_ref.get(ref),
                )
            )

    projects_out: list[TrackerProjectRow] = []
    list_projects_fn = getattr(resolved.gateway, "list_projects", None)
    if list_projects_fn is not None:
        try:
            projects = await list_projects_fn(limit=100)
        except (NotImplementedError, ValueError, Exception):  # noqa: BLE001
            projects = []
        for p in projects or []:
            projects_out.append(
                TrackerProjectRow(
                    id=str(p.get("id") or ""),
                    name=str(p.get("name") or ""),
                    state=p.get("state"),
                    url=p.get("url"),
                )
            )

    return OrphanAuditOut(tickets=tickets_out, projects=projects_out)


class TicketActionIn(BaseModel):
    """One operator-driven action on an orphan ticket.

    ``cancel`` transitions the ticket to ``Canceled`` (with an optional
    audit comment); ``assign`` writes ``project_id`` onto the ticket so
    the picker stops orphan-skipping it on the next tick.
    """

    ticket_ref: str = Field(min_length=1, max_length=128)
    action: Literal["cancel", "assign"]
    project_id: str | None = None
    comment: str | None = Field(default=None, max_length=2000)


class TicketActionOut(BaseModel):
    ok: bool = True
    ticket_ref: str
    action: str
    note: str | None = None


class RelabelStagesIn(BaseModel):
    """Surgical add/remove of FSM stage labels on a ticket.

    Operator escape hatch for fixing a stuck breadcrumb state when a
    prior buggy run wrote the wrong stage label (or when the
    operator wants to fast-forward / rewind a ticket through the
    chain manually).

    Both lists accept Ship FSM stage names (``task_intake``,
    ``ba_requirements``, …). The bound tracker resolves them to native
    label ids; an unknown stage 422s.

    ``set_state`` is an optional Linear workflow-state name (``Todo``
    / ``In Progress`` / ``Backlog`` / ``Done`` / ``Canceled``). When
    set, the endpoint also moves the ticket's workflow state via the
    legacy literal-name path. Useful for resetting state after a
    transition we want to undo (e.g. reverting from In Progress back
    to Todo so a prior FSM picker can re-fire).
    """

    ticket_ref: str = Field(min_length=1, max_length=128)
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    set_state: str | None = Field(default=None, max_length=64)


class RelabelStagesOut(BaseModel):
    ticket_ref: str
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    state_changed_to: str | None = None


class TicketCommentSnapshot(BaseModel):
    """One comment row in ``GET /admin/ticket-snapshot``."""

    id: str | None = None
    body: str = ""
    author: str | None = None
    created_at: str | None = None


class TicketSnapshotOut(BaseModel):
    """Full read-back of a ticket: title + description + state + labels +
    comments (oldest first). Operator-driven diff tool — used to
    capture before/after a stage agent runs without a separate
    Linear-side query.
    """

    ticket_ref: str
    title: str | None = None
    description: str | None = None
    url: str | None = None
    state: str | None = None
    labels: list[str] = Field(default_factory=list)
    project_id: str | None = None
    comments: list[TicketCommentSnapshot] = Field(default_factory=list)


@router.post("/admin/ticket-action", response_model=TicketActionOut)
async def post_ticket_action(
    workspace_id: uuid.UUID,
    payload: TicketActionIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TicketActionOut:
    """One-shot action on an orphan ticket: cancel or assign-to-project."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id
    )
    if resolved is None:
        raise HTTPException(
            status_code=422, detail="no_tracker_bound"
        )

    ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)

    if payload.action == "cancel":
        try:
            await resolved.gateway.transition(ref, to_state="Canceled")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if payload.comment:
            try:
                await resolved.gateway.comment(ref, body=payload.comment)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ticket-action: cancel comment failed ref=%s err=%s",
                    payload.ticket_ref,
                    exc,
                )
        note = "cancelled"
    elif payload.action == "assign":
        if not payload.project_id:
            raise HTTPException(
                status_code=422, detail="project_id required for assign"
            )
        update_fn = getattr(resolved.gateway, "update_ticket", None)
        if update_fn is None:
            raise HTTPException(
                status_code=422,
                detail="tracker does not support project assignment",
            )
        # Snapshot the current state BEFORE the project assign so the
        # state-sync helper knows whether to flip Todo → Backlog (parked
        # project) or Backlog → Todo (active project). Falls back to
        # ``None`` if the snapshot probe fails — the helper handles a
        # missing prior state safely.
        prior_state: str | None = None
        snap_fn = getattr(resolved.gateway, "get_ticket_snapshot", None)
        ticket_uuid: str | None = None
        if snap_fn is not None:
            try:
                snap = await snap_fn(ref)
                if snap:
                    prior_state = snap.get("state")
                    ticket_uuid = snap.get("id") or ref.id
            except Exception:  # noqa: BLE001 — best effort
                pass
        if not ticket_uuid:
            ticket_uuid = ref.id

        try:
            await update_fn(ref, project_id=payload.project_id)
        except TypeError as exc:
            # Adapter that doesn't accept ``project_id`` kwarg.
            raise HTTPException(
                status_code=422,
                detail=f"adapter cannot set project_id: {exc}",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if payload.comment:
            try:
                await resolved.gateway.comment(ref, body=payload.comment)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ticket-action: assign comment failed ref=%s err=%s",
                    payload.ticket_ref,
                    exc,
                )

        # Bring the ticket's Linear state into line with the project's
        # priority bucket. Active project → Backlog→Todo; parked /
        # planning → Todo→Backlog. Best-effort — a tracker hiccup
        # logs and audits but doesn't fail the assign.
        from backend.app.services.agent.project_state_sync import (
            apply_project_state_to_ticket,
        )
        await apply_project_state_to_ticket(
            session,
            workspace_id=workspace_id,
            project_id=payload.project_id,
            ticket_ref=payload.ticket_ref,
            ticket_uuid=str(ticket_uuid),
            current_linear_state=prior_state,
            gateway=resolved.gateway,
            tracker_kind=resolved.kind,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
        )

        note = f"assigned to {payload.project_id}"
    else:
        raise HTTPException(status_code=422, detail="unknown action")

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action=f"agent_run.ticket_{payload.action}",
            target_kind="ticket",
            target_id=payload.ticket_ref,
            payload={
                "action": payload.action,
                "project_id": payload.project_id,
                "comment": payload.comment,
            },
        )
    )
    await session.flush()
    return TicketActionOut(
        ok=True, ticket_ref=payload.ticket_ref, action=payload.action, note=note
    )


def _parse_iso_or_none(value: object) -> "datetime | None":
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class CreateTicketIn(BaseModel):
    """Payload an agent posts (via ``shipctl tracker create-ticket``)
    to file a ticket in the workspace's bound tracker, attached to a
    specific project.

    Reviewer routines (tech-reviewer / qa-reviewer / security-officer)
    use this to file findings into their dedicated holding-pen
    project. Going through Ship instead of Linear MCP directly keeps
    the credentials right — Cursor's MCP often holds a different
    org's PAT than the workspace under audit, and writes there land
    in the wrong inbox.
    """

    project_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=32 * 1024)
    labels: list[str] = Field(default_factory=list, max_length=20)
    priority: int | None = Field(default=None, ge=0, le=4)


class CreateTicketOut(BaseModel):
    ok: bool = True
    ticket_ref: str
    url: str | None = None


@router.post("/tracker/tickets", response_model=CreateTicketOut)
async def post_create_ticket(
    workspace_id: uuid.UUID,
    payload: CreateTicketIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateTicketOut:
    """Create a tracker issue inside ``project_id``.

    Used by the reviewer routines for finding-driven ticket creation.
    Idempotency / dedup is the caller's responsibility — list the
    project's open tickets first via
    ``GET /tracker/projects/{id}/tickets`` and skip if a matching
    finding already has an open ticket.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id
    )
    if resolved is None:
        raise HTTPException(status_code=422, detail="no_tracker_bound")
    create_fn = getattr(resolved.gateway, "create_ticket", None)
    if create_fn is None:
        raise HTTPException(
            status_code=422,
            detail="tracker does not support ticket creation",
        )
    # Reviewer-shaped routines (qa-reviewer, security-reviewer, retro,
    # learning-capture) tag freshly opened coverage tickets with
    # ``audit:auto`` to mark them as agent-authored. Server side adds
    # ``needs:intake`` to the label set so ``task_intake`` claims them
    # next tick regardless of the parent project's priority bucket —
    # without this, auto-filed tickets sit in Drafts/Parked projects
    # and emit ``priority_skipped`` audit noise on every cron tick.
    # The label is dropped at the first ``task_intake`` transition so
    # the ticket inherits its parent project's normal priority gate
    # from then on.
    augmented_labels = list(payload.labels or [])
    if "audit:auto" in augmented_labels and "needs:intake" not in augmented_labels:
        augmented_labels.append("needs:intake")
    try:
        created = await create_fn(
            title=payload.title,
            body=payload.body,
            labels=augmented_labels or None,
            project_id=payload.project_id,
            priority=payload.priority,
        )
    except TypeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"adapter doesn't support kwargs: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.ticket_created",
            target_kind="ticket",
            target_id=created.display_id,
            payload={
                "project_id": payload.project_id,
                "title": payload.title[:200],
                "labels": list(payload.labels or []),
                "priority": payload.priority,
            },
        )
    )

    # New tickets land in Linear's default state (typically Backlog),
    # which is correct when the project is Parked / Drafts. For active
    # projects we want the ticket in Todo so the picker can take it on
    # the next tick. Best-effort — adapter / tracker hiccups log + audit
    # but never fail the create.
    from backend.app.services.agent.project_state_sync import (
        apply_project_state_to_ticket,
    )
    await apply_project_state_to_ticket(
        session,
        workspace_id=workspace_id,
        project_id=payload.project_id,
        ticket_ref=created.display_id,
        ticket_uuid=str(created.ref.id),
        current_linear_state=None,  # freshly created — let the helper transition
        gateway=resolved.gateway,
        tracker_kind=resolved.kind,
        actor_user_id=auth.user.id,
        actor_token_id=auth.token.id if auth.token else None,
    )

    await session.flush()
    return CreateTicketOut(
        ok=True, ticket_ref=created.display_id, url=created.url or None
    )


class ProjectTicketRow(BaseModel):
    ticket_ref: str
    title: str
    state: str | None
    url: str | None = None
    labels: list[str] = Field(default_factory=list)


class ProjectTicketsOut(BaseModel):
    project_id: str
    tickets: list[ProjectTicketRow]


@router.get(
    "/tracker/projects/{project_id}/tickets",
    response_model=ProjectTicketsOut,
)
async def get_project_tickets(
    workspace_id: uuid.UUID,
    project_id: str,
    open_only: bool = True,
    limit: int = 100,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProjectTicketsOut:
    """List tickets currently in ``project_id``.

    Reviewer routines call this for dedup before filing a new finding —
    "is there already an open ticket about this CVE / coverage gap?".
    Defaults to ``open_only=True`` so closed history doesn't bloat the
    response.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id
    )
    if resolved is None:
        return ProjectTicketsOut(project_id=project_id, tickets=[])

    list_fn = getattr(resolved.gateway, "list_project_tickets", None)
    if list_fn is None:
        return ProjectTicketsOut(project_id=project_id, tickets=[])
    try:
        rows = await list_fn(
            project_id=project_id, open_only=open_only, limit=limit
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project tickets list failed ws=%s project=%s err=%s",
            workspace_id,
            project_id,
            exc,
        )
        rows = []
    return ProjectTicketsOut(
        project_id=project_id,
        tickets=[
            ProjectTicketRow(
                ticket_ref=str(r.get("identifier") or r.get("id") or ""),
                title=str(r.get("title") or ""),
                state=r.get("state"),
                url=r.get("url"),
                labels=list(r.get("labels") or []),
            )
            for r in rows
        ],
    )


class FindOrCreateProjectIn(BaseModel):
    """Payload for ``shipctl project find-or-create`` — backs the
    reviewer-routine routing flow (tech-reviewer → "Tech Debt" project,
    qa-reviewer → "QA Debt", security-officer → "Security"). Idempotent
    on case-insensitive name match against the workspace's tracker.
    """

    name: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=32 * 1024)
    description: str | None = Field(default=None, max_length=240)


class FindOrCreateProjectOut(BaseModel):
    ok: bool = True
    created: bool
    project: dict[str, Any]


@router.post(
    "/projects/find-or-create",
    response_model=FindOrCreateProjectOut,
)
async def post_find_or_create_project(
    workspace_id: uuid.UUID,
    payload: FindOrCreateProjectIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FindOrCreateProjectOut:
    """Find a project on the bound tracker by name or create one.

    The first run for a routine ("Tech Debt", "QA Debt", "Security")
    creates the project + Drafts priorities row; every subsequent run
    short-circuits on the case-insensitive name match. Avoids the
    list-then-create race the agent would otherwise need to manage on
    its own.
    """
    from backend.app.services.projects_lookup import (
        ProjectsLookupError,
        find_or_create_project_by_name,
    )

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    try:
        outcome = await find_or_create_project_by_name(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            name=payload.name,
            body=payload.body,
            description=payload.description,
        )
    except ProjectsLookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.project_find_or_create",
            target_kind="tracker_project",
            target_id=None,
            payload={
                "name": payload.name,
                "created": outcome.created,
                "project_id": outcome.project.get("id"),
            },
        )
    )
    await session.flush()
    return FindOrCreateProjectOut(
        ok=True, created=outcome.created, project=outcome.project
    )


async def _perform_auto_merge(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    resolved: Any,
    ticket_ref: str,
    merge_method: str,
    settings: Settings,
) -> dict[str, Any]:
    """Merge the PR linked to ``ticket_ref`` via GH App token.

    Server-side privileged action that the auto-merger agent role
    requests via ``payload.auto_merge_action="merge"``. Steps:

    1. Read the ticket's snapshot from the tracker → look for a PR
       URL in description / comments (matches the agent PR shape
       ``github.com/<owner>/<repo>/pull/<n>``).
    2. Resolve the workspace's GH installation for the PR's repo.
    3. Mint an installation token, call
       ``PUT /repos/{owner}/{repo}/pulls/{n}/merge`` with the
       requested method (squash / merge / rebase, default squash).
    4. Audit ``github.auto_merge.success`` or
       ``github.auto_merge.failed`` with the upstream status. Return
       ``{merged: bool, merge_sha?, reason?}`` for the finish-handler
       to splice into ``actions``.

    Never raises — auto-merger failures must be observable on the
    ticket + inbox, not crash the whole finish call.
    """
    import re as _re
    import httpx as _httpx
    from backend.app.db.models.integrations import (
        GitHubInstallation as _GHInstall,
        WorkspaceRepo as _WSRepo,
    )
    from backend.app.integrations.gateway.tracker import TicketRef as _TR
    from backend.app.integrations.github.app_auth import (
        fetch_installation_token as _fetch_install_token,
    )

    # Collect ALL PR URL candidates from description + comments,
    # then pick the most recent OPEN one. v0 took the first regex
    # match anywhere → on Ship-on-Ship/ELS-7 2026-05-17 that was a
    # closed PR #263 referenced in an old reviewer comment; GH
    # rejected the merge with HTTP 405 ("not mergeable") even
    # though PR #265 was the live one. We now resolve state
    # before commit.
    candidates: list[tuple[str, int]] = []
    try:
        snap = await resolved.gateway.get_ticket_snapshot(
            _TR(kind=resolved.kind, workspace_hint=None, id=ticket_ref)
        )
        haystack = " ".join(filter(None, [
            (snap or {}).get("description") or "",
            (snap or {}).get("url") or "",
        ]))
        for m in _re.finditer(
            r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)", haystack
        ):
            candidates.append((f"{m.group(1)}/{m.group(2)}", int(m.group(3))))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "auto_merge: ticket snapshot lookup failed ws=%s ticket=%s err=%s",
            workspace_id, ticket_ref, exc,
        )

    try:
        list_fn = getattr(resolved.gateway, "list_comments", None)
        if list_fn is not None:
            ref_obj = _TR(kind=resolved.kind, workspace_hint=None, id=ticket_ref)
            comments = await list_fn(ref_obj)
            # Walk newest-first so later candidates rank ahead of
            # older ones in the dedup below.
            for cm in reversed(comments or []):
                body = getattr(cm, "body", "") or ""
                for m in _re.finditer(
                    r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)", body
                ):
                    candidates.append((f"{m.group(1)}/{m.group(2)}", int(m.group(3))))
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "auto_merge: comment scan failed ws=%s err=%s", workspace_id, exc,
            )

    # Dedup candidate (repo, pr_number) pairs preserving order
    # (most recent first per the comment walk above).
    seen: set[tuple[str, int]] = set()
    deduped: list[tuple[str, int]] = []
    for fn, n in candidates:
        if (fn, n) not in seen:
            seen.add((fn, n))
            deduped.append((fn, n))

    if not deduped:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="github.auto_merge.failed",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={"reason": "no_pr_url_found"},
            )
        )
        return {"merged": False, "reason": "no_pr_url_found"}

    # Pick the first candidate whose PR is OPEN on GitHub. Falls
    # through to the first candidate if no GH App installation is
    # available — the merge call further down will fail clearly,
    # which is better than silently picking a stale PR.
    full_name, pr_number = deduped[0]
    pr_url = f"https://github.com/{full_name}/pull/{pr_number}"

    repo_row = (
        await session.execute(
            sa_select(_WSRepo).where(
                _WSRepo.workspace_id == workspace_id,
                _WSRepo.full_name == full_name,
            )
        )
    ).scalars().first()
    install_row = None
    if repo_row is not None and repo_row.installation_id is not None:
        install_row = await session.get(_GHInstall, repo_row.installation_id)
    if install_row is None or install_row.suspended_at is not None:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="github.auto_merge.failed",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "reason": "github_app_unavailable",
                    "pr_url": pr_url,
                },
            )
        )
        return {"merged": False, "reason": "github_app_unavailable"}

    method = merge_method if merge_method in ("squash", "merge", "rebase") else "squash"
    async with _httpx.AsyncClient(timeout=_httpx.Timeout(30.0)) as client:
        try:
            token = await _fetch_install_token(
                install_row.installation_id, settings=settings, client=client
            )
            # Pick the most recent OPEN candidate. v0 attempted the
            # first regex match in description/comments, which was
            # often a stale closed PR — GH then 405's the merge.
            # Walking the deduped list in order (newest-first from
            # comments + description tail) and keeping the first
            # ``state=open`` gives the live PR.
            chosen_full_name = full_name
            chosen_pr_number = pr_number
            chosen_pr_url = pr_url
            for cand_repo, cand_n in deduped:
                cr = await client.get(
                    f"https://api.github.com/repos/{cand_repo}/pulls/{cand_n}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if cr.status_code == 200 and (cr.json() or {}).get("state") == "open":
                    chosen_full_name = cand_repo
                    chosen_pr_number = cand_n
                    chosen_pr_url = f"https://github.com/{cand_repo}/pull/{cand_n}"
                    break
            full_name = chosen_full_name
            pr_number = chosen_pr_number
            pr_url = chosen_pr_url
            r = await client.put(
                f"https://api.github.com/repos/{full_name}/pulls/{pr_number}/merge",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"merge_method": method},
            )
        except Exception as exc:  # noqa: BLE001
            session.add(
                AuditLog(
                    workspace_id=workspace_id,
                    action="github.auto_merge.failed",
                    target_kind="ticket",
                    target_id=ticket_ref,
                    payload={
                        "reason": "gh_request_exception",
                        "pr_url": pr_url,
                        "error": str(exc)[:300],
                    },
                )
            )
            return {"merged": False, "reason": "gh_request_exception"}

    if r.status_code == 200 and (r.json() or {}).get("merged"):
        sha = str(r.json().get("sha") or "")
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                action="github.auto_merge.success",
                target_kind="ticket",
                target_id=ticket_ref,
                payload={
                    "pr_url": pr_url,
                    "method": method,
                    "merge_sha": sha,
                },
            )
        )
        return {"merged": True, "merge_sha": sha, "pr_url": pr_url}

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            action="github.auto_merge.failed",
            target_kind="ticket",
            target_id=ticket_ref,
            payload={
                "reason": "gh_rejected",
                "pr_url": pr_url,
                "upstream_status": r.status_code,
                "error": r.text[:300],
            },
        )
    )
    return {"merged": False, "reason": f"gh_{r.status_code}"}


async def _flip_drafts_row_to_parked(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    resolved,  # ResolvedTracker; avoid circular type import
    *,
    actor_user_id: uuid.UUID | None = None,
    actor_token_id: uuid.UUID | None = None,
) -> bool:
    """Flip the project's priorities row from ``planning`` → ``parked``.

    Called when a decomposition run reaches the terminal stage. The PO
    explicitly promotes ``parked`` → ``active`` from the dashboard
    when ready to ship — Ship doesn't auto-activate. (ELS-81; previous
    behaviour was to auto-flip to ``active``, which combined with
    ELS-80's picker gate would let agents start chewing on every
    project the moment its decomposition finished.)

    Walks: ticket_ref → planning anchor's project_id → priorities row
    → state. Best-effort: a missing priorities row, a deleted project,
    or an adapter that can't tell us the project all log + return False
    instead of failing the finish handler.
    """
    from sqlalchemy import select

    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    # The ticket_ref the agent passes is the anchor's identifier
    # (e.g. ``ELS-83``). Snapshot the issue to read its project_id,
    # then look up the priorities row by the project's native id
    # (Linear UUID / Jira project key — the same id we stamped on
    # the row in PR1's ``_tool_create_project``).
    ref = _ticket_ref_from(resolved.kind, ticket_ref)
    snapshot_fn = getattr(resolved.gateway, "get_ticket_snapshot", None)
    if snapshot_fn is None:
        logger.warning(
            "decomposition completion: tracker_kind=%s lacks "
            "get_ticket_snapshot — cannot resolve project for "
            "ticket=%s; row not flipped",
            resolved.kind,
            ticket_ref,
        )
        return False
    try:
        snapshot = await snapshot_fn(ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "decomposition completion: get_ticket_snapshot failed "
            "ticket=%s err=%s",
            ticket_ref,
            exc,
        )
        return False
    if not snapshot:
        return False
    project_id = snapshot.get("project_id")
    if not project_id:
        logger.warning(
            "decomposition completion: anchor ticket=%s has no project_id; "
            "row not flipped",
            ticket_ref,
        )
        return False

    row = (
        await session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace_id,
                WorkspaceProjectPriority.project_native_id == str(project_id),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        logger.warning(
            "decomposition completion: no priorities row for "
            "workspace=%s project=%s; row not flipped",
            workspace_id,
            project_id,
        )
        return False
    if row.state == "parked":
        return False
    row.state = "parked"
    await session.flush()

    # ELS-91: sync child tickets to match the new state. For
    # ``parked`` the helper moves Todo → Backlog; in-flight tickets
    # in ``In Progress`` / ``Review`` are left alone. Best-effort —
    # tracker errors log but do NOT roll back the priorities-row
    # flip. ``actor_user_id`` is the operator who triggered the
    # agent finish (threaded through from the route handler) so
    # audit-row FKs stay valid.
    if actor_user_id is not None:
        from backend.app.services.agent.project_state_sync import (
            sync_project_tickets_for_state,
        )

        try:
            await sync_project_tickets_for_state(
                session,
                workspace_id=workspace_id,
                project_id=str(project_id),
                new_state="parked",
                gateway=resolved.gateway,
                tracker_kind=resolved.kind,
                actor_user_id=actor_user_id,
                actor_token_id=actor_token_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "decomposition completion: ticket-state sync failed "
                "workspace=%s project=%s err=%s",
                workspace_id,
                project_id,
                exc,
            )
    return True


async def _sweep_inbox_on_ticket_advance(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str | None,
    fsm_stage: str | None,
    actions: list[str],
) -> None:
    """Auto-close recoverable inbox rows after a successful tracker move."""
    if not ticket_ref or not fsm_stage:
        return
    swept = await sweep_auto_resolvable(
        session,
        workspace_id=workspace_id,
        ticket_ref=ticket_ref,
        fsm_stage=fsm_stage,
    )
    if swept:
        actions.append(f"inbox:sweep_auto_resolved:{swept}")


@router.post("/agent-runs/finish", response_model=FinishOut)
async def finish_agent_run(
    workspace_id: uuid.UUID,
    payload: FinishIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinishOut:
    """One-shot terminal endpoint the Cursor agent calls when done.

    Replaces the old ``.ship/run-state.json`` file contract — branchless
    agents (intake, BA, planner) don't commit anything; they POST here
    with the outcome they reached. ``shipctl agent-run`` no longer reads
    a state file; the server is the single source of truth for what the
    run did.

    Idempotency: if the same ``run_id`` has already finished, this is a
    no-op so duplicate ``finish`` calls (network retries from inside
    the agent) don't double-write.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    # Idempotency: bail out if we've already recorded a finish for this
    # run_id under the same workspace.
    prev = (
        await session.execute(
            sa_select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.finish",
                AuditLog.target_id == payload.run_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if prev is not None:
        return FinishOut(
            ok=True,
            outcome=payload.outcome,
            run_id=payload.run_id,
            actions=["duplicate_ignored"],
            tracker_kind=(prev.payload or {}).get("tracker_kind"),
        )

    # ELS-120 safety net — fire BEFORE tracker resolution so the gate
    # works on workspaces with no tracker bound too. A code-changing
    # finish without a PR URL means the agent bypassed the sidecar
    # protocol; the runner-driven flow always splices ``PR: <url>``
    # into ``comment`` after ``gh pr create``. Reject so the ticket
    # doesn't advance past a stage that has no PR to review against.
    if (
        payload.outcome == "ready_next_step"
        and payload.ticket_ref
        and payload.fsm_stage in _PR_AUTHORING_STAGES
        and not _PR_URL_RE.search(payload.comment or "")
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "pr_url_required",
                "fsm_stage": payload.fsm_stage,
                "message": (
                    f"finish with outcome=ready_next_step on "
                    f"fsm_stage={payload.fsm_stage} requires a PR URL in "
                    "``comment``. The runner appends it after "
                    "``gh pr create`` succeeds — call /finish via the "
                    "sidecar protocol (write ``.ship/agent-finish.json`` "
                    "and let the runner own the finish) instead of "
                    "curl-ing directly."
                ),
            },
        )

    actions: list[str] = []
    resolved = await resolve_for_workspace(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    tracker_kind = resolved.kind if resolved else None

    # Outcomes that need a tracker but the workspace has none → drop an
    # inbox item so the operator notices, but still record the run.
    if payload.outcome in {"ready_next_step", "needs_clarification", "out_of_scope"} \
            and resolved is None:
        session.add(
            InboxItem(
                workspace_id=workspace_id,
                repo_id=None,
                type="blocker",
                title=f"agent finished but no tracker bound ({payload.outcome})"[:300],
                summary=(payload.summary or payload.comment or "")[:2000] or None,
                payload={
                    "run_id": payload.run_id,
                    "fsm_stage": payload.fsm_stage,
                    "ticket_ref": payload.ticket_ref,
                    "outcome": payload.outcome,
                    **payload.payload,
                },
                status="new",
                intake_handle=None,
                intake_reason="agent_run_no_tracker",
            )
        )
        actions.append("inbox:no_tracker_bound")

    elif payload.outcome == "noop":
        # Workspace bundle reported "checked, nothing to do". Just
        # record the audit row at the bottom; no transitions, no
        # inbox letter.
        actions.append("noop:workspace_bundle")

    elif payload.outcome == "ready_next_step":
        # Context-free routines (daily_*, audits with no findings, intake
        # on an empty queue) finish with ``ready_next_step`` + no ticket
        # — there's no work to transition. We accept this as a tracker
        # no-op: only the audit row at the bottom of this handler runs.
        # No inbox row, because "agent did its job and nothing was due"
        # is not a thing that needs human attention.
        if not payload.ticket_ref:
            actions.append("noop:no_ticket")
        else:
            # Workspace-scope bundles (self-heal / daily-digest /
            # weekly-audit) reference a ticket in their finish when
            # they make a targeted fix (relabeling an orphan,
            # commenting on a stuck PR), but they don't move the
            # FSM — ``stage_next`` is properly empty. SDLC routines
            # still need ``stage_next``.
            is_workspace_bundle = isinstance(payload.fsm_stage, str) and (
                payload.fsm_stage.startswith("workspace_")
            )
            if not payload.stage_next and not is_workspace_bundle:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "stage_next_required", "outcome": payload.outcome},
                )
            ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
            # Description rewrite goes first so a downstream comment can
            # reference "see updated description above" without the
            # comment being older than the body it points at. Adapter
            # may not implement the verb yet (jira / github_issues /
            # notion in the pilot) — best-effort, log and continue.
            if payload.description:
                setter = getattr(resolved.gateway, "set_description", None)
                if setter is None:
                    logger.warning(
                        "agent_run.finish: set_description not implemented "
                        "for tracker_kind=%s; ticket=%s — description ignored",
                        resolved.kind,
                        payload.ticket_ref,
                    )
                else:
                    await setter(ref, body=payload.description)
                    actions.append("tracker:set_description")
            if payload.comment:
                await resolved.gateway.comment(ref, body=payload.comment)
                actions.append("tracker:comment")
            # Decomposition body sections (ELS-75 write path). Each role
            # in the decomposition chain owns one ``## <section>`` of the
            # project body and emits its artefact here; the adapter's
            # ``upsert_project_section`` replace-or-appends the named
            # block, so re-running a stage cleanly overwrites just its
            # own section. We resolve the project_id from the anchor
            # ticket via the snapshot helper rather than trusting the
            # agent to pass it — the anchor already carries
            # ``project.id`` and that's the canonical source.
            #
            # Ordering note: sections land before ``transition`` so a
            # downstream stage that immediately picks up this anchor
            # already sees the section it should read; otherwise the
            # next picker tick could race ahead of the body write.
            #
            # Best-effort: a ``NotImplementedError`` (adapter doesn't
            # model projects), missing project on the anchor, or a
            # transient Linear 5xx fails the section but doesn't sink
            # the rest of the finish — the audit trail surfaces the
            # specific section so the operator can re-run.
            sections = _collect_project_sections(payload)
            if sections:
                snapshot = await _try_ticket_snapshot(resolved.gateway, ref)
                project_id = (snapshot or {}).get("project_id")
                upsert = getattr(
                    resolved.gateway, "upsert_project_section", None
                )
                if project_id and upsert is not None:
                    for patch in sections:
                        try:
                            await upsert(
                                str(project_id),
                                section=patch.section,
                                body=patch.body,
                            )
                            actions.append(
                                f"tracker:project_section:{patch.section}"
                            )
                        except NotImplementedError:
                            logger.warning(
                                "agent_run.finish: tracker_kind=%s does not "
                                "implement upsert_project_section; "
                                "section=%s skipped",
                                resolved.kind,
                                patch.section,
                            )
                        except Exception as exc:  # noqa: BLE001 — logged below
                            logger.warning(
                                "agent_run.finish: project section write "
                                "failed ws=%s ticket=%s section=%s err=%s",
                                workspace_id,
                                payload.ticket_ref,
                                patch.section,
                                exc,
                            )
                            actions.append(
                                f"tracker:project_section_failed:{patch.section}"
                            )
                elif not project_id:
                    logger.warning(
                        "agent_run.finish: anchor ticket=%s has no project; "
                        "%d project_sections skipped",
                        payload.ticket_ref,
                        len(sections),
                    )
                elif upsert is None:
                    logger.warning(
                        "agent_run.finish: tracker_kind=%s has no "
                        "upsert_project_section adapter; %d sections skipped",
                        resolved.kind,
                        len(sections),
                    )
            # Decomposition ``tasks`` stage: create child tickets
            # carved out of the WBS, then auto-render the ``## Tasks``
            # section listing their identifiers. Agents can't know
            # the identifiers up-front, so the section here is
            # server-built rather than agent-supplied — the agent
            # only declares each slice's title + body.
            #
            # Section ordering note: this lands AFTER any explicit
            # ``project_sections`` writes above, so a developer that
            # mistakenly also sends ``project_sections=[{Tasks,…}]``
            # has its hand-rolled body overwritten by the canonical
            # auto-rendered list. That's intentional: identifiers
            # the agent guesses are wrong by definition.
            if payload.child_tickets:
                snapshot = await _try_ticket_snapshot(resolved.gateway, ref)
                project_id = (snapshot or {}).get("project_id")
                create_fn = getattr(resolved.gateway, "create_ticket", None)
                upsert = getattr(
                    resolved.gateway, "upsert_project_section", None
                )
                if project_id and create_fn is not None:
                    created_rows: list[tuple[str, str]] = []
                    for child in payload.child_tickets:
                        try:
                            created = await create_fn(
                                title=child.title,
                                body=child.body,
                                labels=list(child.labels) or None,
                                project_id=str(project_id),
                                priority=child.priority,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "agent_run.finish: create_ticket failed "
                                "ws=%s anchor=%s title=%r err=%s",
                                workspace_id,
                                payload.ticket_ref,
                                child.title[:80],
                                exc,
                            )
                            actions.append(
                                f"tracker:ticket_create_failed:{child.title[:48]}"
                            )
                            continue
                        identifier = created.display_id or str(created.ref.id)
                        created_rows.append((identifier, child.title))
                        actions.append(
                            f"tracker:ticket_created:{identifier}"
                        )
                    if created_rows and upsert is not None:
                        rendered = "\n".join(
                            f"- **{ident}** — {title}"
                            for ident, title in created_rows
                        )
                        try:
                            await upsert(
                                str(project_id),
                                section="Tasks",
                                body=rendered,
                            )
                            actions.append("tracker:project_section:Tasks")
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "agent_run.finish: Tasks section upsert "
                                "failed ws=%s err=%s",
                                workspace_id,
                                exc,
                            )
                            actions.append(
                                "tracker:project_section_failed:Tasks"
                            )
                elif not project_id:
                    logger.warning(
                        "agent_run.finish: anchor ticket=%s has no project; "
                        "%d child_tickets skipped",
                        payload.ticket_ref,
                        len(payload.child_tickets),
                    )
                elif create_fn is None:
                    logger.warning(
                        "agent_run.finish: tracker_kind=%s has no "
                        "create_ticket adapter; %d children skipped",
                        resolved.kind,
                        len(payload.child_tickets),
                    )
            # Pass ``from_state`` so the adapter adds the breadcrumb
            # label for the role that *just finished* (``fsm_stage``),
            # not the next role's. The picker for ``stage_next`` reads
            # the previous-stage label as its "predecessor done"
            # signal — adding the wrong label breaks the chain.
            #
            # Workspace bundles skip the FSM transition entirely:
            # their corrections land via the agent's tool calls
            # (label edit, comment), finish just records the audit
            # row. Some agents synthesise a bundle-specific
            # ``stage_next`` value like ``workspace_self_heal_done``;
            # we ignore it rather than try to apply it as an FSM
            # label and pollute the ticket.
            is_workspace_bundle = isinstance(payload.fsm_stage, str) and (
                payload.fsm_stage.startswith("workspace_")
            )
            # Hold off on the ``merged`` transition when auto-
            # merger requested a real GitHub merge — the actual
            # squash happens further down in
            # ``_perform_auto_merge``, and the Linear move to Done
            # must be conditional on its success. v0 transitioned
            # first and called merge after, so a GH-rejected merge
            # left Linear in Done with the PR still open. Caught
            # on Ship-on-Ship/ELS-7 2026-05-17.
            extra_payload_peek = payload.payload or {}
            defer_merged_transition = (
                payload.fsm_stage == "auto_merge"
                and payload.stage_next == "merged"
                and isinstance(extra_payload_peek, dict)
                and str(extra_payload_peek.get("auto_merge_action") or "") == "merge"
            )
            if (
                payload.stage_next
                and not is_workspace_bundle
                and not defer_merged_transition
            ):
                await resolved.gateway.transition(
                    ref,
                    to_state=payload.stage_next,
                    from_state=payload.fsm_stage,
                )
                actions.append(f"tracker:transition:{payload.stage_next}")
                await _sweep_inbox_on_ticket_advance(
                    session,
                    workspace_id=workspace_id,
                    ticket_ref=payload.ticket_ref,
                    fsm_stage=payload.fsm_stage,
                    actions=actions,
                )

            # Auto-merger hook (post-E16 WAU). When the auto-merger
            # bundle finishes with ``payload.auto_merge_action="merge"``,
            # we (the server) call GitHub's PR-merge API ourselves so
            # the runner never needs ``contents:write`` to main. The
            # agent's job is decision-making (7-signal gate); the
            # server's job is the privileged action. Stalls (``stall``
            # action) just record an audit row + inbox item via the
            # ``outcome=needs_clarification`` branch the agent picks
            # instead — no new path here.
            extra_payload = payload.payload or {}
            auto_merge_action = (
                str(extra_payload.get("auto_merge_action") or "")
                if isinstance(extra_payload, dict) else ""
            )
            if (
                payload.fsm_stage == "auto_merge"
                and auto_merge_action == "merge"
                and not is_workspace_bundle
            ):
                merge_result = await _perform_auto_merge(
                    session,
                    workspace_id=workspace_id,
                    resolved=resolved,
                    ticket_ref=payload.ticket_ref,
                    merge_method=str(
                        extra_payload.get("merge_method") or "squash"
                    ),
                    settings=settings,
                )
                if merge_result.get("merged"):
                    actions.append("github:pr_merged")
                    actions.append(
                        f"github:merge_sha:{merge_result['merge_sha'][:7]}"
                    )
                    # Now-and-only-now is it safe to move Linear to
                    # ``merged`` (= Done). We deferred this transition
                    # above so a GH-rejected merge wouldn't strand
                    # the ticket in Done with the PR still open.
                    if defer_merged_transition and payload.stage_next:
                        try:
                            await resolved.gateway.transition(
                                ref,
                                to_state=payload.stage_next,
                                from_state=payload.fsm_stage,
                            )
                            actions.append(
                                f"tracker:transition:{payload.stage_next}"
                            )
                            await _sweep_inbox_on_ticket_advance(
                                session,
                                workspace_id=workspace_id,
                                ticket_ref=payload.ticket_ref,
                                fsm_stage=payload.fsm_stage,
                                actions=actions,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "agent_run.finish: post-merge transition "
                                "failed ws=%s ticket=%s err=%s",
                                workspace_id, payload.ticket_ref, exc,
                            )
                            actions.append(
                                f"tracker:transition_failed:{payload.stage_next}"
                            )
                else:
                    actions.append(
                        f"github:auto_merge_failed:{merge_result.get('reason','unknown')}"
                    )
                    # Merge attempt failed — leave Linear at
                    # ``auto_merge``. ``defer_merged_transition`` kept
                    # us from advancing prematurely; no further action
                    # needed. Operator (or refire-cap eventually) will
                    # trigger the next pass.

            # Decomposition completion hook (ELS-75 + ELS-81). When
            # the planning anchor reaches the terminal stage, flip
            # the project's dashboard row Drafts → Parked. The PO
            # then promotes Parked → Active manually when ready to
            # ship — Ship doesn't auto-activate, otherwise the
            # ELS-80 picker gate would let agents start chewing on
            # every project the moment its decomposition finished.
            # We key on the explicit ``process='decomposition'``
            # flag rather than sniffing labels — the runtime that's
            # executing the run knows which process it's under, the
            # server should not have to re-derive that. Best-effort:
            # a missing priorities row logs and continues.
            if (
                payload.process == "decomposition"
                and payload.stage_next == "planning_done"
            ):
                flipped = await _flip_drafts_row_to_parked(
                    session,
                    workspace_id,
                    payload.ticket_ref,
                    resolved,
                    actor_user_id=auth.user.id,
                    actor_token_id=auth.token.id if auth.token else None,
                )
                if flipped:
                    actions.append("priorities:parked")

    elif payload.outcome == "needs_clarification":
        if not payload.ticket_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "ticket_ref_required", "outcome": payload.outcome},
            )
        ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
        if payload.comment:
            await resolved.gateway.comment(ref, body=payload.comment)
            actions.append("tracker:comment")
        # Linear adapter exposes ``add_signal_label``; other adapters
        # need to grow it before this outcome works for them.
        adder = getattr(resolved.gateway, "add_signal_label", None)
        if adder is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "code": "needs_clarification_unsupported",
                    "tracker_kind": resolved.kind,
                },
            )
        await adder(ref, key="needs_clarification")
        actions.append("tracker:label:needs_clarification")
        # Snapshot the source ticket onto the inbox row so the
        # operator can read the original ask without flipping to
        # Linear — without it, the clarification card is just the
        # agent's question with no context for what it's about.
        ticket_snapshot = await _try_ticket_snapshot(resolved.gateway, ref)
        # Mirror to inbox so the operator sees it without scanning the
        # tracker — the agent's question is the inbox row's summary.
        session.add(
            InboxItem(
                workspace_id=workspace_id,
                repo_id=None,
                type="clarification",
                title=f"clarification: {payload.ticket_ref}"[:300],
                summary=(payload.summary or payload.comment or "")[:2000] or None,
                payload={
                    "run_id": payload.run_id,
                    "fsm_stage": payload.fsm_stage,
                    "ticket_ref": payload.ticket_ref,
                    **({"source_ticket": ticket_snapshot} if ticket_snapshot else {}),
                    **payload.payload,
                },
                status="new",
                intake_handle=None,
                intake_reason="agent_run_clarification",
            )
        )
        actions.append("inbox:clarification")
        # ELS-142: project_lock release for needs_clarification /
        # blocked / out_of_scope happens below via
        # ``maybe_release_project_lock_on_finish`` so every finish path
        # writes a single ``dispatch.project_lock_released`` audit row
        # with ``via=agent_run.finish``. Pre-fix the early call here
        # released the lock first with ``via=<outcome>``, and the
        # cascade matrix path then no-op'd — AC1 of ELS-142 (single
        # ``via=agent_run.finish`` audit per release) couldn't fire.

    elif payload.outcome == "blocked":
        # Three sub-cases:
        #
        # (A) ``stage_next`` is set — the agent picked a cascade target
        #     explicitly. The cascade matrix below picks it up; nothing
        #     to do here.
        #
        # (B) ``stage_next`` is null AND review-style stage AND we
        #     haven't already auto-cascaded this ticket out of
        #     blocked+no_next twice in the window → silently rewrite
        #     ``stage_next`` to ``dev_implementation``. Reviewer-side
        #     blockers default to "dev re-runs and fixes" — that's the
        #     reviewer.md prompt contract (commit a045e38). If the
        #     agent still emits no cascade target the server applies
        #     the default rather than bothering the operator. Belt and
        #     braces: refire-cap (3 same-stage blocks in 24h) and
        #     dev_not_converging (3 review-blocks + 2 dev cycles)
        #     catch the longer loops downstream.
        #
        # (C) ``stage_next`` is null AND we've already auto-cascaded
        #     this ticket twice in the 4h window → the auto-cascade
        #     isn't fixing the underlying problem. File a blocker
        #     letter once so the operator can investigate (refire-cap
        #     will also fire on the third same-stage block — this
        #     letter is the lower-latency early warning).
        #
        # (D) Non-review stages (dev_implementation transient blocks)
        #     stay inbox-quiet — refire-cap handles the budget.
        _BLOCKED_NO_NEXT_REVIEW_STAGES = {
            "code_review", "pr_review", "validation",
            "qa_manual", "qa_automation", "auto_merge",
        }
        if (
            not payload.stage_next
            and resolved is not None
            and payload.ticket_ref
            and payload.fsm_stage in _BLOCKED_NO_NEXT_REVIEW_STAGES
        ):
            # Count prior auto-cascades for this ticket in 4h. We tag
            # each one with ``actions=["cascade:blocked_no_next_auto"]``
            # in the finish audit, so a SQL count of that marker tells
            # us how many times the server has already retried.
            auto_cascade_cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
            prior_autos = (
                await session.execute(
                    select(AuditLog.id)
                    .where(
                        AuditLog.workspace_id == workspace_id,
                        AuditLog.action == "agent_run.finish",
                        AuditLog.created_at >= auto_cascade_cutoff,
                        (
                            (AuditLog.target_id == payload.ticket_ref)
                            | (
                                AuditLog.payload["ticket_ref"].astext
                                == payload.ticket_ref
                            )
                        ),
                        AuditLog.payload["auto_cascade_from_no_next"].astext
                        == "true",
                    )
                )
            ).all()
            if len(prior_autos) < 2:
                # Path (B): silent auto-cascade. Rewrite stage_next so
                # the cascade matrix downstream fires dev_implementation
                # for this ticket. Tag the action + audit row so prior-
                # autos query above can find this run on the next pass.
                # NOTE: we mutate ``payload`` because pydantic v2 lets
                # us — the cascade matrix reads ``payload.stage_next``
                # below.
                payload.stage_next = "dev_implementation"
                actions.append("cascade:blocked_no_next_auto")
                logger.info(
                    "blocked+no_next auto-cascade ws=%s ticket=%s stage=%s",
                    workspace_id, payload.ticket_ref, payload.fsm_stage,
                )
            else:
                # Path (C): auto-cascade exhausted, escalate. File a
                # blocker letter with the same action_items the
                # operator had before — but only AFTER the auto-
                # cascade failed twice, not on every blocked+no_next.
                ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
                add_signal = getattr(
                    resolved.gateway, "add_signal_label", None,
                )
                if add_signal is not None:
                    try:
                        await add_signal(ref, key="needs_clarification")
                        actions.append("tracker:label:needs_clarification")
                    except Exception as exc:  # noqa: BLE001
                        logger.info(
                            "blocked+no_next label add failed ws=%s "
                            "ticket=%s: %s",
                            workspace_id, payload.ticket_ref, exc,
                        )
                cascade_title = (
                    f"{payload.ticket_ref}: {payload.fsm_stage} "
                    f"keeps blocking despite auto-retries"
                )[:300]
                cascade_summary = (
                    f"Server auto-cascaded {payload.ticket_ref} "
                    f"from {payload.fsm_stage} to "
                    f"dev_implementation twice in the last 4h; "
                    f"the reviewer is still blocking. Either "
                    f"the dev agent isn't converging on the fix "
                    f"or the reviewer's block needs operator "
                    f"input."
                )[:2000]
                session.add(
                    InboxItem(
                        workspace_id=workspace_id,
                        repo_id=None,
                        type="blocker",
                        title=cascade_title,
                        headline=derive_headline(
                            summary=cascade_summary, title=cascade_title
                        ),
                        summary=cascade_summary,
                        payload={
                            "ticket_ref": payload.ticket_ref,
                            "fsm_stage": payload.fsm_stage,
                            "run_id": payload.run_id,
                            "auto_cascade_attempts": len(prior_autos),
                            "resolution_mode": "single_choice",
                            "action_items": [
                                {
                                    "id": "applied_manually",
                                    "kind": "choice",
                                    "label": "Applied reviewer fix manually",
                                },
                                {
                                    "id": "override_force_merge",
                                    "kind": "choice",
                                    "label": "Override reviewer / force merge",
                                },
                                {
                                    "id": "mark_handled",
                                    "kind": "choice",
                                    "label": "Already handled",
                                },
                            ],
                        },
                        status="new",
                        intake_handle=f"blocked-cascade-exhausted:{payload.ticket_ref}",
                        intake_reason="blocked_cascade_exhausted",
                    )
                )
                actions.append("inbox:blocker:cascade_exhausted")
        # else: stage_next is set OR non-review stage — cascade matrix
        # / refire-cap handle the rest. (Lock release happens via
        # ``maybe_release_project_lock_on_finish`` below.)

    elif payload.outcome == "out_of_scope":
        if not payload.ticket_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "ticket_ref_required", "outcome": payload.outcome},
            )
        ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
        if payload.comment:
            await resolved.gateway.comment(ref, body=payload.comment)
            actions.append("tracker:comment")
        # ``Done`` is the legacy workflow-state path in LinearTracker —
        # it resolves the state by literal name, not via FSM map.
        await resolved.gateway.transition(ref, to_state="Done")
        actions.append("tracker:transition:Done")
        await _sweep_inbox_on_ticket_advance(
            session,
            workspace_id=workspace_id,
            ticket_ref=payload.ticket_ref,
            fsm_stage=payload.fsm_stage,
            actions=actions,
        )
        # ELS-142: lock release deferred to
        # ``maybe_release_project_lock_on_finish`` below.

    if (
        payload.outcome == "ready_next_step"
        and payload.ticket_ref
        and payload.fsm_stage == "dev_implementation"
    ):
        try:
            honour_action = await evaluate_file_overlap_honour(
                session,
                workspace_id=workspace_id,
                ticket_ref=payload.ticket_ref,
                run_id=payload.run_id,
                fsm_stage=payload.fsm_stage,
                comment=payload.comment,
                settings=settings,
            )
            if honour_action:
                actions.append(f"audit:{honour_action}")
        except Exception as exc:  # noqa: BLE001 — must not block finish
            logger.warning(
                "agent_run.finish: file_overlap honour failed ws=%s "
                "ticket=%s run=%s err=%s",
                workspace_id,
                payload.ticket_ref,
                payload.run_id,
                exc,
            )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.finish",
            target_kind="agent_run",
            target_id=payload.run_id,
            payload={
                "tracker_kind": tracker_kind,
                "outcome": payload.outcome,
                "fsm_stage": payload.fsm_stage,
                "stage_next": payload.stage_next,
                "ticket_ref": payload.ticket_ref,
                "actions": actions,
                "had_comment": bool(payload.comment),
                # Persist the description the agent wrote so the
                # next cascade-fired stage can read it from our DB
                # while Linear's replica catches up
                # (``/tracker/next`` overlay path). Without this the
                # downstream dev_implementation stage saw an empty
                # snapshot and exited noop with ``ticket_ref:null``
                # (askslayer/PAC-20,21,22,23 2026-05-15).
                "description": payload.description,
                # Marker for the blocked+no_next auto-cascade counter
                # (path (B) above). Read by the next /finish call to
                # decide whether to keep auto-cascading or escalate.
                "auto_cascade_from_no_next": (
                    "cascade:blocked_no_next_auto" in actions
                ),
            },
        )
    )
    await session.flush()

    # ELS-122 cascade: release the per-ticket dispatch lock and
    # immediately ask the dispatcher if the *next* stage is eligible.
    # Linear's state-change webhook would eventually drive this via
    # the poller, but cascading inline skips the 5-min poll delay
    # between stages. ``maybe_dispatch`` respects the cascade-depth
    # guard so an FSM bug can't loop forever.
    #
    # 15s gap before firing the next stage's workflow_dispatch:
    # Linear's GraphQL is eventually-consistent across read replicas
    # — a ``set_description`` mutation that returned 200 to us takes
    # a few seconds before it shows on a follow-up ``issue(id:…)``
    # read. Without this delay the cascade-fired dev_implementation
    # runs against a stale snapshot (empty title / body) and exits
    # noop with ``ticket_ref: null``, exactly the symptom on
    # askslayer/PAC-23 dev_implementation run 2026-05-15 20:10 UTC.
    # 15s is empirically enough on Linear and keeps the operator
    # latency well under the previous 5-min poller delay.
    if payload.ticket_ref:
        import asyncio as _asyncio
        from backend.app.services.dispatcher import (
            maybe_dispatch,
            maybe_release_project_lock_on_finish,
            release_lock,
        )

        finish_snapshot = None
        if resolved is not None:
            ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
            finish_snapshot = await _try_ticket_snapshot(resolved.gateway, ref)
        await maybe_release_project_lock_on_finish(
            session,
            workspace_id=workspace_id,
            ticket_ref=payload.ticket_ref,
            outcome=payload.outcome,
            stage_next=payload.stage_next,
            labels=list((finish_snapshot or {}).get("labels") or []),
            project_id=(
                str((finish_snapshot or {})["project_id"])
                if (finish_snapshot or {}).get("project_id")
                else None
            ),
            settings=settings,
        )
        await release_lock(
            session,
            workspace_id=workspace_id,
            key=f"ticket:{payload.ticket_ref}",
        )
        if payload.stage_next:
            # 30s settle (was 15s — askslayer/PAC-22 dev_implementation
            # 2026-05-15 21:08 still saw an empty snapshot 25s after
            # planning's set_description committed to Linear primary;
            # the replica lag is longer than expected). 30s keeps us
            # under the previous 5-min poller cadence by 10×.
            await _asyncio.sleep(30)
        await maybe_dispatch(
            session,
            workspace_id=workspace_id,
            ticket_ref=payload.ticket_ref,
            trigger_kind="cascade",
            fsm_stage=payload.stage_next,
            settings=settings,
        )
        await session.flush()

    return FinishOut(
        ok=True,
        outcome=payload.outcome,
        run_id=payload.run_id,
        actions=actions,
        tracker_kind=tracker_kind,
    )


# ---------------------------------------------------------------------------
# Manual routine dispatch (PR-6 of the local-CLI swap — debug harness)
# ---------------------------------------------------------------------------


class RoutineDispatchIn(BaseModel):
    """Body for ``POST /v1/workspaces/{ws}/agent-runs/dispatch``.

    ``repo_id`` is the WorkspaceRepo whose ``ship-trigger-schedule.yml``
    we fire. The routine and optional ticket map straight onto the
    workflow_dispatch ``inputs`` (PR-4 of the local-CLI swap).
    """

    repo_id: uuid.UUID
    routine_id: str = Field(min_length=1, max_length=64)
    ticket_ref: str | None = Field(default=None, max_length=64)


class RoutineDispatchOut(BaseModel):
    accepted: bool
    repo_full_name: str
    workflow_file: str
    routine_id: str
    ticket_ref: str | None


class InstallationTokenOut(BaseModel):
    """Short-lived GitHub App installation token for the bound repo.

    The token is scoped to the repo's installation and inherits the
    Ship App's installed permissions (``contents:write``,
    ``pull_requests:write``, …). The runner uses it as ``GH_TOKEN``
    when ``gh pr create`` runs — the default ``GITHUB_TOKEN`` from
    ``actions/checkout`` is gated by the org's "Allow GHA to create
    PRs" toggle, which most orgs leave off. The App token isn't
    subject to that gate.
    """

    token: str
    expires_at: str
    repo_full_name: str


@router.get(
    "/admin/ticket-snapshot/{ticket_ref}",
    response_model=TicketSnapshotOut,
)
async def get_admin_ticket_snapshot(
    workspace_id: uuid.UUID,
    ticket_ref: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TicketSnapshotOut:
    """Snapshot a ticket's display fields + all comments.

    Operator-driven diff tool: capture the ticket state before a
    stage agent runs, capture again after, eyeball what the agent
    actually changed. Linear keeps the issue activity feed
    server-side so one-shot snapshots are enough — no need for a
    history endpoint.

    Admin-only — surfaces the full description body which can carry
    sensitive ticket text.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id
    )
    if resolved is None:
        raise HTTPException(status_code=422, detail="no_tracker_bound")

    snapshot_fn = getattr(resolved.gateway, "get_ticket_snapshot", None)
    if snapshot_fn is None:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "ticket_snapshot_unsupported",
                "tracker_kind": resolved.kind,
            },
        )

    ref = _ticket_ref_from(resolved.kind, ticket_ref)
    snap = await snapshot_fn(ref)
    if snap is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    comments_fn = getattr(resolved.gateway, "list_comments", None)
    raw_comments: list[Any] = []
    if comments_fn is not None:
        try:
            raw_comments = await comments_fn(ref)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "admin/ticket-snapshot: list_comments failed ref=%s err=%s",
                ticket_ref,
                exc,
            )
    comments: list[TicketCommentSnapshot] = []
    for c in raw_comments or []:
        # ``CommentRef`` is a dataclass-ish shape from gateway/tracker.py;
        # we accept either dataclass attributes or dict keys so this
        # endpoint stays adapter-agnostic.
        get = (lambda obj, k: getattr(obj, k, None) if not isinstance(obj, dict) else obj.get(k))
        comments.append(
            TicketCommentSnapshot(
                id=str(get(c, "id") or "") or None,
                body=str(get(c, "body") or ""),
                author=str(get(c, "author") or "") or None,
                created_at=str(get(c, "created_at") or "") or None,
            )
        )

    return TicketSnapshotOut(
        ticket_ref=snap.get("ticket_ref") or ticket_ref,
        title=snap.get("title"),
        description=snap.get("description"),
        url=snap.get("url"),
        state=snap.get("state"),
        labels=snap.get("labels") or [],
        project_id=snap.get("project_id"),
        comments=comments,
    )


class ReprovisionLinearFsmOut(BaseModel):
    ok: bool
    team_key: str
    stages_provisioned: list[str]
    new_stages: list[str]
    signal_labels: list[str]


@router.post(
    "/admin/reprovision-linear-fsm", response_model=ReprovisionLinearFsmOut
)
async def post_admin_reprovision_linear_fsm(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ReprovisionLinearFsmOut:
    """Re-run the Linear FSM provisioner for the workspace's bound
    team. Picks up new stages added to ``SHIP_FSM_STAGES`` since the
    last OAuth callback (e.g. ``qa_arch_plan`` insertion between
    ``tech_arch_plan`` and ``dev_implementation``) and creates the
    missing ``stage:<X>`` labels on the Linear team.

    Idempotent: existing labels are kept by-name; only missing stages
    get fresh ones. ``Integration.config`` is updated in place with
    the merged ``label_id_by_stage`` / ``state_id_by_name`` /
    ``signal_label_ids`` so the runtime resolver picks up the new
    entries on its next call. Admin-only — provisioning calls
    Linear's mutating API.
    """
    from sqlalchemy import select as sa_select

    from backend.app.db.models.tenancy import Integration
    from backend.app.integrations.linear.tracker_adapter import LinearTracker
    from backend.app.security.encryption import decrypt
    from backend.app.services import linear_provisioner

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    legacy_row = (
        await session.execute(
            sa_select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.kind == "linear",
                Integration.repo_id.is_(None),
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if legacy_row is None or not legacy_row.secret_ciphertext:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "no_linear_oauth",
                "message": "Reconnect Linear first.",
            },
        )

    config = legacy_row.config or {}
    team_key = config.get("team_key")
    if not team_key:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "no_team_picked",
                "message": "Pick a Linear team first.",
            },
        )

    try:
        token = decrypt(legacy_row.secret_ciphertext)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"code": "token_unreadable", "error": str(exc)[:200]},
        ) from exc

    live = LinearTracker(token)
    prior_stages = set((config.get("label_id_by_stage") or {}).keys())
    try:
        result = await linear_provisioner.provision_team(
            tracker=live, team_key=team_key, settings=settings
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"code": "provision_failed", "error": str(exc)[:300]},
        ) from exc

    new_config = dict(config)
    new_config.update(
        {
            "team_id": result.team_id,
            "team_key": result.team_key,
            "state_id_by_name": result.state_id_by_name,
            "label_id_by_stage": result.label_id_by_stage,
            "signal_label_ids": result.signal_label_ids,
            "canonical_to_native": result.canonical_to_native,
            "canonical_resolution_meta": result.canonical_resolution_meta,
            "fsm_provisioned": True,
        }
    )
    legacy_row.config = new_config
    legacy_row.updated_at = datetime.now(timezone.utc)

    new_stages = sorted(set(result.label_id_by_stage.keys()) - prior_stages)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="tracker.reprovision_fsm",
            target_kind="workspace",
            target_id=str(workspace_id),
            payload={
                "team_key": result.team_key,
                "stages_provisioned": sorted(result.label_id_by_stage.keys()),
                "new_stages": new_stages,
                "signal_labels": sorted(result.signal_label_ids.keys()),
            },
        )
    )
    await session.flush()

    return ReprovisionLinearFsmOut(
        ok=True,
        team_key=result.team_key,
        stages_provisioned=sorted(result.label_id_by_stage.keys()),
        new_stages=new_stages,
        signal_labels=sorted(result.signal_label_ids.keys()),
    )


@router.post("/admin/relabel-stages", response_model=RelabelStagesOut)
async def post_admin_relabel_stages(
    workspace_id: uuid.UUID,
    payload: RelabelStagesIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RelabelStagesOut:
    """Surgically add/remove FSM stage labels on a ticket.

    Admin-only. Calls the bound tracker's ``relabel_stages`` —
    available on Linear today; other adapters surface a 501 until they
    implement it.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id
    )
    if resolved is None:
        raise HTTPException(status_code=422, detail="no_tracker_bound")

    relabel_fn = getattr(resolved.gateway, "relabel_stages", None)
    if relabel_fn is None:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "relabel_stages_unsupported",
                "tracker_kind": resolved.kind,
            },
        )

    ref = _ticket_ref_from(resolved.kind, payload.ticket_ref)
    try:
        result = await relabel_fn(ref, add=payload.add, remove=payload.remove)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    state_changed_to: str | None = None
    if payload.set_state:
        # Use the legacy literal-state path on the tracker — it
        # resolves the workflow state by name on the issue's team
        # without touching labels. Lets the operator reset state
        # ("Todo" / "Backlog" / "In Progress" / "Done" / "Canceled")
        # without faking an agent finish.
        try:
            await resolved.gateway.transition(ref, to_state=payload.set_state)
            state_changed_to = payload.set_state
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "set_state_failed",
                    "set_state": payload.set_state,
                    "error": str(exc)[:300],
                },
            ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="tracker.relabel_stages",
            target_kind="ticket",
            target_id=payload.ticket_ref,
            payload={
                "add_requested": payload.add,
                "remove_requested": payload.remove,
                "added": result.get("added") or [],
                "removed": result.get("removed") or [],
                "set_state": payload.set_state,
                "state_changed_to": state_changed_to,
            },
        )
    )
    await session.flush()

    return RelabelStagesOut(
        ticket_ref=payload.ticket_ref,
        added=result.get("added") or [],
        removed=result.get("removed") or [],
        state_changed_to=state_changed_to,
    )


@router.post(
    "/repos/{repo_id}/installation-token",
    response_model=InstallationTokenOut,
)
async def post_repo_installation_token(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InstallationTokenOut:
    """Mint a GitHub App installation token for ``repo_id``.

    The runner uses this token as ``GH_TOKEN`` when ``gh pr create``
    runs after a developer-agent push. The default
    ``actions/checkout`` ``GITHUB_TOKEN`` can't open PRs unless the
    org enables the "Allow GitHub Actions to create PRs" toggle, and
    most orgs leave that off — the App's installation token isn't
    subject to that gate.

    Admin-only. Each call mints a fresh token (caller stores nothing
    long-lived); GitHub auto-expires it within an hour. The mint
    helper caches per-installation so back-to-back calls share the
    same token.
    """
    from datetime import datetime, timezone

    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.integrations.github.app_auth import fetch_installation_token

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo = (
        await session.execute(
            sa_select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not in this workspace")
    if repo.installation_id is None:
        raise HTTPException(
            status_code=409,
            detail="repo has no GitHub App installation; reinstall Ship",
        )
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=409,
            detail="GitHub App installation missing or suspended",
        )

    try:
        token = await fetch_installation_token(
            install.installation_id, settings=settings
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"code": "mint_failed", "error": str(exc)[:200]},
        ) from exc

    # GitHub installation tokens live ~1h. We don't echo the live
    # cache's exact expiry (it's trimmed below by a safety margin
    # before re-mint), so report a conservative +50min from now.
    expires_at = datetime.now(timezone.utc).isoformat()

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.installation_token_minted",
            target_kind="workspace_repo",
            target_id=str(repo_id),
            payload={"full_name": repo.full_name},
        )
    )
    await session.flush()

    return InstallationTokenOut(
        token=token,
        expires_at=expires_at,
        repo_full_name=repo.full_name,
    )


@router.post("/agent-runs/dispatch", response_model=RoutineDispatchOut)
async def post_dispatch_routine(
    workspace_id: uuid.UUID,
    payload: RoutineDispatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RoutineDispatchOut:
    """Manually fire ``ship-trigger-schedule.yml`` for one routine.

    Operator-driven debug path. The workflow_dispatch inputs feed
    straight into ``shipctl run --routine X --commit-and-pr --debug``
    on the runner so the GHA log shows every step of the pipeline
    interleaved with the agent CLI's own output.

    Admin-only — manual dispatch consumes Cursor / Codex / Claude
    quota and opens PRs against the repo, both of which are
    operator-tier actions.
    """
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.integrations.github.workflows import dispatch_workflow

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo = (
        await session.execute(
            sa_select(WorkspaceRepo).where(
                WorkspaceRepo.id == payload.repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not in this workspace")
    if repo.installation_id is None:
        raise HTTPException(
            status_code=409,
            detail="repo has no GitHub App installation; reinstall Ship",
        )
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=409,
            detail="GitHub App installation missing or suspended",
        )

    # Normalise stage label → canonical routine id. The runner names the
    # working branch ``ship-<routine_id>-<ticket>``, so dispatching with
    # a stage name (``dev_implementation``) instead of the routine
    # (``developer``) forks a divergent branch and a duplicate PR for a
    # ticket already in flight. ``maybe_dispatch`` resolves this on the
    # event path; the manual endpoint must too.
    routine_id = normalize_routine_id(payload.routine_id)

    inputs: dict[str, str] = {"routine_id": routine_id}
    if payload.ticket_ref:
        inputs["ticket_ref"] = payload.ticket_ref

    await dispatch_workflow(
        repo,
        install,
        "ship-agent-run.yml",
        inputs=inputs,
        settings=settings,
    )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="agent_run.routine_dispatched",
            target_kind="workspace_repo",
            target_id=str(repo.id),
            payload={
                "routine_id": routine_id,
                "routine_id_requested": payload.routine_id,
                "ticket_ref": payload.ticket_ref,
                "workflow_file": "ship-agent-run.yml",
            },
        )
    )
    await session.flush()

    return RoutineDispatchOut(
        accepted=True,
        repo_full_name=repo.full_name,
        workflow_file="ship-agent-run.yml",
        routine_id=routine_id,
        ticket_ref=payload.ticket_ref,
    )
