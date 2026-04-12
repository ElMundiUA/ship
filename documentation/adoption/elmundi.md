# ElMundi — automatic Ship rollout

Use this page when adopting Ship into **[ElMundiUA/elmundi](https://github.com/ElMundiUA/elmundi)** (or a fork with the same layout: `website/`, `.github/workflows/`, `tools/*`).

## Agent instruction (copy-paste order)

1. Run everything in **`prompts/onboarding/adopt-ship-generic.md`** (see [Agent playbook](agent-playbook.md)).  
2. Then apply **`prompts/onboarding/adopt-ship-elmundi.md`** (delta below).

--8<-- "prompts/onboarding/adopt-ship-elmundi.md"

---

## Human checklist (after the agent opens a PR)

- [ ] `tools/ship` is a **submodule** (or documented vendored copy with license file).  
- [ ] CI checkout uses **submodules: true** (or equivalent).  
- [ ] No remaining references to **`tools/linear-agent`** in active workflows (archived YAML may stay for history if moved).  
- [ ] GitHub Actions secrets: `LINEAR_API_KEY`, `CURSOR_API_KEY`, `GITHUB_TOKEN` (or fine-scoped PAT) as before.  
- [ ] Cursor Cloud Agent env for the repo still includes **`LINEAR_API_KEY`**.  
- [ ] Agent-produced setup verification steps are documented and pass for your chosen stack (or known blockers are recorded).

---

## Related

- [Agent launch matrix](agent-launch-matrix.md) — how to start the agent in Cursor / Copilot / Codex.  
- [Reference org receipts](../examples/elmundi/index.md) — workflow names, domains, SDLC grid.
