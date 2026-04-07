# ElMundi — addendum to `adopt-ship-generic.md`

Apply **after** the generic playbook. Target: **[ElMundiUA/elmundi](https://github.com/ElMundiUA/elmundi)**-style monorepo (`website/` + `.github/workflows/` + Ship under `tools/*`).

## Target layout

- **`tools/ship`** — recommended: **git submodule** → `https://github.com/ElMundiUA/ship` (main).  
  Legacy mirror **`tools/linear-agent/`** should be **replaced** or symlinked; workflows must not call dead paths.

## Workflows

- Files named `linear-agent-*.yml` are **historical names**; they orchestrate Ship, not “only Linear.”
- Update every step that references:
  - `tools/linear-agent` → `tools/ship` (or your `SHIP_ROOT`)
  - `node scripts/...` → `node tools/ship/runtime/scripts/...` **or** set `defaults.run.working-directory: tools/ship/runtime` and use `node scripts/...` + `node dist/cli.js` relative to that cwd.
- **`actions/checkout`**: enable **submodules** so `tools/ship` is populated in CI.

## Prompts & launch

- **`cloud-agent-launch.mjs`** must resolve prompts under **`tools/ship/prompts/cloud-agent/`** (paths in script are relative to Ship layout — keep Ship tree intact).

## Environment

- **`.env`** at **monorepo git root** (parent of `website/` and `tools/ship`).
- ElMundi identifiers: `LINEAR_SDLC_PROJECT_*`, `LINEAR_TEAM_KEY=ELM`, etc. — see Ship **`documentation/examples/elmundi/index.md`** Environment table.

## Verification

```bash
cd tools/ship/runtime && bash scripts/verify-setup.sh
```

(Or from root with adjusted `SCRIPT_DIR` — goal is to run the same script against repo-root `.env`.)

## First green path

1. Submodule + one workflow file updated (e.g. `workflow_dispatch` only) → PR.  
2. Merge → add secrets → manual `workflow_dispatch` dry run.  
3. Then migrate remaining workflows in a second PR.
