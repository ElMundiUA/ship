# E14 — Server-side smart orchestration

**Priority:** P0 — closed-beta exit blocker
**Effort:** M–L (~3 days)
**Owner:** TBD

> **Scope decision (2026-04-30):** E14 is **in scope for the closed beta**,
> not a post-beta refactor. Without it, the dogfood walks demonstrate the
> legacy "smart agent / dumb server" loop, not the product Ship is built
> to ship. Treat this like E05 (adoption gauntlet): something the closed
> beta cannot exit without.

## Goal

Move the entire routine-run loop (tracker pull → context pack → LLM invoke → result write-back) **into the Ship backend**. The customer-side CLI becomes a thin auth-passing proxy. Patterns gain explicit FSM-stage and structured-output declarations so an agent run is deterministic input → deterministic output.

## Why

Current ("smart agent / dumb CLI") model has the agent runtime fetching tracker state through its own tools, deciding what to do, and writing back through tools. That made the agent the integration layer — fragile, non-deterministic, hard to audit, and forces every customer's agent runtime (Cursor Cloud, Claude Code, etc.) to ship the same tracker glue.

Target ("smart server / dumb agent / dumb CLI") model:

- **Server** owns: cron eligibility, tracker pull, FSM transitions, policy injection, LLM invocation, structured-output parse, result write-back, audit log.
- **CLI** is a 3-line wrapper: `POST /v1/.../routines/{id}/run` with auth header, print response.
- **Agent** is a pure transformation: prompt + context → structured output. Knowledge retrieval stays agent-side (semantic search is open-ended).

Why server-side instead of fat CLI: all the building blocks already live in `backend/app/integrations/gateway/` (uniform `TrackerGateway` Protocol with implementations for Linear, GitHub Issues, Notion, Jira, GitLab, Azure DevOps) and `backend/app/services/tracker_fsm.py`. The CLI is Node; reimplementing this in JS would double the surface area and create drift.

## Existing building blocks (verified 2026-04-30)

| Component | Location | Status |
|---|---|---|
| Generic `TrackerGateway` Protocol — `list_tickets`, `transition`, `comment`, `create_ticket`, `list_comments`, `remove_label`, `list_issues_with_label` | `backend/app/integrations/gateway/tracker.py:86` | ✅ Defined |
| Linear adapter | `backend/app/integrations/linear/tracker_adapter.py:34` (`LinearTracker`) | ✅ Implements Protocol |
| GitHub Issues adapter | `backend/app/integrations/github/issues_tracker.py` | ✅ Implements Protocol |
| Notion / Jira / GitLab / Azure DevOps adapters | `backend/app/integrations/{notion,jira,gitlab,azure_devops}/` | ✅ Stubbed/partial; gated behind `SHIP_ENABLE_PARTIAL_TRACKERS` |
| FSM definitions | `backend/app/services/tracker_fsm.py` (13 KB; `FsmState`, `render_tracker_fsm`) | ✅ |
| OAuth + token storage | `linear_oauth.py`, `notion_oauth.py`; secrets in `Integration` rows | ✅ |
| LLM client (OpenAI + Anthropic) | `backend/app/services/agent/client.py`, `embedding.py` | ✅ |
| Workspace policies preamble fetch | `/v1/.../policies-preamble` (already used by current `shipctl run`) | ✅ |

## What's missing — actual scope of E14

### Tasks

#### T01 — `routines/{id}/run` endpoint **[M]**

