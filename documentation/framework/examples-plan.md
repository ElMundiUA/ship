---
id: framework/examples-plan
title: Book → Examples map (ElMundi)
status: internal
audience: authors, editors
---

# Book → Examples map (ElMundi)

This page is a **working document** for the authors of **[The book — why Ship](./index.md)**. It is not part of the manual itself. It pins every new narrative passage in the book to a **real commit** in the reference org (`ElMundiUA/elmundi`) — the monorepo where Ship was first stress-tested. The rule: no abstract claim gets inserted into the book without a concrete, checkable scar next to it in this file. If a chapter here lacks a commit, the chapter is not ready.

All SHAs below are **short git SHAs** in the `elmundi` repository; dates are from `git show --format='%ad' --date=short`.

---

## New chapters and their anchoring commits

### Prologue — The night the agent shipped nothing
- **Commit:** `ed73ef2ecf` — `fix(ci): SDLC scheduled slot must not skip on odd UTC hour` (2026-04-15)
- **Why this one:** GitHub Actions delivered a cron run at `05:08 UTC` instead of the scheduled `05:00`. An even-hour guard (`HOUR % 2 == 0`) was false, so the whole SDLC slot was skipped — role cleared, no ticket picked, `main` stayed green, backlog unchanged, humans opened Linear in the morning and saw *nothing*. It is a true story about the difference between "the automation ran" and "the automation did the thing you hired it for."
- **Second commit to cite (same bug, larger blast radius):** `cb9d1be2c2` — `fix(ci): make SDLC schedule robust to stale github.event.schedule` (2026-04-14). Four crons collapsed to one; role resolved from UTC on the runner instead of `github.event.schedule`, because GitHub was delivering a **stale** cron string after edits and skipping every role.

### Preface — Who this is for, and how to read it
- No single commit; cites the repo `ElMundiUA/elmundi` as the living lab.
- Mentions that every "Field note" in the book below can be traced to a real scar in that tree.

### Part — Artifacts as work objects

#### Ch. 18.A — The artifact is the unit of change
- **Commit:** `c22589cb9d` — `fix(linear-agent): prevent duplicate PRs on developer Cloud Agent runs` (2026-03-31)
- **Diff shape (2 files, 7 insertions / 4 deletions):**
  - `tools/linear-agent/cloud-prompts/developer.md` — stopped telling the model to `autoCreatePr`, added a rule to "check for an open PR on the fixed branch `fix/ISSUE-auto` before opening a new one."
  - `tools/linear-agent/scripts/cloud-agent-launch.mjs` — read the new flag.
- **Why this one:** Production stopped producing duplicate PRs on the same ticket not because a service was rewritten, but because a **prompt** was. The artifact — a versioned markdown file living in git — is where the organization's intent was re-stated. The code change is downstream glue.

#### Ch. 18.B — Reading a pattern like a map
- **No single commit is needed** for the tutorial body, but we reference the shape of `tools/linear-agent/cloud-prompts/*.md` in the elmundi tree — every prompt opens with front-matter (role, model, scope), a "What you may touch" section, and a "How you stop" section. This is the pattern anatomy readers should learn to parse.

#### Ch. 18.C — Authoring a new pattern
- **Commit:** `89d726f5f9` — `Add daily Linear audit roles (tech, QA, Snyk security)` (2026-04-07)
- **Diff shape (11 files, new workflow + three new prompts):**
  - New workflow: `.github/workflows/linear-agent-daily-audits.yml` (+178 lines).
  - Three new prompt artifacts born in one commit: `tech-architect.md` (+27), `qa-architect.md` (+28), `security-officer.md` (+37).
  - Helpers: `ensure-audit-linear-projects.mjs` (+118), `audit-linear-projects.mjs` (+54).
- **Why this one:** It is the cleanest single-commit birth of a **new pattern family** (daily audit roles) in the reference org. It shows the minimum you must write when you invent a new kind of agent work: one prompt per role, a wiring script, a workflow, and a Linear project for evidence.

#### Ch. 18.D — Versions, channels, yank
- **Commit:** `977c4afb01` — `chore(website): bump next to 16.2.3 (Snyk SNYK-JS-NEXT-15954202)` (2026-04-10)
- **Why this one:** This is a real-world example of a **yanked-by-advisory** dependency. The previous version is not deleted; it is marked unsafe. A new version is published. Downstream consumers (here, the website) must move. In `shipctl` terms, this is exactly the shape of `deprecated: true` and `replaced_by: <id>@<new-version>` in a manifest, driven by an external authority (Snyk advisory ID). The chapter uses this to explain why artifacts carry a version, a `deprecated` flag, and a `replaced_by` pointer — because dependencies change under your feet whether or not your book acknowledges the fact.

