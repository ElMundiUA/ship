"""Add ELS-90 / ELS-91 / ELS-92 follow-up tickets to the existing
Agent launch pre-flight audit project. Idempotent on title.

Usage (from repo root):

    python -m scripts.add_audit_followups
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import safe_decrypt
from backend.app.integrations.linear.tracker_adapter import LinearTracker


SHIP_ON_SHIP_WS = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
ELS_TEAM_ID = "854ffe38-2ac7-404f-b482-7260ac707593"
PROJECT_ID = "2cebe53d-4e58-48ac-bbcf-4549e728aff8"


TICKETS: list[tuple[str, str]] = [
    (
        "ELS-92: asymmetric intake filter — Todo without `stage:*` lands in task_intake",
        """\
**Problem.** Today the picker's filter for ``state=task_intake`` is
``Linear state == Todo AND label == stage:task_intake``. **Nobody
sets that label** on a freshly-created ticket — Linear-native tickets
land in Todo without any ``stage:*`` lab and become invisible to
intake. The pipeline is dead-on-arrival for any workflow other than
Navigator's ``create_ticket`` (which doesn't even apply
``stage:task_intake`` either).

Discovered while planning ELS-90 (Linear-native auto-onboard) — the
auto-onboard moves the project to ``active`` but the picker still
won't see the tickets because of the missing label.

**Decision.** Intake is the **default entry point** — it picks Todo
tickets that haven't been classified yet. Every other stage is an
explicit transition (the previous agent put the label there).

**Fix.** Change the Linear adapter's ``_fsm_filter`` for the
``task_intake`` stage to:

```
Linear state == Todo
AND (NO ``stage:*`` label  OR  label == ``stage:task_intake``)
```

GraphQL via ``IssueFilter.or`` + ``labels.every.name.nin`` (no stage
label from the canonical list) plus ``labels.some.name.eq:
stage:task_intake`` (explicit re-classification). The canonical
``stage:*`` list lives in ``SHIP_FSM_STAGES`` already.

``bug_triage`` stays **explicit** — operator chooses "this is a
bug" and labels it ``stage:bug_triage`` themselves. Only intake
catches unclassified tickets by default.

All other stages (``ba_requirements`` / ``tech_arch_plan`` / ...)
keep their current ``Todo + stage:<name>`` filter — those labels
are set by the upstream agent on transition.

**Acceptance.**

- A fresh Linear ticket in ``Todo`` with no labels is returned by
  ``GET /tracker/next?state=task_intake``.
