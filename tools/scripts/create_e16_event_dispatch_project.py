"""E16 — event-driven orchestration + SDLC bundles.

One-shot script (idempotent): creates the E16 project in Ship-on-Ship Linear
plus its sub-tickets. Re-runs detect existing project / tickets by name and
skip what already exists.

Usage:
    DATABASE_URL=... ENCRYPTION_KEY=... python tools/scripts/create_e16_event_dispatch_project.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from sqlalchemy import text

from backend.app.security.encryption import safe_decrypt
from backend.app.integrations.linear.tracker_adapter import LinearTracker


SHIP_ON_SHIP_WS = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
ELS_TEAM_ID = "854ffe38-2ac7-404f-b482-7260ac707593"

PROJECT_NAME = "E16 — event-driven orchestration + SDLC bundles"

# Linear caps `description` at 255 chars. Full body goes into `content`
# (Markdown), which the adapter handles separately. Keep description as a
# one-liner that reads well in the projects sidebar.
PROJECT_DESCRIPTION = (
    "Replace cron-driven SDLC dispatch with tracker-FSM events + "
    "bundled multi-stage agent runs. Tracker is the message bus; "
    "cron survives only for daily/workspace routines."
)

PROJECT_BODY = """\
Replace the cron-driven agent pickup loop with an event-driven dispatcher
that reads tracker FSM transitions as the canonical "ticket moved" signal
and fires agents immediately, with per-ticket locks and per-workspace caps.

## Why now

- The current cron picker adds 30+ min median latency between "ticket is
  ready" and "agent picks it up". Backend already has full state; polling
  on a schedule is wasteful.
- 8 separate stage runs per ticket reload ~50K of context each (~250K
  tokens of input across the lifecycle). Bundling related stages into one
  agent run with internal phases cuts that to ~85K — ~65% saving on
  input tokens per ticket.
- Linear status is the single source of truth for "what stage are we on".
  Git events (PR-merge regex on titles, etc.) are noisy and brittle —
  drop them as orchestration triggers, keep them as agent inputs only.

## Architecture

```
backend services:
  tracker_poller.py    — 1×/5min (1×/1min in dev): pulls Linear updates,
                         diffs against last_seen_state, emits internal
                         "ticket.transitioned" events.
  dispatcher.py        — listens to internal events + agent_runs.finish;
                         per-ticket lock + per-workspace cap; fires GH
                         workflow_dispatch.
  bundle_orchestrator  — maps stages to bundles (e.g. planning bundle =
                         intake + BA + tech_arch + qa_arch in one run).
  daily_scheduler.py   — single cron entry, ONLY workspace-level
                         routines (knowledge harvest, daily summary).
```

## SDLC bundle map (8 stages → 4 routines)

| Bundle         | Stages bundled                                        | Linear status |
|----------------|-------------------------------------------------------|---------------|
| planning       | context-gather → intake → BA → tech_arch → qa_arch    | Planned       |
| implementation | dev (no bundling — it owns the git side)              | In Dev        |
| validation     | qa_manual → qa_auto                                   | In Review     |
| code_review    | code_review (human-facing gate; alone)                | Review        |

Plus `decomposition` (planning anchor flow): bundles brief → WBS →
architecture → test_arch → tasks into one agent run that commits all
project sections + child tickets in a single pass.

## Decisions locked

- Per-workspace cap stored in DB (per-workspace override, env default).
- Lock TTL = 60 min (planning bundle can run 15-20 min comfortably under
  this; safety against orphaned locks).
- Tracker poll: 5 min prod, 1 min dev override via env.
- Max cascade depth: 3 per ticket per minute (catches FSM bugs).
- No backwards compatibility — we break once and fix what falls down.

## Out of scope

- GitHub Issues / Jira trackers (Linear-only first; adapter abstraction
  lands when a real customer needs them).
- Webhook subscription on Linear (poll is simpler and webhook fidelity is
  known capricious).
"""


TICKETS: list[tuple[str, str]] = [
    (
        "E16-1: tracker_poller + agent_dispatch_locks (shadow mode)",
        """\
**Goal.** Stand up the new event source without changing live behaviour.

**Scope.**
- New service `backend/app/services/tracker_poller.py`: every N minutes
  (env `SHIP_TRACKER_POLL_INTERVAL_S`, default 300, dev 60), pulls Linear
  updates for every workspace with a `linear` integration via
  `updatedAt >= last_seen_at`. Writes `tracker.event.received` to
  `audit_log` with `(workspace_id, ticket_ref, old_state, new_state)`.
- New table `agent_dispatch_locks` (workspace_id, key, claimed_at,
  expires_at) + Alembic migration. Key strategy: `<workspace>:<ticket_ref>`.
  TTL 60 min default, overridable per row.
- New table column `workspaces.max_concurrent_dispatches` (smallint,
  default 4), per-workspace cap override. Plus settings env
  `SHIP_DEFAULT_WORKSPACE_DISPATCH_CAP=4`.

