# Tools

Ship is **tool-agnostic** in principle. This page is about **where the rubber meets the road**: the adapter layer between “pick → launch → PR → audit trail” and the vendors we actually plug in at ElMundi today. Names change; the **contract** does not. Honesty matters here — some of what follows is **pattern** (replaceable), some is **coupling** we chose on purpose.

---

## Why “tools” are not the product

The loop in [Framework → The system](../framework/index.md#the-system) does not depend on a logo. Tools are where **money, secrets, and data residency** show up — so we say so plainly in [Trust & boundaries](../framework/index.md#trust-and-boundaries). Read this tab when you need to know *what runs* and *why that slice exists*, not when you want a feature matrix.

---

## What we run today

We need five kinds of capability: **truth on the board**, **something that fires on a schedule**, **an agent runtime**, **hosted regression**, and **dependency/security signal**. Today that maps to [Linear](#linear), [GitHub Actions](#github-actions), [Cursor Cloud Agent](#cursor-cloud-agent), [Playwright](#playwright), and [Snyk](#snyk) — in that order of “how central to the story,” not coolness.

That list is our **reference deployment**, not a mandate. Swap an adapter, keep the boundaries.

---

!!! note "Field note — scripts are the spine"

    The boring part that saves you at 3 a.m. lives under `scripts/`: **pick** and **launch** (and friends) are the spine between “cron said go” and “agent actually ran.” They are not a SKU you buy; they are **versioned code** you can run locally, log, and diff. SaaS gives you buttons; **scripts** give you a reproducible story when something mis-fires. If you replace Cursor with another agent API, you still want that spine — different HTTP client, same seams.

---

## Example wiring

Filenames, secrets, and cron-shaped truth for ElMundi stay in **[Examples → ElMundi](../examples/elmundi/index.md)** so this page stays about *roles*, not copy-paste tables.

---

## Linear (issue tracker adapter) {#linear}

**Why this layer:** Ship needs a **single place** where state is visible to humans *and* machine-readable. Linear is our pick for that system of record: columns, labels, team, project membership — the fences automation is allowed to touch.

---

### What the adapter must provide

- **Stable issue keys** — humans and scripts agree on identifiers across months.  
- **Workflow states** per team (Backlog, Todo, In Progress, Done, …) through the API — not UI-only.  
- **Labels** as guards (`ready:*`, `stage:*`, severity, source tags).  
- **Projects** separating delivery vs audit vs security — boards should read as a story.

If a field matters in standup but not in the API, it cannot be a **fence**. You get “the bot should have known” — and it could not.

---

### Why not “just use the UI”

Automation reads what humans see **through the API**. That keeps pick logic **testable**, audits **replayable**, and incidents **explainable** (“state was X, label Y missing”).

---

### How pick uses Linear (mental model)

Pick is a **pure function** of board snapshot → **zero or one** issue keys, plus explicit ordering.

**Healthy:** guards are booleans on fields you would defend in a PR comment; ordering is one documented policy (FIFO, priority label, created date — pick one).

**Unhealthy:** “whatever looks urgent” and team assumptions nobody updates after a reorg.

Framework: [Deterministic pick](../framework/index.md#deterministic-pick).

---

### Swapping Linear for something else

Keep **pick** returning one **issue key string** (or empty). Keep **launch** resolving the same metadata (title, team, branch-safe slug). Coupling surface: [The system](../framework/index.md#the-system). Reference project names: [Examples → ElMundi → SDLC](../examples/elmundi/index.md#sdlc-scheduled).

---

## GitHub Actions (scheduler) {#github-actions}

**Why this layer:** Something has to be the **clock** — cron, manual dispatch, concurrency — and a safe place for secrets at trigger time. GHA is our default: badge on the repo, encrypted vars, a **run URL** you can paste into a ticket.

---

### What we ask of it

- Run **Node** (or your runtime) on a checkout.  
- Pass secrets into **pick** / **launch** without echoing them.  
- Emit a **workflow run URL** for audit glue.  
- Fail **loud** on misconfiguration (missing secret), not “green with no work.”

---

### What we do *not* ask of it

Business rules in YAML spaghetti. Pick stays in **scripts**; YAML stays **when** and **with which env**. Smart conditionals nobody can run locally belong in code with tests.

---

### Concurrency and duplicate work

Concurrency groups when push + schedule + manual could fire the same lane — otherwise overlapping agents, the failure mode Ship tries to delete. ElMundi patterns: [Workflows catalog](../examples/elmundi/index.md#workflows-catalog).

---

### Alternatives

GitLab CI, Buildkite, CircleCI — same idea: schedule + checkout + `node scripts/…`. The badge is branding; the pattern is not. Filenames for our repo: [Workflows catalog](../examples/elmundi/index.md#workflows-catalog).

---

## Cursor Cloud Agent {#cursor-cloud-agent}

**Why this layer:** After pick says *which* ticket, something has to **run** in an environment with repo access, tools, and a path to branch + PR. Cursor Cloud Agent is **one** implementation of that — managed runtime, our **launch script** and `cloud-prompts/` driving behavior. This section is how Ship uses it, not a full product tour.

---

### The contract we care about

- Headless runs from CI with **versioned** prompts.  
- Branch names and PR titles that carry the **ticket key**.  
- Tracker updates humans can grep — not disposable chat.

If the runtime cannot meet that, adapter work is required — model quality does not fix a missing contract.

---

### Secrets (Linear from the repo)

**GitHub → Settings → Secrets and variables → Actions:** **`LINEAR_API_KEY`** and **`CURSOR_API_KEY`**. Without `LINEAR_API_KEY`, pick / `cli start` fails; without `CURSOR_API_KEY`, the cloud agent does not start.

GitHub passes **`CURSOR_API_KEY`** when launching. For the agent to **update Linear**, Cursor must also expose **`LINEAR_API_KEY`** in the Cloud Agent environment for this repo:

1. **Cursor Dashboard** → Cloud Agents / Repository / Environment (wording may vary).  
2. Add **`LINEAR_API_KEY`** (same value as in GitHub). `GITHUB_TOKEN` is usually not required for Linear.  
3. Optional: **`LINEAR_SDLC_PROJECT_ID`** or **`LINEAR_SDLC_PROJECT_NAME`** — SDLC picks scoped to one project (default in code matches ElMundi pre-release).

Until that key exists in the agent env, prompts may fall back to asking for a manual **`[LINEAR-DRAFT]`** comment — that is **miswired secrets**, not a feature.

API reference: [Cloud Agents API](https://cursor.com/docs/background-agent/api/overview).

---

### Why the same secret twice

CI and cloud runtime are **different trust boundaries**. Duplication feels silly until workflows are green and tickets never move. Policy over vibes: [Trust & boundaries](../framework/index.md#trust-and-boundaries).

---

### Prompt assembly

Launch reads `_base.md` + role + optional skills. Keep skills **small**; keep policy in git-tracked markdown so it diffs cleanly.

More: [Prompt catalog](../prompts-workflows/index.md#prompt-catalog) · [Iterating on prompts](../prompts-workflows/index.md#iterating-on-prompts).

---

## Playwright (E2E) {#playwright}

**Why this layer:** Delivery automation can open PRs; it cannot replace **“does the product still work for a human in a real browser?”** Playwright is our **hosted** regression tool — runs against a **real URL**, not only `localhost`, on schedule or manual dispatch.

---

### Why hosted matters

`localhost` catches a lot. **Hosted** catches auth cookies, CDN/edge behavior, third-party embeds, and config drift. Users live on hosted URLs; the loop should care about that signal on purpose.

---

### How this fits the Ship story

E2E answers a different question than “did the agent push code?” Keep that split clear — otherwise red E2E gets blamed on “the bot” instead of on **release readiness**.

---

### Where tests live in the monorepo

Application tests under `website/`; CI sets `PLAYWRIGHT_BASE_URL` to **dev**, **preview**, or another approved URL. Jobs and secrets: [Pre-release & E2E](../examples/elmundi/index.md#pre-release-e2e).

---

### Operating discipline

- **Flake is debt** — fix or quarantine with an owner; do not train people to ignore red.  
- **Smoke vs full** — smoke often; full can be nightly.  
- **Secrets** — test users and tokens are still secrets; shorter TTL, same discipline.

---

## Snyk (dependency signal) {#snyk}

**Why this layer:** Ship wants **evidence-backed** security/dependency signal for audit roles — not vibes. Snyk gives JSON we can feed prompts. Without a token, the job **skips** by design so bootstrap is not a wall of red.

---

### Why skip-by-default is not laziness

Lanes you **promised** should fail closed on missing secrets. Scanners are different: “required on day one” often means noise or blocked PRs while envs still spin up. Skipping with a clear log line is **honest** if you know where skipping is **not** allowed (production-adjacent or scheduled audits). ElMundi schedules: [Daily audits](../examples/elmundi/index.md#daily-audits).

---

### How we use the output

Audits **open tracker issues** only when the report justifies it; “no findings” means **no spam**.

**Good issue:** links artifact, names CVE/package/path, states severity. **Bad issue:** “please review dependencies” with no anchor.

That bar is **prompt + habit** more than Snyk itself — [Prompt catalog → security-officer](../prompts-workflows/index.md#prompt-catalog).

---

### Threat model (short)

Scanner output is **tooling**, not scripture — blind spots, false positives, stale lockfiles. Ship still wants the signal; humans **triage** like any other queue.