### Part — The improvement loop

#### Ch. 25.A — Where a fix becomes feedback
- **Commits (a single-day recurrence):** ELM-64 — `fix(ELM-64): keep zero-target standup runs successful on Slack membership errors` — **fifteen commits in one day**, 2026-03-16, same title: `eaeb06e792`, `71ef351007`, `b115a072bc`, `ec038e9a9a`, `01188caa97`, `47253d06b4`, `031b27a036`, `698d6830d5`, `d3d73f4da0`, `0034fe64c3`. Plus variants: `873b67975d` (`handle Slack bot-not-in-this-channel wording`), `82293f5a38` (`bot-is-not-in-this-channel`), `de3b2679d0` (`harden zero-target Slack audit and delivery recovery`).
- **Why this one:** Slack was changing the exact wording of the "bot is not in this channel" error. Each commit matched one new spelling. After the third, the right move was to send a **feedback event** upstream — "this pattern keeps breaking on a moving vendor string; please stop matching English" — instead of shipping variant #15. This is the ancestor story of `shipctl feedback`: the moment where an on-call patch should turn into a structured note that raises the artifact, not the symptom.

#### Ch. 25.B — Telemetry that serves operators, not vendors
- **Commits (same day, self-heal introduction + aftershocks, 2026-03-16):**
  - `cf695f2269` — `feat(ci): add automated failed-check recovery workflow` (+312 lines)
  - `a59e002302`, `b1c8be647b`, `39487fe054`, `648d6b7f8f`, `6d92bd0ea4`, `70e6c79247`, `b144f0686e`, `70cabbd924`, `75fe3025e2`, `0e697b6691`, `9a93a82147`, `1e79b62481`, `280c26fe5d`, `fdfaffb636`, `3b83c93e84`, `29194d3a9b`, `4528bee083` — **fifteen-plus** "install runtime deps" fixes for the same self-heal/recovery CLIs over the next few hours.
- **Why this one:** Self-heal was introduced, and self-heal immediately required self-heal. Without telemetry, the shape of "same failure class, fifteen variants, one afternoon" is visible only retroactively in a commit log. With it (`doctor.result`, `artifact.use`, `artifact.sync`), the shape becomes a **signal an operator can watch** while the fires are still small. Telemetry that serves operators is exactly this: not vanity counters, but early evidence that the mop is becoming the kitchen's problem.

#### Ch. 25.C — Agent regression triage
- **Commit:** `c22589cb9d` (again) — `fix(linear-agent): prevent duplicate PRs on developer Cloud Agent runs`
- **Why this one:** Before the fix, a single prompt change caused the *same* regression across unrelated tickets (duplicate PRs everywhere). That is the agent-regression fingerprint: one artifact update; many tickets suddenly lying the same way. The chapter uses this to define the triage move — roll the artifact back to the last known good version, open a single ticket against the artifact, and do not chase the tickets that merely caught the smell.

### Ship Manifesto
- No single commit; a distillation page. Deliberately short. Its job is to sit at the end of the book and tell the reader, out loud, what the whole manual believes. Style: one long paragraph per claim, no bullets.

### Part — Operator's dashboard and economics (Day 3)

#### Chapter 20.A — What to measure in the morning
- **Anchors (composite):**
  - `54ef3e1836` — cost sketch in `CURSOR-AUTOMATIONS-MIGRATION.md` (2026-04-07), supplies the envelope-first framing for the *cost envelope* metric.
  - Seven `stabilize E2E` commits across the ElMundi history (`bf32821eb0`, `e7de66c139`, `c512809e9c`, `8ef085e114`, `8bd036f9ad`, `d75c94021d`, and one more in the 2026-04 cluster) — empirical backing for treating E2E flake ratio as a first-class wall signal, not a triage afterthought.
  - Ship's own artifact instrumentation (`shipctl doctor`, `shipctl feedback`, `.ship/telemetry/outbox`) — supplies the shape of artifact-drift and feedback-emission metrics without needing a new external product.
