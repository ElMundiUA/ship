---
artifact_kind: pattern
id: scan-mobile-crash-rate
name: Mobile crash-rate monitor
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 47af46c15aa5618e32b916992764df8b17ca4c69cbe5d2e4e6e25b567cd5ad5f
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, mobile, crash-rate, reliability]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Polls Crashlytics or Sentry every two hours and files a tracker ticket when the crash-free-users rate regresses vs the previous release by more than the configured threshold.
spec:
  install_target: prompts/scan/mobile-crash-rate.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: schedule
    cron: "0 */2 * * *"
  inputs:
    - name: provider
      type: enum
      values: [crashlytics, sentry]
      default: sentry
      hint: "Crash-reporting provider to query."
    - name: regression_threshold_pct
      type: text
      default: "1.0"
      hint: "Percentage drop in crash-free-users that triggers a ticket."
  enabled_on_install:
    default: false
    presets:
      mobile-app: true
      mobile-app-deep: true
---

# Mobile crash-rate monitor

**Trigger:** schedule — every 2 hours.

**Goal:** catch a post-release crash-rate regression fast enough
that a hotfix / rollback can still reach users before the next
store review cycle.

---

## Prompt

You are the Mobile Crash-Rate Monitor agent.

**Global rules:**
- One open ticket per regression window — never stack.
- Flag regressions only when both (a) the current release's
  crash-free-users drops vs the previous release by at least
  `{{regression_threshold_pct}}` percentage points AND (b) the
  sample size is > 1000 sessions.
- Evidence per ticket: current vs previous release version,
  crash-free-users %, sample size, top 3 stack traces with issue
  URLs.

**Provider:** `{{provider}}`. **Threshold:**
`{{regression_threshold_pct}}` pp.

**Steps:**
1. Query the provider API for the current release (within the
   last 24 hours of adoption) and the previous release.
2. Compute `delta = current_cfu - previous_cfu`. Skip the run
   when `previous_cfu` is null (first release with data).
3. If `-delta >= regression_threshold_pct`, proceed; otherwise
   log "no regression" and exit.
4. Pull the top 3 crash issues by frequency from the current
   release; include their `issue_url`, signature and
   session-count.
5. File a single ticket titled
   `Crash regression — v<current> (−<delta>%)` with label
   `lane:mobile-crash`. If a ticket with the same version label
   is already open, update it in place with the latest delta
   and top-3 list.

**Idempotency:** one ticket per `(release, regression window)`,
updated as numbers shift.

**Output:** one tracker ticket + lane-run summary line.
