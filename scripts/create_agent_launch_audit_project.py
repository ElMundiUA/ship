"""One-shot: create the 'Agent launch pre-flight audit' project + tickets in
Ship-on-Ship Linear (elship org). Idempotent: re-runs detect existing project
by name and skip ticket creation when one with the same title already exists.

Usage (from repo root, with backend deps in venv):

    python -m scripts.create_agent_launch_audit_project
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

# Load .env (DB_URL, encryption key) the same way scripts/* expect.
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import safe_decrypt
from backend.app.integrations.linear.tracker_adapter import LinearTracker


SHIP_ON_SHIP_WS = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
ELS_TEAM_ID = "854ffe38-2ac7-404f-b482-7260ac707593"

PROJECT_NAME = "Agent launch pre-flight audit"
PROJECT_BODY = """\
Pre-flight audit before opening the autonomous agent pipeline to real tickets.

Eight gaps surfaced on 2026-05-06; this project tracks every fix needed before
flipping the kill-switch on customer cron.

Work order (do not start agents on real tickets until P1+A1+A2+A4 are merged):

1. **P1** — promote `decomposition` to a first-class process in
   `processes.py` with routines (oriented like `development`); customer cron
   drives it via `shipctl run --routine X`. Symmetric to SDLC, no post-finish
   hook chaining.
2. **A1 + A2 + A3 + A4 + A5** — input filter for the agent picker:
   only `active` projects, no orphan tickets, no `needs:clarification` /
   `blocked` overlays, project body flips to `parked` (not `active`) at
   `planning_done` so PO promotes manually.
3. **ELS-72** — AST tooling (`find_definition` / `find_references` /
   `symbol_outline`). Without it agents grep-ad themselves into bad
   refactors. Must land before any non-trivial code work.
4. **K5** — inject parent project body (Brief / WBS / Architecture / Test
   architecture sections) into SDLC startup context so the child-ticket
   agent sees the surrounding plan. Harvester unchanged.
5. **B-walk** — pass over all 24 `agent_roles/*.md` prompts: drop refs to
   tools removed in ELS-76/77/78, confirm explicit outcome contracts.
6. **E1** — pick-race protection in `get_next_task` (advisory lock or
   claim-transition before returning to CLI).
7. **G5** — kill-switch (env / feature flag) for instant pause.
8. **H** — end-to-end smoke test plan and execution.

Knowledge feedback decision (K3/K4): harvester stays as-is, single sanctioned
write path = `Clarification(answered)` only. Agents read project context but
do not write to knowledge.

Reference: `~/.claude/projects/.../memory/project_agent_launch_audit.md`.
"""


TICKETS: list[tuple[str, str]] = [
    (
        "P1: promote `decomposition` to first-class process with routines",
        """\
**Problem.** `decomposition` FSM exists in
`backend/app/services/catalog.py:386` (`default_planning_process_config`)
but `routines: {}` and shipctl has zero references to its stages. After
`start_decomposition` puts the anchor in `stage:wbs`, nothing triggers BA.

**Decision.** Lift `decomposition` into a first-class process in
`backend/app/api/v1/routes/processes.py` next to `development`, with
routines per stage so customer GitHub Actions cron drives it through
`shipctl run --routine X`. Symmetric to SDLC, no post-finish hook chaining.

**Scope.**
- `processes.py` — register `decomposition` process alongside `development`,
  expose its stages (`wbs/architecture/test_architecture/tasks/planning_done`)
  via the same shape used by `task_intake/...`.
- `catalog.py` — drop the "anchor-driven, not cron-driven" comment, populate
  `routines` per stage so they show up in the customer config.
- `linear_provisioner.py` — verify the decomposition stage labels are still
  provisioned correctly (already true at lines 87-93).
- shipctl side: ensure `shipctl run --routine` resolves decomposition
  routines without code changes (process is just another id).

**Acceptance.**
- A new `.ship/config.yml` from `shipctl init` includes the decomposition
  routines.
- Running `shipctl run --routine decomposition.wbs` against a workspace
  with anchors in `stage:wbs` actually picks one and dispatches BA.
""",
    ),
    (
        "A1: filter `get_next_task` by WorkspaceProjectPriority.state='active'",
        """\
**Problem.** `backend/app/api/v1/routes/agent_runs.py:239`
(`get_next_task`) calls `gateway.list_tickets(state=state, limit=10)` and
returns `rows[0]` with no project-state filter. A ticket whose project is
in `planning` or `parked` is still picked up.

**Fix.**
- After `list_tickets` returns, join each row's `project_id` against
  `WorkspaceProjectPriority(workspace_id, project_native_id)` and drop rows
  where `state != 'active'`.
- Pre-requisite: A4 must surface `project_id` from
  `linear/tracker_adapter.list_tickets`.
- Pick the first survivor; if none, return `ticket=None`.

**Acceptance.**
- Unit test: ticket from a `parked` project is not returned.
- Unit test: ticket from an `active` project IS returned.
- Audit log records `agent_run.pick_skipped` with reason for each skipped
  row so it's visible why the picker fell through.
