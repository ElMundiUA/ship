# E11 — Documentation alignment to code

**Priority:** P3
**Effort:** L (~7–10 days, mostly editorial)
**Owner:** TBD

## Goal

Every page in `documentation/` and every page in `landing/src/app/{docs,getting-started,book,patterns}/` describes what the code actually does as of the close of the closed beta. References to deleted pages (`/cli`, `/tools`, `/collections`), pre-RFC-0010 vocabulary in operator-facing prose, and stale CLI commands are gone.

## Why

The maintainer said outright: "documentation and landing may be outdated, code is source of truth". Five documentation phases shipped in April. RFC-0010 renamed nouns (Plays / Automations / Runs / Inbox). Several catalog pages were deleted. The CLI changed shape. Beta users will find the gaps the moment they search the docs for something they saw in the console.

This epic comes **last** in the schedule because docs lag code by design — fix the code, then describe what landed.

## Tasks

### T01 — Build the source-of-truth matrix **[S]**

- Spreadsheet (or just a table here): for each documentation page, list:
  - what it claims (TL;DR per section)
  - what code module / route / config field it references
  - whether the reference still exists, was renamed, was deleted
- Pages to scan: every file under `documentation/*.md` plus `documentation/protocol/*.md`, `documentation/internal/*.md` (latter only for accuracy of internal references), `documentation/authoring/*.md`.

**Acceptance:** matrix exists; about 20 docs pages and ~80 references.

### T02 — Scan for dead links **[S]**

- `rg "/cli\b|/tools\b|/collections\b" documentation/ landing/`
- `rg "shipctl workflow|workflow artifact|kind=workflow" .` — RFC-0007 Phase 6 retired this vocabulary.
- `rg "lanes:" documentation/` — fine in protocol/RFC; remove from operator-facing prose.

**Acceptance:** zero dead links to removed routes; zero pre-RFC-0010 vocabulary in operator-facing pages.

### T03 — Rewrite `documentation/configuration.md` **[M]**

- This page is the canonical schema reference for `.ship/config.yml`. It must match `cli/lib/config/schema.mjs` exactly.
- Generate a side-by-side diff: what schema field appears here vs what's in code.
- Bring the doc in line with code. Add a footnote "When this page disagrees with the schema, the schema wins" — already there; verify accuracy of each row.

**Acceptance:** every field in the doc maps to a field in the schema; every field in the schema appears in the doc OR is internal-only and explicitly listed as such.

### T04 — Rewrite `documentation/automations.md` **[M]**

- Operator-facing page about the routine/lane concept post-RFC-0010.
- Reflect: routines are scheduled, dispatched via `ship-trigger-schedule`, run inside `run-agent.workflow.yml` in the customer repo.
- Remove any references to "workflows" as an artifact kind.

**Acceptance:** technical reviewer can wire a new routine using only this page + `configuration.md`.

### T05 — Rewrite `documentation/operating.md` **[M]**

- Daily review section already aligned. Verify Inbox shapes match RFC-0010 + E06 (5 shapes: clarification, improvement, approval, failure, exception).
- Remove any references to `/clarifications` or `/improvements` as separate routes (they're under `/inbox` now).

**Acceptance:** operator-facing prose uses the unified Inbox vocabulary throughout.

### T06 — Rewrite `documentation/concepts.md` **[S]**

- Map of product nouns → backend nouns is the most useful section. Keep it.
- Verify each row.
- Add: "Three-project closed beta" entry only if it survives publicly (probably not — keep `documentation/internal/`).

**Acceptance:** every product term has a working link to where the technical noun lives.

### T07 — Rewrite `documentation/discovery.md` **[S]**

- The discovery contract for first-time agents.
- Match: `shipctl doctor` output, the wizard's questions, the `.ship/config.yml` shape.

**Acceptance:** an agent following this page can get from cold start to a valid config.

### T08 — Rewrite `documentation/troubleshooting.md` **[M]**

- Symptom-first. Each entry: symptom, what to check, fix.
- Add entries for new common failures from E03 (golden path bugs).
- Add: "I'm not receiving emails" (from E09).
- Add: "Knowledge import seems stuck" (from E01).
- Add: "Tracker reconnect" (from E07).

**Acceptance:** every closed-beta tester's "huh, this broke" gets an entry here.

### T09 — RFC promotion / closure pass **[S]**

- RFC-0008 / 0009 / 0010 had multi-phase rollouts. After E03–E07 close out, mark phases done in each RFC.
- Promote any "Draft" RFC that is now fully shipped to "Accepted".
- Move historical RFC content that is no longer normative (because it's superseded by current code) to a "Historical notes" section at the bottom.

**Acceptance:** RFC index page (`documentation/protocol/index.md`) has accurate status for each.

### T10 — Landing alignment **[M]**

- File: `landing/src/app/getting-started/page.tsx`, `landing/src/app/docs/page.tsx`, `landing/src/components/*.tsx`.
- Hero copy: still "front door is product owner" — confirm aligned with the blog post.
- "How it works" 5 steps: match exactly the wizard steps in `console/src/app/onboarding/page.tsx` (Bootstrap → Pick Plays → Assign as Automations → Watch Runs → Triage Inbox).
- Footer: remove tools / workflows / collections links if present (already done in Phase 9, verify).

**Acceptance:** every step mentioned on landing maps to a step in the actual onboarding wizard.

### T11 — Manual changelog **[S]**

- File: `documentation/CHANGELOG.md`.
- Add "Phase 10 — Closed-beta alignment (2026-05)" entry summarizing every doc page touched and every assertion verified.

**Acceptance:** changelog reflects this epic's scope.

### T12 — Internal-vs-public docs split **[S]**

- `documentation/internal/` is not served on the website (verify in `landing/src/lib/documentation-fs.ts`).
- Move any genuinely internal stuff out of public-served pages into `internal/`.

**Acceptance:** website renders no `internal/` content publicly.

## Definition of done

- [ ] All public docs pages reflect current code.
- [ ] No dead links to removed routes.
- [ ] RFC statuses accurate.
- [ ] Landing copy matches console wizard.
- [ ] CHANGELOG entry merged.

## Risks / unknowns

- Doc rewrites can creep into product changes ("while I'm fixing the page I'll also fix the code"). Resist; spawn separate tasks instead.
- The `book.md` is intentionally the philosophical layer — do not "align" it to code; only fix factual errors.
- RFCs are a contract with future contributors; promotion to Accepted is a small commitment.

## Out of scope

- Translating docs.
- Adding video walkthroughs (covered in E12).
- Full information architecture redesign.
- Adding new documentation pages — only fix existing.