**Shadow mode.** Poller writes audit log entries but does NOT call the
dispatcher. Validates poll diff math against prod data before we wire it
to anything that fires workflows.

**Acceptance.**
- Move a test ticket on Linear (UI) → within 5 min an audit_log row
  appears with `action='tracker.event.received'` and correct old/new
  state.
- Re-poll without changes → no duplicate audit rows (cursor advances
  monotonically on `updatedAt`).
- `agent_dispatch_locks` table exists; can claim + release a key from a
  unit test.
""",
    ),
    (
        "E16-2: dispatcher.maybe_dispatch + cascade chain limit",
        """\
**Goal.** Wire the poller's events into actual `workflow_dispatch` calls
on the GitHub Action, with safety semaphores.

**Scope.**
- New service `backend/app/services/dispatcher.py`:
  - `maybe_dispatch(workspace_id, trigger_kind, ctx)` —
    1. resolves which bundle/routine fires for `ctx.ticket_state` via
       `tracker_fsm.resolve_next_stage`.
    2. acquires per-ticket lock from `agent_dispatch_locks` (skip if
       held by another live run).
    3. checks per-workspace cap (count of locks not expired) ≤
       `workspaces.max_concurrent_dispatches`.
    4. fires `workflow_dispatch` on `ship-agent-run.yml` with
       `bundle_id + ticket_ref + run_id`.
  - On `agent_run.finish` payload: release lock for that ticket, then
    immediately call `maybe_dispatch` again with `trigger_kind=cascade`
    so the next stage starts without waiting for the poller.
- Cascade depth limit: `audit_log` query — if 3+ `agent_run.dispatch`
  rows for the same `ticket_ref` in the last 60s, refuse dispatch and
  log `dispatch.cascade_blocked`. Catches FSM loops.

**Acceptance.**
- Unit tests for lock acquire/release races, cap enforcement, cascade
  depth limit.
- Integration test: move ticket in Linear → poller picks up → dispatcher
  fires real workflow run. End-to-end latency `<60s` from Linear update
  to workflow run started.
- Cascade test: finish agent → next bundle starts within 5s (no poll
  wait).

**Depends on:** E16-1.
""",
    ),
    (
        "E16-3: SDLC bundle prompts (planning, validation, decomposition)",
        """\
**Goal.** Collapse 8 single-stage agent roles into 4 bundle prompts that
internally orchestrate sub-phases against one loaded context.

**Scope.**
- New `apps/backend/app/resources/agent_roles/planning.md` bundle:
  internal phases `context_gather → intake → BA → tech_arch → qa_arch →
  summary`. Outputs Brief + Tech plan + Test plan + acceptance into the
  ticket as a single finish call.
- New `validation.md`: phases `qa_manual → qa_auto` against the open PR.
- New `decomposition.md`: phases `brief → WBS → architecture →
  test_arch → tasks`. Replaces the chain of 4-5 separate decomposition
  routines with one anchor-driven run.
- DELETE legacy role files: `task_intake.md`, `ba_requirements.md`,
  `tech_arch_plan.md`, `qa_arch_plan.md`, plus the decomposition
  per-stage role files. No backwards compat.
- Update `tracker_fsm.py`: collapse the corresponding stage labels in
  Linear to the new bundle states (`Planned`, `In Review`, etc).
  Migration: re-provision Linear stage labels for ELS team.

**Token economics.**
- Old: 5 separate planning runs × ~50K context = ~250K input tokens.
- New: 1 bundled run loads ~50K once, then ~5K per sub-phase + ~10K
  orchestrator summary = ~85K input. ~65% saving.

**Acceptance.**
- A test ticket in `Planned` state produces all four output sections
  (Brief / Tech plan / Test plan / acceptance) in one finish call.
- Linear stage labels visible on the ticket reflect the new collapsed
  set (no orphaned old labels).

**Depends on:** E16-2.
""",
    ),
    (
        "E16-4: cutover — delete ship-trigger-schedule.yml, replace with ship-agent-run.yml",
        """\
**Goal.** Stop relying on cron. Workflow becomes a dumb runner that
takes `bundle_id + ticket_ref + run_id` from `workflow_dispatch` inputs.

**Scope.**
- New `.github/workflows/ship-agent-run.yml`: takes
  `workflow_dispatch.inputs.{bundle_id, ticket_ref, run_id}`. No more
  `shipctl trigger` picker — backend already picked the work.
- DELETE `.github/workflows/ship-trigger-schedule.yml`. No back-compat.
- DELETE `cli/lib/commands/trigger.mjs` (no longer used as picker).
  `shipctl run --bundle <id> --ticket <ref>` keeps working as before;
  trigger picker logic moves entirely server-side.
- DELETE legacy `auto_dispatch_*` functions in
  `apps/backend/app/api/v1/routes/runs.py` (the post-finish hook chain
  is replaced by dispatcher cascade).

