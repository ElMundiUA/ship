---
artifact_kind: pattern
id: scan-performance-budget
name: Performance budget scanner
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 4945141e430c44ae7bfa3fbe68c7065d44e009e4f52d353a28ecfdddd369fb07
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, performance, lighthouse, web-vitals]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Lighthouse and Core Web Vitals sweep per route. Enforces LCP / CLS / INP budgets and regresses on PRs that push them over.
spec:
  install_target: prompts/scan/performance-budget.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: event
    event: pull_request
    pattern: "paths:**/*.tsx,**/*.jsx,**/*.ts,**/*.js,**/*.css,**/*.scss"
    idempotency_key: "{{pr}}"
  inputs:
    - name: route_manifest_path
      type: text
      default: .ship/perf-routes.json
      hint: "Path to the JSON array of routes to benchmark."
    - name: budget_profile
      type: enum
      values: [strict, default, loose]
      default: default
      hint: "strict = INP<=150ms, LCP<=2000ms, CLS<=0.05; default = INP<=200ms, LCP<=2500ms, CLS<=0.1; loose = INP<=300ms, LCP<=3500ms, CLS<=0.2"
  enabled_on_install:
    default: false
    presets:
      web-app: true
      api-backend: true
      mobile-app-deep: true
      monorepo: true
---

# Performance budget scanner

**Trigger:** PR event on render-path files.

**Goal:** catch a PR that regresses LCP / CLS / INP beyond the
`{{budget_profile}}` budget before it lands.

---

## Prompt

You are the Performance Budget Scanner agent.

**Global rules:**
- Never approve the PR. Post findings only.
- Median of 3 runs per route — a single cold-start spike is not a
  regression.
- Evidence per finding: route, metric, base value, PR value, delta,
  budget.

**Route manifest:** `{{route_manifest_path}}` (JSON array of route
paths or URLs). **Budget profile:** `{{budget_profile}}`.

**Steps:**
1. Load the route manifest. If absent, fall back to `['/']` and
   note the fallback in the comment.
2. Run Lighthouse mobile / desktop (or unlighthouse / Web Vitals
   CLI equivalent) against the PR preview and the base ref.
3. Take the median of 3 runs per route per environment.
4. Compute the PR-vs-base delta per metric. Flag a regression when
   the PR value exceeds the budget *and* worsens by > 5 % vs base.
5. Post a single PR comment titled **Performance report** with a
   route × metric table. Regressions get a visible block;
   improvements get a collapsed block.
6. Request changes when at least one regression is present.

**Idempotency:** one comment per PR (`perf-report` anchor).

**Output:** one PR comment + optional `changes-requested` review.
