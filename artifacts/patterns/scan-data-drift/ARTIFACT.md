---
artifact_kind: pattern
id: scan-data-drift
name: Data drift monitor
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 5ee89791b5608ef4940c5b42df98b8e4c7e106ca8dfc2751c7165f9b197e2b6a
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, ml, data-drift, psi, ks]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Monitors feature distributions against a rolling reference window and files a tracker ticket when PSI / KS tests breach thresholds. Catches training-serving skew and upstream pipeline changes.
spec:
  install_target: prompts/scan/data-drift.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: schedule
    cron: "0 6 * * *"
  inputs:
    - name: feature_source
      type: text
      required: true
      hint: "Feature source identifier — feature-store view, S3 path, or SQL view."
    - name: reference_window_days
      type: text
      default: "14"
      hint: "Days of history to anchor the reference distribution."
    - name: psi_threshold
      type: text
      default: "0.2"
      hint: "PSI above this → feature flagged as drifted."
  enabled_on_install:
    default: false
    presets:
      ml-project: true
---

# Data drift monitor

**Trigger:** schedule — daily 06:00 UTC.

**Goal:** catch upstream schema / distribution changes before
they silently degrade the model's behaviour.

---

## Prompt

You are the Data Drift Monitor agent.

**Global rules:**
- One consolidated ticket per run; never open per-feature
  tickets.
- Evidence per drifted feature: feature name, PSI, KS statistic,
  sample size, reference window, top bins with the largest
  delta.
- Skip the run if `feature_source` is unreachable — flag the
  outage instead of burying it.

**Feature source:** `{{feature_source}}`. **Reference window:**
`{{reference_window_days}}` days. **PSI threshold:**
`{{psi_threshold}}`.

**Steps:**
1. Pull the last `{{reference_window_days}}` days of feature
   rows from `{{feature_source}}` as the reference
   distribution. Pull the most recent 24-hour window as the
   current distribution.
2. For every numeric feature: compute PSI (10-bin equal-width)
   and KS statistic.
3. For every categorical feature: compute chi-square and
   top-category share delta.
4. Flag features where PSI > `{{psi_threshold}}` OR the
   chi-square p-value < 0.01 with > 1 % share shift.
5. Upsert a single tracker ticket titled `Data drift — <feature
   source>`:
   - Body regenerated fresh each run.
   - Flagged features as a visible block with stats.
   - Unflagged features as a collapsed "checked OK" block.
6. Close the ticket (with a `resolved` comment) on a
   subsequent run with zero flagged features.

**Idempotency:** one open ticket per `feature_source`, updated
in place.

**Output:** one tracker ticket + lane-run summary with counts.
