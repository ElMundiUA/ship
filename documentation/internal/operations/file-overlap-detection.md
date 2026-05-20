# File-overlap detection (Level 2 file coordination)

**Ticket:** ELS-154 (A5.1 design) · **Implementation:** ELS-155+ (A5.2)  
**Feature flag:** `SHIP_ENABLE_FILE_OVERLAP_WARNINGS` (default off)  
**Epic:** Inbox UX overhaul post-mortem (ELS-143/144/147 migration collision, 2026-05-18)

## Problem

Parallel `dev_implementation` runs on sibling tickets in the same Linear project can each open a PR that touches the same high-risk files (notably Alembic migrations) without any stage surfacing what siblings already changed. On 2026-05-18, ELS-143/144/147 each added a conflicting `0074_*` revision under `apps/backend/migrations/versions/` because dispatch serialized one ticket at a time (project WIP lock) but did not show open sibling PR diffs before the agent wrote code.

Level 1 (`blocks` gate in `dispatcher.py`) prevents starting work when Linear says ticket A blocks B. Level 2 (this feature) **warns** when unblocked siblings already have open PRs touching migrations or identical paths. Level 3 (DAG dispatcher) is out of scope.

## Behaviour (v1)

| Step | Detail |
| --- | --- |
| **When** | `maybe_dispatch` for `routine_id=developer` (`dev_implementation`), not `planning:anchor`, feature flag on, after `_pick_dispatch_repo` and before `workflow_dispatch`. Cascade dispatches still run the check (siblings are other tickets). |
| **Discovery** | Open PRs on **all** workspace-activated repos (`WorkspaceRepo` + `GitHubInstallation`, same join as dispatch repo pick but no single-repo limit). Cap ~30 open PRs per repo. |
| **Mapping** | PR title → ticket ref via `TICKET_REF_PR_TITLE_RE` (`app/services/ticket_ref.py`); first ref is primary; extra refs audit-only. Sibling = mapped ticket’s Linear `project_id` matches dispatch ticket. |
| **Overlap** | **Schema:** any sibling path under `migrations/versions/`. **Hard:** same non-lockfile path in two or more sibling PR file lists. Lockfiles excluded: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`. |
| **Output** | Markdown blockquote prepended to dev agent prompt (CLI `renderPrompt`); `file_coordination_warning` on `GET /v1/agent-runs/tracker/next`. |
| **Dispatch** | Warn-only — `fired=True` always when other gates pass. Audit: `agent_run.dispatch` payload + optional `dispatch.file_overlap_warning` row. |

### Prompt contract (example)

```markdown
> **File-coordination warning**: PR #276 (ELS-144) is OPEN and modifies `apps/backend/migrations/versions/`. If your task also adds a migration, coordinate revision numbers with that PR or rebase after it merges. Do not independently add another `0074_*.py`.
```

Hard overlap adds explicit shared paths. Dev role template (`developer.md`) documents that this blockquote appears **above** `## Routine instructions`.

## Edge cases

| Case | Expected behaviour |
| --- | --- |
| PR title has multiple ticket refs | First ref for mapping; others in audit only |
| Sibling PR on non-activated repo / fork | Not discovered; silent degrade |
| Sibling PR closed | Ignored (`state=open` only) |
| `planning:anchor` | Skip overlap check |
| GitHub API failure listing files | Debug log; dispatch without warning |
| Monorepo PR >100 files | Cap at `list_pull_request_files` default; warn on intersection within cap |
| Soft overlap (tests/README only) | No warning in v1 |
| Same file, non-overlapping hunks | Still warn (path-level) |
| Unparseable PR title | Ignored — no false sibling |
| Feature flag off | No check, unchanged dispatch |

## Operator guidance

- PR titles must include a parseable ticket ref (`feat(ELS-99): …`) for sibling mapping.
- On warning: coordinate migration revision numbers or rebase after the cited PR merges; do not add a duplicate `0074_*.py`-style file.
- Enable per workspace only after engineer sign-off: `SHIP_ENABLE_FILE_OVERLAP_WARNINGS=true`.
- Warnings do **not** block dispatch; honour rate is tracked in A5.3.

## Repro (ELS-143/144/147)

Three inbox tickets in one project, each adds `apps/backend/migrations/versions/0074_*.py` on sequential dispatches with prior PRs still open. With the flag on, the second and third dispatches should show a blockquote naming the prior open PR(s) and the migrations directory.

## Implementation map (A5.2)

| Component | Role |
| --- | --- |
| `app/services/file_overlap.py` | Discovery, classification, markdown render |
| `app/services/dispatcher.py` | Invoke builder; attach audit payload |
| `app/services/ticket_ref.py` | Shared PR title regex |
| `app/integrations/github/code_host_adapter.py` | `list_open_pull_requests`, `list_pull_request_files` |
| `app/api/v1/routes/agent_runs.py` | `TaskTicketOut.file_coordination_warning` |
| `packages/cli/lib/commands/run.mjs` | Prepend blockquote in `renderPrompt` |

## Telemetry (A5.3)

Structured rows in `agent_run.dispatch` payload as `file_overlap_warnings` (sibling ref, PR number, `overlap_kind`, paths). Honour rate computed when the dev PR lands.

## Non-goals (v1)

- Refuse-to-dispatch on overlap
- Soft overlap heuristics, ticket-body path prediction
- `pr_to_ticket` table, cross-project warnings
- Private-fork PRs the App cannot see

## Rollback

Set `SHIP_ENABLE_FILE_OVERLAP_WARNINGS=false` or remove the CLI prepend — no DB migration.
