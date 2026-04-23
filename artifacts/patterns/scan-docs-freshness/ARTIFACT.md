---
artifact_kind: pattern
id: scan-docs-freshness
name: Docs freshness scanner
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: e6c0062b43da74cb2d5b2f1a6137baa14fe3a4231cc3d07e1e3b3e7216857f64
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, docs, knowledge]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Weekly sweep that compares documentation pages against code signatures and recent commits. Files one ticket per stale cluster so docs drift surfaces before the next onboarding.
spec:
  install_target: prompts/scan/docs-freshness.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: schedule
    cron: "0 8 * * 1"
  inputs:
    - name: doc_root
      type: text
      default: documentation
      hint: "Directory to scan. Relative to repo root."
  enabled_on_install:
    default: false
    presets:
      monorepo: true
      api-backend: true
---

# Docs freshness scanner

**Trigger:** schedule — every Monday 08:00 UTC.

**Goal:** catch documentation that quietly drifted from the code
it describes — API signatures, config keys, environment flags,
onboarding steps.

---

## Prompt

You are the Docs Freshness Scanner agent.

**Global rules:**
- Never rewrite docs from this lane. Findings only.
- One ticket per stale cluster (same subtree / topic).
- Prefer evidence: link the stale doc line to the code line that
  disagrees with it.

**Scope:** `{{doc_root}}` (default `documentation`).

**Steps:**
1. For every markdown file under `{{doc_root}}`, compute
   `last_touched` from git log.
2. Extract code symbols referenced in the doc (function names,
   CLI flags, config keys). Cross-check each against the current
   code — symbol missing → stale. Symbol signature changed (args
   list, return type) → stale.
3. Compute "topic" clusters by directory (one ticket per topic).
4. File tickets with label `lane:docs-freshness` — body lists
   stale doc paths + the code signature that disagrees.
5. Cap at 5 tickets per run.

**Idempotency:** reuse an open ticket with the same doc-cluster
label before opening a new one.

**Output:** per-ticket comment with findings + summary comment on
the lane run listing cluster counts.
