# Prompts & workflows

This chapter exists because behaviour and wording drift together. You can wire Linear, GitHub Actions, and a cloud agent correctly and still watch the loop go sideways when the *instructions* live only in someone’s head or in a product UI that forgets to version itself. Mature Ship adoptions treat prompts the same way they treat workflow YAML: **git is the source of truth**, PRs are the review room, and “we fixed it in chat” is a waypoint — not the finish line.

**Framework** says what must stay true about automation. **Tools** names the adapters. **Here** we spell out how prompts and workflow *patterns* evolve without turning the repo into a graveyard of one-off hacks: start thin, let a scheduled or manual run prove where the text lied, patch once at human speed, then **promote** the lesson into `cloud-prompts/` so the **next cron tick** inherits it. That is how tonight’s thread becomes tomorrow’s run — with a diff you can blame and roll back.

A delivery slot picks a ticket and the agent misreads a label guard; you reproduce in inline chat, land a one-line clarification, open a small PR against `developer.md`, merge, and the following morning’s grid no longer argues with the board. Intake keeps guessing on hollow tickets until you tighten the **stop** language in `intake.md` after a real clarification comment on Linear — same loop: chat proves the fix, the file makes it durable. An audit role files a finding without a path or log pointer; you adjust `tech-architect.md` once, and the next **daily audit** cadence stops emitting vibes-as-tickets. Platform noise is different: `workflow-self-heal.md` stays narrow because the temptation to “just be heroic” in prose is how headless runs start editing product code you did not mean to hand them.

The habit underneath is almost boring on purpose — **boring survives reality**. Prompts sit next to the code and workflows they steer. When reality punches a hole, you fix **interactively** once, then **write the lesson back** so headless runs do not depend on who is online.

---

## Read this first

