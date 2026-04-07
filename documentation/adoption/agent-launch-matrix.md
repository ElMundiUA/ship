# Agent launch matrix — how to run the adoption playbook

Use the **same** playbook file for every product; only the **invocation** changes.

**Canonical prompt files** (in a Ship checkout):

| File | Audience |
|------|----------|
| `prompts/onboarding/adopt-ship-generic.md` | Any repository |
| `prompts/onboarding/adopt-ship-elmundi.md` | ElMundi monorepo **after** generic (delta only) |

---

## Cursor — Cloud Agent (GitHub / API)

**Not** the adoption step itself — this is how **SDLC roles** run after adoption. For **onboarding**, use Cursor **IDE** or **Chat** with the playbook below.

Reference: [Tools → Cursor Cloud Agent](../tools/index.md#cursor-cloud-agent).

---

## Cursor — IDE (Composer / Agent mode)

**Shortcut:** from the product repo, `curl` + `bash` the launcher in [Adoption index](index.md) and choose **Cursor** — it opens the folder (if `cursor` is on `PATH`) and prints `@…` paths.

1. Open the **target product repository** (e.g. ElMundi) in Cursor.  
2. **@** mention files: `prompts/onboarding/adopt-ship-generic.md`  
   - If you use Ship as submodule: `@tools/ship/prompts/onboarding/adopt-ship-generic.md`  
3. Instruction: *“Execute this playbook. Create a branch `chore/ship-adopt`. Open a PR when done.”*  
4. For ElMundi: add second `@` `adopt-ship-elmundi.md` from the same `prompts/onboarding/` path under Ship.

---

## GitHub Copilot (VS Code / JetBrains / chat)

1. Checkout target repo + ensure Ship is visible (submodule or copy).  
2. **Paste** the contents of `adopt-ship-generic.md` into chat, then *“Follow this in the workspace.”*  
3. Attach `adopt-ship-elmundi.md` as a follow-up message for ElMundi.

---

## OpenAI Codex CLI / similar headless CLI

```bash
cd /path/to/target-repo
# Example; adjust to your CLI
codex -- "Read tools/ship/prompts/onboarding/adopt-ship-generic.md and execute it against this repo. Branch chore/ship-adopt."
```

If Ship is not yet present, first prompt: *“Add Ship as submodule at tools/ship from https://github.com/ElMundiUA/ship then run the playbook.”*

---

## Claude Code (Anthropic terminal agent)

**Interactive launcher** (submodule at `tools/ship` if the playbook is missing, then choose **Cursor** or **Claude Code**):

```bash
cd /path/to/target-repo
curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash
```

Review [`adopt-ship.sh`](https://github.com/ElMundiUA/ship/blob/main/adopt-ship.sh) before piping to `bash`. For **Claude Code** only, after Ship is on disk:

```bash
claude "Read tools/ship/prompts/onboarding/adopt-ship-generic.md and execute it against this repo. Create branch chore/ship-adopt. Open a PR when done."
```

---

## Claude / other chat with repo upload

Upload `adopt-ship-generic.md` (+ `adopt-ship-elmundi.md` for ElMundi) and a **zip** or file tree of the target repo **or** connect the official “repo” integration if available.

---

## Ticket systems (ship-agent)

| System | `TRACKER_PROVIDER` | Doc |
|--------|-------------------|-----|
| Linear | `linear` (default) | [Ship Agent & trackers](../tools/ship-agent-trackers.md#configure-linear) |
| Jira Cloud | `jira` | same page |
| GitHub Issues | `github` | same page |
| Azure DevOps Boards | `azure-devops` | same page |
| ClickUp | `clickup` | same page |

Pick scripts in `runtime/scripts/pick-*.mjs` remain **Linear-first**; call that out in the adoption PR if you use another tracker.

---

## Summary

| Agent surface | What to pass |
|---------------|----------------|
| One-command launcher | [Adoption index](index.md) — curl the raw `adopt-ship.sh`, pipe to `bash` |
| Cursor IDE | `@…/adopt-ship-generic.md` (+ ElMundi addendum) |
| Claude Code | Launcher above, or `claude "Read …/adopt-ship-generic.md …"` |
| Copilot chat | Paste playbook markdown |
| Codex / CLI | Path to playbook file + execute instruction |
| Humans | [Adoption overview](index.md) + this matrix |
