# Prompts & workflows

This part of the manual is about **words that become behaviour**. Wire Linear, GitHub Actions, and a cloud agent perfectly, and you can still lose the plot when instructions live only in chat, a SaaS text box, or someone’s memory. Mature Ship adoptions treat prompts like workflow YAML: **git is the source of truth**, pull requests are the review room, and “we fixed it in Cursor” is a waypoint—not the system of record.

**Framework** says what must stay true. **Tools** names the adapters. **Here** we describe how prompt text evolves without turning the repo into a graveyard of one-off hacks—and how that evolution **starts small on purpose**.

**Jump:** [Start with a skeleton](#iterating-on-prompts) · [State flow (ElMundi)](#elmundi-sdlc-flow) · [Full `prompts/cloud-agent/` files](#prompt-catalog) · [Workflow patterns](#workflow-patterns)

Concrete filenames, cron minutes, and secrets stay in **[Examples → Reference org](../examples/elmundi/index.md)**. This page names **habits** and shows **real prompt files** from this package (the same ones ElMundi wires in production).

---

## Start with a skeleton, not a cathedral {#iterating-on-prompts}

### The trap of the “final” prompt

The mistake everyone makes is the same mistake as big-bang automation: sitting down to write the **perfect** prompt in one session. You produce a wall of rules—half of them untested, half of them wrong for your repo—and no story for how they stay true when the board, the tests, or the product changes next month. The model will happily obey contradictory instructions; the failure mode is not disobedience, it is **confidence**.

We do the opposite. You ship a **base**: enough structure that a headless run can attempt the job under **clear guards** (tracker state, branch contract, what “done” means for this role, when to **stop**). If the first draft is longer than a screen, **cut** it. Depth is not virtue; **testability** is. The grid and the ticket timeline will teach you where the text lied; your job is to listen and **grow** the prompt in thin layers, each layer merged like code.

### What the base must answer

A minimum prompt does not need poetry. It needs four kinds of clarity, usually in plain bullets:

**Scope** — one sentence on what this role is allowed to do—and what is explicitly out of bounds (no merge, no promote, no drive-by refactors).

**Preconditions** — which project, column or state, and labels must be true before the agent acts. If this is missing, you will debug “mood” instead of **guards**.

**Reporting** — how the run leaves traces: comment marker pattern, PR title shape, branch name. Auditability is not optional; it is how the next human knows what happened.

**Stops** — when to pause and ask a human instead of guessing: hollow ticket, ambiguous acceptance criteria, tests failing for reasons outside this change.

Everything else—edge cases, style guides, long examples—is **seasoning**. Add seasoning **after** at least one real run proves which corner actually hurts.

### Let one run be the teacher

The second step is not “brainstorm more rules.” It is: let the **schedule** run once (or fire `workflow_dispatch` for a single issue) and watch the run **end-to-end**. Note what the agent did, skipped, or misunderstood; pair **ticket timeline** with **workflow run** so the story is reconstructable. If you cannot pair them, stop—you are not ready to tune wording; your audit trail is broken.

Then fix **interactively** at human speed: inline agent, small patch, small explanation. **One** misunderstanding per iteration. Prefer a concrete example from *this* repository over abstract lecture. If the real fix is a **policy** change—new label, tighter pick—change tracker or pick **before** you add another paragraph to the prompt.

### Write the lesson back

The interactive fix is not durable until it lives in **`prompts/cloud-agent/*.md`** (for scheduled Cursor Cloud runs). Open a PR, link the ticket that exposed the gap, and merge to the branch your schedules checkout—usually `main`. Reviewers should ask: does this rule **overfit** one weird ticket, or does it generalise? Chat is fast; **merged markdown** is what `cloud-agent-launch.mjs` actually reads.

!!! note
    If you skip the PR, the next cron tick will happily repeat the bug. That is not the model forgetting—it is you forgetting where the source of truth lives.

### Why git matters

If the prompt lives only in a vendor UI, you cannot **diff** it, **blame** it, or **roll back** when a clever rule blows up production. You also cannot prove what text ran last Tuesday. Treat prompts as **config code**—same review bar as `*.yml`.

### Order of operations when something breaks

Often the bug is not the prompt—it is **what** got picked or **how** the agent was launched.

1. Fix **pick** / cron / project guards if the wrong work is selected.  
2. Fix **launch** wiring if the agent never sees the right branch, ref, or secrets.  
3. Then tighten **prompt** wording.

See [Workflow patterns](#workflow-patterns) for intent; **[Workflows catalog](../examples/elmundi/index.md#workflows-catalog)** for filenames in the reference org.

### Anti-patterns we have actually seen

**No user story** — “be smart” is not a requirement. **Silent retries** without ticket comments destroy auditability. **One mega-prompt** for every role makes diffs unreadable; split by **role file** (`intake`, `developer`, …). **Pasting** a novel from Notion guarantees drift the day someone edits Notion. **Testing only in chat** — chat lies cheerfully; CI and scheduled runs tell the truth.

The habit underneath is almost boring on purpose: **boring survives reality**. When reality punches a hole, fix interactively once, then **write the lesson back** so headless runs do not depend on who is online.

---

## ElMundi reference: states, labels, and the delivery graph {#elmundi-sdlc-flow}

The diagram is the **pretty view** of the same wiring spelled out in **[Examples → ElMundi → SDLC scheduled](../examples/elmundi/index.md#sdlc-scheduled)**: human-only **Backlog**, automation picking only **Todo** in the delivery project, four roles on the **even-hour UTC grid** (`:10` intake → `:25` clarification → `:40` BA → `:55` developer), and **developer** as the only role that moves a card to **In Progress** after pick + `cli start` + the `fix/<ISSUE>-auto` branch. The **legend** box on the diagram names the label-shaped milestones in plain language (without colons in edge labels, so the graph stays readable).

![ElMundi reference — Linear delivery board × SDLC grid](../diagrams/sdlc-linear-states.svg)

**Columns (canonical names for that wiring):**

| # | Status | Meaning |
|---|--------|---------|
| 1 | **Backlog** | Human triage only; no SDLC pick |
| 2 | **Todo** | Intake → clarification → BA → (with `ready:developer`) developer |
| 3 | **In Progress** | Implementation after developer pick + `cli start` |
| 4 | **In Review** | PR, preview, QA |
| 5 | **Done** | Complete |
| 6 | **Blocked** | Stop |

**Label-shaped milestones on the happy path (simplified):** intake may set `needs:clarification` or `stage:intake`; after BA, **`ready:developer`** on **Todo** is the gate for the developer pick. Exact pick scripts and filters live beside the workflows in the example chapter—this page stays the **story**; that chapter stays the **receipts**.

Daily **audit** roles (tech / QA / security) use **separate** Linear projects and **no** delivery-queue pick; see **[Daily audits](../examples/elmundi/index.md#daily-audits)**.

---

## Cloud-agent prompt catalog {#prompt-catalog}

The bodies that used to live under `prompts/cloud-agent/*.md` are now first-class **artifacts** in this repository — every cloud-agent role is a versioned `pattern/cloud-*` body resolved by `shipctl pattern fetch` and pinned in `.ship/config.yml`. The catalog below is the canonical map; click the link to read the full body, the routing block (which Linear project, which schedule), and the front-matter (`install_target`, `marker`, `version`).

Placeholders such as `{{ISSUE}}`, `{{BASE}}`, and `{{SKILLS_CONTEXT}}` are filled by your project launcher/runtime at run time; treat them as part of the contract, not as typos.

**How to change them safely:** [Start with a skeleton](#iterating-on-prompts), then open a PR against the relevant `artifacts/patterns/cloud-*/ARTIFACT.md` file. Cron schedules pick up the new body on the next sync; no vendor UI involved.

| Bucket | Pattern | Role |
|--------|---------|------|
| Shared | [`pattern/cloud-base`](/patterns/cloud-base) | Cross-cutting rules for every headless role: queue boundaries, Linear as the human channel, idempotency, branch contract, audit markers. |
| Delivery | [`pattern/cloud-intake`](/patterns/cloud-intake) | First pass on **Todo** work — triage, questions, **stop** when hollow. |
| Delivery | [`pattern/cloud-clarification`](/patterns/cloud-clarification) | Follow-up when humans answer (or nudge if still stuck). |
| Delivery | [`pattern/cloud-ba`](/patterns/cloud-ba) | Spec shape, AC, set `ready:developer` when appropriate. |
| Delivery | [`pattern/cloud-developer`](/patterns/cloud-developer) | Implement, test, **one** PR, move card to **In Review**. |
| Platform | [`pattern/cloud-workflow-self-heal`](/patterns/cloud-workflow-self-heal) | Minimal pipeline fixes; narrow scope. |
| Audit | [`pattern/cloud-tech-architect`](/patterns/cloud-tech-architect) | Architecture / tech-debt findings with paths. |
| Audit | [`pattern/cloud-qa-architect`](/patterns/cloud-qa-architect) | Test-strategy gaps with file paths. |
| Audit | [`pattern/cloud-security-officer`](/patterns/cloud-security-officer) | Snyk-grounded issues only. |

The full A-series catalog (`catalog-a1-intake` through `catalog-a13-daily-retro`) describes the **idealised** SDLC roles independent of runtime. The `cloud-*` family above is the cloud-agent **implementation** of that catalog. Use the catalog as the contract, the cloud bodies as the wiring.

**Adding a new role:** decide **which project** receives output, **which schedule** fires it, **what guards** stop the run, and **what artefact** proves success. If you cannot answer, you are adding noise — not a prompt. Then add an `artifacts/patterns/<id>/ARTIFACT.md` file with the front-matter spec (`kind: pattern`, `id`, `version`, optional `install_target`/`marker`).

**Skills** under `.cursor/skills/` are embedded by the launch path — keep them **short**; link out to this manual for depth. **Onboarding** playbooks live as `pattern/adopt-ship-*` (see [Adoption → Overview](../adoption/index.md)).

---

## Workflow patterns {#workflow-patterns}

YAML files differ by **intent**. This section names the **patterns**—the *why*—so you can choose your own filenames without copying ours by accident.

**Concrete filenames** for the reference org: **[Workflows catalog](../examples/elmundi/index.md#workflows-catalog)**.

### Scheduled delivery grid

**Intent:** cron ticks for ordered SDLC roles on the **delivery** lane. Each tick: **pick** (deterministic) → **maybe** launch agent.

**Invariants:** one **delivery** role per time slot; pick runs before expensive steps; “no ticket qualified” exits zero—green, quiet.

### Daily audits

**Intent:** cadence that **does not** starve the delivery queue. Writes to **audit** projects with evidence rules.

**Invariants:** audit jobs do not pick from the same **Todo** column as delivery unless you want stand-up to catch fire; new tickets cite **artifacts**.

**Canonical role for cross-system retro:** [`catalog-a13-daily-retro`](/patterns/catalog-a13-daily-retro) — once-a-day pass that reads the **tracker delta** and the last 24 h of run journals to surface dead loops (`tracker_delta == 0`), regression drift against the 7-day baseline, vendor outages clustered into one finding, and replay/coverage gaps. Pairs with [`catalog-a12-learning`](/patterns/catalog-a12-learning) (per-issue lessons) and [`catalog-a11-retry-sweep`](/patterns/catalog-a11-retry-sweep) (every-6-hours stuck-issue sweep) to cover three cadences without overlap.

### Self-heal

**Intent:** CI / pipeline diagnostics; optional agent follow-up on a dedicated ticket or thread.

**Invariants:** platform health, not “ship features faster”; no silent product edits outside normal review.

### Autonomous loop (complementary)

**Intent:** extra automation on another cadence.

**Invariants:** not a replacement for the delivery grid; explicit **guards** and **stop** conditions.

### PR preview + checks

**Intent:** human-opened PR path—previews, smoke tests, policy checks.

**Why it matters:** humans and bots share one repo; preview workflows keep **human** lanes fast without borrowing delivery cron.

### Hosted E2E regression

**Intent:** Playwright (or similar) against a **live** dev/stage URL.

**Why hosted matters:** auth, CDN, third-party behaviour—`localhost` will not see them.

### Release / promote

**Intent:** promote images or assets toward production—often **manual** or policy-gated.

**Invariants:** promotion stays human-owned unless automated with the same rigour as pick/launch; rollback story exists before you brag about velocity.

### Webhooks / integrations

**Intent:** optional glue—events, notifications, bridging.

**Invariants:** webhooks should not become an undocumented second scheduler; idempotency matters.
