# E03 walk plan — manual scenario walks

Companion to [`E03-golden-path-audit.md`](./E03-golden-path-audit.md). That spec is the *what we're checking*; this file is the *how we go through it scenario by scenario*. Living doc — updated as we walk.

## Scenario axis

State of the customer's repo when they come to Ship:

- **S1** — empty bare repo (smoke only, no real customer matches this)
- **S2** — existing project, never had Ship
- **S3** — existing project, repo already has some Ship state (old `.ship/`, partial workflows, etc.)

Plus a pre-repo step:

- **S0** — fresh user, fresh workspace, before any repo connection. Tests JIT, invite gate, Welcome panel, wizard launch.

## Pinned-axis decisions for closed beta

| Axis | Pin |
|---|---|
| Agent | **Cursor** (only validated) |
| CI | **GH Actions** (only validated) |
| Auth | Auth0 (prod) |

Tracker per dogfood project listed below.

## Dogfood mapping

Updated 2026-04-30 after maintainer review:

| Project | Scenario | Stack | Tracker | Order |
|---|---|---|---|---|
| **Ship-on-Ship** (this repo) | **S3** — most polluted; everything was iterated on it directly | Python + TS monorepo | GitHub Issues | **1st** |
| **ElMundi** (sibling project, predecessor used a Ship-like setup but never the Ship product itself) | **S2** | Next.js + Drizzle | Linear | 2nd |
| **.NET → Go migration** (TBD third-party) | **S2** | .NET → Go | TBD | 3rd |

S1 stays as a 10-minute smoke before the dogfood walks — proves the floor doesn't fall out.

## Walk order

1. **S0** — fresh user lands on Welcome / wizard. Verifies no-repo state.
2. **S1** — empty bare repo smoke (~10 min).
3. **Ship-on-Ship (S3)** — full walk, the dirtiest case first. Most likely to surface bugs.
4. **ElMundi (S2)** — second walk; test S2 cleanly after we drained S3 bugs.
5. **.NET → Go (S2)** — third walk, validates S2 across a different stack family.

Closed-beta exit when all four (S0/S1/S3/S2×2) are green and three blog posts written.

## Acceptance per scenario

Minimal exit criteria per scenario. Walked manually, results logged here under "Walk log" sections.

- **S0** — workspace home renders Welcome panel, "Continue setup" lands on wizard step 1, invite-gate is honored on a fresh email.
- **S1** — empty repo gets a seed PR with `.ship/config.yml`, `.github/workflows/run-agent.yml`, agent rule files. One scheduled routine fires within 30 min.
- **S2** — seed PR doesn't break existing CI. First clarification reaches Inbox. Tracker bidirectional check passes.
- **S3** — bundle migration preserves existing routines and prior `pipeline_runs`. No data loss. Old `.ship/config.yml` shape upgrades cleanly.

## Test-environment decisions

| Decision | Choice | Reason |
|---|---|---|
| User identity | Maintainer's existing user (`denys@bodyman.io`) | Already provisioned, already platform_admin, less setup overhead |
| Workspace | Existing personal workspace | S3 anyway — pollution is the point |
| Sandbox repo for S1 | New empty `ElMundiUA/ship-canary-empty` *(create just-in-time)* | Avoids polluting `ship-canary` |
| Logging walks | Bug-list section appended to this file | One source of truth |

## Architectural decisions (locked 2026-04-30 during S3 prep)

These shape what we expect to see in customer repos and what we're walking toward, not the current state. Existing seed bundles already mostly match.

### Customer repo gets exactly ONE workflow file

`.github/workflows/ship-trigger-schedule.yml` — cron every 30 min plus `workflow_dispatch` for the manual-override lever. **No event-triggered second file.** No `pull_request`, `issues`, `push`, `repository_dispatch` workflows.

### Why no GitHub event triggers

The PO model is asynchronous. PO files tasks in the morning, drains Inbox, walks away. Nothing in the loop justifies sub-30-min latency:

- PR review by an agent — next tick is fine. Reviewer is the agent, not a waiting human.
- "Run X now" from console — server creates a tracker issue, next tick picks it up.
- External producers (Slack / Sentry / Bunny) — post-beta concern.

Trade-off explicitly accepted: 0–30 min average latency. PO never feels it because they're not at the keyboard during execution.

### shipctl trigger is a dumb wake-up

The CLI on the cron tick:

1. Calls Ship server: "what's due for this repo right now?"
2. Server returns a list of `routine_id`s.
3. CLI runs each routine inline via `shipctl run --routine X` (which loads the pattern and starts the agent).
4. Agent does its work and writes results back to **the tracker**, never directly to ship-internal state.

The CLI has no opinions about: GitHub state, tracker state, knowledge diffs, server queues. It is a 5-line shell wrapper around two server calls.

### Tracker is the single source of truth

- All work items live in the tracker (Linear / GH Issues).
- Server-driven nudges ("rules updated", "re-bootstrap please", "manual run requested") are emitted by Ship server **as tracker issues**, never as `repository_dispatch` calls.
- PO interacts with the tracker (or the Inbox console which sits over it).
- Agent state machine = tracker state machine.

Consequence: tracker bidirectional integration is the load-bearing feature. Without it, the loop has no inbox.

### Self-care is a role, not a CLI function

GitHub polling for stuck PRs / failed runs / orphan branches is the responsibility of the **self-care agent** (`op-workflow-self-heal` pattern). It runs as a scheduled routine like any other. When it fires, the agent uses its own tools to read GitHub state and act. The CLI doesn't know about this.

### Knowledge ingestion is server-side, async

Server consumes git pushes via its own webhook (or polls), updates the methodology / bucket index out-of-band. Customer repo's cron loop is not involved.

### What this means for the seed bundle

Half-matches today (verified 2026-04-30):

- ✅ `.ship/config.yml` is rendered with `process.routines:` — the v0.7 live shape; no `lanes:`. (`backend/app/services/seed_bundle.py:260-267`)
- ✅ `.github/workflows/ship-trigger-schedule.yml` is the only cron workflow installed.
- ✅ Legacy `run-agent.yml` is NOT installed by the seed (vendored in CLI for `shipctl lanes install` only).
- ✅ The other 4 registered starter workflows (`pr-and-ci-gate`, `scheduled-sdlc-lane`, `parallel-audit-lanes`, `pipeline-self-heal`) are NOT installed by the seed.
- ❌ **`.github/workflows/ship-bootstrap.yml` IS installed by the seed** (`seed_bundle.py:278-282`). It is event-triggered on `push` to `.ship/config.yml`, which directly violates the "exactly one workflow file, no event triggers" rule above. Tracked as B8.

Upgrade-path note: `commit_bundle_pr` is purely additive (it uses `base_tree` + new entries; never deletes). When an existing repo at v0.6 re-runs the wizard, `.ship/config.yml` is overwritten cleanly (so the legacy `lanes:` block disappears), but stale workflow files from prior bundles stay on disk. Ship-on-Ship is already clean, so this is a future-repo concern, not a current blocker.

## Open questions (resolve as we go)

