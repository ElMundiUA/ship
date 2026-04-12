# Cursor Cloud Agent (agent runtime)

**Role in Ship:** one supported **agent runtime** — executes prompts against a **clone** of the repository, opens PRs, and (when configured) calls tracker APIs.

## Contract (short)

- **Branch naming** and **PR body** must match your org’s automation rules (`Closes …`, preview markers, check lists).
- **Secrets** live in GitHub Actions for orchestration; **Cursor Cloud** may need a **separate** mirror for the same API keys your prompts use — document both sides in adoption notes.
- **Skills / rules** — keep portable markdown under `prompts/` and repo rules; avoid duplicating policy in a host-only UI without a git trail.

## Read next

- [Agent launch matrix](/docs/adoption/agent-launch-matrix) — compare runtimes and launch paths.
- Org patterns under **Cloud agent** on [/patterns](/patterns) — role prompts (`prompts/cloud-agent/*.md`).
- Ukrainian deep-dive (same repo): [Cursor Cloud Agent secrets (UA) on GitHub](https://github.com/ElMundiUA/ship/blob/main/documentation/tools/cursor-cloud-agent.uk.md) — env mirroring details until an English split is published.
