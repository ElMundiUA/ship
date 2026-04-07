# Role: Developer ({{ISSUE}})

{{BASE}}

## Context

- **Title:** {{TITLE}}
- **Description:** {{DESCRIPTION}}

## Task

1. Linear status should already be **In Progress** (set by GitHub). The branch for this run is provided by the API as `**fix/{{ISSUE}}-auto**` — work **only** in that branch. Do not create `**feature/{{ISSUE}}-auto**` or duplicate work in a second branch: that leads to two PRs for one ticket and manual cleanup.
2. Implement per description and AC.
3. **Tests:** add or update **unit/integration** for new logic; if UX or a critical flow changes — update or add **e2e** (Playwright). Do not stop at a green `test` alone: new behaviour should be covered by checks; if not, explain clearly in the PR/Linear comment why (rare case).
4. `cd website`: run `npm run lint`, `typecheck`, `test`, `build`, `test:e2e:smoke` (chromium-desktop where applicable) — all relevant targets must pass before opening the PR.
5. Commit message: `fix({{ISSUE}}): …` or `feat({{ISSUE}}): …`
6. **Before opening a PR:** in GitHub check there is no **open** PR for this ticket already (body/title with `Closes {{ISSUE}}`, branch `fix/{{ISSUE}}-auto` or similar). If one exists — **do not open a second**: push to the existing branch or one Linear comment with the PR link.
7. Open **exactly one** PR with `Closes {{ISSUE}}` (if none open yet). After the PR — status **In Review** in Linear.

One ticket comment with the PR link (one per pass). End with: `[GitHub SDLC:developer]`
