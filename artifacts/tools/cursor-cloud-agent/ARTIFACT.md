---
artifact_kind: tool
id: cursor-cloud-agent
name: Cursor Cloud Agent
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: 5264c81959144c4acecb3c539ec530d80a77469bd6daaaf05103171607f19bd9
deprecated: false
replaced_by: null
yanked: false
group: agents
tags: [branch, pr, runtime]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Cloud runtime — PR contract, secrets mirroring, prompts versioned in git. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under agents needs to call into it.
spec:
  capability: agents
  install_target: documentation/tools/integrations/cursor-cloud-agent.md
---

# Cursor Cloud Agent (agent runtime)

**Role in Ship:** one supported **agent runtime** — executes prompts against a **clone** of the repository, opens PRs, and (when configured) calls tracker APIs.

## Contract (short)

- **Branch naming** and **PR body** must match your org’s automation rules (`Closes …`, preview markers, check lists).
- **Secrets** live in GitHub Actions for orchestration; **Cursor Cloud** may need a **separate** mirror for the same API keys your prompts use — document both sides in adoption notes.
- **Skills / rules** — keep portable markdown under `prompts/` and repo rules; avoid duplicating policy in a host-only UI without a git trail.

## Read next

- [Agent launch matrix](/docs/agent-matrix) — compare runtimes and launch paths.
- Org patterns under **Cloud agent** on [/patterns](/patterns) — role prompts (`prompts/cloud-agent/*.md`).
- Ukrainian deep-dive (same repo): [Cursor Cloud Agent secrets (UA) on GitHub](https://github.com/ElMundiUA/ship/blob/main/documentation/tools/cursor-cloud-agent.uk.md) — env mirroring details until an English split is published.
