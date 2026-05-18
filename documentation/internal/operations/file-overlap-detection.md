# File-overlap detection (Level 2 file coordination)

**Epic:** Inbox UX overhaul — parallel dev collision prevention  
**Tickets:** [ELS-154](https://linear.app/elship/issue/ELS-154) (A5.1 design), A5.2 (implementation), A5.3 (telemetry)  
**Status:** Design accepted in A5.1; implementation ships behind a feature flag in A5.2 (default off).

## Problem

Ship serializes dev work per Linear project via the project-WIP lock in `dispatcher.py` (one non-`planning:anchor` ticket in flight per project). Level 1 dependency gating (`get_ticket_blockers` / `dispatch.blocked_by_dep`) refuses dispatch when Linear says ticket A blocks B.

Neither gate prevents **sequential** sibling tickets from each opening an open PR that touches the same high-risk paths. On 2026-05-18, ELS-143, ELS-144, and ELS-147 (same project, no `blocks` edges) each added a conflicting `0074_*` Alembic revision under `apps/backend/migrations/versions/` because no stage surfaced sibling open PR diffs before the dev agent wrote code.

## Goal

Before `dev_implementation` runs, the dev agent sees a concise, actionable warning when another **open** PR in the **same Linear project** touches paths that would be high-risk if this ticket modifies them too. Warnings are **warn-only** in v1 — dispatch still fires (`fired=True`).

Honour rate (dev agent coordinates instead of duplicating work) is measured in A5.3.

## Approach

Compute warnings **server-side at dispatch time** and echo the same markdown on `GET /v1/agent-runs/tracker/next` so the runner cannot miss them.

```
dispatch ticket (ELS-X, project P)
    → list open PRs on workspace activated repos
    → parse ticket ref from PR title (first match)
    → keep PRs whose mapped ticket has project_id == P
    → fetch changed paths per sibling PR (cap 100 files)
    → classify schema + hard overlap
    → inject blockquote at top of dev agent user message
    → audit: dispatch.file_overlap_warning (+ structured payload for A5.3)
```

Discovery is **reactive**: classify from sibling open PR diffs only. v1 does not predict the dispatch ticket's future files from ticket-body keywords.

### PR → ticket mapping

Reuse the existing title regex (today in `github_app.py` ~line 699):

```python
TICKET_REF_PR_TITLE_RE = re.compile(r"\b([A-Z][A-Z0-9]{2,9}-\d+)\b")
```

Extract to `app/services/ticket_ref.py` as `parse_ticket_refs_from_pr_title(title) -> list[str]`; **first ref is primary** for project mapping; additional refs go to audit only.

Filter siblings with `get_ticket_snapshot(ticket_ref)["project_id"]` compared to the dispatch ticket's `project_id`. PRs with no parseable ref, closed PRs, or tickets in other projects are ignored.

### Overlap classes (v1)

| Class | Trigger | Dev guidance |
| ----- | ------- | ------------ |
| **Schema** | Any sibling open PR path under `**/migrations/versions/` | WAIT/ALIGN on revision numbers; do not independently add another `0074_*.py` |
| **Hard** | Same non-lockfile path in **two or more** sibling open PR file lists | List intersecting paths; coordinate or rebase |

Lockfiles excluded from hard overlap: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`.

Soft overlap (same test dir, README only) does **not** warn in v1. Same file with non-overlapping hunks still warns (path-level).

The dispatch ticket's not-yet-open PR is excluded from hard intersection (only counts sibling PRs).

## Prompt contract

Full blockquote text is stored on `TaskTicketOut.file_coordination_warning` (string, not JSON). The CLI prepends it **before** `## Routine instructions` in `renderPrompt()` when set.

Example (schema):

```markdown
> **File-coordination warning**: PR #276 (ELS-144) is OPEN and modifies `apps/backend/migrations/versions/`. If your task also adds a migration, coordinate revision numbers with that PR or rebase after it merges. Do not independently add another `0074_*.py`.
```

`developer.md` documents that this blockquote precedes routine/role template content.

## When the check runs

In `maybe_dispatch`, after project context + dependency gate (§5–5a) and project-WIP gate (§5b), **before** `workflow_dispatch`:

- `routine_id == "developer"` (`fsm_stage == "dev_implementation"`)
- Ticket does **not** have `planning:anchor` (decomposition — no dev code)
- Feature flag on (workspace or env; A5.2, default off)
- `trigger_kind=cascade` still runs the check (siblings are other tickets)

On overlap: dispatch proceeds; audit action `dispatch.file_overlap_warning` (or warnings embedded in `agent_run.dispatch` payload).

## Audit payload (A5.3 contract)

Structured warnings on `agent_run.dispatch` audit rows:

```json
{
  "file_overlap_warnings": [
    {
      "sibling_ticket_ref": "ELS-144",
      "pr_number": 276,
      "repo": "org/ship",
      "pr_html_url": "https://github.com/...",
      "overlap_kind": "schema",
      "paths": ["apps/backend/migrations/versions/"]
    }
  ]
}
```

`GET /tracker/next` returns the rendered blockquote from the latest dispatch audit for the ticket, or recomputes with the same helper (deterministic).

## Components (A5.2)

| Module | Responsibility |
| ------ | -------------- |
| `app/services/file_overlap.py` | `build_file_coordination_warning(...) -> str \| None` |
| `app/services/dispatcher.py` | Invoke builder; attach warnings to dispatch audit |
| `app/services/ticket_ref.py` | Shared `TICKET_REF_PR_TITLE_RE` + parser |
| `app/integrations/github/code_host_adapter.py` | `list_open_pull_requests(repo, limit=30)`; reuse `list_pull_request_files(limit=100)` |
| `app/api/v1/routes/agent_runs.py` | `TaskTicketOut.file_coordination_warning` |
| `packages/cli/lib/commands/run.mjs` | Prepend blockquote in `renderPrompt()` |

No DB migration in v1. No `pr_to_ticket` table.

## Edge cases

| Case | Behaviour |
| ---- | --------- |
| Multiple ticket refs in PR title | First ref maps; others in audit only |
| PR on fork / repo not in activated set | Not discovered; silent degrade |
| Sibling PR closed | Ignored (`state=open` only) |
| `planning:anchor` | Skip overlap check |
| GitHub API failure on file list | Log debug; dispatch without warning |
| Monorepo PR >100 files | Warn on intersection within cap |
| Unmapped PR title ("fix stuff") | Ignored — no false sibling |

## Repro: ELS-143 / 144 / 147

Three sibling tickets in one project, each opens a PR adding `apps/backend/migrations/versions/0074_*.py`:

1. ELS-143 dispatches first — no prior sibling PR → no warning (expected).
2. ELS-144 dispatches — open PR from ELS-143 touches `migrations/versions/` → **schema warning** naming PR # and ELS-143.
3. ELS-147 dispatches — open PRs from 143 and 144 → warning references prior PR(s) and migrations directory.

Manual staging replay with flag on is the acceptance gate for A5.2.

## Non-goals (v1)

- Refuse-to-dispatch on overlap
- Implicit coupling heuristics (e.g. same model file)
- Soft overlap (tests/README)
- Ticket-body path prediction
- Cross-project / cross-workspace warnings
- Private-fork PRs the App cannot see
- Level 3 DAG dispatcher

## Risk and rollback

| Risk | Mitigation |
| ---- | ---------- |
| False positives (unrelated shared path) | v1 limited to migrations dir + exact path match across siblings |
| False negatives (PR title lacks ticket ref) | Document `feat(ELS-NNN): …` convention; branch fallback deferred |
| Latency (N PRs × file lists) | Cap open PR scan (~30/repo); filter by title + `project_id` before file fetch |
| Rollback | Disable feature flag; remove CLI prepend |

## Test architecture (A5.2)

| Layer | Focus |
| ----- | ----- |
| Unit | `file_overlap.py`: schema/hard detection, lockfile exclusion, project filter, title-parse |
| Integration | `maybe_dispatch`: audit payload + `fired=True` on overlap |
| CLI | `renderPrompt` prepends when `file_coordination_warning` set |
| E2E | Deferred (needs open PR fixtures) |

Fixtures under `apps/backend/tests/fixtures/file_overlap/`. Extend `test_dispatcher.py` and add `test_file_overlap.py`.

## Rollout

1. A5.1 — this document + operator review (1+ engineer sign-off before flag enable).
2. A5.2 — implement behind flag; enable per workspace gradually.
3. A5.3 — telemetry: warnings emitted vs honour rate when dev PR lands.

## Open questions

1. Post-mortem URL for ELS-143/144/147 — link when published.
2. Branch-name fallback if title-parse miss rate is high — optional A5.2+.

## References

- Level 1 dependency gate: `dispatcher.py` §5a (`dispatch.blocked_by_dep`), shipped `e702819`.
- Project-WIP gate: `dispatcher.py` §5b (`dispatch.project_busy`).
- PR title regex: `github_app.py` `_TICKET_REF_PR_TITLE_RE`.
- Linear `project_id`: `tracker_adapter.py` `get_ticket_snapshot`.
