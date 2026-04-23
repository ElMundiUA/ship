---
artifact_kind: pattern
id: scan-os-support-matrix
name: OS support matrix drift
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 98a396934f2e88f0526322b645a9d569f3f41908660612f45e7405e5de5315a0
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, desktop, os, ci, support-matrix]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Cross-checks the declared supported-OS list against the CI matrix every week — flags drift when a version you promise to support stops being tested, or when CI quietly tests on something you never claimed to support.
spec:
  install_target: prompts/scan/os-support-matrix.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: schedule
    cron: "0 6 * * 1"
  inputs:
    - name: support_matrix_path
      type: text
      default: SUPPORTED_OS.md
      hint: "Markdown or YAML file enumerating supported OS versions. One row per (platform, version, arch)."
    - name: ci_matrix_path
      type: text
      default: .github/workflows/
      hint: "Directory or file containing the CI matrix definitions to parse for actual test coverage."
  enabled_on_install:
    default: false
    presets:
      desktop-app: true
---

# OS support matrix drift

**Trigger:** schedule — weekly Monday 06:00 UTC.

**Goal:** the support matrix on the download page should never
out-live the CI matrix. Drift in either direction ships bugs to
customers whose OS nobody's running the test suite on.

---

## Prompt

You are the OS Support Matrix agent.

**Global rules:**
- Never edit `{{support_matrix_path}}` or workflow YAMLs.
  Compare + ticket only.
- Evidence per drift row: platform, version, arch, source
  (declared / tested), first-seen timestamp, last-seen timestamp
  in the other side.
- Treat EOL'd vendor versions as a separate class — flag them
  even when declared and tested match, because shipping to an
  EOL OS is its own policy question.

**Declared list:** `{{support_matrix_path}}`. **CI matrix:**
`{{ci_matrix_path}}`.

**Steps:**
1. Parse `{{support_matrix_path}}` into a set of tuples
   `(platform, version, arch)`. Accept either a markdown table
   with columns `Platform | Version | Arch` or a YAML list
   under `supported:`.
2. Walk `{{ci_matrix_path}}` for matrix declarations:
   - GitHub Actions: `strategy.matrix.os` / `jobs.*.runs-on`,
     expand matrix includes/excludes.
   - GitLab CI, CircleCI, Azure Pipelines: walk their
     respective matrix syntaxes if present.
   Normalise runner labels (`windows-latest` →
   `windows-2022`, `macos-14` → `macOS 14 Sonoma`) via a
   lookup table kept at `.ship/desktop/runner-aliases.yml`.
3. Compute three sets:
   - **Declared-not-tested** — rows in the support matrix with
     no CI coverage. Highest-severity finding.
   - **Tested-not-declared** — rows CI covers that the support
     list doesn't advertise. Usually harmless but worth
     surfacing so marketing / docs can update.
   - **EOL risk** — any row whose vendor-EOL date has passed
     or is within the next 90 days (lookup via
     `endoflife.date` or a local cache).
4. Upsert a tracker ticket titled `OS support matrix drift —
   <ISO week>` with label `lane:os-matrix`:
   - Three sections, one per set; each row linked to the
     source (support matrix line / workflow path).
   - Close automatically when all three sets empty out on a
     subsequent run.
5. Skip any parsing error quietly but record it in a single
   `scan errors` sub-section — transient YAML breakage from an
   unrelated PR shouldn't produce dozens of false drift tickets.

**Idempotency:** one open ticket per ISO week; reruns within the
same week update the existing ticket.

**Output:** one tracker ticket + lane-run summary with per-set
counts.
