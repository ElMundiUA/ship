# Framework

## The idea {#the-idea}

Most “AI in SDLC” demos are **loud**: one button, a magic PR, no story about *who* may touch *what*, or what happens when two agents wake up at once.

**Ship** is **quiet on purpose**. Quiet does not mean slow. It means **predictable**: the same inputs tomorrow should produce the same *class* of outcomes, and when something goes wrong you can **trace** it without heroics.

In the ElMundi monorepo we have lived the unglamorous half of that story—duplicate PRs for one ticket, pick logic that looked “fine” until a label changed, preview deploys that needed several probe iterations before they told us anything useful. None of that was the model being stupid. It was the system being under-specified.

---

### The loud demo trap

The trap is emotional. A slick video shows an agent “just fixing” production. Your team asks: *why don’t we have that?*

You bolt on access. You skip the tracker. You let the model pick “whatever is urgent.” For a week it feels fast. Then:

- Two PRs land for the same ticket.  
- Someone merges the wrong branch.  
- An agent “helpfully” touches a ticket that was **not** ready.  
- Nobody can answer *which* automation moved *which* state, *when*.

That is not a failure of the model. It is a failure of **governance**. Ship exists so you get the upside of agents **without** surrendering the story of control.

---

### Three truths we do not negotiate

#### 1. Humans own intent

Backlog, merge, production, and policy live with **people**. Automation gets a **lane**, not the keys to the kingdom.

**What that means in practice**

- A ticket in **Backlog** is a *wish*. Automation does not “help” by dragging it forward because it looks easy.  
- **Merge** is a human decision (or a policy you explicitly encoded — still *yours*).  
- **Production** is promoted when *your* org says so — not when an agent feels done.

If you blur this line, you stop being able to say “we decided” and start saying “it happened.” That is a bad place for regulated, customer-facing, or multi-team work.

#### 2. Machines need fences

If you cannot say — in plain language — *“automation may only touch issues in column X with label Y in project Z”*, you do not have a system. You have a chatbot with repo access.

**Fences are not insults to the model.** They are **interfaces**. Just like you would not let a microservice call every table in the database, you do not let an agent see every ticket on the board.

Good fences are **boring**:

- A **project** scopes *which* backlog belongs to this product.  
- A **state** says *where* in the human workflow the work sits.  
- A **label** carries *intent* (“ready for automation,” “blocked on human,” “evidence attached”).

When fences are explicit, you can **test** them. When they are vibes, you can only **argue** about them in Slack.

We learned that lesson in ElMundi when delivery work had to respect a small **label contract** (for example ELM-scoped guards): if the contract is clear, pick logic stays boring; if it drifts, “green” runs lie and humans stop trusting the board.

#### 3. Throughput must be bounded

One automated role per time window beats a stampede. Queues stay visible in the tracker so the team can *see* work piling up instead of guessing.

**Why one role per slot**

Overlapping agents on the same codebase correlate with:

- branch races,  
- conflicting file edits,  
- duplicate PRs,  
- reviewer fatigue.

**Bounded throughput** is how you keep signal. If the queue grows, that is **data**: you need more humans, narrower scope, or a policy change — not an extra cron job at :02 and :04 and :06.

!!! note "Field note"
We hit duplicate PRs and branch fights in the ElMundi monorepo when two jobs thought they owned the same ticket or naming drifted. The durable fix is never “smarter model”—it is one delivery role per window plus a branch/title contract everyone actually follows.
!!!

---

### What you are actually buying {#what-you-are-actually-buying}

Not “Cursor.” Not “Linear.” You are buying a **control loop** you can operate for years.

The table below is the whole product in four nouns. If you cannot explain each row to a new hire in one sentence, pause before you buy another integration.

| Piece | Job |
|-------|-----|
| **Tracker** | System of record for **state** and **guards** — what is allowed to move, and where it sits today. |
| **Scheduler** | Wakes on a clock, runs **cheap** selection logic first, stops early when nothing qualifies. |
| **Agent** | Executes a **versioned** prompt against a branch, under a **contract**: branch name, PR title/body, ticket comments, allowed tools. |
| **Audit** | Every automated touch ties to a **ticket**, a **CI run**, and a **tagged comment** — so “what happened?” has an answer without opening Slack archaeology. |

If you cannot draw this loop on a whiteboard in two minutes, you are not ready to buy more tools. You are ready to simplify.

