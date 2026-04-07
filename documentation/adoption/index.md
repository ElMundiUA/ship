# Adopt Ship to your project

Ship is designed to be **vendored or submodule’d** into a product repo, then wired with **GitHub Actions**, a **ticket system**, and optionally **Cursor Cloud Agent**. We intentionally avoid a one-size-fits-all NPM installer: stacks and monorepo layouts differ too much.

Instead, use:

1. **An agent playbook** — a single markdown file the coding agent follows step-by-step.  
2. **A launch matrix** — how to invoke that playbook from Cursor, Copilot, Codex, etc.

---

## Quick links

| Resource | Purpose |
|----------|---------|
| **[Agent playbook](agent-playbook.md)** | Full instructions (included from `prompts/onboarding/`). |
| **[Agent launch matrix](agent-launch-matrix.md)** | Cursor / Copilot / Codex / … — how to run the playbook. |
| **[ElMundi rollout](elmundi.md)** | Concrete path for **ElMundiUA/elmundi** + submodule. |
| **[Ship Agent & trackers](../tools/ship-agent-trackers.md)** | `TRACKER_PROVIDER`, env vars, GitHub Issues `ship-status:*`, etc. |
| **[Examples → ElMundi](../examples/elmundi/index.md)** | Receipts: cron, workflows, secrets (reference org). |

---

## Philosophy

- **Agent** = flexible installer for *your* tree and CI.  
- **Ship repo** = source of truth for prompts, CLI (`runtime/`), and manual.  
- **Secrets** = always created by humans in GitHub/Cursor; the playbook only *names* them.

---

## Files on disk (for @-mentions)

From a Ship checkout or submodule:

```
prompts/onboarding/adopt-ship-generic.md
prompts/onboarding/adopt-ship-elmundi.md
```

Point your agent at these paths in the **product** repo once Ship is present (e.g. `tools/ship/prompts/onboarding/…`).
