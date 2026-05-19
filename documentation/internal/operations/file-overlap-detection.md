# File-overlap detection (Level 2 file coordination)

**Ticket:** ELS-154 (A5.1 design) · **Implementation:** ELS-155+ (A5.2)  
**Feature flag:** `SHIP_ENABLE_FILE_OVERLAP_WARNINGS` (default off)

## Problem

Parallel `dev_implementation` runs on sibling tickets in the same Linear project can each open a PR that touches the same high-risk files (notably Alembic migrations) without any stage surfacing what siblings already changed. On 2026-05-18, ELS-143/144/147 each added a conflicting `0074_*` revision under `apps/backend/migrations/versions/` because dispatch serialized one ticket at a time (project WIP lock) but did not show open sibling PR diffs before the agent wrote code.

Level 1 (`blocks` gate in `dispatcher.py`) prevents starting work when Linear says ticket A blocks B. Level 2 (this feature) **warns** when unblocked siblings already have open PRs touching migrations or identical paths.

## Behaviour (v1)

1. **When:** `maybe_dispatch` for `routine_id=developer` (`dev_implementation`), not `planning:anchor`, feature flag on, after repo pick and before `workflow_dispatch`.
2. **Discovery:** Open PRs on all workspace-activated repos; map PR → ticket ref via title regex (`app/services/ticket_ref.py`); keep siblings in the same Linear `project_id`.
3. **Overlap classes:**
   - **Schema:** sibling PR touches any path under `migrations/versions/`.
   - **Hard:** same non-lockfile path in two or more sibling PR file lists.
4. **Output:** Markdown blockquote at the top of the dev agent prompt; audit payload on `agent_run.dispatch` plus `dispatch.file_overlap_warning` when overlap exists.
5. **Never blocks dispatch** in v1.

## Operator guidance

- PR titles must include a parseable ticket ref (`feat(ELS-99): …`) for sibling mapping.
- On warning: coordinate migration revision numbers or rebase after the cited PR merges; do not add a duplicate `0074_*.py`-style file.
- Enable per workspace only after engineer sign-off: `SHIP_ENABLE_FILE_OVERLAP_WARNINGS=true`.

## Repro (ELS-143/144/147)

Three inbox tickets in one project, each adds `apps/backend/migrations/versions/0074_*.py` on sequential dispatches with prior PRs still open. With the flag on, the second and third dispatches should show a blockquote naming the prior open PR(s) and the migrations directory.

## Telemetry (A5.3)

Structured rows live in `agent_run.dispatch` payload as `file_overlap_warnings` (sibling ref, PR number, `overlap_kind`, paths). Honour rate is computed when the dev PR lands.

## Rollback

Set `SHIP_ENABLE_FILE_OVERLAP_WARNINGS=false` or remove the CLI prepend — no DB migration.
