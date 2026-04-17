---
artifact_kind: collection
subkind: preset
preset_id: web-app
compatible_trackers: [linear, jira, github-issues]
compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
compatible_agents: [cursor, codex, claude, aider, copilot]
required_tools: [tool/tracker/<current>, tool/ci/<current>, tool/playwright, collection/agent-rules-<agent>]
optional_tools: [tool/preview/vercel, tool/preview/netlify, tool/flags/launchdarkly]
addendums: []   # preset itself declares no addendum; user opts in separately
min_shipctl: "0.3.0"
---

# Preset — Web application

## Product shape

Browser-first product: a single-page app (React/Vue/Svelte) or
server-rendered framework (Next.js, Remix, Nuxt) that users
consume in the browser. Bounded context is **"the browsing
session"** — URL state, auth cookies, feature flags, and the
preview URL every PR produces.

## SDLC columns the preset expects

- `Backlog → Todo → In Progress → In Review → Done`
- `Blocked` as a parallel state (any lane, any time).
- Optional `Preview Ready` checkpoint between `In Progress`
  and `In Review`: a PR cannot move to `In Review` until its
  preview URL is reachable and smoke-green.

## Label contract (preset-specific)

- `preview:ready` — preview URL up, healthcheck 200.
- `preview:broken` — preview build failed; blocks `In Review`.
- `flag:behind` — change is dark-launched behind a feature flag.
- `flag:exposed` — user-visible toggle flipped on.
- `a11y:needs-review` — accessibility lane owes a pass.
- `perf:budget-at-risk` — Lighthouse/Core-Web-Vitals slipped.
- Plus the base Ship labels (`type:*`, `lane:*`, `promote:*`).

## CI stages (pseudocode)

```
on: pull_request
jobs:
  install:        # cache deps
  lint-typecheck: # eslint + tsc --noEmit
  unit:           # vitest/jest
  build-preview:  # vercel/netlify/amplify preview deploy
  e2e:            # playwright against the preview URL
  a11y:           # axe-core against key routes (optional)
  lighthouse:     # perf budget (optional)
  doctor:         # shipctl doctor (artifact pins, labels)
```

A preview URL is the single shared artifact across every
downstream stage; once it exists, E2E, a11y, and Lighthouse
all point at it.

## Evidence types

- Preview URL in the PR body, re-posted by a bot comment on
  every push.
- Playwright HTML report, uploaded as a run artifact and
  linked in the PR.
- Lighthouse JSON summary (scores + budget deltas).
- Release notes auto-drafted from PR titles at promote time.

## Promote gates

`preview green → PR approved → main merge → staging deploy →
smoke in staging → production rollout (flag-gated)`.

Each gate writes a line into the ticket: staging URL, smoke
run id, rollout percentage, feature-flag owner.

## Required secrets (generic names)

- Tracker API key (resolved by the tracker adapter — Linear,
  Jira, or GitHub Issues).
- CI token for the bot user (GitHub App token or equivalent).
- Preview host token (Vercel / Netlify / AWS Amplify).
- Feature-flag SDK key (LaunchDarkly / Unleash / ConfigCat),
  if used.
- Sentry DSN for client error reporting (recommended).

## Recommended addendums

- `addendum-pharma` — if the web app handles PHI or consent
  flows (telehealth, patient portals).
- `addendum-fin` — if the web app takes payments or drives
  regulated financial actions.

Addendums layer on top of this preset; they never relax a
rule the base preset enforced.