- Onboarding wizard UX-friendliness review — pulled out as separate task; the wizard works mechanically, but a "is it confusing" pass needs its own session.
- Need or skip: re-running S0 with a true fresh email via invite (versus reusing the maintainer's user).
- ~~Server-side `shipctl trigger` endpoint: does it already implement FSM-aware / tracker-aware "what's due" logic, or is it still the legacy "lane-based" chooser?~~ **Resolved 2026-04-30.** It's the legacy "smart CLI / dumb server" model: CLI reads `.ship/config.yml` from disk, computes due routines locally via `cli/lib/runtime/routines.mjs:dueRoutines`, then asks the server only to claim the window via `POST /v1/.../routine-runs/claim`. The CLI iterates **only** `process.routines` (line 92) — `lanes:` is still readable for `shipctl run --routine X` lookup but never produces "due" entries. So a Ship-on-Ship config with empty `process.routines: []` and populated `lanes:` is effectively *no-op on every cron tick*. The "smart server / dumb CLI" inversion is E14, post-beta.
- ~~Migration path for legacy `lanes:` configs (Ship-on-Ship's own `.ship/config.yml`) → `process.routines:`. When does it run? On bootstrap? On bundle bump? Manually?~~ **Resolved 2026-04-30.** It runs **only when the operator re-invokes the wizard** (`POST .../wizard_seed`), which composes a brand-new `.ship/config.yml` (with `process.routines:` populated, no `lanes:`) and opens a PR. There is no automatic migration on bundle-version bump and no in-place rewriter. Ship-on-Ship is currently in the in-between state (empty `process.routines: []` + populated `lanes:`) and would only flip after a wizard re-run + PR merge.

## Walk log

### S0 — fresh user, no repo

> Pending — to be walked next.

### S1 — empty repo smoke

> Pending.

### S3 — Ship-on-Ship

#### 2026-04-30 — first automated walk via Playwright (`e2e/scripts/walk-s3.mjs`)

Walk used the maintainer's authenticated session against `app.ship.elmundi.com`. Workspace `denys-99938640`, repos `ElMundiUA/ship` + `ElMundiUA/ship-canary` already activated. Bundle versions both at `0.6` (legacy `lanes:` config).

| Surface | Status | Observed |
|---|---|---|
| `/` Workspace home | 200 | "Bundle out of date" banner for both repos (v0.6 → v0.7); "Critical issues need attention" card; `BROKEN AUTOMATIONS (24H) = 7`; `FAILED PIPELINE RUNS (24H) = 0` |
| `/r/ElMundiUA/ship` | 200 | "Bundle out of date" warn. Stats: `IN_FLIGHT=20`, last 24h `59 runs (46 ok / 7 fail / 78%)`, `3 of 5 lanes wired`. Recent feed shows real GitHub Actions: "Ship · Schedule trigger 25m ago success" — cron is alive. |
| **`/inbox`** | **500** | `Application error: a server-side exception has occurred while loading app.ship.elmundi.com (see the server logs for more information). Digest: 1450717433` |
| `/knowledge` | 200 | 13 buckets, 1 article, 8 chunks, 5 sources, last indexed 1h ago. Several "EMPTY WORKSPACE" buckets visible (Architecture Decisions, …) |
| `/process` | 200 | 2 repos + 1 "Development" process with `8 states · 20 active tasks · 7 …` |
| `/settings` | 200 | All tabs render |
| `/integrations` | 200 | Custom webhook (PENDING), Notion connection visible. **No Linear, no GH Issues bound.** |
| `/members` | 200 | 4 members, denys@bodyman.io = Owner, Danylo Mochuliak shown |

#### Bug list captured from this pass

| # | Severity | Finding | Action |
|---|---|---|---|
| **B1** | **P0** | `/inbox` 500 — page-level server-side exception on the most product-critical surface | **Fixed in PR #56.** Root cause: `InboxItemRow` is a server component but passed an inline `onClick={() => onSelect?.(item.id)}` arrow function to `<Link>` (a client component). Next.js cannot serialize a non-Server-Action function across the RSC boundary, so each row threw with digest `1450717433` and the page 500'd. Fix: dropped the unused `onSelect` prop entirely (no caller used it). Sibling check: every other inline `onClick={...}` in the console is inside a `"use client"` file. |
| B2 | P1 | "Bundle out of date v0.6 → v0.7" — banner is correct. Wizard re-seed flow inspected: it overwrites `.ship/config.yml` with the new `process.routines:` shape and **drops the legacy `lanes:` block**. `commit_bundle_pr` is additive-only — it never deletes files, so the upgrade path leaves stale workflows on disk if any were installed by older bundles. Ship-on-Ship's repo is already clean (only `ship-trigger-schedule.yml` present), but the dirtiness budget for other repos is unbounded. Need to verify that an actual re-seed produces a sane PR diff and that shipctl reads `process.routines:` (not the now-absent `lanes:`) once the PR merges. | Walk the wizard end-to-end on Ship-on-Ship as the next step |
| **B8** | **P0 (arch)** | **Seed installs TWO workflows, not one** — `ship-trigger-schedule.yml` (the cron — correct) AND `ship-bootstrap.yml` (event-triggered on push to `.ship/config.yml` — VIOLATES the locked "exactly one workflow file" decision). `ship-bootstrap.yml` runs `shipctl knowledge bootstrap` after a config change; per the locked architecture, knowledge ingestion is server-side / async via webhook, not an in-repo workflow. `backend/app/services/seed_bundle.py:278-282` adds it unconditionally; `backend/app/api/v1/routes/repos.py:1178` even names it in the PR body. The 4 other registered starter workflows (`pr-and-ci-gate`, `scheduled-sdlc-lane`, `parallel-audit-lanes`, `pipeline-self-heal`) are NOT in the seed — those are reachable only via the legacy `shipctl lanes install` path and don't pollute new repos. | Drop `ship-bootstrap` from `compose_seed_files`; move "open generated knowledge PR" to a server-side webhook handler (post-beta E14 territory, but the workflow drop itself is a one-line change). |
| B9 | P3 | Cron is `*/15 * * * *`, not `*/30 * * * *` as the locked decision says (`E03-walk-plan.md` "Why no GitHub event triggers"). 15-min ticks are more responsive than 30-min — strictly inside the budget — but the doc and the workflow disagree. | Either bump the workflow to `*/30` or amend the doc; cheapest is doc-side. |
| B3 | P1 | `BROKEN AUTOMATIONS (24H) = 7` (workspace home) and `IN_FLIGHT = 20`, `59 runs (46 ok / 7 fail / 78%)` (per-repo home) are all **mis-labeled real signals from `workflow_runs`** (i.e., raw GitHub Actions runs for the repo), not from Ship pipelines. DB-confirmed counts for `ElMundiUA/ship` over 24h: 48 success + 7 failure + 6 in-flight = 61 GHA runs. Of the 7 failures, 5 are from "Ship — platform images (backend + console + landing)" — Ship's *own* docker-publish workflow that crashed during PRs #52-55, plus 1 lanes-smoke and 1 bundle-version-check. The 20 "in flight" are also GHA runs whose `workflow_run.status ∈ {in_progress, queued}` (GitHub's own webhook delivery is often eventually-consistent — some of those probably never resolved on Ship's side). `dashboard.py:593` flips `overall_status` to `critical` on any non-zero count, so a normal-noise repo permanently red-flags the workspace. The label "broken automations" promises Ship-installed automations failed; the source counts every CI run in the repo. | Filter `failed_workflow_runs` to Ship-installed workflow names (`ship-trigger-schedule`, `ship-bootstrap`) before counting; rename the metric to "FAILED CI RUNS (24H)" if the broader count is wanted. Same for `IN_FLIGHT` per-repo. Run a sweep that closes `workflow_runs` whose GitHub-side status has been `in_progress` for more than ~6h (likely missed webhooks). |
| B4 | P2 (UX) | No global "tracker not bound" alert. The fact that PRs fill the WIP list "without a tracker" is buried as descriptive text in a card subtitle, not a surfaced action. | E12 polish — add a workspace banner |
| B5 | OK | Cron loop is alive — Schedule trigger ran 25m ago and succeeded. Legacy "smart agent" loop is functional. | No action |
| B6 | P1 | "20 active tasks" on `/process` is **not** mock data — it's `pipeline_runs.status ∈ (running, queued, pending)` plus open `inbox_items` (`processes.py:_tasks_for`). The 20 in-flight runs are real cron-fired execution windows that never finished writing back. This conflicts with the locked architecture ("tracker is the single source of truth — all work items live in the tracker"); `/process` is currently a Ship-internal *execution monitor*, not a tracker view. Ship-on-Ship has no tracker bound, so `_tasks_for` only sees pipeline-runs and inbox items — works mechanically, but mis-named. | Decide intent: either (a) keep `/process` as the "execution windows" view and rename / reframe so users don't expect tracker tasks, or (b) make it pull from the bound tracker (matches the locked architecture and lets us drop `pipeline_runs` from the surface). Out of scope for E03 walk; resolve when E14 lands. Also: 20 stale in-flight runs deserve their own check — likely the CLI never emits a "completed" callback when it no-ops the routine. |
| B7 | P2 | Workspace `13 buckets / 1 article / 8 chunks` is **all leftover state**, not a healthy seed signal. DB query confirms: 10 buckets came from `scripts/reseed_knowledge_buckets.py` run by hand on 2026-04-26 (the `RECOMMENDED_BUCKETS` set in `services/knowledge_reseed.py`); 3 buckets + 1 article (`phase7-smoke.md` in `e2e-upload-molkg5fo`) were created today by an e2e test that left debris. The expected E01 T07 starter ("How this workspace was set up") is **not** present. | Clean the `e2e-*` buckets and confirm new workspaces seed correctly when E01 T07 is wired (see B10). |
| **B10** | **P1** | E01 T07 is checked off in the task list, but `seed_default_knowledge` (the function in `services/seed_bundle.py:423` that creates the `product-knowledge` bucket and the "How this workspace was set up" article) is **never called from production code** — only from `tests/test_seed_default_knowledge.py`. New workspaces today get **no buckets and no starter article** automatically; the 10 buckets we see on Ship-on-Ship were planted by hand via `scripts/reseed_knowledge_buckets.py`. So E01 T07 was implemented mechanically but not wired to the JIT-provisioning / workspace-creation path. | Wire `seed_default_knowledge` into the workspace-creation flow (probably in `security/auth0.py`'s JIT path or a workspace-create service). For closed beta we may want both the 10-bucket recommended set AND the starter article — or pick one and document the choice. |

### S2 — ElMundi

> Pending.

### S2 — .NET → Go

> Pending.
