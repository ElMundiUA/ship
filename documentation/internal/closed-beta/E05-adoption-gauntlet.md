# E05 — Three-project adoption gauntlet (ElMundi · Ship-on-Ship · .NET→Go)

**Priority:** P1
**Effort:** XL (~3 weeks across three tracks)
**Owner:** Maintainer (PO of all three)

## Goal

Three real projects are running on Ship under their own teams' delivery rules. Each project produces a blog post documenting the migration. **Closing this epic closes the closed beta.**

## Why

Exit criterion #1, #2, #3 of the closed beta. No product-market fit conversation is honest until three different stacks have been moved onto the workspace without manual hand-holding.

The three tracks intentionally span different dimensions:

| Track | Tracker | Agent | Scheduler | Stack | Use-case dimension |
|---|---|---|---|---|---|
| **5a — ElMundi** | Linear | Cursor | GH Actions | Next.js + Drizzle | Steady-state delivery |
| **5b — Ship-on-Ship** | TBD | Cursor / Claude Code | GH Actions | Python + TS monorepo | Dogfood + multi-language |
| **5c — .NET→Go migration** | TBD | TBD | TBD | .NET → Go | Migration project (not steady-state) |

Each track shakes out a different category of bug.

## Track 5a — ElMundi adoption

**Repo:** `../elmundi/` (sibling to this repo).
**Tracker:** Linear (already in use).
**Stack:** Next.js, Drizzle, Auth0, Bunny, Algolia.
**PO:** Maintainer.

### Tasks

- **T01** — Pre-flight: verify the existing `e2e/` adoption flow works against the deployed `app.ship.elmundi.com`.
- **T02** — Run WOW onboarding in the console: install GitHub App on `denyskuzin/elmundi`, activate `website/`, bind Linear, accept seed PR.
- **T03** — Verify the seed PR contains: `.ship/config.yml` with `tracker: linear`, `agents: [cursor]`, `language: ts`, the run-agent workflow, agent rule files (`.cursor/rules/*.mdc`).
- **T04** — Wait for first scheduled routine. Confirm in dashboard.
- **T05** — Trigger a clarification by labeling a Linear issue `ship:needs-clarification`. Verify Inbox.
- **T06** — Trigger an improvement: add a PR-self-review pattern to the daily routines.
- **T07** — Run the project for a week. Track every issue in `documentation/internal/dogfood-elmundi.md`.
- **T08** — Write blog post: `landing/content/blog/we-moved-elmundi-onto-ship.md`. Honest, with screenshots.

### Definition of done — Track 5a

- [ ] ElMundi has been on Ship for 7 consecutive days without backend hand-fixes.
- [ ] At least 3 Inbox items resolved through the UI.
- [ ] Blog post merged.
- [ ] All bugs found are either fixed or filed as P1/P2 issues.

## Track 5b — Ship-on-Ship

**Repo:** this one (`/Users/denyskuzin/Projects/ship/`).
**Tracker:** decide between Linear and GH Issues. (Recommendation: GH Issues, because then the tracker, agent, scheduler and code all live on github.com — easier evidence threads.)
**Stack:** Python (backend), TypeScript (console + landing + cli), e2e Playwright.
**PO:** Maintainer.

### Tasks

- **T09** — Decide tracker. Document choice in `.ship/config.yml` of this repo.
- **T10** — Run `shipctl init --interactive` in this repo (or use the console). Resolve any conflict between the existing `.ship/config.yml` and what the wizard wants to write.
- **T11** — Activate the repo in the maintainer's primary workspace.
- **T12** — Configure agents: Cursor + Claude Code rules. Verify they coexist.
- **T13** — Pick three routines to run: `daily_security_review`, `flow-pr-self-review`, `flow-dependency-update`. Each should produce evidence within a week.
- **T14** — Self-heal flow: introduce a deliberate test failure, verify Inbox gets a `failure` item and the routine self-heals.
- **T15** — Blog post: `landing/content/blog/ship-on-ship.md`. Lessons learned from running a meta-product on itself.

### Definition of done — Track 5b

- [ ] Three routines visible in dashboard with successful runs.
- [ ] Self-heal demonstrated with screenshots.
- [ ] Blog post merged.
- [ ] No "agents are confused" reports — agent rule loading verified.

## Track 5c — .NET → Go migration project

**Repo:** TBD (maintainer will provide).
**Stack:** .NET (legacy) being migrated to Go (target).
**PO:** Maintainer.
**Use case:** **migration**, not steady delivery. Tests Ship's ability to track parallel codebases and progressive cutover.

### Tasks

- **T16** — Receive the source repo path / clone instructions from the maintainer.
- **T17** — Confirm artifact-rules coverage: `agent-rules-cursor` works for both .NET and Go? If not, identify what's missing in `artifacts/collections/`.
- **T18** — Run WOW onboarding. Linear or GH Issues binding. Pick agent.
- **T19** — Define the migration knowledge bucket: target architecture, in-flight modules, rollout policy.
- **T20** — Configure two parallel routines: one over `.NET` paths (deprecation notes, parity checks), one over `Go` (review, lint, test). Use `policies` to enforce: "do not change .NET production behaviour without an Inbox approval".
- **T21** — Run for two weeks. Track migration progress as the dashboard's primary KPI (custom metric or PR count tagged `migration:`).
- **T22** — Blog post: `landing/content/blog/migrating-net-to-go-on-ship.md`. The use-case argument: Ship is not just for steady-state delivery; it shapes one-off migrations too.

### Definition of done — Track 5c

- [ ] Migration project runs both code lanes simultaneously without policy violations.
- [ ] Inbox produces useful clarifications during the migration (not noise).
- [ ] Blog post merged.
- [ ] At least one feature actually migrated end-to-end with evidence.

## Definition of done — entire E05

- [ ] All three tracks closed.
- [ ] Three blog posts merged in `landing/content/blog/`.
- [ ] Closed-beta exit declared in `documentation/internal/closed-beta-plan.md`.
- [ ] First public-beta cohort invited.

## Risks / unknowns

- Track 5a depends on E01 (knowledge live) and E02 (mock cleanup) being done first or it will look broken.
- Track 5c depends on the third-party repo being available and migration scope agreed.
- Cursor agent on .NET may not have rules; check `artifacts/collections/agent-rules-cursor/` for language-specific gaps.
- Three blog posts is a non-trivial writing load — schedule them as part of the work, not "after".

## Out of scope

- Onboarding any project that is not one of these three.
- Adding new artifact patterns just because a track wants them; defer to a follow-up epic.
- Customer support tooling (use email + maintainer's direct chat).
- Public testimonials beyond the blog posts themselves.

## Bug tracking convention

Every bug found in any track goes into `documentation/internal/dogfood-<track>.md` with:

- Short description
- Affected file/route
- Severity (P0/P1/P2)
- Hotfixed (link) OR opened issue (link) OR deferred to (epic)

These files become the input list for E03's bug list and any subsequent technical-debt epic.