- A ``Todo`` ticket with label ``stage:ba_requirements`` is NOT
  returned by intake (it's later in the chain).
- A ``Todo`` ticket with label ``stage:task_intake`` is still returned
  by intake (explicit re-classification works).
- Adapter unit test pins the GraphQL filter shape.

**Scope.** ~30 lines in
``backend/app/integrations/linear/tracker_adapter.py::_fsm_filter`` +
test. No DB schema changes. No CLI changes. No role-prompt changes.

**Why this is a launch blocker.** Without it, even after merging the
12-PR audit stack, no Linear-native ticket can ever enter the
pipeline through intake. The whole "agents work on real customer
tickets" claim is broken.
""",
    ),
    (
        "ELS-91: project-state ↔ ticket-state sync (Active ↔ Todo, Parked/Drafts → Backlog)",
        """\
**Problem.** When the PO toggles a project Active ↔ Parked in the
dashboard (or via Navigator's ``set_priority_state``), the picker
already gates pickup correctly (ELS-80). But the **tickets stay in
Todo** in Linear's UI, looking like queued work. Operator opens
Linear → sees "lots of work in progress" while agents silently
ignore it. Disconnect between project state and visible tracker
state.

**Decision.** Project state drives child-ticket state on the tracker:

| Project state | Action |
|---|---|
| ``active`` (just promoted) | move children's ``Backlog`` → ``Todo`` |
| ``parked`` / ``planning`` (Drafts) | move children's ``Todo`` → ``Backlog`` |
| (any) | leave ``In Progress`` / ``Review`` / ``Done`` alone |

The ``In Progress`` / ``Review`` carve-out matters: parking a project
mid-flight should NOT yank an in-progress ticket back to Backlog;
it just stops *new* pickups. The single in-flight ticket completes,
then no new work starts on that project.

**Fix.**

* New helper ``_sync_project_tickets_for_state`` (likely in
  ``services/agent/project_state_sync.py`` or co-located with the
  flip site). Walks: project_id → list child tickets filtered by
  Linear state → bulk transition.
* Wire into all three trigger sites:
    1. ``api/v1/routes/dashboard_priorities.py`` — dashboard UI flip
       (``POST /priorities/{id}/state``).
    2. ``services/agent/tools.py::_tool_set_priority_state`` — Navigator
       command.
    3. ``api/v1/routes/agent_runs.py::_flip_drafts_row_to_parked`` —
       post-decomposition auto-flip.
* Best-effort: tracker 5xx logs and continues — does NOT roll back
  the priorities row flip. Audit trail captures both.
* Per moved ticket: ``priorities.synced_ticket_state`` audit row with
  ``from``/``to``/``ticket_ref``/``project_id``.

**Acceptance.**

- Park a project with 5 ``Todo`` children + 1 ``In Progress`` child:
  the 5 ``Todo`` move to ``Backlog``; the ``In Progress`` stays put.
- Activate the same project: the 5 ``Backlog`` children move to
  ``Todo``; ``In Progress`` unchanged.
- Tracker 5xx mid-sync: priorities row still flipped, partial
  transitions audited; remainder retried on next operator action
  (or ELS-N follow-up adds an explicit "retry sync" tool).

**Linear adapter requirements.** Adapter needs a way to enumerate
issues by ``project_id + state``. ``list_tickets`` already accepts
``state``; extend to accept ``project_id`` filter (Linear GraphQL
``IssueFilter.project.id.eq``).

**Scope.** ~150 lines: helper, three trigger wires, adapter
extension, tests.
""",
    ),
    (
        "ELS-90: auto-onboard Linear-native projects on first picker encounter",
        """\
**Problem.** ELS-80 + ELS-82 gate the picker by
``WorkspaceProjectPriority.state == 'active'``. Combined with default
``state='planning'`` for new rows, this means **a project created
directly in Linear (not via Navigator's ``create_project``) becomes
permanently invisible to the picker** — it has no priorities row at
all, so the gate skips it forever.

This contradicts Ship's product call: wrap the user's existing
tracker workflow, don't force them onto Ship's UI. If the operator
prefers Linear (or Jira), they should be able to file projects +
tickets there and the agents pick up.

**Decision.** When the picker encounters a ticket with a
``project_id`` for which no ``WorkspaceProjectPriority`` row exists,
**auto-create the row with ``state='active'``** and proceed to pick
the ticket.

Justification for ``active`` (not ``planning``): the ticket is
already flowing through FSM stages — that's evidence enough of
operator intent. They created the project in Linear AND created
tickets under it AND the tickets reached an FSM stage. No further
opt-in needed.

The new row also triggers ELS-91's state sync: any other Backlog
children of the same project move to Todo.

**This does NOT change ELS-82's default** — ``planning`` remains the
default for ``_tool_create_project`` (Navigator's flow), which sets
state explicitly. Auto-onboard is a separate code path keyed on
"row missing" specifically.

**Fix.**

* In ``get_next_task`` after ELS-80's priority check: when
  ``priority_state is None`` AND ``project_id`` is set, INSERT row
  with ``state='active'`` and **fall through** to picking the ticket
  (don't skip).
* Audit row: ``agent_run.project_auto_onboarded`` with ``project_id``,
  ``ticket_ref``, ``fsm_stage``.
* Ordinal: append (max ordinal + 1).

**Acceptance.**

- Operator creates a project in Linear directly + a Todo ticket in
  intake stage. ``GET /tracker/next?state=task_intake`` returns the
  ticket. ``WorkspaceProjectPriority`` now has a row in
  ``state='active'`` for that project.
- A second pickup against the same project hits the existing row
  (no second auto-onboard).
- Operator parks the auto-onboarded project — ELS-91 moves children
  to Backlog. Picker stops returning them.

**Scope.** ~40 lines in ``get_next_task`` + helper + audit + test.
Depends on ELS-92 (intake filter — without it, the auto-onboarded
ticket is invisible to intake anyway).
""",
    ),
]


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 2
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
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
            print("ERROR: no Linear Integration row", file=sys.stderr)
            return 3
        token = safe_decrypt(row.secret_ciphertext)
        if not token:
            return 4

    tracker = LinearTracker(access_token=token, team_id=ELS_TEAM_ID)

    existing = await tracker.list_tickets(state="all", limit=50)
    existing_titles = {(r.get("title") or "").strip() for r in existing}

    created = skipped = 0
    for title, body in TICKETS:
        if title in existing_titles:
            print(f"  skip (exists): {title}")
            skipped += 1
            continue
        ticket = await tracker.create_ticket(
            title=title, body=body, project_id=PROJECT_ID
        )
        print(f"  + {ticket.display_id}  {title}")
        print(f"    {ticket.url}")
        created += 1

    print(f"\ndone. created={created}  skipped={skipped}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
