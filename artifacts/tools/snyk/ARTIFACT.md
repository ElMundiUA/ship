---
artifact_kind: tool
id: snyk
name: Snyk
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: 13d7c7fde0feedbee8b09349dfb5bfe2d61e7e8a00f59a96395f5aba1dbdc9f8
deprecated: false
replaced_by: null
yanked: false
group: platform
tags: [dependencies, sca]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Dependency and security signal — triage with agents and release gates. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under platform needs to call into it.
spec:
  capability: platform
  install_target: documentation/tools/integrations/snyk.md
---

# Snyk (security / dependency signal)

**Role in Ship:** optional **security/dependency signal** — findings feed evidence for risk decisions and PR hygiene; it is not a replacement for human review or QA gates.

## How to use it with agents

- Treat Snyk (or similar) output as **inputs** to tickets and PR comments, not silent auto-merge triggers unless policy explicitly allows.
- Keep **severity policy** in repo docs so agents do not invent CVSS drama where your org prefers calm triage.

## Read next

- [Delivery, quality & release](/docs/adoption/delivery-quality-and-release-process) — where security evidence meets release gates.
- [Tools — capability map](/docs/tools) — “security/dependency signal” row.
