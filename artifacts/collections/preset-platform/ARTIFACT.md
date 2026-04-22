---
artifact_kind: collection
id: preset-platform
name: Preset — Platform / SRE
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 6bfc851d3eefb1c4fa0464426f7fd62a53369e9a3ca24e5aa62d6f07e3708404
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, infra, platform, sre]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for infra / SRE / platform teams. Wires Terraform drift monitoring, Kubernetes policy gating, SLO burn paging, SBOM drift at release, cost-delta review, blast-radius comments on every PR, monthly runbook-freshness sweeps, and an on-call handoff flow.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>, tool/iac/terraform]
  optional_tools: [tool/k8s/kyverno, tool/k8s/opa, tool/cost/infracost, tool/obs/prometheus, tool/obs/datadog, tool/sbom/syft, tool/cve/osv]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: platform
  install_target: documentation/collections/preset-platform.md
---

# Preset — Platform / SRE

## Product shape

A repo (or monorepo section) whose primary output is the cloud
estate plus the guardrails around it — Terraform / Pulumi /
Crossplane, Kubernetes manifests, Helm charts, SLO definitions,
runbooks, on-call config. Bounded context is **"the fleet"** —
what's deployed, to which regions, with which permissions, and
at what cost.

The preset assumes:

- A declarative IaC layer (Terraform + state backend) that
  owns every production resource.
- A metrics backend (Prometheus / Datadog / CloudWatch) with
  SLO queries the `slo/` registry can reference.
- A tracker the SRE rotation actually reads (Linear / Jira /
  GitHub Issues) — the platform lanes open tickets there.

## Lanes & patterns enabled out of the box

- `pr_review` (`flow-pr-self-review`)
- `daily_standup` (`flow-daily-retro`)
- `tech_debt`
- `code_map`
- `scan-terraform-drift` — daily out-of-band drift check.
- `scan-k8s-policy` — per-PR Kyverno / OPA / Conftest gate.
- `scan-cost-delta` — per-PR Infracost estimate, warn / block
  thresholds.
- `scan-slo-health` — 15-minute SLO burn-rate monitor.
- `scan-sbom-drift` — release-tag SBOM diff with CVE callouts.
- `scan-iam-policy-diff` — IAM / role / scope delta on every
  auth-touching PR (shared with `regulated`).
- `scan-audit-log-integrity` — hourly audit-log chain check
  (shared with `regulated`).
- `flow-runbook-freshness` — monthly runbook rot sweep.
- `flow-blast-radius` — per-PR blast-radius comment.
- `flow-oncall-handoff` — one-shot handoff note drafting.
- `scan-test-coverage`, `scan-dead-code`, `scan-license-deps`,
  `scan-env-var-catalog` — cross-cutting quality pack (Wave 1)
  pre-enabled.

## SDLC columns the preset expects

Standard Ship flow plus:

- `Canary` — between `In review` and `Done`, holds PRs that
  have landed but are still observing in the canary slice.
- `Rollback ready` — parallel state pinned while a rollout
  bakes; removed once the change is fully accepted.

## Label contract (preset-specific)

- `lane:tf-drift` · `lane:k8s-policy` · `lane:slo-burn`
- `lane:sbom` · `lane:runbook` · `lane:oncall-handoff`
- `cost:warn` · `cost:block` · `blast-radius:high`
- `schema-change` · `infra-change` · `auth-change` ·
  `shared-lib-change`

Plus the base Ship labels.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user.
- Cloud credentials for each environment the IaC covers
  (scoped to read-only for drift scans).
- Metrics backend token (Prometheus remote-read, Datadog API
  key, CloudWatch role).
- Container registry read token (for SBOM pulls).
- Pager / Slack token, if the on-call handoff publishes to
  Slack.

## Recommended addendums

- `addendum-fin` — when the fleet serves regulated financial
  infrastructure.
- `addendum-pharma` — when the fleet runs clinical / patient-
  facing services subject to HIPAA.

Compose `preset-regulated` on top when SOC2 / HIPAA / PCI
artefact refreshes are a lane you want to run alongside the
platform lanes.

## Evidence types

- Per-workspace drift ticket, upserted daily.
- Per-service SLO burn ticket with dashboard link.
- Per-release SBOM diff with CVE callout block.
- Per-PR blast-radius + cost-delta comments.
- Monthly runbook-freshness ticket per rotten runbook.
- Per-shift handoff ticket with on-call ack trail.