- New POST route `backend/app/api/v1/routes/repos.py` (or new file `routines.py`):
  - `POST /v1/workspaces/{ws}/repos/{repo}/routines/{routine_id}/run`
  - Body: `{ event, window_key, scheduled_for, github_metadata? }`
  - Steps in handler:
    1. Resolve routine config (read from `WorkspaceRepo.config_yaml` cached on activation, or pull fresh from GitHub via App API).
    2. Resolve declared `fsm_stage` from pattern frontmatter (loaded from `artifacts/patterns/{routine.pattern}/ARTIFACT.md`).
    3. Pull candidate tickets via `TrackerGateway.list_tickets(state=fsm_stage)`. If none and pattern requires tickets → return `{ status: "noop", reason: "no work" }`.
    4. Pack context: ticket body, comments, attached PR refs, workspace policies preamble, repo facts (last sha, branch).
    5. Render full prompt = pattern body + context.
    6. Invoke LLM via `services/agent/client.py`. Force JSON output via response-format / tool-call.
    7. Parse structured output against the pattern's declared `output_schema`. On parse failure → `{ status: "error", reason: "schema mismatch", raw: <…> }`.
    8. Apply tracker actions from output: `transition`, `comment`, `create_ticket` (whatever the schema declared).
    9. Audit log every step.
    10. Return `{ status: "completed", routine_id, ticket_ref, actions: [...], llm_metrics: {...} }`.

#### T02 — Pattern frontmatter extension **[S]**

- Add two optional frontmatter fields to `artifacts/patterns/<id>/ARTIFACT.md`:
  - `fsm_stage:` — the tracker state this pattern operates on (e.g. `ba_requirements`, `dev_implementation`). When absent → routine is "context-free" (e.g. `daily-digest`); orchestrator skips ticket pull.
  - `output_schema:` — JSON schema or short DSL describing what the agent should return. Orchestrator parses + validates.
- Update 4 reference patterns first: `role-intake`, `role-ba`, `role-developer`, `role-qa-architect`. Others can land later.
- Validate via `scripts/ship_artifact_check.py` (already exists for SHA checks; extend for schema lint).

#### T03 — Thin CLI **[S]**

- Rewrite `cli/lib/commands/run.mjs` to ~50 lines:
  - Take `--routine`, `--event`, `--workspace`, `--repo` flags.
  - Resolve workspace + repo IDs via the existing `/v1/workspaces` and `/v1/.../repos` endpoints.
  - POST to the new `routines/{id}/run` endpoint.
  - Print response. Map status to exit code (0 = completed/noop, 1 = error, 3 = LLM failed).
- Drop pattern fetching, idempotency markers, prompt rendering, callback wiring — all of this moves to the server.
- Keep `--dry-run` flag: server returns `{ would_pull, would_invoke, would_apply }` without side effects.

#### T04 — `shipctl trigger` becomes a server call too **[S]**

- Server endpoint `POST /v1/workspaces/{ws}/repos/{repo}/routines/due` returning `{ due: [routine_id, ...] }`.
- Server reads cached `WorkspaceRepo.config_yaml`, evaluates cron locally (server-side), returns due list.
- CLI `trigger.mjs` shrinks to a fetch+print.
- Removes the duplicated cron evaluator currently living in `cli/lib/runtime/routines.mjs`.

#### T05 — `WorkspaceRepo.config_yaml` cache **[S]**

- New column on `workspace_repos`: `config_yaml text NULL`, `config_yaml_sha text NULL`, `config_yaml_fetched_at timestamptz`.
- Populated:
  - On repo activation (initial seed PR has the config; cache it post-merge).
  - On webhook from GitHub when `.ship/config.yml` changes.
  - On manual refresh from console.
- Migration `0047_workspace_repo_config_cache.py`.

#### T06 — Pattern-by-pattern migration **[M]**

For each role pattern (`role-intake`, `role-ba`, `role-developer`, `role-qa-architect`, `role-security-officer`, `role-tech-architect`, `flow-qa-acceptance`, `flow-pr-self-review`):

- Declare `fsm_stage` matching its name.
- Declare `output_schema` — what the agent should return (e.g. for `role-ba`: `{ acceptance_criteria: [...], questions: [...], state_transition: "tech_arch_plan" | "needs_clarification" }`).
- Update prompt body to remove "go fetch the ticket" instructions; the context arrives in-place. Replace with "you receive the ticket below; process it and emit the schema".
- Pattern bodies become smaller and more deterministic.

Context-free patterns (`flow-daily-retro`, `flow-learning-capture`, `op-workflow-self-heal`) stay roughly as-is, but the `op-workflow-self-heal` pattern in particular needs a pass — currently relies on agent tools to read GitHub. Move "list stuck PRs" into the server context-pack step.