""",
    ),
    (
        "A2: flip Drafts → Parked (not Active) at `planning_done`",
        """\
**Problem.** `_flip_drafts_row_to_active` in
`backend/app/api/v1/routes/agent_runs.py:432` sets
`row.state = "active"` (line 502) when decomposition completes. Per product
call, completed decomposition should land the project in `parked` so the PO
explicitly promotes to `active` when ready to ship.

**Fix.**
- Rename `_flip_drafts_row_to_active` → `_flip_drafts_row_to_parked`,
  change line 502 to `row.state = "parked"`.
- Update doc/comments at the following sites (search for
  `Drafts → Active`):
  - `backend/app/services/catalog.py:423-424, 487-489, 492-494`
  - `backend/app/resources/agent_roles/developer.md:32`
  - `backend/app/api/v1/routes/dashboard_priorities.py:579-583`
  - `backend/app/services/linear_provisioner.py:85-86, 134-137`

**Acceptance.**
- Decomposition that reaches `planning_done` flips dashboard row to
  `parked`, not `active`.
- All Drafts → Active phrases in doc/comments are gone.
""",
    ),
    (
        "A3: default `WorkspaceProjectPriority.state` for new projects",
        """\
**Problem.** `backend/app/db/models/dashboard_priorities.py:84` has
`server_default=text("'active'")`. With A2 in place (Drafts → Parked at
planning_done), the entry-point default also matters: if a project is
created and immediately gets a priorities row at `'active'`, the picker
takes it before decomposition even starts.

**Fix.**
- Change default to `'planning'` (project starts in Drafts) so the dashboard
  drafting UX owns the lifecycle: Drafts → (decomp runs) → Parked → (PO) →
  Active.
- Migration to backfill any existing rows that should have been planning.

