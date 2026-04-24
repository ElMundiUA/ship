---
artifact_kind: pattern
id: scan-localization-gap
name: Localization gap scanner
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 15b95d427a2f3fc926963d148b151529c75a0e4ab5a7bd3de1490778cab3bc5e
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, i18n, localization, mobile]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Detects strings missing a translation across configured locales. Files one consolidated tracker ticket per locale so i18n debt surfaces instead of compounding one PR at a time.
category: health_checks
subcategory: compliance
critical: false
spec:
  install_target: prompts/scan/localization-gap.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: schedule
    cron: "0 7 * * 1"
  inputs:
    - name: string_root
      type: text
      default: i18n/
      hint: "Root folder that contains one subfolder / file per locale."
    - name: locales
      type: textarea
      required: false
      hint: "Explicit locale list (one per line). Leave empty to infer from folder names."
  enabled_on_install:
    default: false
    presets:
      mobile-app-deep: true
      web-app: true
      monorepo: true
---

# Localization gap scanner

**Trigger:** schedule — weekly Monday 07:00 UTC.

**Goal:** every locale should carry the same string keys as the
source locale — missing keys ship as the English fallback, which
is fine once but rots the experience over time.

---

## Prompt

You are the Localization Gap Scanner agent.

**Global rules:**
- Never translate strings directly. Tickets only.
- Tickets are per-locale; one ticket per locale updated in place
  across runs.
- Evidence per ticket: locale code, missing key count, top-20
  missing keys with their source value.

**String root:** `{{string_root}}`. **Locales:** `{{locales}}`
(empty → auto-detect from subfolders).

**Steps:**
1. Pick the source locale (`en` / `en-US` / `source`, whichever
   is configured). Parse every string key it declares.
2. Enumerate target locales: either the explicit `{{locales}}`
   list or every sibling folder under `{{string_root}}`.
3. For each locale: compute `missing = source_keys - locale_keys`
   (keys that exist in source but not in the locale) and
   `orphan = locale_keys - source_keys` (stale keys whose source
   was removed).
4. Skip locales with `len(missing) == 0 and len(orphan) == 0`.
5. For each locale with findings, upsert a ticket titled
   `i18n gap — <locale>` with label `lane:i18n`:
   - Body: counts + top 20 missing keys (with source value) +
     top 20 orphans.
   - Update in place if the ticket already exists (the body is
     regenerated fresh every run so numbers stay accurate).
6. Close a ticket automatically when a subsequent run finds
   zero missing and zero orphan keys (add a `resolved` comment).

**Idempotency:** exactly one open ticket per locale at a time.

**Output:** upserted tickets + lane-run summary line with
per-locale counts.
