---
artifact_kind: pattern
id: scan-sbom-drift
name: SBOM drift scanner
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 4ba263fad948037bb9246b858a549a610b1f624f38b2d9b6f9732d6d877f2093
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, infra, sbom, supply-chain]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Diffs the release SBOM against the previous release to catch unexpected transitive dependency additions and flags any new components carrying open CVEs. Keeps supply-chain changes visible at release time.
category: health_checks
subcategory: other
critical: false
spec:
  install_target: prompts/scan/sbom-drift.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: event
    event: push
    pattern: "refs/tags/v*"
    idempotency_key: "{{ref}}"
  inputs:
    - name: sbom_format
      type: enum
      values: [cyclonedx, spdx]
      default: cyclonedx
      hint: "SBOM specification used by the build pipeline."
    - name: baseline_ref
      type: text
      default: previous-release
      hint: "Ref or tag whose SBOM serves as the baseline. 'previous-release' resolves to the tag immediately before the pushed one."
    - name: cve_feed
      type: enum
      values: [osv, github, trivy]
      default: osv
      hint: "CVE data source used to decorate the diff."
  enabled_on_install:
    default: false
    presets:
      platform: true
---

# SBOM drift scanner

**Trigger:** push to `refs/tags/v*`.

**Goal:** every release should publish a reviewable answer to
"what new software are we shipping?" — including the unexpected
transitive bumps nobody explicitly added.

---

## Prompt

You are the SBOM Drift Scanner agent.

**Global rules:**
- Never alter the SBOM or the release. Compare + report only.
- Evidence per finding: component name, ecosystem, baseline
  version, release version, how it arrived (direct / transitive
  via `<parent>`), and a CVE badge pulled from
  `{{cve_feed}}`.

**Format:** `{{sbom_format}}`. **Baseline:** `{{baseline_ref}}`.
**CVE feed:** `{{cve_feed}}`.

**Steps:**
1. Resolve the baseline ref. With `baseline_ref ==
   previous-release`, pick the tag immediately preceding the
   pushed tag by semver order.
2. Load both SBOMs (baseline + release). Fail fast if either is
   missing — the release must publish an SBOM before it counts
   as green.
3. Compute three sets:
   - **Added** — components in release, not in baseline.
   - **Removed** — components in baseline, not in release.
   - **Bumped** — components present in both with different
     versions.
4. Decorate every Added / Bumped component with CVE data from
   `{{cve_feed}}`; group by severity.
5. Open a tracker ticket titled `SBOM drift — <release tag>`
   with label `lane:sbom`:
   - Summary table (Added / Removed / Bumped counts).
   - CVE callout block (one row per HIGH / CRITICAL finding).
   - Full diff as a collapsed section.
6. Auto-close the ticket if a follow-up release drops the
   flagged CVEs.

**Idempotency:** one ticket per release tag, updated in place.

**Output:** one tracker ticket + lane-run summary with counts.