**Acceptance.**
- Newly created projects appear in the Drafts bucket.
- Existing rows in `'active'` are left alone (don't reclassify live work).
""",
    ),
    (
        "A4: surface `project_id` in `list_tickets` + reject orphan tickets",
        """\
**Problem (1).** `linear/tracker_adapter.py::list_tickets` (lines 122-224)
projects the issue's identifier/title/url/state but NOT its `project { id }`.
A1 cannot run without that field.

**Problem (2).** A ticket without a project is a symptom of someone bypassing
the dashboard flow; the picker should reject it instead of silently working
on it.

**Fix.**
- Extend `list_tickets` GraphQL projection to include
  `project { id }` and surface it as `project_id` in the returned dict.
- In `get_next_task`, drop rows where `project_id is None` and write an
  inbox notification ("orphan ticket: <ref>") so the operator sees them.
- Add `AuditLog` entry `agent_run.orphan_skipped`.

**Acceptance.**
- Unit test: ticket created via Linear UI without a project is not picked
  and a single inbox notification is emitted.
- Unit test: ticket created via `_tool_create_project` flow has
  `project_id` and is picked normally.
""",
    ),
    (
        "A5: exclude `needs:clarification` / `blocked` labels from picker",
        """\
**Problem.** Tickets carrying overlay labels (`needs:clarification`,
`blocked`) are frozen — operator owes a reply. The picker shouldn't hand
them back to an agent until those labels are cleared.

**Fix.**
- In `get_next_task` (or upstream in the Linear adapter's
  `list_tickets`), filter rows whose `labels` include any signal label from
  `linear_provisioner.SIGNAL_LABELS` that means "frozen".
- Decision needed in PR: do we filter at adapter or route layer? Adapter
  side avoids round-tripping rows we'll throw away.

**Acceptance.**
- Unit test: ticket with `needs:clarification` is not picked.
- Unit test: ticket with `blocked` is not picked.
- Unit test: same ticket without those labels is picked.
""",
    ),
    (
        "E1: pick-race protection in `get_next_task`",
        """\
**Problem.** Two parallel `shipctl run` calls on the same FSM stage can
both call `get_next_task`, both get the same ticket, both call
`transition_ticket`. Atomicity of the transition prevents double-write,
but only one agent's work is meaningful — the other wastes a Cursor run.

**Fix (decide in PR).** Two viable mechanics:

(a) **Postgres advisory lock keyed on `(workspace_id, ticket_id)`** held
    for the duration of the pick handler. If a second caller arrives, it
    falls through to the next ticket in the list.

(b) **Pre-return claim transition** — move the ticket out of the polled
    state inside the same handler before returning to the CLI. Simpler;
    drawback is a stranded ticket if the runner dies between pick and
    actual run start.

**Acceptance.**
- Two parallel `shipctl run --routine X` calls against a workspace with
  ≥2 tickets in stage produce two distinct tickets, never the same one.
""",
    ),
    (
        "K5: inject parent project body into SDLC startup context",
        """\
**Problem.** When an agent picks up a child ticket spawned by
decomposition (`task_intake` stage), it sees only the ticket body. The
project body — Brief, WBS, Architecture, Test architecture — is the
context that explains *why* this ticket exists and how it fits into the
broader plan. Today that context is invisible to the SDLC chain.

**Decision (from audit).** Do NOT extend the harvester to ingest project
bodies into knowledge. Instead, fetch and inject the parent project body
into the run's session context at startup time.

**Fix.**
- Find the assembly point where session context is built for SDLC runs
  (likely `services/agent/topic.py` or wherever
  `/agent-runs/...` prepares the prompt).
- For each picked child ticket, fetch its `project_id` (depends on A4),
  load the project body via `LinearTracker.get_project`, and stitch the
  Brief / WBS / Architecture / Test architecture sections into the
  session context as a "Project context" block.
- Harvester remains unchanged.

**Acceptance.**
- A test SDLC run on a child ticket from a decomposed project shows the
  parent project body in its system message.
- Cap the injected text (e.g. 8KB) so a runaway project body doesn't
  blow context.
""",
    ),
    (
        "B-walk: audit all 24 agent role prompts after tool consolidation",
        """\
**Problem.** Several rounds of tool consolidation landed (ELS-76 dropped
4 redundant tools; ELS-77 consolidated KB search; ELS-78 added 6 mutating
+ read-only Navigator tools). Role prompts in
`backend/app/resources/agent_roles/` may still reference dropped tools or
miss new contracts.

**Scope.** Walk every prompt and verify:
- No reference to a tool that no longer exists.
- Each role explicitly states its outcome contract: which of
  `ready_next_step | needs_clarification | blocked | out_of_scope`,
  with what `stage_next` / `process` fields.
- Each role describes when to call `knowledge_search` vs `search_repo_kb`
  vs `search_code` (post ELS-77 consolidation).
- `denied_tools` matches role intent (e.g. reviewer can't commit/push).

**Files.** All 24 in `backend/app/resources/agent_roles/`:
ba, bug-triage, clarification, daily-retro, designer, desktop-reviewer,
developer, game-balance-reviewer, intake, learning-capture, ml-reviewer,
mobile-reviewer, navigator, process-reviewer, product-manager,
qa-architect, qa-automation, qa-engineer, qa-reviewer, reviewer,
security-officer, system, tech-architect, tech-reviewer,
workflow-self-heal.

**Acceptance.**
- Diff per role attached to the PR.
- No prompt references a removed tool.
- Each non-routine role has an explicit outcome contract block.
""",
    ),
    (
        "G5: kill-switch for autonomous agent pickup",
        """\
**Problem.** If something goes wrong post-launch (cost spike, cascading
bad transitions, prompt regression), there's no central knob to halt
autonomous pickup. We'd be racing customer cron schedules.

**Fix.**
- Env-driven feature flag (e.g. `SHIP_AGENT_PICKUP_ENABLED=false`) read
  in `get_next_task`.
- When false, return `TaskResponseOut(ticket=None)` and emit an
  `AuditLog` entry `agent_run.pickup_disabled` per call so we can see
  who's still calling.
- Default: `true` once we ship; controllable via deploy without code
  push.

**Acceptance.**
- Toggle the env var, customer cron sees `ticket: null` immediately,
  no Cursor runs spawned.
""",
    ),
    (
        "H: end-to-end smoke test plan",
        """\
**Goal.** Before flipping the kill-switch on customer cron for real
tickets, verify the full chain on a controlled workspace.

**Smoke matrix.**

- **H1.** One safe SDLC ticket → run end-to-end (intake → BA → tech-arch
  → dev → review), watching each stage by hand.
- **H2.** A ticket whose project is in `parked` → confirm picker does
  NOT return it (tests A1).
- **H3.** Two parallel `shipctl run --routine X` calls → confirm they
  pick different tickets (tests E1).
- **H4.** A ticket on a topic the KB knows nothing about → confirm
  agent emits `needs_clarification` instead of inventing.
- **H5.** End-to-end decomposition: create test project → click
  "Hand off to decomposition" → reach `planning_done` → confirm
  project lands in **parked** bucket on the dashboard (tests A2 + P1).

**Workspace.** Probably Ship-on-Ship; decision pending.

**Acceptance.** All five pass; any failure blocks public launch and
becomes its own bug ticket.
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
    # asyncpg doesn't accept psycopg's `sslmode` query param — strip it and
    # pass `ssl=True` via connect_args when present.
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

    async with Session() as session:
        row = (
            await session.execute(
                select(Integration)
                .where(
                    Integration.workspace_id == SHIP_ON_SHIP_WS,
                    Integration.kind == "linear",
                )
                .order_by(Integration.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            print("ERROR: no Linear Integration row for Ship-on-Ship", file=sys.stderr)
            return 3
        token = safe_decrypt(row.secret_ciphertext)
        if not token:
            print("ERROR: Integration secret decrypted to empty", file=sys.stderr)
            return 4

    tracker = LinearTracker(access_token=token, team_id=ELS_TEAM_ID)

    # Idempotency 1: existing project by name?
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
            name=PROJECT_NAME, body=PROJECT_BODY
        )
        project_id = created["id"]
        project_url = created["url"]
        print(f"created project: {created['name']}  id={project_id}")
        print(f"  url: {project_url}")

    # Idempotency 2: skip ticket creation when one with the exact title
    # already exists in this team (regardless of project).
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