[Iterating on prompts](#iterating-on-prompts) — the piece everyone skips, then regrets skipping.

---

## Catalogs

- [Prompt catalog](#prompt-catalog) — roles and files in `cloud-prompts/`.  
- [Workflow patterns](#workflow-patterns) — what each **class** of YAML is for (concrete filenames stay under **Examples**).

---

## Where the files actually are

In this package: `cloud-prompts/` at the repo root (or the same path inside a vendored copy).  
Skills embedded by the launch path live under `.cursor/skills/` — keep them **short**; link out to this manual for depth.

---

## Iterating on prompts {#iterating-on-prompts}

### The mistake everyone makes

Sitting down to write the **final** prompt in one session. You get a wall of rules, half of them wrong, and no story for how they stay true when the repo moves.

We do the opposite.

---

### The loop (four steps)

#### 1. Ship a **minimum** prompt

Enough for the agent to **attempt** the job under clear guards (tracker state, branch name, tests to run, files it may touch). If it is longer than a screen, **cut** it.

**Good minimum prompts answer**

- **What** is in scope for this role (one sentence).  
- **Where** state must be before you start (project + state + labels).  
- **How** you report back (ticket comment pattern, PR title pattern).  
- **When** you must **stop** and ask a human (ambiguous ticket, missing spec, failing tests you did not introduce).

**Bad minimum prompts**

- “Be smart.”  
- “Follow best practices.”  
- “Refactor as needed.” (Needed *according to whom*?)

---

#### 2. Let the schedule run (or fire manually)

Watch **one** run end-to-end. Capture:

- what it did,  
- what it skipped,  
- what it misunderstood,  
- which **ticket** and **workflow run** to pair in the timeline.

If you cannot pair them, stop — your audit story is broken before you tune wording.

---

#### 3. Fix **interactively** when something breaks

Open the ticket or log, reproduce in **Copilot / inline agent** mode: small patch, small explanation. This is **human speed** — cheap and precise.

**Rules of thumb**

- Fix **one** misunderstanding per iteration.  
- Prefer **concrete** examples from *this* repo over abstract lecture.  
- If the fix requires a **policy** change (new guard, new label), change **pick** or tracker **before** you patch the prompt again.

---

#### 4. **Write the lesson back** into `cloud-prompts/*.md`

The interactive fix is **not** the system of record. The **markdown in the repo** is. Merge the wording so the next **headless** run inherits the behaviour.

!!! note
    Chat is fast; **merged** `cloud-prompts/*.md` is what the launch path actually reads. If you skip the PR, the next scheduled run will happily repeat the bug.

**Definition of done**

- PR with prompt diff, linked to the ticket that exposed the bug.  
- Reviewer checks: does this rule **overfit** one ticket, or does it generalise?  
- Merged to the branch your schedules actually checkout (usually `main`).

That is how tonight’s chat becomes tomorrow’s automation.

---

### Why git matters

If the prompt is only in a SaaS UI, you cannot:

- **diff** it in a PR,  
- **blame** who changed a guard,  
- **roll back** when a “clever” rule blows up production,  
- prove **what** ran last Tuesday.

Treat prompts like **config code** — same review bar as `*.yml`.

---

### Pair with workflow changes

Sometimes the bug is not the prompt — it is **when** the job runs or **which** issue gets picked.

**Order of operations**

1. Fix **pick** / cron / project guards if the wrong work is selected.  
2. Fix **launch** wiring if the agent never sees the right branch or env.  
3. Then tighten **prompt** wording.

See [Workflow patterns](#workflow-patterns) for intent; [Examples → Workflows catalog](../examples/elmundi/index.md#workflows-catalog) for filenames.

---

### Anti-patterns we have actually seen

- **No user story** — “be smart” is not a requirement.  
- **Silent retries** without logging to the ticket — you lose auditability.  
- **One mega-prompt** for every role — split by **role file** (`intake`, `developer`, …).  
- **Copy-pasting** a novel-length policy from Notion — it will drift the day someone edits Notion.  
- **Testing only in chat** — chat lies cheerfully; CI tells the truth.

---

### Where the files live

In this monorepo: `cloud-prompts/` (see [Prompt catalog](#prompt-catalog)).

**Skills** under `.cursor/skills/` are embedded by the launch script — keep them **short** and link out to deeper docs when needed.

---

## Prompt catalog {#prompt-catalog}

Files live in **`cloud-prompts/`** in the repository. `_base.md` is shared scaffolding; others are **one role per file** so diffs stay reviewable.

**How to change them safely:** [Iterating on prompts](#iterating-on-prompts).

---

### Shared

| File | Role | Lane |
|------|------|------|
| `_base.md` | Shared rules, tone, cross-cutting guardrails (what every headless run must assume about the repo). | All |

**Guidance:** if a rule truly applies everywhere, it belongs here. If it is specific to implementation, keep it in the role file so you do not widen blast radius.

---

### Delivery lane

| File | Role | What “good” looks like |
|------|------|-------------------------|
| `intake.md` | First pass on new **Todo** work | Triage, initial scope, explicit questions — **stops** instead of guessing when the ticket is hollow. |
| `clarification.md` | Clarify scope / questions | Surfaces unknowns with crisp asks; updates ticket for humans to answer. |
| `ba.md` | Analysis / acceptance shape | Turns fuzzy intent into testable acceptance notes **without** inventing product decisions. |
| `developer.md` | Implementation + tests + PR | Branch + PR contract honoured; tests run or explicitly called out; no drive-by refactors outside scope. |

**Culture:** delivery prompts **move** scoped work. They do not **merge** it and do not **promote** it unless you explicitly gave that mandate (we recommend you do not).

---

### Platform

| File | Role | Lane |
|------|------|------|
| `workflow-self-heal.md` | Pipeline health / CI follow-up | Platform |

**Guidance:** keep this prompt **narrow** — diagnose, link artifacts, suggest minimal fix. Platform prompts become dangerous when they try to be hero developers.

---

### Audit lane

| File | Role | What “good” looks like |
|------|------|-------------------------|
| `tech-architect.md` | Architecture audit | Findings tied to paths, logs, or metrics — not vibes. |
| `qa-architect.md` | QA audit | Risk-grounded notes; respects existing test strategy. |
| `security-officer.md` | Security / dependency audit | Grounded in scanner output (e.g. Snyk JSON) — **no fabrication** tickets. |

**Culture:** audit prompts **surface evidence**. They do not compete with delivery for “who ships fastest.”

---

### Adding a new role

Before you add `new-hat.md`, answer:

1. **Which tracker project** receives its output?  
2. **Which schedule** fires it — delivery grid, audit grid, or something else?  
3. **What stops** the role from running (guards)?  
4. **What artefact** proves it did the right thing?

If you cannot answer, you are not adding a prompt — you are adding noise.

---

## Workflow patterns {#workflow-patterns}

YAML files differ by **intent**. This page names the **patterns** — the *why* — so you can design your own filenames without copying ours by accident.

**Concrete filenames** for the reference org live in [Examples → Workflows catalog](../examples/elmundi/index.md#workflows-catalog).

---

### Scheduled delivery grid

**Intent:** cron ticks for ordered SDLC roles on the **delivery** lane. Each tick: **pick** (deterministic) → **maybe** launch agent.

**Invariants**

- One **delivery** role per time slot (unless you have a documented exception — exceptions rot).  
- Pick runs **before** any expensive step.  
- “No ticket qualified” exits **zero** — green, quiet.

**When to split workflows** per role vs one workflow with matrix: operational taste — keep logs readable for on-call.

---

### Daily audits

**Intent:** separate cadence that **does not** starve the delivery queue. Writes to **audit** tracker projects with evidence rules.

**Invariants**

- Audit jobs do **not** pick from the same Todo column as delivery unless you want stand-up to catch fire.  
- Tickets created here should cite **artifacts** (report path, job URL, failing test name).

---

### Self-heal

**Intent:** CI / pipeline diagnostics; optional agent follow-up on a **dedicated** ticket or comment thread.

**Invariants**

- Self-heal is for **platform health**, not “ship features faster.”  
- It should **not** silently change product code without the same review path as any other PR.

---

### Autonomous loop (complementary)

**Intent:** extra automation that may run analysis or prep work on another cadence.

**Invariants**

- **Not** a replacement for the delivery grid — otherwise you reintroduce overlapping agents under a prettier name.  
- Explicit **guards** and **stop** conditions — autonomous should still mean *bounded*.

---

### PR preview + checks

**Intent:** human-opened PR path — build previews, smoke tests, policy checks.

**Why it matters in Ship:** humans and bots share one repo. Preview workflows are how you keep **human** lanes fast without borrowing delivery cron.

---

### Hosted E2E regression

**Intent:** Playwright (or similar) against a **live** dev/stage URL — scheduled or manual.

**Why hosted matters:** catches auth, CDN, third-party sandbox issues that `localhost` will not see. This is the usual companion to **release** discipline, not to “agent wrote code.”

---

### Release / promote

**Intent:** promote container images or static assets toward production — often **manual** or policy-gated.

**Invariants**

- Promotion decisions stay **human-owned** unless you have explicitly automated them with the same rigour as pick/launch.  
- Tagging and rollback story must exist before you brag about velocity.

---

### Webhooks / integrations

**Intent:** optional glue — review events, notifications, bridging to other systems.

**Invariants**

- Webhooks should **not** become a secret second scheduler unless you document them like one.  
- Idempotency matters — GitHub will retry.
