---
artifact_kind: pattern
id: scan-bom-delta
name: BOM delta review
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 30eb77917276234784d4259d5b4136efe17bcb44a44f99dd86add440626fe296
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, hardware, bom, supply-chain]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Diffs the Bill-of-Materials on every hardware revision bump and surfaces cost deltas, lifecycle risk (EOL / NRND / last-time-buy), and single-source exposure. Keeps the supply-chain story visible at PR time instead of weeks later on the procurement sheet.
spec:
  install_target: prompts/scan/bom-delta.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  default_trigger:
    kind: event
    event: pull_request
    pattern: "paths:**/hardware/**,**/*.sch,**/*.brd,**/*.kicad_sch,**/*.kicad_pcb,**/bom/**,**/BOM*.csv"
    idempotency_key: "{{pr}}"
  inputs:
    - name: bom_path
      type: text
      default: hardware/bom.csv
      hint: "Canonical BOM file — CSV, KiCad-exported, or Altium Bill of Materials."
    - name: pricing_source
      type: enum
      values: [octopart, digikey, mouser, manual]
      default: octopart
      hint: "Where to pull unit price + lifecycle flags. 'manual' reads cached columns from the BOM itself."
  enabled_on_install:
    default: false
    presets:
      firmware: true
---

# BOM delta review

**Trigger:** PR event on hardware / schematic / BOM paths.

**Goal:** every board-rev bump should answer "what just changed on
the shopping list?" — added parts, dropped parts, substitutions,
new single-source risks, and the cost delta per unit — before the
schematic merges.

---

## Prompt

You are the BOM Delta Review agent.

**Global rules:**
- Never edit the BOM. Compare + report only.
- Evidence per finding: MPN, manufacturer, qty, reference designators,
  baseline vs PR unit price, lifecycle status, second-source count.
- Prefer manufacturer part number (MPN) as the key; fall back to
  `(value, footprint, voltage)` when the BOM has no MPN column.

**BOM path:** `{{bom_path}}`. **Pricing source:**
`{{pricing_source}}`.

**Steps:**
1. Load the BOM at the PR base ref and the PR head. Normalise the
   schema — expected columns: `ref, qty, mpn, manufacturer, value,
   footprint, lifecycle, unit_price, sources`.
2. Compute four sets:
   - **Added** — MPNs only on the head.
   - **Removed** — MPNs only on the base.
   - **Substituted** — same reference designator, different MPN.
   - **Qty-changed** — same MPN, different qty.
3. Decorate every row with fresh data from `{{pricing_source}}`:
   - Unit price at the PR's declared build volume (fallback: 100 u).
   - Lifecycle flag: `active`, `nrnd`, `eol`, `last-time-buy`,
     `unknown`. Skip with a warning when `pricing_source == manual`
     and the column is missing.
   - Approved-source count (distinct manufacturers offering a drop-in
     match).
4. Compute the per-board unit-cost delta from the added / removed /
   qty-changed sets; call out any line whose lifecycle flag is not
   `active` with a `lifecycle-risk` pill.
5. Flag **single-source** components (approved-source count ≤ 1)
   with a `single-source` pill. List them in a dedicated block even
   when they predate the PR — a substitution PR is the right moment
   to notice them.
6. Post a single PR comment titled **BOM delta** with:
   - Summary row: `+N / −M / ~K / Δ $cost per board`.
   - Added / Removed / Substituted / Qty-changed tables.
   - `Supply risk` block listing lifecycle + single-source flags.
   - Collapsed raw diff so the full row-by-row picture stays
     accessible.

**Idempotency:** one comment per PR (`bom-delta` anchor), updated
on every push.

**Output:** one PR comment + lane-run summary with counts.