- **Why these:** The chapter argues five boring metrics beat forty cheerful ones. The reference org has paid, on the record, for the flake metric (seven stabilisation commits), the pick-rate metric (the 2026-04 SDLC scheduling work), and the envelope metric (`54ef3e1836`). No new vocabulary is invented; the chapter just names what the reference org was already paying for.

#### Chapter 28.A — Regulated-vertical overlays
- **Anchor (in-repo artifact, not ElMundi):** `documentation/collections/addendum-pharma.md` — Ship's own `addendum-pharma` collection artifact, declaring `regulatory_frameworks: [HIPAA, GDPR, 21-CFR-Part-11, EU-AI-Act]` and `min_shipctl: "0.3.0"`.
- **Why this one:** ElMundi is a non-regulated product, so a pharma example that survives archaeology has to come from Ship itself. The artifact is already versioned, already has the "tighten never relax" rule from RFC-0004, and already enumerates the boring controls — PHI redaction, six-year audit retention, 21 CFR Part 11 approval comments, separation of duties. The chapter cites the artifact itself instead of a commit SHA, which is deliberate: the book's answer to *"how should a regulated team diverge from the base preset?"* is *"open this file"*, not *"read a war story."*

#### Chapter 31.A — The price of a bounded loop
- **Commit:** `54ef3e1836` — `docs(linear-agent): Automations vs GitHub-orchestrated comparison, cost sketch, model flexibility` (2026-04-07).
- **Why this one:** This is the single commit where the reference org wrote down the arithmetic of running an agentic SDLC grid: 4 roles × ~12 even hours/day = ~48 scheduler ticks, ~25% pick rate ⇒ ~12 real agent runs/day, illustrative $1–5/run × 250 workdays ≈ $3k–15k/year from SDLC alone, and the shape of the argument for *bounded schedules forecast / open triggers surprise*. The chapter paraphrases that math and points readers at the commit for the full table. The commit also names the "single choke point for model tier" pattern (`cloud-agent-launch.mjs`), which is the same pattern the chapter asks operators to preserve.

---

### Part — Running the loop: extensions (Day 2)

#### Ch. 22.A — Evals for prompt artifacts
- **Commit:** `d2801fbaee` — `fix(linear-agent): verify preview serves real app, not Bunny placeholder` (2026-03-15).
- **Diff shape:** +611 lines across `tools/linear-agent/src/cli.ts` (+587) and `github-client.ts` (+24). release-check now fetches the preview URL, inspects the HTML body, and rejects `waiting_for_deploy` when the page contains the Bunny placeholder string `We're deploying your app!`.
- **Supporting cohort:** seventeen `pr-preview` commits in March 2026 (e.g. `4fa24cab1c`, `bf14aba23e`, `9bdb243086`, `3752339043`, `c4e8c39c02`) — all attempts to use a *proxy* signal (probe shape, port, cold-start delay) in place of an actual content check.
- **Why this one:** It is the cleanest single story in the repo for the difference between a probe (the proxy) and an eval (reading the output you care about). The chapter uses it to argue that prompt artifacts deserve the same discipline: small, per-artifact fixtures and assertions, living beside the artifact in the same PR.

#### Ch. 33.A — How humans review what agents wrote
- **Commit:** `ceec221fec` — `fix(ci): resolve Neon DB/role from parent branch for PR preview` (2026-04-11).
- **Diff shape:** +57 lines in `bin/ops/neon_pr_preview_branch.sh`, +2 in `website/lib/db/postgres/README.md`. Auto-discovers `database` and `role` from the Neon branch when env vars are unset, because Neon maps role names to database names for PR branches instead of using the default `neondb` owner.
- **Why this one:** The diff is small, the logic is correct in isolation, and the *wiring assumption it fixes* would not have been visible to a reviewer who read only the code. This is the textbook case for "audit the boundary, not the body" in agent PR review.

#### Ch. 35.A — Onboarding a human into an agent team
- **Commits:** `3de1f5b832` (`docs(linear-agent): framework-first site, ElMundi examples, tools & prompts`, 2026-04-07) and `f872ff8bdf` (`docs(linear-agent): tech-writer + stakeholder structure`, 2026-04-07).
- **Why these:** They are the same-day rewrite of the onboarding surface around *verbs* (pick, launch, PR, merge, audit) and *artifacts* (prompts directory as first read), not tools. They are also the moment the reference org split documentation authorship between operator- and stakeholder-facing voices — a small but load-bearing organisational move.