#### T07 — Tests **[M]**

- New `backend/tests/test_v1_routines_run.py`:
  - Routine with `fsm_stage` and tickets present → full happy path: pull, invoke (mocked LLM), apply.
  - Routine with `fsm_stage` and 0 tickets → returns `noop`, no LLM invocation, no tracker writes.
  - Routine without `fsm_stage` (context-free) → invokes directly, applies whatever the schema returns.
  - Output schema mismatch → `error` status, raw output captured.
  - Tracker call fails → `error` with provider error code.
  - Audit log entries cover every step.
- Snapshot tests for the 4 reference patterns' migrated frontmatter.

#### T08 — Backward-compat shim **[S]**

- Existing `shipctl run` callers (open-source CLI users on the released v0.13.x) hit a stub that calls the new endpoint internally so they don't break. Old marker-file logic is preserved on disk for one release cycle, then removed.

## Acceptance — E14 done when

- [ ] `POST /v1/.../routines/{id}/run` lives in backend, handler complete.
- [ ] Pattern frontmatter format documented + 4 reference patterns updated.
- [ ] CLI `run.mjs` and `trigger.mjs` shrunk to thin proxies.
- [ ] Migration 0047 applied; repo activation populates `config_yaml`.
- [ ] Test suite green covering happy / no-work / schema-mismatch / tracker-fail.
- [ ] Smoke against live: a routine fires on cron tick, pulls a Linear ticket, posts a comment back via the gateway. End-to-end on at least one dogfood project.

## Risks / unknowns

- **LLM cost**: Ship pays for tokens now (closed-beta scope). Set per-workspace daily/monthly budget caps in T07; surface in `/admin/kpi`.
- **Schema drift**: pattern bodies and orchestrator must stay in sync. Snapshot tests + an `assert_pattern_matches_schema` helper.
- **Self-care patterns** (`op-workflow-self-heal`) currently use agent tools to poll GitHub. We move that to server-side context-pack — needs care so we don't double-write.
- **`config_yaml` cache freshness**: customer can edit `.ship/config.yml` between webhook and our refresh — short window of staleness. Document; not a blocker.
- **Concurrency**: same routine triggered from cron and a manual run-now race. The existing `routine-runs/claim` mutex handles this; the new endpoint reuses it.

## Out of scope

- BYO LLM key from customer side (post-beta — adds `agent_provider_override` per workspace).
- Self-hosted agent runtime (where customer's GH Actions does the LLM call) — defer; closed-beta = Ship-side LLM.
- Streaming responses (closed-beta = blocking response, ≤5 min per routine; if longer, async via run-token + callback like today).
- Multi-LLM fan-out (one pattern → multiple model providers in parallel).
- Custom tracker providers beyond the 6 already wired.

## Order of operations

E14 lands **inside** the closed beta — the dogfood walks have to demonstrate
the smart-server loop, not the legacy "smart agent" one, because that's the
product the beta is meant to validate.

Concrete ordering:

1. **S3 Ship-on-Ship walk on legacy model** (done 2026-04-30) — surfaced the
   bug list (B1–B10) plus the open-question pinned items, all of which now
   have answers in `E03-walk-plan.md`. We **do not** repeat the full walk on
   every other scenario in legacy mode; the legacy run was a baseline and a
   forcing function for finding the gaps E14 must close.
2. **E14 in 2 PRs** (T01-T05 then T06-T08) — server endpoint + thin CLI +
   pattern frontmatter + reference-pattern migration + tests.
3. **Resume the walks** (S0, S1, S2 ElMundi, S2 .NET→Go) on the new model.
   These are the walks the blog posts actually describe.
4. Three blog posts merged.
5. Closed beta exit declared.

Treat the legacy-model S3 finding list as scaffolding: it told us where the
seams are. The seams move with E14 — for example, B6 ("/process surfaces
pipeline_runs not tracker tickets") and B3 ("BROKEN AUTOMATIONS counts every
GHA run") both stop being problems once tracker is the SoT and Ship-owned
workflow names are the only ones the dashboard scopes to.
