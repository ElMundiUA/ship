# ElMundi — reference deployment

This chapter is the **receipts**: not the philosophy of Ship, but the **paper trail** you can audit after the fact. It is the wiring diagram for **ElMundi** — a battle-tested reference implementation in a public monorepo (`website/` + `.github/workflows/` + Ship content under `tools/*`).

If you are new here, use **[Getting started](../../getting-started/index.md)** to wire the repo, then this **Examples** chapter for **filenames**, **cron minutes**, and **domains**. Read **[The book](../../framework/index.md)** when you want the full **why** (one long page — scroll).

!!! tip "Adopt Ship into ElMundi (agent-driven)"
    Use **[Adoption → ElMundi rollout](../../adoption/elmundi.md)** with `prompts/onboarding/adopt-ship-generic.md` + `adopt-ship-elmundi.md` in Cursor (or another agent from the [launch matrix](../../adoption/agent-launch-matrix.md)). Submodule target: `tools/ship` → [ElMundiUA/ship](https://github.com/ElMundiUA/ship).

---

### How Ship maps to ElMundi (one table)

| Ship concept (framework) | Where it lives in ElMundi (examples) |
|--------------------------|----------------------------------------|
| **Tracker as system of record** | Linear — projects, states, labels; see [SDLC scheduled](#sdlc-scheduled). |
| **Scheduler / clock** | GitHub Actions — cron + `workflow_dispatch`; full table in [Workflows catalog](#workflows-catalog). |
| **Deterministic pick** | Implemented as project-specific automation scripts/workflows; details in SDLC + operator docs. |
| **Versioned prompts** | `prompts/cloud-agent/*.md` — one role per file; see [Prompt catalog](../../prompts-workflows/index.md#prompt-catalog). |
| **Agent launch** | `cloud-agent-launch.mjs` + Cursor Cloud Agent API — secrets in [Operator setup](#operator-setup) and [Tools → Cursor Cloud Agent](../../tools/index.md#cursor-cloud-agent). |
| **Delivery lane grid** | `linear-agent-sdlc-scheduled.yml` — minutes and roles in [SDLC scheduled](#sdlc-scheduled). |
| **Audit loop (separate board)** | `linear-agent-daily-audits.yml` — tech / QA / security roles; [Daily audits](#daily-audits). |
| **Self-heal (diagnostics)** | `workflow-self-heal.yml` — **not** the same as SDLC intake; see workflows catalog. |
| **Hosted E2E** | Playwright under `website/` + `e2e-regression-dev.yml` — URLs in [Pre-release & E2E](#pre-release-e2e). |
| **Promote / release** | Bunny promote workflows + manual gates — same page. |

**Rule of thumb:** if a row in this table is missing in *your* fork, you have not finished porting Ship — you have only installed a chatbot.

---

### Reading order for implementers

1. [SDLC scheduled](#sdlc-scheduled) — understand the **grid** before you touch prompts.  
2. [Operator setup](#operator-setup) — secrets, env mirroring, local `cli` debugging.  
3. [Workflows catalog](#workflows-catalog) — which YAML file answers which question.  
4. [Daily audits](#daily-audits) — add **after** delivery is boring.  
5. [Pre-release & E2E](#pre-release-e2e) — when you are ready for hosted regression and promote discipline.  
6. [Cursor Automations](#cursor-automations) — only if you are comparing or migrating from Cursor’s first-party automation product.

---

### What you will find (topic index)

| Topic | Page |
|-------|------|
| SDLC cron, picks, columns | [SDLC scheduled](#sdlc-scheduled) |
| Daily tech / QA / security audits | [Daily audits](#daily-audits) |
| Dev/prod URLs, E2E, promote | [Pre-release & E2E](#pre-release-e2e) |
| Secrets, variables, local debug | [Operator setup](#operator-setup) |
| YAML file → purpose | [Workflows catalog](#workflows-catalog) |
| Cursor Automations vs our default | [Cursor Automations](#cursor-automations) |

---

### Repository

- **Ship** (this manual, CLI, prompts, scripts): **[ElMundiUA/ship](https://github.com/ElMundiUA/ship)** — canonical open package.  
- **ElMundi reference wiring** (GitHub Actions next to `website/`, org-specific secrets, cron as deployed today): **[ElMundiUA/elmundi](https://github.com/ElMundiUA/elmundi)**.

---

### Contribute your own reference

If your team has a production setup worth sharing, follow **[Contribute a reference setup](../contribute-reference-setup.md)**.

### Honest scope (read before you fork)

Names like **ElMundi pre-release**, **Bunny**, **dev.elmundi.com** are **this org’s** choices. Your fork should rename projects, URLs, and secrets — **Ship** (the **Framework** tab) stays valid.

We publish the manual at **https://ship.elmundi.com.ua** — the same Next.js site that serves this page also exposes a downloadable PDF of *The book* at [/book.pdf](/book.pdf).

### Environment identifiers (Linear / GitHub)

The **Ship** package ships **no** org-specific UUIDs in code. For this reference wiring, set values in **`.env`** (see repository root **`.env.example`**):

| Variable | Role |
|----------|------|
| `LINEAR_SDLC_PROJECT_ID` or `LINEAR_SDLC_PROJECT_NAME` | Delivery lane — here **ElMundi pre-release** |
| `LINEAR_TEAM_UUID`, `LINEAR_STATE_BACKLOG_UUID`, `LINEAR_LABEL_BUG_UUID` | `scripts/create-prerelease-e2e-bugs.mjs` |
| `LINEAR_E2E_ISSUE_SEED_PROJECT_ID` | Optional; else that script uses `LINEAR_SDLC_PROJECT_ID` |
| `GITHUB_PR_PREVIEW_COMMENT_MARKERS` | PR preview marker(s) in comments — default in tooling is `<!-- ship-pr-preview -->`; legacy workflows may emit a second marker (comma-separate both if you need compatibility) |
| `E2E_ISSUE_SEED_BASE_URL` | `create-prerelease-e2e-bugs.mjs` — dev URL in issue bodies; else `PLAYWRIGHT_BASE_URL`, else placeholder |
| `IN_REVIEW_NOTIFY_EMAIL` | Optional SendGrid recipient (no default address in repo) |

---

## SDLC — six columns + GitHub schedule {#sdlc-scheduled}

**Purpose:** operate the **scheduled SDLC lane** (intake → developer) with Linear + GitHub Actions + Cursor Cloud Agent.  
**Audience:** engineers and platform operators.  
**Outcomes:** you understand columns, cron behaviour, secrets, manual runs, and queue hygiene.

The grid below is **deliberate infrastructure**: each even-hour slot runs **one** job (intake **or** clarification **or** BA **or** developer), concurrency groups wait instead of tearing a pick mid-flight, and **self-heal** stays on **odd** hours so “fix the pipeline” never pretends to be “do intake.” That separation is the difference between tuning a schedule and debugging mysterious crosstalk.

### Overview

Linear is the **source of truth** for issue state.

Stages **intake → dev** run from **`.github/workflows/linear-agent-sdlc-scheduled.yml`**. Cursor Automations are **off** for these stages in this deployment. GitHub **schedules** runs or accepts **`workflow_dispatch`**; role work runs on **Cursor Cloud Agent** via `runtime/scripts/cloud-agent-launch.mjs`.

**Self-heal:** **`workflow-self-heal.yml`** runs on a separate cadence (CLI analysis first, optional Cloud Agent). It is **not** the same job as SDLC intake/BA/developer.

!!! tip "Ship documentation site"
    **Production:** [ship.elmundi.com.ua](https://ship.elmundi.com.ua/). **Local:** from the **ElMundiUA/ship** repo root, `npm install` → `npm run landing:dev` and open <http://127.0.0.1:3000/docs>. The same site exposes a downloadable PDF of *The book* at [/book.pdf](/book.pdf). Start at [Start here](../../index.md).

### Schedule (canonical)

**UTC, even hours:**

| Minute | Role |
|--------|------|
| :10 | intake |
| :25 | clarification |
| :40 | BA |
| :55 | developer |

**Odd hours :15:** `workflow-self-heal.yml` (see [Workflows catalog](#workflows-catalog)).

Do not duplicate this grid elsewhere — **link here** from other pages.

### Required GitHub secrets

Repository **ElMundiUA/elmundi** must have **repository** secrets (or org secrets scoped to this repo):

| Secret | Purpose |
|--------|---------|
| `LINEAR_API_KEY` | Pick scripts, `runtime/dist/cli.js start`, agent comments |
| `CURSOR_API_KEY` | `cloud-agent-launch.mjs` (Cursor Cloud Agent) |

Without `LINEAR_API_KEY`, the **Pick issue** step fails with `MISSING_LINEAR_API_KEY`. Check **Settings → Secrets and variables → Actions** (exact names).

**Important:** each cron run is a **separate** workflow run with **one** job only (intake **or** clarification **or** BA **or** developer). A green run **does not** imply developer ran: only **SDLC Developer** (cron **:55**) moves tickets **Todo → In Progress**. Run titles include `cron …` or `manual …`.

### Scope: project, Backlog, Todo

**SDLC lane:** all pick scripts (intake, clarification, BA, developer) filter issues to the configured delivery project — in this reference deployment **ElMundi pre-release**, via **`LINEAR_SDLC_PROJECT_ID`** or **`LINEAR_SDLC_PROJECT_NAME`**.

**Backlog** is **human-only** — automation does **not** pick from it. To start intake → … → dev, move the card to **Todo** and keep it in the pre-release project.

**Developer pick:** **Todo** + **`ready:developer`** + the same project. Other Todo cards without that label are intentionally skipped.

### Columns (six)

| # | Status | Meaning |
|---|--------|---------|
| 1 | Backlog | Human triage only; no SDLC pick |
| 2 | Todo | Intake → clarification → BA → (with `ready:developer`) developer |
| 3 | In Progress | Implementation |
| 4 | In Review | PR, preview, QA |
| 5 | Done | Complete |
| 6 | Blocked | Stop |

### GitHub: pick → Cloud Agent

| Job | Pick script | Cloud prompt | Agent |
|-----|-------------|--------------|-------|
| intake | `runtime/scripts/pick-intake-issue.mjs` | `prompts/cloud-agent/intake.md` | Only if an issue was picked |
| clarification | `runtime/scripts/pick-clarification-issue.mjs` | `prompts/cloud-agent/clarification.md` | Only if picked |
| ba | `runtime/scripts/pick-ba-issue.mjs` | `prompts/cloud-agent/ba.md` | Only if picked |
| developer | `runtime/scripts/pick-next-dev-issue.mjs` | `prompts/cloud-agent/developer.md` | After `cli start` → In Progress |

**Concurrency:** `concurrency.group` per stage (`linear-sdlc-intake`, …), **`cancel-in-progress: false`** — newer scheduled runs wait for the previous one (fewer torn picks).

**Skills:** checkout includes `.cursor/skills`; each `SKILL.md` (truncated) is embedded by `scripts/cloud-agent-launch.mjs`.

**Linear from the agent:** provide **LINEAR_API_KEY** to Cloud Agent env (see [Cursor Cloud secrets](../../tools/index.md#cursor-cloud-agent)).

### Manual run

```bash
gh workflow run linear-agent-sdlc-scheduled.yml -f role=developer -f issue=ELM-XX
## role: intake | clarification | ba | developer
```

### Daytime checklist

**Goal:** clear queues by **end of day**, not only “by morning”.

1. **Morning (5–10 min):** GitHub → Actions → **Linear SDLC (scheduled)** — recent runs green; if red, logs + Linear label `auto:failed`.
2. **Queues:** `scripts/agent-queue-snapshot.mjs` — counts per pick pool and next candidate.
   ```bash
node runtime/scripts/agent-queue-snapshot.mjs
   # JSON: node runtime/scripts/agent-queue-snapshot.mjs --json
   ```
3. **Cron throughput:** at most **one** task per slot **every ~2h** per role. Heavy Backlog → Todo promotion is the human throttle.
4. **Catch up:** repeated **workflow_dispatch** with `issue=ELM-XX` and `role` (subject to Cursor limits).
5. **Evening:** snapshot again + board (Backlog / Todo / In Review). Escalate stuck items manually.

**Ticket stays Todo after a green run:** see [Troubleshooting](../../framework/index.md#when-things-break) (verify `cli start` / team state).

### Optional extensions

- **A5 PR review / A6–A7 webhooks** — Cursor or extend GitHub; see `linear-agent-webhooks.yml`.
- **No double-dev:** after `start`, developer is **In Progress** and no longer matches Todo pick.

### Labels

Base set in `config/sdlc-workflow.json` + `dist/agent-contracts.js`. Sync labels to Linear:

```bash
node runtime/scripts/sync-linear-team-labels.mjs
## --dry-run — plan only
```

Includes `ready:developer`, `stage:intake`, `needs:clarification`, `audit:auto`, `source:*`, etc.

### Pre-release, dev hosting, E2E, prod promote

Canonical guide: **[Pre-release & E2E](#pre-release-e2e)** — domains, `docker-build-push`, prod promote workflows, scheduled **E2E regression (dev)**, PR branch rules (`fix/ELM-XX-auto`), manual smoke `website/docs/manual-smoke-dev-to-prod.md`.

**Related:** [Glossary](../../framework/index.md#vocabulary) · [Workflows catalog](#workflows-catalog) · [Troubleshooting](../../framework/index.md#when-things-break).

---

## Daily audits — tech architect, QA architect, security {#daily-audits}

Think of this as the **second board**: while SDLC moves cards on pre-release delivery, daily audits **look sideways** at the repo and only open tickets when there is something to say. Three roles run **once per day** from **`.github/workflows/linear-agent-daily-audits.yml`** using the same **Cursor Cloud Agent** entrypoint as SDLC (`runtime/scripts/cloud-agent-launch.mjs`). They **do not** consume the pre-release SDLC queue; they analyse the repo (and for security, **Snyk JSON**) and **create** Linear issues in dedicated projects when justified.

### “No fabrication” rule

Prompts require **verifiable evidence** (file paths, Snyk records, existing tests). If there is nothing new — **no** Linear tickets and **no** filler comments. Security: if Snyk reports **0** vulnerabilities, the Cloud Agent step is **skipped**.

### Linear projects

| Project | Writers |
|---------|---------|
| **ElMundi tech debt** | Tech architect, QA architect |
| **ElMundi security** | Security officer |

Bootstrap once:

```bash
node runtime/scripts/ensure-audit-linear-projects.mjs
## plan only: ... --dry-run
```

Optional GitHub **variables** (skip name lookup):

- `LINEAR_TECH_DEBT_PROJECT_ID`
- `LINEAR_SECURITY_PROJECT_ID`

Optional: `LINEAR_TECH_DEBT_PROJECT_NAME`, `LINEAR_SECURITY_PROJECT_NAME` if you rename projects.

### Labels

After label list changes:

```bash
node runtime/scripts/sync-linear-team-labels.mjs
```

Dedup-related: `source:tech-architect`, `source:qa-architect`, `source:security-officer`, `audit:auto`.

### Secrets & schedule

| Secret / var | Purpose |
|--------------|---------|
| `LINEAR_API_KEY` | Project resolution in `cloud-agent-launch` + agent updates |
| `CURSOR_API_KEY` | Start Cloud Agent |
| `SNYK_TOKEN` | Security job only (missing token → Snyk step skipped with log line) |
| `LINEAR_TEAM_KEY` | Same as SDLC, usually `ELM` |

**UTC:** tech **05:20**, QA **05:50**, security **06:20** — adjust cron in the workflow.

### Manual run

**Actions → Linear daily audits → Run workflow** — pick a role or `all`.

### Local debug

```bash
# From ElMundiUA/ship repo root (in elmundi monorepo: cd tools/linear-agent)
export CURSOR_API_KEY=... LINEAR_API_KEY=... LINEAR_TEAM_KEY=ELM
node runtime/scripts/cloud-agent-launch.mjs --role=tech-architect --issue=NONE
## security with report:
node runtime/scripts/cloud-agent-launch.mjs --role=security-officer --issue=NONE --report-file=/path/to/snyk.json
```

Prompts: `prompts/cloud-agent/tech-architect.md`, `qa-architect.md`, `security-officer.md`.

---

## Pre-release: dev / prod, deploy, E2E, Linear {#pre-release-e2e}

This is where **hosted dev** meets **CI truth**: PR previews and smoke checks prove a branch; **`e2e-regression-dev.yml`** proves the same surface your users see at **https://dev.elmundi.com**; promote workflows move **Docker Hub** tags to **https://www.elmundi.com**. When two PRs claim the same ticket, the convention **`fix/ELM-XX-auto`** is the one the developer launch path expects — close the stray branch instead of merging twice.

Single reference for **go/no-go**, automation alignment, and SDLC (GitHub Actions + Linear).

**On this page:** [Domains & branches](#domains--branches) · [Relevant GitHub Actions](#relevant-github-actions) · [Bunny / keys](#bunny-keys-operators) · [Duplicate PRs](#duplicate-prs-for-one-elm-ticket) · [Manual smoke](#manual-smoke-gono-go) · [Automated regression](#automated-regression-on-dev) · [Linear pre-release](#linear-project-elmundi-pre-release) · [SDLC integrity](#sdlc-integrity-after-changes)

!!! note "Stakeholder-facing exports"
    This page contains **deployment-specific** hostnames and infrastructure names. For **customer PDFs**, generalise URLs and hosting sections or mark the doc as **internal-only**.

### Domains & branches {#domains--branches}

| Environment | URL | Code source |
|-------------|-----|-------------|
| **Dev (hosted)** | https://dev.elmundi.com | `main` → `docker-build-push.yml` → Bunny **elm-dev** (Magic Containers) |
| **Prod** | https://www.elmundi.com | Manual promote of image from Docker Hub → `bunny-promote-prod-*.yml` |

There is **no** separate long-lived git branch **`dev`**: **dev = last successful deploy from `main`**.

### Relevant GitHub Actions {#relevant-github-actions}

| Workflow | Purpose |
|----------|---------|
| `Build and Push Docker Images` | Push to `main` → tag `v0.0.x`, image `dekus/elmundi-frontend:<version>`, update **elm-dev** |
| `Promote latest release to Bunny (prod)` | `workflow_dispatch` → prod on **latest** git tag `v*` |
| `Promote specific tag to Bunny (prod)` | `workflow_dispatch` → prod on chosen tag |
| **`E2E regression (dev.elmundi.com)`** | Scheduled + manual full Playwright **regression** against **live dev** |
| `PR Checks + Preview Deploy` | PR → smoke + preview |
| `Linear SDLC (scheduled)` | intake / clarification / BA / developer (Cloud Agent); pick **Todo** + **ElMundi pre-release** (`LINEAR_SDLC_PROJECT_*`) |

### Bunny / keys (operators) {#bunny-keys-operators}

- **Magic Containers API:** exchange via `https://api.bunny.net/apikey/exchange` with **`BUNNY_MAIN_API_KEY`** (may differ from `BUNNYNET_API_KEY` which can 401 on exchange).
- **Dev app** in MC: hostname **dev.elmundi.com** (id from API — do not confuse with prod).
- **Prod:** **www.elmundi.com**; `bin/.env` **`BUNNY_APP_ID`** often points at **prod** — for CI/dev use GitHub Environment **elm-dev**.

### Duplicate PRs for one ELM ticket {#duplicate-prs-for-one-elm-ticket}

If two branches exist (`feature/ELM-XX-auto` and `fix/ELM-XX-auto`): developer Cloud Agent targets **`fix/ELM-XX-auto`** (`cloud-agent-launch.mjs`). Close extra PRs **without merge** (keep the correct one).

### Manual smoke go/no-go {#manual-smoke-gono-go}

Checklist: **`website/docs/manual-smoke-dev-to-prod.md`** (Mailosaur, Paddle sandbox on dev, blockers).

### Automated regression on dev {#automated-regression-on-dev}

- **CI:** `.github/workflows/e2e-regression-dev.yml` — smoke + `tests/e2e/regression`, `PLAYWRIGHT_BASE_URL=https://dev.elmundi.com`.
- **Local:** `cd website && npm run test:e2e:regression:dev` + vars from `website/.env` (`env.example.txt`).

#### Secrets / vars (full CI run)

**Secrets and variables → Actions:**

- `MAILOSAUR_API_KEY`, `MAILOSAUR_SERVER_ID`
- Optional: `E2E_SUBSCRIBER_EMAIL`, `E2E_FREE_EMAIL`
- Optional: `PADDLE_CLIENT_TOKEN`, `PADDLE_PRICE_ID_MONTHLY` (job may set `PADDLE_ENVIRONMENT=sandbox`)

Without Mailosaur most OTP tests **skip** — workflow still adds partial signal.

### Linear project **ElMundi pre-release** {#linear-project-elmundi-pre-release}

Bugs from E2E on dev / pre-release. Script (may create duplicates if re-run blindly):

```bash
node runtime/scripts/create-prerelease-e2e-bugs.mjs
```

Requires `LINEAR_API_KEY` in `.env` at Ship package root (`tools/linear-agent/.env` when inside **elmundi**).

### SDLC integrity (after changes) {#sdlc-integrity-after-changes}

1. [SDLC (scheduled)](#sdlc-scheduled) — cron grid, secrets.
2. [Workflows catalog](#workflows-catalog) — full workflow list.
3. `prompts/cloud-agent/developer.md` + `_base.md` — branch `fix/ELM-XX-auto`, anti-duplicate PR.
4. Local: `bash runtime/scripts/verify-setup.sh` (from Ship package root).
5. GitHub: recent **E2E regression (dev)** and **Linear SDLC (scheduled)** green or documented failures.

---

## Autonomous pipeline setup {#operator-setup}

**Purpose:** prerequisites, **GitHub secrets/variables**, local debugging, and PR creation modes for the linear-agent tooling.  
**Audience:** platform engineers, maintainers running agents locally or in CI.  
**Outcomes:** you can verify env, run dry-runs, and know where **workflow YAML** is indexed.

When Linear comments and agent runs disagree with reality, this section is where you **ground-truth** the environment: same secret names in GitHub and local `.env`, **`verify-setup.sh`** before heroics, and **`pr-create`** behaving when a PR already exists (idempotent return of the existing URL — the friendly opposite of duplicate ELM branches).

!!! tip "Schedules & YAML index"
    **SDLC UTC grid** (even-hour :10 / :25 / :40 / :55) and column semantics: **[SDLC (scheduled)](#sdlc-scheduled)** (canonical).  
    **All workflow files** in one table: **[Workflows catalog](#workflows-catalog)**.  
    **Daily audits** cron: **[Daily audits](#daily-audits)**.

### Prerequisites

1. **Linear** — team with labels: `stage:*`, `ready:*`, `flow:*`, `result:*`
2. **GitHub** — Actions enabled
3. **Cursor** — API key for Cloud Agent / CLI
4. **SendGrid** — ready-for-review emails (if used)

### Secrets

GitHub **Settings → Secrets → Actions**:

| Secret | Description |
|--------|-------------|
| `LINEAR_API_KEY` | Linear API key (Linear settings → API) |
| `GITHUB_TOKEN` | Auto-provided; ensure Actions have `contents: write`, `pull-requests: write` |
| `CURSOR_API_KEY` | [Cursor dashboard](https://cursor.com/dashboard?tab=integrations) |
| `SENDGRID_API_KEY` | For transactional email notifications (if enabled for your deployment) |
| `SNYK_TOKEN` | Optional: **`linear-agent-daily-audits.yml`** security job; without it Snyk is skipped |

### Variables (**Settings → Variables**)

| Variable | Description |
|----------|-------------|
| `BUNNY_APP_ID` | App id for PR preview clones; without it deploy job in `pr-preview` may skip |
| `LINEAR_TEAM_KEY` | Optional Linear team key (default team key in pick scripts) |
| `LINEAR_SELF_HEAL_ISSUE` | Linear issue for workflow-self-heal agent. Without it Launch step is skipped |
| `LINEAR_TECH_DEBT_PROJECT_ID` | Optional; else lookup by name — [Daily audits](#daily-audits) |
| `LINEAR_SECURITY_PROJECT_ID` | Optional; Snyk ticket target |
| `LINEAR_SDLC_PROJECT_ID` | Optional; SDLC pick project override |

**Agent + Linear env:** [Cursor Cloud secrets](../../tools/index.md#cursor-cloud-agent).

### Quick debug checklist

| Step | Command |
|------|---------|
| 1. Verify env | `bash runtime/scripts/verify-setup.sh` |
| 2. Verify ticket | `bash runtime/scripts/run-ticket-verify.sh ELM-XX` |
| 3. Dry-run (no changes) | `bash runtime/scripts/run-autonomous-local.sh ELM-XX --verify-only` |
| 4. CI verify-only | `gh workflow run linear-agent-autonomous.yml -f verify_only=true` (and/or step 1 locally) |

### Init flow

For each new Linear issue:

```bash
# From ElMundiUA/ship repo root (in elmundi monorepo: cd tools/linear-agent)
node runtime/dist/cli.js init --issue ELM-XX        # Feature
node runtime/dist/cli.js init --issue ELM-XX --bug  # Bug
```

Or use Linear automation to add `ready:ba` / `ready:bug-agent` on creation.

### Autonomous loop schedule

**`linear-agent-autonomous.yml`** uses its own cadence (default every 6 hours). It **complements** SDLC; it does not replace it.

```yaml
schedule:
  - cron: '0 */6 * * *'
```

### Who creates the PR

| Mode | PR creation |
|------|-------------|
| **`--cloud`** | Cursor Cloud Agent creates PR when done (`autoCreatePr: true`) |
| **Local agent** | `run-autonomous-local.sh` runs `pr-create` after commit+push |
| **GitHub Actions** | `linear-agent-sdlc-scheduled.yml` or `linear-agent-autonomous.yml` → pick / loop → Cloud Agent → PR (developer) |
| **`--no-agent`** | Run `node runtime/dist/cli.js pr-create -i ELM-XX` manually after you push |

`pr-create` is idempotent: if a PR already exists for the branch, it returns the existing URL.

### Testing

#### Local (full control)

```bash
# From ElMundiUA/ship repo root (in elmundi monorepo: cd tools/linear-agent)
bash runtime/scripts/run-autonomous-local.sh ELM-XX [--no-agent] [--cloud] [--yes] [--verify-only]
```

- `--no-agent`: create branch, move to In Progress; you run the agent manually in Cursor
- `--cloud`: use **Cursor Cloud Agents API** — agent creates branch + PR when done
- `--yes`: skip commit confirmation (auto-commit and push)
- `--verify-only`: only `verify-setup` + `run-ticket-verify`
- Requires: `CURSOR_API_KEY` in `.env` (for `--cloud` or local agent)

#### GitHub Actions

1. Install [GitHub CLI](https://cli.github.com/): `brew install gh`
2. `gh auth login` (if needed)
3. SDLC manual: `gh workflow run linear-agent-sdlc-scheduled.yml -f role=developer -f issue=ELM-XX`
4. Autonomous: `gh workflow run linear-agent-autonomous.yml -f issue=ELM-XX`
5. Or leave `issue` empty where the workflow supports pick-next
6. Watch Actions → PR → deploy → release-check

### Debugging & verification

#### 1. Verify setup (local)

```bash
# From ElMundiUA/ship repo root (in elmundi monorepo: cd tools/linear-agent)
bash runtime/scripts/verify-setup.sh [--issue ELM-XX] [--skip-bunny]
```

Checks: Linear API, GitHub token, Cursor API, Bunny (optional), `runtime/dist/` CLI.

#### 2. Verify ticket state

```bash
bash runtime/scripts/run-ticket-verify.sh ELM-XX [--release-check]
```

- Without `--release-check`: show issue + PR link, no state changes
- With `--release-check`: run release-check (may move to In Review)

#### 3. Dry-run autonomous (no agent, no commits)

```bash
bash runtime/scripts/run-autonomous-local.sh ELM-XX --verify-only
```

#### 4. Step-by-step debug

```bash
bash runtime/scripts/verify-setup.sh
node runtime/dist/cli.js next -r developer
node runtime/dist/cli.js get ELM-XX
bash runtime/scripts/run-autonomous-local.sh ELM-XX --no-agent
bash runtime/scripts/run-autonomous-local.sh ELM-XX --cloud
```

#### 5. GitHub Actions verify-only

```bash
gh workflow run linear-agent-autonomous.yml -f verify_only=true
```

Or in GitHub UI: Actions → Linear Agent (Autonomous) → Run workflow → “Only verify setup”.

**If something fails:** [Troubleshooting](../../framework/index.md#when-things-break).

---

## GitHub workflows catalog {#workflows-catalog}

**Purpose:** single index of automation workflows that touch Linear, agents, or release quality.  
**Audience:** platform engineers, repo maintainers.  
**Outcomes:** you can find the right YAML file, its trigger, and the deep-dive doc.

Use this table when someone asks “which workflow fired?” — **`workflow-self-heal.yml`** on the odd-hour cadence is the **diagnostics lane** (CLI report first, optional Cloud Agent on the configured issue), not a duplicate of SDLC intake. **`pr-preview.yml`** is the PR path; **`e2e-regression-dev.yml`** is the scheduled **full regression** against live dev.

For the **SDLC cron grid** (UTC slots, pick scripts), see **[SDLC (scheduled)](#sdlc-scheduled)** — canonical schedule description.

!!! note "Ship log"
    If preview checks flap, start from the row for **`pr-preview.yml`** and **`linear-agent-release-check-on-deploy.yml`**, then cross-check secrets (`BUNNY_APP_ID`, Mailosaur/Paddle vars) in **[Pre-release & E2E](#pre-release-e2e)** and **[Operator setup](#operator-setup)** — same names, fewer ghost failures.

| Workflow file | Typical trigger | Role |
|---------------|-----------------|------|
| `linear-agent-sdlc-scheduled.yml` | Cron (even UTC hours) + `workflow_dispatch` | Intake → clarification → BA → developer via pick + Cloud Agent |
| `workflow-self-heal.yml` | Cron (odd UTC hours) + dispatch | CLI pipeline report, optional Cloud Agent on configured issue |
| `linear-agent-autonomous.yml` | Cron (default every 6h) + dispatch | Separate autonomous loop; complements SDLC |
| `linear-agent-daily-audits.yml` | Daily cron + dispatch | Tech / QA / security audits → dedicated Linear projects |
| `linear-agent-release-check-on-deploy.yml` | After successful preview deploy | Release-check automation |
| `check-failure-recovery.yml` | PR check failures | Recovery / follow-up |
| `pr-preview.yml` | PR events | Playwright + preview deploy |
| `linear-agent-webhooks.yml` | Webhooks (optional) | A5/A6/A7-style integrations |
| `e2e-regression-dev.yml` | Scheduled + manual | Full Playwright regression vs hosted dev — see **[Pre-release & E2E](#pre-release-e2e)** |
| `docker-build-push.yml` | Push to `main` | Build/push image, refresh dev host — see **Pre-release & E2E** |
| `bunny-promote-prod-*.yml` | `workflow_dispatch` | Prod promote from Docker Hub tags — see **Pre-release & E2E** |

**Related:** [Autonomous pipeline setup](#operator-setup) (secrets, variables, local debug), [Daily audits](#daily-audits), [Troubleshooting](../../framework/index.md#when-things-break).

---

## Migrating to Cursor Automations {#cursor-automations}

The current flow (GitHub Actions + linear-agent CLI + Cloud Agent API) can be moved to **Cursor Automations** — native triggers and agents inside Cursor.

### Current state (this repository)

| Area | Today |
|------|--------|
| **Orchestrator** | GitHub Actions (`linear-agent-sdlc-scheduled.yml`, related workflows — see [Workflows catalog](#workflows-catalog)) |
| **Agent launch** | `cloud-agent-launch.mjs` + **Cursor Cloud Agents API** + `CURSOR_API_KEY` |
| **Issue metadata** | `runtime/dist/cli.js get` / Linear API via pick scripts and CLI |
| **Cursor Automations** | **Off** for SDLC stages (documented in [SDLC (scheduled)](#sdlc-scheduled)) |

### GitHub-orchestrated agent vs Cursor Automations — why migration is usually **not** worth it

This repo deliberately keeps **GitHub Actions + deterministic pick + `cloud-agent-launch.mjs`**. Cursor Automations are documented for completeness, not as a recommended default.

| Dimension | **This repo (GitHub + pick + Cloud Agent API)** | **Cursor Automations** |
|-----------|-----------------------------------------------|-------------------------|
| **When an agent runs** | Agent starts **only if** a pick script returned an issue — scheduled workflow runs are mostly cheap CI minutes; **no agent spend** on empty queues. | Triggers (status change, schedule, webhook) can **invoke an automation run** even when the prompt exits immediately; **provider-side agent usage** can accrue on “no-op” or guard-rail exits unless the product billing model exempts them — **verify in your Cursor plan**. |
| **Cost predictability** | Bounded by **cron slots × roles** and **~1 issue max per slot**; easy to forecast “at most N agent invocations per day.” | Event-driven triggers multiply with board activity (every transition into **Todo**, every webhook); spikes are harder to cap without extra guards. |
| **Bias & output variance** | Same **versioned** prompt bundle from `prompts/cloud-agent/` + `.cursor/skills`; changes go through PR review. | Prompts live in the **Automation UI**; drift across automations, harder to diff/review like code; **LLM bias / anchoring** applies to both stacks, but **governance** (review, tests, PR) is tighter when prompts are in-repo. |
| **Audit trail for compliance** | Every meaningful step ties to a **GitHub Actions run**, commit context, and Linear ticket — see [Executive brief](../../framework/index.md#the-idea). | Mix of Cursor product logs + GitHub; story is **splintered** if Automations replace the workflow boundary. |
| **Deterministic gating** | **Labels + project + column** enforced in **Node pick scripts** before any agent call. | Gating often relies on **natural-language rules inside the prompt** (“if no label, exit”) — easier to misconfigure or bypass. |
| **Throughput control** | **One role per cron window** — avoids stampedes ([Vision & extensibility](../../framework/index.md#the-idea)). | Easier to accidentally **overlap** runs (multiple status changes, overlapping schedules) unless you design locks externally. |
| **Vendor / model flexibility** | Orchestration is **plain HTTP + repo**; `cloud-agent-launch.mjs` is the **single choke point** to swap provider parameters, endpoints, or **per-role model tiers** as Cursor (or a future API) exposes them — see below. | Each automation is typically a **separate configuration**; changing models or vendors means **touching many triggers**, not one script. |

#### Model flexibility (why the current pattern scales on cost **and** quality)

- **Per-task model choice:** the launch path is centralized. As your provider adds options (faster/cheaper models for intake or labelling, stronger models for developer implementation), you can route **by role** in one place instead of duplicating N automations.
- **Move with the market:** if a cheaper or open-weight stack appears behind the same “checkout + branch + PR” contract ([Vision & extensibility](../../framework/index.md#the-idea)), you replace **launch + secrets**, not every Linear trigger definition.
- **Automations** tend to **pin** one agent profile per automation; mixing “cheap vs premium” models across SDLC stages means **many** automations to maintain.

#### Illustrative cost sketch (not a quote — verify on [cursor.com](https://cursor.com) / your dashboard)

Pricing and included **Cloud Agent** / **Automations** allowances **change often**. Treat the following as **order-of-magnitude planning math**, not invoices.

**Assumptions (example):** SDLC grid = **4 roles × ~12 even hours/day** ⇒ **~48 scheduler ticks/day**. Suppose **only 25%** of ticks actually pick an issue and launch an agent ⇒ **~12 agent runs/day**. If Automations fired on **broader** triggers (e.g. every transition into **Todo** without a prior pick filter), you could easily reach **tens of extra invocations/day** for the same backlog.

| Illustrative line item | GitHub + pick (this design) | Automations-heavy design |
|------------------------|----------------------------|---------------------------|
| **Scheduler / idle** | Mostly **GitHub Actions minutes** on empty picks; **$0** marginal agent cost when no issue is picked. | Depends whether **trigger evaluation** bills as agent usage — **confirm with Cursor**. |
| **Agent runs / day** (example) | ~**10–15** (low pick rate) | Often **higher** if triggers are event-rich unless strictly deduplicated. |
| **Annual sensitivity** | If marginal cost per **real** agent run were on the order of **$1–5** (illustrative), **12 runs × 250 workdays** ≈ **$3k–15k/year** from SDLC alone — scale linearly with pick rate. | Add **20–50%** or more if “empty” or duplicate triggers bill — **your mileage will vary**. |

**Action:** export usage from **Cursor** and map it to **workflow run** timestamps in GitHub before any migration decision.

---

### If you still migrate — target models

The sections **Option 1–4** below describe **target** architectures. Pick one based on control vs native-Linear trade-offs.

**Governance context:** [Executive brief](../../framework/index.md#the-idea).

### What Cursor Automations offer

- **Triggers:** Linear (issue created, status changed), schedule (cron), webhook, GitHub
- **Tools:** open PR, comment on PR, Slack, MCP, memories
- **Billing:** cloud agent usage (team plan)

### Option 1: Linear “Status changed” (common)

When an issue moves to **Todo** with `ready:developer`:

1. **cursor.com/automations** → New automation
2. **Trigger:** Linear → Status changed → New status: Todo
3. **Tools:** Open pull request, Linear (if MCP available)
4. **Repository:** ElMundiUA/elmundi, base: `main`
5. **Prompt** (sketch):

```
You are the Developer agent for the Linear issue in this run.

RULES:
- Only proceed if the issue has label "ready:developer". Otherwise exit without changes.
- Update Linear: status In Progress, label "stage:developer".
- Implement per issue description.
- Run tests: cd website && npm run test && npm run test:e2e:smoke -- --project=chromium-desktop
- Branch: fix/{ISSUE_ID}-auto
- Commit: fix(ISSUE_ID): <short>
- Open PR with body containing: Closes {ISSUE_ID}
- If blocked: Linear comment "Blocked: <reason>" and exit.
```

**Limitation:** “Status changed → Todo” fires for any transition into Todo — guard in the prompt with `ready:developer`.

---

### Option 2: Webhook (minimal GitHub change)

Keep GitHub Actions as orchestrator but replace Cloud Agent API with an Automation webhook:

1. **cursor.com/automations** → New automation
2. **Trigger:** Webhook
3. **Tools:** Open pull request
4. **Prompt:** same rules as option 1; issue id arrives in webhook body

5. In **`linear-agent-sdlc-scheduled.yml`** (or a dedicated workflow) replace `cloud-agent-launch` with:

```yaml
- name: Launch Cursor Automation via webhook
  run: |
    ISSUE="${{ steps.issue.outputs.issue }}"
    curl -X POST "${{ secrets.CURSOR_AUTOMATION_WEBHOOK_URL }}" \
      -H "Authorization: Bearer ${{ secrets.CURSOR_AUTOMATION_WEBHOOK_KEY }}" \
      -H "Content-Type: application/json" \
      -d "{\"issue\": \"$ISSUE\"}"
```

6. Store webhook URL + API key in GitHub Secrets.

---

### Option 3: Scheduled (full GitHub schedule replacement)

1. **Trigger:** Scheduled → e.g. `0 */6 * * *`
2. **Repository:** ElMundiUA/elmundi
3. **Tools:** Open PR, Linear MCP (if configured)

**Gap:** Automation has no `linear-agent` CLI `next -r developer`. You need **Linear MCP** (or equivalent) to list/filter by labels.

---

### Option 4: Linear “Issue created” + delegation

Use Linear triage rules or manual assign to Cursor.

**Pros:** no schedule, Linear-native.  
**Cons:** per-issue assign or triage rules.

---

### Recommendation

| Goal | Option |
|------|--------|
| Minimal change, keep GitHub schedule | **2 Webhook** |
| Native Linear flow | **1 Status changed** |
| Remove GitHub Actions entirely | **3** (needs Linear MCP) |

### Linear MCP (options 1 & 3)

1. Cursor Dashboard → Integrations → Linear
2. Or add Linear MCP server to the project
3. Automation → Tools → MCP → Linear

### Webhook migration checklist

- [ ] Create automation with Webhook trigger
- [ ] Enable “Open pull request”
- [ ] Author prompt (see above)
- [ ] Save → copy Webhook URL + API key
- [ ] GitHub Secrets: `CURSOR_AUTOMATION_WEBHOOK_URL`, `CURSOR_AUTOMATION_WEBHOOK_KEY`
- [ ] Update workflow: `curl` step vs `cloud-agent-launch`
- [ ] Remove or comment `cloud-agent-launch` when verified
