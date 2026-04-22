# Ship product model — the one-pager

> **Read this first before planning any feature that touches
> "pipelines", "agents", "artifacts", "workflows", or "executors".**
> If a plan contradicts this page, the plan is wrong. Update this
> page only after an explicit architectural decision with the owner.
>
> **Owner:** Denys / Ship core
> **Last updated:** 2026-04-20 (D13 planning)

---

## What Ship *is*

Ship is a **methodology registry + orchestration & observability plane**
for SDLC automation. Concretely we ship three things:

1. **A versioned catalog of artifacts** (`patterns`, `tools`,
   `workflows`, `collections`, `docs`) served over HTTP from
   `/api/methodology/*` (RFC-0001). Clients fetch, never vendor.

2. **A lightweight CLI (`shipctl`)** that the customer's repo uses to
   resolve artifacts, record evidence, and file feedback. `shipctl` is
   a **fetch/verify/feedback client**, **not** an executor.

3. **An orchestration/observability dashboard** (this repo's `backend/`
   + `console/`) that:
   - watches customer-repo webhooks (installs, PR merges, workflow
     runs, push/schedule triggers),
   - tracks `PipelineRun`s + their evidence,
   - surfaces clarifications + feedback + artifacts for humans to
     review and resolve,
   - hosts a single-window agent chat (C12) for the **console user**,
     not for the execution path.

## What Ship is **not**

