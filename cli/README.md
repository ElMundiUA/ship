# @elmundi/ship-cli

**Ship** in your repository: agents get a standing policy to call the **methodology HTTP API** (semantic search, full doc fetch, catalog bodies, retro feedback) via the **`ship`** CLI and the URLs/snippets `ship init` writes.

Published as **`@elmundi/ship-cli`** under the [elmundi](https://www.npmjs.com/org/elmundi) org; the binary name is **`ship`**.

## Requirements

- **Node.js 20+**

## Install

```bash
npm install -g @elmundi/ship-cli
# or one-off:
npx @elmundi/ship-cli help
```

## Bring Ship into your project (main path)

You **do not** need the Ship monorepo cloned for day-to-day use. Work in **your product repo** and wire agents to the same methodology API the CLI uses.

### 1. Pick the API URL

- **`SHIP_API_BASE`** — env var the CLI and injected snippets use (no trailing slash).
- Or pass **`--base-url`** on each command.
- Default matches other Ship tooling (public methodology host unless you override for local FastAPI).

### 2. Preview what `ship init` will change

From the **root of the repo** you want agents to use:

```bash
cd /path/to/your-product
npx @elmundi/ship-cli init --dry-run
```

`ship init` **detects what is already in the tree** and only plans injections for those stacks:

| If the repo has… | `ship init` can add… |
|------------------|----------------------|
| `.cursor/` | Cursor rule **`.cursor/rules/ship-methodology-api.mdc`** |
| **`AGENTS.md`** | Appended section (Codex-style / generic agents file) |
| **`CLAUDE.md`** | Appended section |
| **`.codex/`** | **`SHIP_API.md`** under `.codex/` |
| **`.github/copilot-instructions.md`** | Appended section |

If **none** of the above exist, init offers a **standalone** **`SHIP_AGENT_API.md`** in the repo root so humans can copy the contract into whatever system you use later.

Use **`--only cursor|agents-md|claude-md|codex|copilot`** to limit targets; **`--cwd <dir>`** to point at another root.

### 3. Apply with confirmation (recommended)

Interactive run prints the plan and asks **Apply these changes? [y/N]**:

```bash
npx @elmundi/ship-cli init
```

After you confirm **`y`**, it writes/updates the files above. Injected content tells agents to **use the Ship methodology API** (and the **`ship`** CLI from a dev shell): **search → fetch** workflow, **`ship docs fetch`** for documentation paths, **`ship pattern|tool|workflow|collection`** for catalog entries, and **`ship docs feedback`** for safe retro notes — so methodology and **tools/workflows** discovery stay consistent with the server.

### 4. Non-interactive (CI or scripts)

Only after you are happy with **`--dry-run`**:

```bash
npx @elmundi/ship-cli init --yes
```

**`--force`** replaces blocks that were already injected (same marker). Without **`--force`**, existing injections are skipped.

## Commands (quick reference)

| Command | Role |
|--------|------|
| **`ship init`** | Inject agent-facing rules / sections with your **`SHIP_API_BASE`** (or **`--base-url`**). |
| **`ship search …`** | Vector search over methodology corpus (`POST /search`). |
| **`ship docs fetch …`**, **`ship docs feedback …`** | Documentation file fetch and retro feedback (`POST /fetch` with `path`, `POST /feedback`). |
| **`ship pattern|tool|workflow|collection`** **`list` \| `show` \| `fetch` \| `search`** | Catalogs; hosted mode uses the same API (including **`fetch`** via `POST /fetch` with `kind` + `id`). Plural aliases (`patterns`, `tools`, …) work. |

**Maintainers / full Ship checkout:** if the current directory (or **`SHIP_REPO`**) is inside the Ship monorepo, **`list` / `show` / `fetch`** for catalogs can read manifests from **disk** instead of HTTP. **`ship search`** always uses HTTP.

Run **`ship help`** for full usage.

## Versioning (until the CLI stabilizes)

Releases **bump the patch (third) number only** (e.g. `0.8.0` → `0.8.1`) while the command surface and docs are still settling. Minor/major bumps resume once things are stable.

## Publishing (maintainers)

GitHub Action **Publish @elmundi/ship-cli to npm** (tag **`cli-v<version>`** must match **`cli/package.json`**, or use **workflow_dispatch**). Repository secret **`NPM_TOKEN`** required. Publish from monorepo root: **`npm publish -w @elmundi/ship-cli`**, not `npm publish --prefix cli` (root package is private).
