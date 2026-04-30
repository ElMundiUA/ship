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

It already matches: the latest seed only emits the schedule workflow, not the legacy `run-agent.yml` (vendored in CLI for `shipctl lanes install` only). `process.routines:` is the live config shape; `lanes:` lingers in already-installed repos (Ship-on-Ship S3 included) and needs an upgrade-path migration.

## Open questions (resolve as we go)

- Onboarding wizard UX-friendliness review — pulled out as separate task; the wizard works mechanically, but a "is it confusing" pass needs its own session.
- Need or skip: re-running S0 with a true fresh email via invite (versus reusing the maintainer's user).
- Server-side `shipctl trigger` endpoint: does it already implement FSM-aware / tracker-aware "what's due" logic, or is it still the legacy "lane-based" chooser? **Next thing to check.**
- Migration path for legacy `lanes:` configs (Ship-on-Ship's own `.ship/config.yml`) → `process.routines:`. When does it run? On bootstrap? On bundle bump? Manually?

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
| **B1** | **P0** | `/inbox` 500 — page-level server-side exception on the most product-critical surface | **Fixed.** Root cause: `InboxItemRow` is a server component but passed an inline `onClick={() => onSelect?.(item.id)}` arrow function to `<Link>` (a client component). Next.js cannot serialize a non-Server-Action function across the RSC boundary, so each row threw with digest `1450717433` and the page 500'd. Fix: dropped the unused `onSelect` prop entirely (no caller used it). Sibling check: every other inline `onClick={...}` in the console is inside a `"use client"` file. |
| B2 | P1 | "Bundle out of date v0.6 → v0.7" — banner is correct (we just bumped in #54). Need to verify the wizard's upgrade-path actually applies cleanly. | Walk the wizard |
| B3 | P1 | `BROKEN AUTOMATIONS (24H) = 7` on workspace home — origin unclear. Can't tell if real failures or stale signal. | Trace which signal feeds this counter |
| B4 | P2 (UX) | No global "tracker not bound" alert. The fact that PRs fill the WIP list "without a tracker" is buried as descriptive text in a card subtitle, not a surfaced action. | E12 polish — add a workspace banner |
| B5 | OK | Cron loop is alive — Schedule trigger ran 25m ago and succeeded. Legacy "smart agent" loop is functional. | No action |
| B6 | curio | "20 active tasks" attributed to the Development process — but no tracker is bound. Where are those tasks coming from? Repo code metadata? Mock fallback? | Investigate (might surface another mock leak) |
| B7 | P2 | Workspace shows `13 buckets / 1 article / 8 chunks` — knowledge surface is alive but mostly empty. The single article is the seed E01 T07 starter ("How this workspace was set up") — confirm. | Read article via API |

### S2 — ElMundi

> Pending.

### S2 — .NET → Go

> Pending.