---

## Existing chapters that will cite ElMundi only in a "Field note" (no body change)

We do not rewrite bodies of existing chapters. Where a real scar maps one-to-one, the editor may add a single indented **field note** below the chapter.

Status legend: **[installed]** — field note landed in `index.md`. **[pending]** — still a candidate, editor has not accepted yet.

- **[installed] Ch. 4 — Machines need fences.** Cites `64aee78b4f` (SDLC: Todo-only picks scoped to ElMundi pre-release, 2026-04-07) and `de2e4f71f8` (fail pick in CI if LINEAR_API_KEY missing, 2026-03-24): two different shapes of the same fence — scope-by-project, and fail-closed-on-missing-secret.
- **[installed] Ch. 15 — Why boring is load-bearing.** Cites `64aee78b4f` (SDLC: Todo-only picks scoped to ElMundi pre-release, 2026-04-07) in the Day 3 field note, pointing at the four `pick-*.mjs` scripts in `tools/linear-agent/scripts/` as the grep-able shape of a deterministic selector — the pick as a shell-testable unit, not a sentence.
- **[installed] Ch. 19 — Why "always on" is a trap.** Cites `cb9d1be2c2` (stale `github.event.schedule`, 2026-04-14) and `ed73ef2ecf` (even-hour guard, 2026-04-15). Two CI realities that make a 24×7 grid a lie if you don't build tolerance into the edges.
- **[installed] Ch. 23 — Audits are still not delivery.** Cites `89d726f5f9` (daily audit roles, 2026-04-07) and `977c4afb01` (next 16.2.3 / Snyk advisory `SNYK-JS-NEXT-15954202`, 2026-04-10).
- **[preserved] Ch. 24 — First boredom, then self-heal.** The original field note (author wrote it in the 2026-03 edition) stays untouched. The `cf695f2269` + 15 install-dep cascade already carries this chapter's thesis in chapter 25.B; no second note is needed here.
- **[installed] Ch. 39 — It worked yesterday.** Cites `b96ab6e66d` (intake skips rows past intake label, 2026-04-14). Label-schema drift in production: new labels `stage:ba`, `ready:ba`, `ready:developer` appeared upstream and the old intake query starved the pipeline downstream.
- **[installed] Ch. 40 — Fix in place or escalate.** Cites the ELM-64 recurrence on 2026-03-16 (fifteen same-title fixes in one day — full SHA list in the note). The right move, by the third commit, was to stop patching Slack's error string and redesign the contract.

---

## Style rules for inserting ElMundi examples

1. **Cite the commit as a short SHA plus one line of context**, not a URL, not a screenshot. Example: `ed73ef2ecf (fix: SDLC scheduled slot must not skip on odd UTC hour, 2026-04-15)`.
2. **Never quote more than three lines of diff** inside a chapter body. If more detail is needed, point to this page.
3. **Do not name people** by hand. Co-authored-by lines stay in git; chapters name *roles* (on-call, reviewer, operator), not humans.
4. **No emojis, no bullet lists in chapter bodies.** Field notes stay the one allowed indent.
5. **Never present an example as a "best practice."** Present it as a scar. The book is an operator's memoir, not a playbook of successes.
6. **Every example must survive commit log archaeology.** If the commit is amended, squashed, or rewritten, update this page *first*, then the book.

---

## Open gaps (candidates for later waves, not day-one writing)

- No end-to-end ElMundi example for **Ch. 27 — Where the bits flow** (vendor data-flow diagram). We can seed one from the `.github/workflows/` tree but it needs a security review before citation.
- No public commit for **procurement-to-go-live** narrative. We may need to fabricate a composite with explicit "synthetic" labelling rather than cite a single SHA.
- ~~No example yet for **evaluation of a prompt artifact** (the "evals" chapter proposed but not yet written).~~ — **Closed in Day 2** by Ch. 22.A, anchored to `d2801fbaee`.
- Still missing an ElMundi-grade anchor for the **"insecurity of ownership"** theme — the specific fear an operator feels when they own a system and do not understand a part of it. Candidate commits exist (the on-call handovers around `cf695f2269` self-heal, the rollback conversations around `977c4afb01` Snyk) but none are clean enough to anchor a chapter on their own. Keep looking before writing.

---

_Last updated: 2026-04-17 (Day 3 — metrics, regulated verticals, economics)._