**Cutover plan.**
1. Land E16-1 + E16-2 + E16-3 on a feature branch.
2. Flip `SHIP_TRACKER_POLL_FIRE=true` env in prod (poller fires
   dispatcher, no longer shadow). Cron `ship-trigger-schedule` keeps
   running for one tick to observe.
3. Disable cron schedule workflow. Watch audit_log for any tickets that
   don't get picked up within 10 min.
4. Delete the cron workflow file + the legacy code paths.

**Acceptance.**
- For 24h after cutover, every ticket transition shows a matching
  `agent_run.dispatch` in audit_log within 10 min (median <60s).
- No `workflow_run` events fired by the cron schedule workflow after
  cutover.
- Repo has zero references to `ship-trigger-schedule`, `_tool_trigger`,
  or `auto_dispatch_*`.

**Depends on:** E16-3.
""",
    ),
    (
        "E16-5: daily_scheduler for workspace-level routines",
        """\
**Goal.** Cover the routines that have no ticket trigger (knowledge
harvester, daily PO summary, weekly review) without resurrecting the old
cron model.

**Scope.**
- New service `apps/backend/app/services/daily_scheduler.py`: in-process
  scheduler that fires `workspace.daily_tick(workspace_id)` once per
  configured cadence per workspace. Configuration via existing
  `WorkflowSchedule` rows (or whatever the equivalent is post-refactor).
- Daily-tick handler reads the workspace's enabled workspace-level
  routines and calls `dispatcher.maybe_dispatch` with
  `trigger_kind=daily_tick`. Same lock/cap semantics apply.
- Routines categorized as workspace-level:
  - `knowledge_harvest` (Notion/docs pull → new tickets if needed)
  - `daily_summary` (PO-facing digest in Inbox)
  - `weekly_review` (DORA metrics roll-up)
- Workspace-level routines do NOT compete with ticket-bound dispatches
  for the per-workspace cap (or do — TBD; cheaper to give them a
  separate cap of 1).

**Acceptance.**
- Knowledge harvester continues to run on schedule (no ticket required).
- Daily summary fires once per day per workspace (idempotent).
- No regressions on ticket-bound dispatch latency from harvester
  competing for slots.

**Depends on:** E16-4 (cleanup done before adding new entry point).
""",
    ),
]


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not db_url:
        print("ERROR: DATABASE_URL / DB_URL not set in env", file=sys.stderr)
        return 2
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(db_url)
    qs = dict(parse_qsl(parts.query))
    sslmode = qs.pop("sslmode", None)
    qs.pop("channel_binding", None)
    db_url = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )
    connect_args: dict = {}
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = True

    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    # Fresh access tokens live in ``native_integration_credentials``;
    # ``integrations.secret_ciphertext`` holds a stale snapshot from
    # before the refresher started rotating into the native tables.
    async with Session() as session:
        result = await session.execute(
            text(
                """
                SELECT nic.secret_ciphertext
                FROM native_integration_installations nii
                JOIN native_integration_credentials nic
                    ON nic.installation_id = nii.id
                WHERE nii.workspace_id = :ws
                  AND nii.provider = 'linear'
                  AND nic.kind = 'access_token'
                  AND nic.revoked_at IS NULL
                ORDER BY nic.updated_at DESC
                LIMIT 1
                """
            ),
            {"ws": SHIP_ON_SHIP_WS},
        )
        ct = result.scalar_one_or_none()
        if ct is None:
            print(
                "ERROR: no native_integration_credentials access_token for "
                "Ship-on-Ship Linear",
                file=sys.stderr,
            )
            return 3
        token = safe_decrypt(bytes(ct))
        if not token:
            print("ERROR: access_token decrypted to empty", file=sys.stderr)
            return 4

    tracker = LinearTracker(access_token=token, team_id=ELS_TEAM_ID)

    existing = await tracker.list_projects(limit=50, query=PROJECT_NAME)
    project = next(
        (p for p in existing if (p.get("name") or "").strip() == PROJECT_NAME),
        None,
    )
    if project:
        print(f"reuse project: {project['name']}  id={project['id']}")
        project_id = project["id"]
        project_url = project.get("url") or ""
    else:
        created = await tracker.create_project(
            name=PROJECT_NAME,
            description=PROJECT_DESCRIPTION,
            body=PROJECT_BODY,
        )
        project_id = created["id"]
        project_url = created["url"]
        print(f"created project: {created['name']}  id={project_id}")
        print(f"  url: {project_url}")

    existing_titles: set[str] = set()
    rows = await tracker.list_tickets(state="all", limit=50)
    for r in rows:
        existing_titles.add((r.get("title") or "").strip())

    created_count = 0
    skipped_count = 0
    for title, body in TICKETS:
        if title in existing_titles:
            print(f"  skip (exists): {title}")
            skipped_count += 1
            continue
        ticket = await tracker.create_ticket(
            title=title,
            body=body,
            project_id=project_id,
        )
        print(f"  + {ticket.display_id}  {title}")
        print(f"    {ticket.url}")
        created_count += 1

    print()
    print(f"done. project={project_url}  created={created_count}  skipped={skipped_count}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
