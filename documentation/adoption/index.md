# Adopt Ship to your project

Ship is delivered primarily as **instructions + prompts + reference setups**.

You bring your stack. The agent adapts Ship interfaces to it.

## Quick links

| Resource | Purpose |
|----------|---------|
| [Getting started](../getting-started/index.md) | Ready-to-go entry with one default copy-paste block for agents. |
| [Agent setup contract](agent-setup-contract.md) | Mandatory interactive discovery behavior for agents. |
| [Agent playbook](agent-playbook.md) | Canonical generic onboarding playbook. |
| [Delivery, quality & release](delivery-quality-and-release-process.md) | End-to-end operating model, QA split, release gates, daily digest/retro. |
| [Agent launch matrix](agent-launch-matrix.md) | How to run the same playbook from Cursor/Codex/Claude/Copilot. |
| [ElMundi rollout](elmundi.md) | Reference-org specific delta. |
| [Tracker adaptation contract](../tools/ship-agent-trackers.md) | Vendor-neutral tracker mapping requirements. |
| [The book](../framework/index.md) | Long-form rationale and trade-offs. |
| [Migration guide (v0.6)](migration-instruction-first-v0.6.md) | Breaking-change summary and staged transition checklist. |

## Optional helper launcher

From your product repository root:

```bash
curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash
```

Treat launcher as convenience. Canonical contract is the playbook + setup contract.

## Philosophy

- Agent first: onboarding is interactive, not hardcoded.
- Interface first: methodology over vendor lock-in.
- Evidence first: every automation step should remain auditable.