---

### What we refuse to optimize for

- **Surprise work** — picking from Backlog because it is “faster.” Speed without guardrails is indistinguishable from chaos.  
- **Hero agents** — overlapping runs that fight for the same branch or ticket. Heroes do not scale; **schedules** do.  
- **Prompts only in a SaaS UI** — if it is not in git, it is not reviewed like code. If it is not reviewed like code, it will drift the week you are on holiday.  
- **Vanity throughput** — “we ran the agent 400 times this week.” Great. How many outcomes were **mergeable**, **auditable**, and **intended**?

---

### The mistake serious teams still make

They try to write the **final** policy before the first run. You get a wall of rules, half of them wrong, and no feedback loop from reality.

Ship assumes the opposite: **thin prompts**, **visible queues**, **tight fences**, then **iterate** (see [Prompts & workflows → Iterating on prompts](../prompts-workflows/index.md#iterating-on-prompts)). The framework gives you the **shape** so iteration does not break production.

---

### Proof and where to go next

**[Examples → ElMundi](../examples/elmundi/index.md)** is one full wiring — cron minutes, project names, secrets, workflows. The framework chapters describe **that shape** without locking you to our domains or image tags.

**Reading order from here**

1. [The system](#the-system) — boxes, arrows, and where business rules live.  
2. [Running the loop](#running-the-loop) — cadence, queues, “green” CI, audits.  
3. [Trust & boundaries](#trust-and-boundaries) — data flows, vendors, threats in plain language.

**Procurement / stakeholders:** after *The system*, read *Trust & boundaries* and [Buying & procurement](../index.md#buying-and-procurement) on **Start here**.

---

## The system {#the-system}

### One paragraph

A **work tracker** holds states and labels. A **scheduler** (usually CI) fires on a cadence, runs **deterministic pick logic** (at most one item per slot), and only then may call an **agent API**. The agent works on an isolated branch and opens a PR under a fixed naming contract. A **second loop** handles audits (architecture, QA, security) in **separate tracker projects** so governance does not drown the sprint board.

Everything else — vendor logos, exact YAML filenames, domain names — is **adapter detail**. This page is the **shape**.

---

### The life of one ticket (happy path)

Imagine a feature ticket that is **real**: scoped, owned, and ready for automation *when humans say so*.

1. **Human** moves it into the automation entry state (in our reference deployment that is **Todo** in a delivery project — never picked straight from **Backlog**). Guards like labels and team membership must already be true.  
2. **Scheduler** fires. **Pick** scans the board with boring, testable rules. Either **zero** or **one** ticket is selected for this role and this time slot.  
3. **Launch** builds a **versioned** prompt (from git), passes issue metadata, and calls the **agent API**.  
4. **Agent** checks out the repo, works on a **branch that encodes the ticket key**, opens or updates a **PR** with a predictable title/body pattern, and leaves **structured** notes on the ticket.  
5. **Human** reviews, requests changes, or merges — *outside* the agent’s mandate unless you explicitly gave that mandate (we recommend you do not).  
6. **Audit roles** on a **different cadence** may open *separate* findings in *separate* projects. They do not steal capacity from the delivery lane.

If you cannot narrate your own process in those six beats, pause tooling and fix the story first.

ElMundi runs this shape with **Todo-only** picks for delivery: Backlog stays human turf, and the scheduler’s job is to say “zero or one” from a column that already means “we agreed this is eligible.”

---

### Diagrams

**Context** — who talks to whom:

![System context](../diagrams/architecture.svg)

**SDLC board** — columns left to right (Backlog → … → Done). Automation enters at **Todo**, not Backlog:

<div class="ship-sdlc-board" markdown="0" aria-label="SDLC delivery board by column">
  <section class="ship-sdlc-col ship-sdlc-col--human">
    <h3 class="ship-sdlc-col__title">Backlog</h3>
    <div class="ship-sdlc-col__body">
      <p><strong>Human</strong> — triage, priority, shaping.</p>
      <p>SDLC automation <strong>does not</strong> pick cards from this column.</p>
    </div>
  </section>
  <section class="ship-sdlc-col ship-sdlc-col--human">
    <h3 class="ship-sdlc-col__title">Todo</h3>
    <div class="ship-sdlc-col__body">
      <p><strong>Automation entry</strong> — delivery project + guards (labels, team).</p>
      <p>Inside the column: Intake → Clarification → BA → <code>ready:developer</code> (one automated role per schedule slot).</p>
    </div>
  </section>
  <section class="ship-sdlc-col ship-sdlc-col--auto">
    <h3 class="ship-sdlc-col__title">In progress</h3>
    <div class="ship-sdlc-col__body">
      <p><strong>Agent + human oversight</strong> — branch <code>fix/TICKET-auto</code>, Cloud Agent run, PR in flight.</p>
    </div>
  </section>
  <section class="ship-sdlc-col ship-sdlc-col--auto">
    <h3 class="ship-sdlc-col__title">In review</h3>
    <div class="ship-sdlc-col__body">
      <p><strong>Human + CI</strong> — PR review, preview deploy, required checks.</p>
    </div>
  </section>
  <section class="ship-sdlc-col ship-sdlc-col--human">
    <h3 class="ship-sdlc-col__title">Done</h3>
    <div class="ship-sdlc-col__body">
      <p><strong>Human</strong> — merged; ticket closed on the board.</p>
    </div>
  </section>
</div>

The **context** diagram above still comes from <code>docs/diagrams/architecture.d2</code> (SVG on <code>mkdocs build</code> when <code>d2</code> is on your <code>PATH</code>). This board is plain HTML in the page so type stays readable at any zoom.

---

### The four players (and what each must *not* do)

#### Scheduler (CI platform)

**Must:** wake on time, expose secrets safely, checkout the right ref, run scripts with predictable exit codes, leave a **workflow run URL** in the audit trail.

**Must not:** encode business rules in 200-line YAML conditionals. YAML is for **wiring**; scripts are for **logic**.

#### Pick layer

**Must:** return **at most one** issue key per run (or a clear “nothing to do”), using only fields you would put in a code review: state, labels, project, team, ordering rules.

**Must not:** call the agent “just in case.” If nothing qualifies, the run should be **quietly** successful — that is a feature, not a bug.

#### Agent runtime

**Must:** respect branch/PR contracts, stay inside tool allow-lists, and write **machine-readable** updates back to the tracker when the prompt says so.

**Must not:** improvise scope. If the ticket is vague, the correct output is often a **comment** and a **stop** — not a speculative refactor.

#### People

**Must:** own intent, merge policy, production promotion, and prompt **governance** (who may merge changes to `cloud-prompts/`).

**Must not:** micromanage every run. If you need to babysit each execution, your fences are too loose or your prompts are too vague.

---

### Projects in the tracker (pattern)

Delivery, findings, and scanner noise want different boards. The table is the minimum split; names are yours to choose.

| Project type | Role |
|--------------|------|
| **Delivery / pre-release** | Operational SDLC. Automation does **not** pick from Backlog; **Todo** + labels + project membership gate every pick. |
| **Tech debt / findings** | Evidence-based outputs from audit roles — each ticket should point at a log, a report, or a failing check. |
| **Security / dependencies** | Findings from scanners (e.g. Snyk), deduplicated so the board does not become spam. |

Exact names are **example-specific** — see [ElMundi → SDLC scheduled](../examples/elmundi/index.md#sdlc-scheduled) for one concrete mapping.

**Why split projects at all?** So you can stand in stand-up and answer: *“What is blocking release?”* without wading through fifty architecture nits that are **true** but **not release-blocking today**.

---

### Deterministic pick (why boring is load-bearing) {#deterministic-pick}

**Deterministic** means: given the same board snapshot, pick returns the same choice. No random tie-breaks, no “model’s favourite ticket,” no time-of-day creativity.

**Why it matters**

- **Debugging** — “why did it pick ENG-123?” has an answer in tracker fields.  
- **Fairness** — ordering rules are explicit (e.g. oldest in Todo first).  
- **Safety** — you can write a test or a dry-run script for pick logic.

**Anti-patterns**

- “Pick anything in Todo.” (Too broad — add project + label guards.)  
- “Pick the newest.” (Starves old work — sometimes intentional, always a **policy** call.)  
- Multiple picks per slot. (Breaks the one-role-per-window rule.)

When we tightened **Todo-only** deterministic picks in ElMundi, the win was not cleverness—it was sleep. “Same board, same winner” turns triage from a séance into a diff.

---

### Branch and PR contract

Ship works best when **every** automated PR obeys a naming scheme humans recognize: ticket key in the branch, ticket key in the title, predictable labels for bots.

That contract is how you:

- prevent duplicate PRs for the same issue,  
- grep history by ticket,  
- teach reviewers what to expect from automation vs humans.

Concrete patterns for our repo: [ElMundi → Pre-release & E2E](../examples/elmundi/index.md#pre-release-e2e).

---

### Audits live in a parallel universe

Delivery automation answers: *“move scoped work forward.”*  
Audit automation answers: *“surface evidence-backed risk.”*

When you mix them on one board without discipline, humans mute notifications and miss real blockers. Separate projects (and often separate schedules) keep both honest.

**Daily audit roles** in ElMundi sit on that second track: same clock discipline, different project, evidence attached—so a security or architecture finding does not masquerade as “the next dev ticket” on the sprint.

Example wiring: [ElMundi → Daily audits](../examples/elmundi/index.md#daily-audits).

---

### Swapping pieces (adapters, not rewrites)

Orchestration stays **plain scripts + HTTP**. The tracker and agent vendor are **adapters**: same contract (issue key, branch name, PR body, state transitions), different APIs.

**What you keep**

- Versioned prompts in git.  
- Pick → launch → PR flow.  
- One role per time slot for delivery.  
- Separate audit lane.

**What you swap**

- Linear → Jira / YouTrack / …  
- GitHub Actions → GitLab CI / Buildkite / …  
- Cursor Cloud Agent → another hosted agent with an HTTP API (adapter work).

Tooling detail: [Tools → overview](../tools/index.md).


## Running the loop {#running-the-loop}

### Cadence (pattern)

Split **delivery roles** across time windows so only **one** automated role runs per slot. Typical shape: **intake → clarification → analysis → implementation**, each in its own cron tick.

**Why a grid instead of “always on”**

Always-on sounds efficient. In practice it creates **correlated failures**: two jobs start together, fight over locks, or stampede the agent API. A grid gives you:

- **predictable load** for rate limits,  
- **clear ownership** (“the :40 slot is BA”),  
- **human-friendly debugging** (“check the 14:40 run”).

**Canonical numbers** (UTC minutes, even hours, which role lands where) are **example-specific** — see [ElMundi → SDLC scheduled](../examples/elmundi/index.md#sdlc-scheduled).

---

### A day on the grid (mental model)

Morning: tickets sit in **Todo** with correct labels — nothing moves until the schedule says so.  
Each slot: **pick** runs first. If nothing qualifies, the workflow is **green** and **silent**. That is success.  
When something *is* picked, **launch** runs and the agent leaves traces: branch, PR link, ticket comment.

Humans review PRs when *they* are ready. The loop does not replace review; it **feeds** review with smaller, scoped units of work.

---

### What “green” means (read the right layer)

A green scheduler run does **not** always mean “the developer agent shipped code.” It means **that stage’s job** finished without infrastructure failure.

**Green can mean**

- pick found nothing (valid).  
- agent ran but concluded “needs human input” (valid if your prompts say so).  
- scanner step skipped because a token is missing in dev (sometimes valid — know your policy).

**Green is *not* automatically**

- merge to main,  
- deploy to prod,  
- “the ticket is done.”

Learn to read **which job** executed and **which** ticket (if any) was touched. The workflow title and the ticket timeline should tell the same story.

PR preview checks are part of that story: a green **delivery** run can still leave you iterating on **probe** steps until the hosted URL actually proves what you think it proves. Treat “preview up” and “preview verified” as different sentences.

---

### Queues are a feature, not embarrassment

If you cannot see the queue, you cannot manage WIP. The tracker board is the **first** dashboard; optional snapshot scripts are the **second**.

**Healthy signs**

- Todo depth grows and shrinks with staffing — not as a hidden surprise.  
- Stuck tickets have **visible** labels or comments explaining the block.  
- Automation stops when guards fail — instead of pushing half-baked PRs.

**Unhealthy signs**

- “Everything is In Progress.” (Often means WIP limits failed.)  
- Silent retries without ticket comments. (You lose auditability.)  
- Humans bypass the board “just this once.” (Board rot follows.)

---

### Audits ≠ delivery

Daily architecture / QA / security passes should **not** consume the delivery queue. They write to **other projects** with **evidence-only** rules: no ticket without a log, report, or failing check to point at.

**Why strict evidence**

Without it, audit bots become **opinion engines** — interesting, not actionable. With evidence, a security ticket can be **reproduced** and **closed** like any other bug.

Example wiring: [ElMundi → Daily audits](../examples/elmundi/index.md#daily-audits).

---

### When you outgrow the defaults

Add **self-heal** or **autonomous** loops only after the main lane is **boring**:

- Delivery picks are stable.  
- Duplicate PRs are rare and understood.  
- Prompt changes go through review and **do not** surprise the team on Monday morning.

Extra schedulers are **additive**. They are not replacements for the delivery grid — otherwise you reintroduce overlapping agents under a new name.

In ElMundi we leaned on **workflow self-heal** and failed-check recovery when the real problem was not “the agent is dumb” but “required checks flaked or handoff never happened.” That loop is a mop for a messy kitchen—not a substitute for washing dishes after dinner.

See [Workflow patterns](../prompts-workflows/index.md#workflow-patterns) for intent; [ElMundi → Workflows catalog](../examples/elmundi/index.md#workflows-catalog) for filenames.

!!! note "Field note"
Self-heal shines when the main grid is already trustworthy. If duplicate PRs or fuzzy picks are still normal, a recovery bot just moves faster around a broken compass.
!!!

---

### “Sounds slow” — the honest answer

Bounded automation can feel slower than a demo where the agent “just does everything.” Ship optimizes for **repeatable** throughput, not **theatrical** throughput.

If you need more speed, the right levers are usually:

- narrower tickets,  
- clearer guards,  
- more human review capacity — *not* more overlapping bots.


## Trust & boundaries {#trust-and-boundaries}

### The question nobody asks early enough

*Where does our code go during a run, who can see it, and what gets logged?*

If you cannot answer that in one sentence per vendor, you are not ready to wire money — you are ready to run a pilot with synthetic data.

---

### Data flows (conceptual)

- **CI** holds secrets for tracker and agent APIs. It sees repo content at checkout time and passes environment variables into steps.  
- **Agent runtime** may need the **same** tracker credential in **two** places (workflow + provider cloud env) — that is a **policy** conversation, not just a checkbox. If GitHub has the key but the agent does not, you get “green CI” and **silent** failure to update tickets.  
- **Optional scanners** (dependencies, containers) feed JSON into audit roles. Treat those reports as **untrusted input** until validated — same as issue descriptions.

No passwords in this chapter — see [Tools → Cursor Cloud Agent](../tools/index.md#cursor-cloud-agent) for placement detail and [ElMundi → Operator setup](../examples/elmundi/index.md#operator-setup) for a full secret map.

---

### What to ask vendors (before you standardize)

Use this as a checklist in procurement calls — boring questions save careers:

- **Data residency & subprocessors** — where does code go during a run; who is subprocessors under your DPA?  
- **Retention** — how long are logs, prompts, and artifacts kept; can you shorten it?  
- **Export** — can you export usage to reconcile against CI timestamps and ticket IDs?  
- **Isolation** — per-repo, per-branch, per-PR boundaries — what breaks if two jobs run concurrently?  
- **Offboarding** — how do you revoke access cleanly when a project ends?

If a vendor cannot answer, assume the worst and **narrow** scope until they can.

---

### Threats (plain language)

#### Credential leakage

**Symptom:** tokens in logs, pasted into tickets, shared across environments.  
**Fix:** rotate, least privilege, **separate** prod vs dev identities, never reuse “the big key” because it is convenient.

#### Prompt injection

Issue titles and descriptions are **untrusted**. Prompts must assume an attacker — or a well-meaning PM — will paste instructions that contradict your policy.  
**Mitigation:** hard fences in **pick** (what gets selected at all) and **tool allow-lists** in the agent runtime, not clever wording alone.

#### Duplicate PRs / branch fights

**Symptom:** two PRs for the same ticket, or agents overwriting each other.  
**Fix:** enforce **one** naming contract; close extras without merge; keep **one role per window** on delivery.

Example of duplicate handling: [ElMundi → Pre-release & E2E](../examples/elmundi/index.md#pre-release-e2e).

#### Audit spam

**Symptom:** dozens of low-value tickets from scanners.  
**Fix:** dedupe rules, evidence-only creation, separate projects — see [Running the loop](#running-the-loop) and [Examples → Daily audits](../examples/elmundi/index.md#daily-audits).

---

### Trust but verify (lightweight ops habits)

- Spot-check **one** full run weekly: ticket timeline ↔ workflow run ↔ PR list.  
- Alert on **sudden pick rate drops** — often a label or project guard changed.  
- Review **prompt diffs** like code diffs — because they *are* code diffs.


## Rolling it out {#rolling-it-out}

Big-bang automation fails for the same reason big-bang rewrites fail: **nobody** remembers which assumption broke first. Ship is designed to roll out in **layers** — each layer observable before you add the next.

---

### Phase 0 — Alignment (before you touch cron)

**Outcomes:** leadership agrees on three non-negotiables:

1. We are **not** automating Backlog picks.  
2. We **are** bounding throughput (one delivery role per time slot).  
3. Prompts and skills that drive headless runs live in **git** and go through **review**.

**Anti-patterns**

- “Let’s turn everything on Friday evening.”  
- “We’ll add guardrails after we see value.” (You will not.)

**Exit criteria:** you can explain the [control loop](#what-you-are-actually-buying) on one whiteboard photo.

---

### Phase 1 — Pilot (delivery lane only)

**Scope:** one tracker project, one team, **scheduled delivery** roles only — no audits yet if you can avoid them.

**Success looks like**

- Predictable **Todo → In Progress** transitions when automation runs.  
- Ticket timelines show **which** workflow run touched the ticket.  
- No “surprise” picks — every automated pick explainable from board fields.

**Failure looks like**

- Duplicate PRs.  
- Tickets moved without comments.  
- Humans cannot tell if the last change was a bot or a teammate.

**Exit criteria:** two weeks of boring Mondays — same classes of tickets, same guardrails, no emergency retro about automation.

Operator patterns: [ElMundi → Operator setup](../examples/elmundi/index.md#operator-setup).

---

### Phase 2 — Audits (parallel universe)

**Scope:** separate projects for tech / QA / security findings; evidence-only ticket creation.

**Success looks like**

- Audit noise stays **out** of the release stand-up board.  
- Findings reference **artifacts** (logs, JSON, failing checks).  
- Teams triage audit tickets like normal work — because they are actionable.

**Failure looks like**

- Audit bot opens vague tickets (“consider improving architecture”).  
- Delivery throughput collapses because audit and dev fight for the same WIP.

Example wiring: [ElMundi → Daily audits](../examples/elmundi/index.md#daily-audits).

---

### Phase 3 — Hardening (release reality)

**Scope:** hosted E2E tied to release habits; tune cadence vs provider rate limits; tighten duplicate PR handling.

**Success looks like**

- Regressions caught against **hosted** URLs before promote.  
- Promote steps are **manual or policy-gated** — not “agent decided.”  
- On-call knows [When things break](#when-things-break) by heart.

**Failure looks like**

- Flaky E2E ignored until red means nothing.  
- Automation runs faster than humans can review.

---

### Governance (lightweight, but named)

Someone **owns** prompt changes (usually platform + EM). Someone **owns** secrets rotation. Security **owns** scanner policy and what “evidence” means.

Full RACI templates rot in Confluence. **Ship** only requires **named owners** and a decision path when two owners disagree.


## When things break {#when-things-break}

Symptom → look → fix. **Example-specific** commands, hostnames, and exact env var names sit in [Examples → ElMundi](../examples/elmundi/index.md).

Start with the table, then read the **patterns** below — they help when your symptom is “something feels off” rather than a clean error message.

---

### Triage table

| Symptom | Where to look | Typical fix |
|---------|---------------|-------------|
| Pick fails: missing API key | CI secrets for tracker | Add token; rerun workflow; confirm secret available to the *job* that runs pick |
| Agent never starts | Agent provider dashboard + CI secret | Allow repo/org linkage; verify API key secret name matches workflow |
| Ticket stuck after “green” run | Wrong workflow state / team mapping / guards | Compare ticket fields to pick rules; run local `cli start` for that issue (see example SDLC doc) |
| Duplicate PRs for one ticket | Branch naming contract drift | Keep `fix/TICKET-auto` (or your scheme); close extras; find who diverged |
| Agent updates ticket in CI but not “in the cloud” | Keys only in GitHub, not agent env | Mirror tracker key to agent provider env (see Cursor doc) |
| Scanner job skipped | Missing scanner token | Expected in dev; add token for full signal; document “skip is OK here” |
| Queues unclear | Board vs snapshot script | Run snapshot utility from example repo; fix labels |
| Prompt change “did nothing” | Wrong branch / not deployed / cached image | Confirm merge to default branch; confirm schedule checks out that ref |
| Rate limits / throttling | Too many concurrent jobs or tight cron | Widen grid; reduce overlap; ask vendor for quotas |

**Deep setup:** [ElMundi → Operator setup](../examples/elmundi/index.md#operator-setup) · **Terms:** [Vocabulary](#vocabulary).

---

### Pattern A — “Green but nothing happened”

**Often correct behaviour.** Pick found no qualifying ticket.

**Verify**

1. Is the ticket in the **right project** and **state**?  
2. Do **labels** match what pick requires?  
3. Was this the **right** workflow run for the role you expected (intake vs developer)?

If you expected work and got silence, the bug is **guards**, not the agent.

---

### Pattern B — “Red in CI, unclear stack trace”

**Start from the failing step name**, not the bottom of the log.

- **Checkout / install** failures → infra or lockfile.  
- **Pick** failures → tracker auth or query assumptions.  
- **Launch** failures → agent API or misnamed secret.  
- **Test** failures after agent PR → product bug or flaky test — still **auditable** via PR + ticket.

---

### Pattern C — “It worked yesterday”

Suspect **external drift** first:

- Tracker workflow renamed states.  
- Label policy changed.  
- Project ID rotated when someone “cleaned up” Linear.  
- Token expired or scopes reduced.

Ship is only stable if **tracker fields** stay stable or changes are **versioned** like any other API migration.

---

### When to escalate vs fix in place

**Fix in place** when a single secret, label, or prompt line resolves the issue and you can **point** to the commit.

**Escalate** when the same class of failure repeats across tickets — that is a **design** problem (guards too loose, prompts too vague, schedule too aggressive).


## Vocabulary {#vocabulary}

Short definitions for words we reuse everywhere. If two engineers mean different things by **pick**, Ship will not feel safe.

---

### Delivery lane (scheduled)

The **main** SDLC automation path: ordered roles, **one per time slot**, picks only from **Todo** in a designated tracker project — **not** straight from **Backlog**.

**Why the name matters:** when someone says “the lane,” they mean *this* throughput-bounded path — not audits, not self-heal, not ad-hoc chats.

---

### Audit loop

A **separate** schedule writing to **tech debt / security / findings** projects with **evidence-only** rules: a ticket should point at a log, report, or failing check.

**Not the same as** “the security person filed a bug.” It is a **machine-assisted** loop with the same audit standards you would demand of a human filing on behalf of CI.

---

### Pick

Deterministic script or function choosing **at most one** issue per run using team, column/state, project, labels, and explicit ordering.

**Pick is not “AI selection.”** If the model chooses the ticket, you have removed the fence. Pick should be boring enough to **unit test**.

---

### Launch script

Thin client that assembles **versioned prompts** (from git), attaches issue metadata, and calls the **agent HTTP API**.

**Why separate from pick:** selection policy and execution policy change at different rates. Keeping them apart avoids “helpful” coupling that breaks debugging.

---

### CLI (tooling)

Compiled or scripted commands next to pick/launch (`start`, `get`, `init`, …) — **implementation-specific** helpers for operators.

**Typical uses:** reproduce a failed run locally, dry-run pick output, bootstrap a branch for a ticket.

---

### Guards

Labels, project membership, team, and state predicates that must be **true** before automation is allowed to select or touch a ticket.

**Culture note:** guards are not bureaucracy; they are **APIs** between humans and machines.

---

### E2E (end-to-end)

Browser tests against a **hosted** environment (often Playwright) — **real URL**, real auth cookies or test users, real CDN edge behaviour.

**Contrast:** local smoke against `localhost` catches different failures. Ship cares about **both**, but release gates usually want hosted signal.

---

### UTC grid

Evenly spaced scheduler ticks for delivery roles. **Example** deployments define exact minutes; the **framework** only requires that roles **do not** overlap in a way that causes duplicate work.

---

### Versioned prompts

Markdown (and optional skills) committed to the repo, reviewed in PRs, executed by headless agents on schedule.

**Opposite:** the “final prompt” living only in a SaaS text box — fast to type, impossible to diff.

---

### Where ElMundi names things

For **exact** Linear project names, workflow filenames, and cron tables, use **[Examples → ElMundi](../examples/elmundi/index.md)** — not this page.