- **Ship is not the agent runtime.** We do not hold customer
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` for executing work against
  their repo. We do not invoke LLMs on behalf of the customer's
  workflows. The customer's agent runs in the customer's CI and uses
  the customer's LLM credits.
- **Ship is not a vendor-specific orchestrator.** We don't want to
  maintain executors for "GitHub Actions" *and* "GitLab CI" *and*
  "Buildkite" *and* "Jenkins". We publish **descriptions** and let the
  customer's agent (which already knows their CI) do the integration.
- **Ship is not a code-execution platform.** No Ship-hosted workers,
  no persistent volumes, no `git_sync`, no cloned repo cache. That
  was explicitly dropped during the Model-A pivot (see
  `pilot-plan.md` TL;DR).

## The five kinds of artifacts

| Kind | What it is | Typical contents | Example |
|---|---|---|---|
| `pattern` | A **prompt/instruction slice** for a role or lane. Versioned markdown. The agent reads it and follows it. | Role definition, invariants, steps, idempotency rules. | `cloud-intake`, `cloud-tech-architect`, `cloud-developer` |
| `tool` | A **description of a capability** the agent may need. Two flavours: **global** (e.g. Snyk — "install from upstream, configure, done") and **custom** (Ship-packaged code + instructions, e.g. a methodology API adapter). | Integration surface, auth story, agent-facing interface. | `snyk`, `playwright`, `linear`, `methodology-api` |
| `workflow` | A **thin scheduler wrapper** (cron + manual dispatch + reporting contract) that triggers a pattern on the customer's agent at a given time. **Workflows run in the customer's CI, not in Ship.** | The actual CI YAML (e.g. GitHub Actions) with a kickoff step that invokes the customer's agent CLI against a named pattern. | `scheduled-sdlc-lane`, `pr-and-ci-gate`, `pipeline-self-heal` |
| `collection` | A **recommended bundle** for a project type (presets) or **agent-rules glue** for a specific agent runtime (CLAUDE.md, AGENTS.md, .cursorrules, etc.). Collections are **recommendations, not mandates**. | A list of patterns/tools/workflows bound by a theme, or agent-rules markdown that teaches the customer's agent how to use `shipctl`. | `preset-web-app`, `preset-mobile-app`, `agent-rules-claude`, `agent-rules-cursor` |
| `doc` | Any indexed markdown under `documentation/` — fallback catch-all. | Reference docs, runbooks, RFCs. | `documentation/adoption/delivery-quality-and-release-process.md` |

Every artifact ships with: semver `version`, `content_sha256`,
`channel` (`stable`/`edge`), `min_shipctl`, `deprecated`, `replaced_by`,
`yanked`. See RFC-0001 for wire format.

## The execution model (how a run happens)

The short version: **Customer's CI schedules → customer's agent runs →
customer's agent uses `shipctl` to fetch patterns and tools → agent
does real work against repo/tracker → workflow calls back to Ship for
observability.**

Detailed lifecycle of one run:

1. **Trigger.** A GitHub Actions workflow in the customer's repo
   fires — either on `cron:` (scheduled lane), on `workflow_dispatch`
   (manual "Run now" from Ship dashboard), or on a `pull_request` /
   `workflow_run` event (self-heal, PR gate).
2. **Checkout.** Standard `actions/checkout@v4`.
3. **Resolve agent runtime.** The workflow reads `.ship/config.yml`
   in the repo (installed by the right `agent-rules-*` collection
   during onboarding) to find `agent.provider` (`claude-code` |
   `cursor-cloud` | `codex` | `aider` | `copilot` | `windsurf` |
   `gemini` | ...).
4. **Install agent CLI + auth.** The workflow installs the chosen
   agent CLI and uses secrets synced by Ship's repo-secrets (B10) —
   e.g. `ANTHROPIC_API_KEY` for Claude Code, `CURSOR_API_KEY` for
   Cursor Cloud, `OPENAI_API_KEY` for Codex CLI, plus tracker
   credentials (`LINEAR_API_KEY`, etc.) and Ship callback creds.
5. **Kickoff prompt.** The workflow invokes the agent CLI with a
   small, standardized kickoff message:
   > "You are running Ship workflow `<workflow_id>` in run `<run_id>`.
   > Fetch and follow pattern `<pattern_id>` via `shipctl pattern
   > show <pattern_id>`. Use `shipctl tool show <id>` for any tool
   > you need. Record the artifact versions you used in the PR /
   > ticket evidence trail. If you need human input, post a ticket
   > comment starting with `> **@ship clarification:**` and add the
   > `ship:needs-clarification` label to the ticket, then exit. At
   > end of session, optionally draft feedback via
   > `shipctl feedback draft`."
6. **Agent runs.** The customer's agent reads the pattern, does the
   work (creates tickets, opens PRs, leaves tracker comments, runs
   tools). All LLM calls go through the **customer's agent**, with the
   **customer's LLM credentials**. Ship never sees prompts or tokens.
7. **Evidence back to tracker.** The agent writes evidence (tickets,
   PR urls, CI run ids, comment threads) into the tracker — that's
   the source of truth. Ship does not duplicate tracker state.
8. **Observability back to Ship.** The workflow's last step does
   `shipctl callback` (or the current `curl` form) with
   `{status, summary, metrics: {ticket_ids, pr_urls, ...}}` so Ship's
   dashboard can surface the run.
9. **Human review loop.** When the agent couldn't decide, it leaves
   a labelled comment on the ticket (see "Clarifications" section
   below) — Ship's tracker adapter picks it up into the console
   inbox for the operator to answer. When the agent wants to propose
   an improvement to an artifact, it drafts a **feedback** via
   `shipctl feedback draft` (C12's artifact-feedback tray) — human
   owner accepts or declines.

## What lives where

| Location | Role |
|---|---|
| `artifacts/patterns/<id>/ARTIFACT.md` | Pattern body (prompt). |
| `artifacts/tools/<id>/ARTIFACT.md` | Tool description. |
| `artifacts/workflows/<id>/ARTIFACT.md` + `workflow.yml` | Workflow metadata + actual CI YAML to install in the customer's `.github/workflows/`. |
| `artifacts/collections/<id>/ARTIFACT.md` | Collection (preset or agent-rules). |
| `backend/app/` (FastAPI) | `/api/methodology/*` (fetch/search/manifest), `/v1/*` (orchestration: workspaces, repos, pipelines, runs, clarifications, feedback, secrets). |
| `console/` (Next.js) | Operator dashboard: onboarding wizard, dashboard, pipelines, clarifications, feedback, chat (C12), secrets (B10). |
| `cli/` (Node, `@elmundi/ship-cli`) | CLI that talks to `/api/methodology/*` and run-token endpoints. Shipped as `npm i -g @elmundi/ship-cli` or `npx @elmundi/ship-cli`. Used both by humans at setup time (`shipctl init`) and by customer workflows at run time (`shipctl pattern show`, `shipctl callback`, …). |
| `backend/app/integrations/trackers/` | Per-tracker adapters (Linear / GitHub Issues / Jira / Notion) that project tracker state into Ship's `clarifications` (and other projection tables) and write-back answers. |

## Multi-agent awareness

We explicitly do **not** pick an agent runtime for the customer.
During onboarding:

1. **Ask** which agent they use (Cursor / Claude Code / Codex / Aider
   / Copilot / Windsurf / Gemini / etc.).
2. **Optionally auto-detect** by scanning for `CLAUDE.md`,
   `AGENTS.md`, `.cursorrules`, `.aider.conf.yml`, `copilot-*.yml`, …
3. **Install the matching agent-rules collection** — a markdown file
   (`CLAUDE.md` for Claude Code, `AGENTS.md` for Codex, etc.) with
   the Ship artifacts protocol appended between
   `<!-- ship-cli: artifacts-protocol v1 -->` markers. This teaches
   the customer's agent how to call `shipctl`.
4. **Write `.ship/config.yml`** with `agent.provider` so workflows
   know which CLI to install.

We do not push patterns into the agent's context directly. The agent
fetches them via `shipctl` at run time, with version pinning, so
bumping a pattern is a `shipctl sync` away from every downstream
customer.

## Ship stores are projections, not sources of truth

Ship maintains its own Postgres store, but most rows in it are
**projections / read-through caches** over the customer's tracker or
GitHub, not authoritative data. The rule: if the customer could
legitimately work inside the tracker (or git) without Ship running,
Ship must not be the system of record. Anything that's **pure
methodology** or **Ship-lifecycle** is Ship-owned.

| Surface | Source of truth | Ship's role |
|---|---|---|
| Ticket bodies, status, assignees | Tracker (Linear / GitHub Issues / Jira / Notion) | Only stores links + external refs; never duplicates ticket content |
| **Clarifications** (open questions from the agent) | **Tracker** — a ticket carrying the `ship:needs-clarification` label plus a marked comment | **Projection / indexer.** Ship polls (or webhooks) for tickets carrying the label, surfaces them in `/clarifications`, and write-backs answers by posting a comment + removing the label. |
| PR links, CI run ids, commit SHAs | GitHub / GitLab | Stored as references inside `PipelineRun.payload.metrics`; never as copies |
| Repo tree, file contents | Customer's git | Fetched at run-time via installation token; not cached |
| PipelineRun lifecycle (queued → running → done, summary, metrics) | Ship | **Owned** — this is our observability layer |
| Artifact feedback (agent's suggestions about a pattern) | Ship | **Owned** — methodology is Ship's product, no tracker representation exists |
| Improvements (proposed backlog items) | Ship while draft / customer's tracker once accepted | Owned in draft state; once accepted, a tracker ticket is created and Ship keeps only the external ref |
| Knowledge buckets, repo memory | Ship | **Owned** — methodology-adjacent, not SDLC state |
| Methodology artifacts (patterns / tools / workflows / collections / docs) | Ship | **Owned** — served via `/api/methodology/*` |
| Secrets (per-repo) | Customer's GitHub Actions Secrets | **Ship cipherstore + sync mirror.** Ship holds Fernet-encrypted copy and PUT/DELETE to GitHub Actions via installation token. Sync-status surfaced per row. |

Why this matters: **zero parallel-status bugs.** If the human works in
Linear directly (answers a clarification by commenting and removing
the label), Ship picks it up on the next poll. If they work in Ship
(types the answer in `/clarifications`), Ship's adapter writes it
back to Linear and only then reflects locally. There is no
"source A says open, source B says answered" divergence — Ship
always defers to the external source when both have an opinion.

## Feedback & clarification loops (the human-in-the-loop contract)

### Clarifications — tracker-backed, label-driven

The agent is missing context. Protocol:

1. **Agent** posts a regular comment on the ticket, starting with the
   marker `> **@ship clarification:**` so it's easy for humans to
   spot and for adapters to identify the question body:
   ```
   > **@ship clarification:** This feature lacks a business goal.
   > What user need does it address?
   ```
   Then adds the label `ship:needs-clarification` (or the
   tracker-native equivalent — see table below) to the ticket.
   Exits gracefully — it does **not** keep the GHA runner alive
   waiting for an answer.

2. **Ship's tracker adapter** (webhook-first, polling fallback)
   notices the label, ingests a row into `clarifications` with
   `tracker_provider`, `tracker_issue_id`, `tracker_comment_id`,
   `question` (parsed from the marker comment body). `status = open`.

3. **Human** can answer in **either place**:
   - *In the tracker* — reply with a comment, remove the label.
     Webhook/poll catches it, Ship flips `status = answered` and
     copies the reply text into `answer`.
   - *In Ship console* `/clarifications` — types the answer. Ship's
     adapter (a) posts a `> **@ship answer:** ...` comment to the
     tracker, (b) removes the label, (c) mirrors locally only
     after the tracker ack.

4. **Next scheduled agent run** reads the ticket comments normally —
   sees the answer in the thread, continues with full context. No
   Ship-specific round-trip needed on the agent side.

Labels per tracker (same semantic, different spelling):

| Tracker | Open marker | Close action |
|---|---|---|
| Linear | Label `ship:needs-clarification` | Remove label |
| GitHub Issues | Label `ship:needs-clarification` | Remove label |
| Jira | Label `ship-needs-clarification` (Jira labels disallow `:`) | Remove label |
| Notion (database item) | Multi-select tag `ship:needs-clarification` | Remove tag |

The label is the source of truth for **open/closed**. The
`> **@ship clarification:**` and `> **@ship answer:**` markers in
comment bodies are the source of truth for **content**.

**What we explicitly do not do:** we never add a second "Ship-owned
clarification" outside the tracker. The agent has exactly one way to
ask for input — via the tracker. The `shipctl` CLI has no
`clarification ask` command by design.

**Implementation anchor (D13, commit 2):**

- Service: `backend/app/services/clarifications_sync.py`
- Constants: `CLARIFICATION_LABEL = "ship:needs-clarification"`;
  markers `@ship clarification:` / `@ship answer:` (case-insensitive,
  blockquote-tolerant).
- Model: `Clarification.source ∈ {"manual","pipeline","tracker"}` +
  `tracker_provider` / `tracker_issue_key` / `tracker_issue_url` /
  `tracker_comment_id` / `tracker_synced_at`. Partial unique index
  `uq_clarifications_tracker_comment` on
  `(workspace_id, tracker_provider, tracker_comment_id)` guards
  projection idempotency.
- Cron: `cron_sync_tracker_clarifications` (every 5 min); admin
  one-shot via `POST /v1/workspaces/{ws}/clarifications/sync`.
- Write-back on `PATCH` when `source='tracker'`: posts
  `@ship answer:` comment, then `remove_label`. Tracker failure →
  502, Ship row rolled back.
- Adapter surface (extended on
  `backend/app/integrations/gateway/tracker.py`):
  `list_issues_with_label` (returns `ListedIssue`),
  `list_comments` (returns `CommentRef`), `remove_label`. Linear and
  GitHub Issues implement them; Notion raises `NotImplementedError`
  and the service skips silently (pilot scope — see labels table).

### Feedback — Ship-owned, methodology-scoped

At end of session, the agent may propose an improvement to an
artifact it consumed ("this pattern was ambiguous about X", "add a
step for Y"). Drafted via `shipctl feedback draft`, lands in Ship's
Feedback tray (the artifact-feedback surface built in C12). The
methodology owner (us, for in-tree artifacts; or the customer, for
their private addendums) accepts or declines. **Never auto-applied.**

This one stays Ship-owned because methodology feedback has no
tracker representation by definition — it's meta about our product,
not SDLC work.

## What Ship owns vs what Ship federates

**Ship owns:**
- The versioned catalog + the HTTP API for fetching it.
- The `shipctl` CLI (fetch, verify, feedback draft, callback).
- The orchestration/observability layer (PipelineRun rows, webhook
  reconciliation, secrets mirroring, dashboard, C12 console agent).
- The **clarifications projection** over customer trackers (read +
  write-back via adapters).
- Agent-rules collections for every supported agent CLI.
- Reference workflow YAMLs (cron, pr-and-ci-gate, self-heal, ...).

**Ship federates to the customer:**
- The LLM call itself (their agent, their key, their cost).
- The CI runtime (their GHA / GitLab / Buildkite).
- The tracker (Linear / GitHub Projects / Notion / Jira).
- The actual code execution (in the customer's runner, with the
  customer's permissions).

This federation is the whole point: it's what lets Ship stay a
lightweight methodology plane instead of growing into yet another
"one-platform-to-rule-them-all" engineering cloud.

## The console agent (C12) — separate concern

The single-window SSE agent chat at `/chat` in the Ship console is a
**convenience surface for the operator** (the human running Ship). It
helps them plan tickets, review artifacts, ask repo questions from
inside the dashboard. It is **not** the agent that executes
customer-repo work. Keep these two concerns separate:

- **Console agent** = Ship's LLM, Ship's key, helps the operator.
- **Customer's agent** = Customer's LLM, customer's key, executes
  patterns against the customer's repo under scheduled workflows.

Both use the same artifact catalog (via different call paths —
console agent uses internal service calls, customer's agent uses
`shipctl` HTTP).

## The shortest possible mental model

```
  Ship = catalog of versioned patterns/tools/workflows
       + dashboard that watches customer repos
       + shipctl CLI for the customer's agent to fetch them.

  A "pipeline run" = a scheduled GHA workflow in the customer's repo
                     that invokes the customer's agent
                     with a kickoff prompt pointing at a pattern id,
                     and reports status back to Ship for dashboard visibility.

  Ship never holds the LLM key.
  Ship never runs the agent.
  Ship owns the methodology, not the execution.
```

When in doubt, re-read that block.
