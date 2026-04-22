---
artifact_kind: collection
id: preset-regulated
name: Preset — Regulated industry
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: aee73fa53623b7071286a98fba64d5fe912d99e1594df57da6b4fd118784a329
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, compliance, regulated, soc2, hipaa, pci]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for fintech / healthtech / regulated SaaS that operates under SOC2, HIPAA, PCI, or ISO27001. Wires PII leakage sweeps, IAM policy-diff reviews, audit-log integrity checks, consent-coverage drift, and a compliance-artifact refresh flow on top of the cross-cutting quality pack.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>]
  optional_tools: [tool/privacy/onetrust, tool/privacy/transcend, tool/siem/splunk, tool/siem/elastic, tool/secrets/vault, tool/iac/terraform, tool/log/cloudwatch]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: regulated
  install_target: documentation/collections/preset-regulated.md
  wizard_questionnaire:
    - key: framework
      label: "Primary compliance framework"
      options: [soc2, hipaa, pci, iso27001]
      required: true
      hint: "Stored as a workspace config key and consumed by flow-compliance-artifact as the default."
---

# Preset — Regulated industry

## Product shape

A product subject to external audit and regulator scrutiny:
SOC2 service organisation, HIPAA-covered entity or business
associate, PCI merchant or service provider, ISO27001-certified
operation. Bounded context is **"the audit"** — which controls
apply, which evidence they demand, who reviewed what, and when.

The preset assumes:

- A data map at `privacy/data-map.yml` enumerating data
  categories × processing purposes × legal basis.
- An audit log persisted with integrity metadata (hash chain,
  sequence, checkpoints).
- A policies / procedures folder under source control
  (`policies/`).
- A tracker with labels the audit workflow can rely on.

## Wizard questionnaire

Installing `regulated` asks one question before seeding lanes:

- **Primary compliance framework:** `soc2 / hipaa / pci /
  iso27001`.

The answer is stored as a workspace-scoped config key and
consumed by `flow-compliance-artifact` as the default
`framework` input so operators don't re-pick it on every
evidence refresh.

## Lanes & patterns enabled out of the box

- `pr_review` (`flow-pr-self-review`)
- `daily_standup` (`flow-daily-retro`)
- `code_map`
- `scan-pii-leakage` — daily PII sweep across logs /
  fixtures.
- `scan-iam-policy-diff` — IAM / role / scope delta on every
  auth-touching PR (shared with `platform`).
- `scan-audit-log-integrity` — hourly hash-chain verification
  (shared with `platform`).
- `scan-consent-drift` — weekly consent × telemetry coverage
  check.
- `flow-compliance-artifact` — one-shot evidence-bundle
  refresh per audit window.
- `scan-test-coverage`, `scan-dead-code`, `scan-license-deps`,
  `scan-env-var-catalog` — cross-cutting quality pack (Wave 1)
  pre-enabled.

## SDLC columns the preset expects

Standard Ship flow plus:

- `Audit-evidence` — holds tickets opened by
  `flow-compliance-artifact` until the compliance lead signs
  off.
- `Regulator-response` — parallel state for any ticket the
  external auditor is actively reviewing.

## Label contract (preset-specific)

- `lane:pii-leak` · `lane:audit-integrity` ·
  `lane:consent` · `lane:compliance`
- `compliance:access-review` · `compliance:change-record` ·
  `compliance:incident`
- `privilege-escalation` · `legal-basis-mismatch` ·
  `integrity-break`

Plus the base Ship labels.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user.
- Audit-log read credentials (DB role / S3 bucket / CloudWatch
  log group scoped to the audit trail).
- PII-redaction library keys (if the regex pack defers to a
  managed service like Transcend or OneTrust).
- Compliance-framework framework-of-record credentials (e.g.
  Drata / Vanta API token) when the evidence bundle syncs out.

## Recommended addendums

- `addendum-pharma` — when HIPAA PHI is in scope.
- `addendum-fin` — when PCI or fin-services rules apply.
- Compose `preset-platform` on top when the SRE rotation for
  the regulated service lives in the same repo.

## Evidence types

- Daily PII-leak tickets per leaking source (redacted
  evidence only).
- Per-PR IAM diff with privilege-escalation pills.
- Hourly integrity check; a SEV-1 ticket the moment the chain
  breaks.
- Weekly consent-coverage ticket, closed when the three sets
  empty out.
- Per-audit-window compliance-bundle PR with control matrix
  and reviewer trail.
