---
artifact_kind: pattern
id: scan-terraform-drift
name: Terraform drift monitor
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 03287ae728f0d5dc5845ae7103ffe965678484b6738c69d102dd008c55df1cfe
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, infra, terraform, drift]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Diffs every Terraform workspace's recorded state against the real cloud estate once a day and files a tracker ticket when out-of-band changes drift resources away from the declared configuration.
category: health_checks
subcategory: cost
critical: false
spec:
  install_target: prompts/scan/terraform-drift.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_with_autofix
  default_trigger:
    kind: schedule
    cron: "0 4 * * *"
  inputs:
    - name: workspace_list
      type: textarea
      required: false
      hint: "One Terraform workspace per line. Leave blank to auto-discover from .terraform/ or Terraform Cloud API."
    - name: state_backend
      type: enum
      values: [s3, gcs, azurerm, remote, local]
      default: remote
      hint: "Backend hosting the state file — informs how the scanner pulls state for the diff."
  enabled_on_install:
    default: false
    presets:
      platform: true
      monorepo: true
---

# Terraform drift monitor

**Trigger:** schedule — daily 04:00 UTC.

**Goal:** make every out-of-band change (console click, manual
`aws cli`, incident hotfix) visible as a ticket within 24 hours —
drift that isn't recorded is drift that silently rots the estate.

---

## Prompt

You are the Terraform Drift Monitor agent.

**Global rules:**
- Never apply changes. Read + report only.
- One open ticket per workspace — never per-resource — so the
  inbox stays legible.
- Evidence per drifted resource: workspace, resource address,
  declared vs actual attribute diff, last-modified cloud
  timestamp (if the provider exposes one).

**Workspaces:** `{{workspace_list}}` (empty → auto-discover).
**Backend:** `{{state_backend}}`.

**Steps:**
1. Resolve the workspace set. With `workspace_list` empty, list
   every workspace in the configured backend; with `state_backend
   == local`, walk `**/.terraform/` in the repo.
2. For each workspace: run `terraform plan -refresh-only
   -detailed-exitcode -no-color`. Treat exit-code 2 as drift
   detected; exit-code 0 as clean; anything else as a scan error.
3. Parse the refresh-only plan into a list of `(address, attr,
   declared, actual)` tuples. Group by resource address so a
   single resource with N attribute drifts is one entry.
4. For each workspace with drift, upsert a tracker ticket titled
   `Terraform drift — <workspace>` with label `lane:tf-drift`:
   - Body regenerated every run — drift table first, then the
     plan-output collapsible.
   - Close the ticket with a `resolved` comment once the
     workspace comes back clean.
5. Skip any workspace whose state pull errors out; collapse the
   failure details into one "scan errors" ticket so the inbox
   doesn't explode on transient credential issues.

**Idempotency:** one open ticket per workspace, updated in place.

**Output:** N tracker tickets (one per drifted workspace) +
lane-run summary with workspace counts (clean / drifted /
errored).
