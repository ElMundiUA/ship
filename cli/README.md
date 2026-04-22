# @elmundi/ship-cli

**Ship** in your repository: agents get a standing policy to consume Ship **artifacts** (patterns, tools, workflows, collections, docs) via the **`shipctl`** CLI and the snippets `shipctl init` writes.

Published as **`@elmundi/ship-cli`** under the [elmundi](https://www.npmjs.com/org/elmundi) org; the binary name is **`shipctl`**.

## Requirements

- **Node.js 20+**

## Install

```bash
npm install -g @elmundi/ship-cli
# or one-off:
npx @elmundi/ship-cli help
```

## Bring Ship into your project (main path)

You **do not** need the Ship monorepo cloned for day-to-day use. Work in **your product repo** and wire agents to the same artifacts protocol the CLI uses.

### 1. Pick the API URL

- **`SHIP_API_BASE`** — env var the CLI and injected snippets use (no trailing slash).
- Or pass **`--base-url`** on each command.
- Default matches other Ship tooling (public host unless you override for local FastAPI).

### 2. Preview what `shipctl init` will change

From the **root of the repo** you want agents to use:

```bash
cd /path/to/your-product
npx @elmundi/ship-cli init --dry-run
```

`shipctl init` **detects what is already in the tree** and only plans injections for those stacks:

| If the repo has… | `shipctl init` can add… |
|------------------|-------------------------|
| `.cursor/` | Cursor rule **`.cursor/rules/ship-artifacts-protocol.mdc`** |
| **`AGENTS.md`** | Appended section (Codex-style / generic agents file) |
| **`CLAUDE.md`** | Appended section |
| **`.codex/`** | **`SHIP_API.md`** under `.codex/` |
| **`.github/copilot-instructions.md`** | Appended section |
| **`.aider.conf.yml`** / `AIDER.md` | **`AIDER.md`** section |
| **`.clinerules`** / `.rooignore` | **`.clinerules`** section |
| **`.continue/`** | **`.continue/ship.md`** side-file |
| **`.windsurfrules`** | **`.windsurfrules`** section |
| **`.zed/`** | **`.zed/ship.md`** |
| **`GEMINI.md`** / `.gemini/` | **`GEMINI.md`** section |
| **`.opencode/`** | **`.opencode/ship.md`** |
| **`.cursor/environments.json`** | Marker-guarded update to existing file |

If **none** of the above exist, init offers a **standalone** **`SHIP_AGENT_API.md`** in the repo root so humans can copy the contract into whatever system you use later.

Use **`--agents <csv>`** to limit targets and **`--cwd <dir>`** to point at another root. Example:

```bash
npx @elmundi/ship-cli init --agents cursor,codex,claude --dry-run
```

### 3. Stack hints

`init` also accepts `--tracker`, `--ci`, `--preset` (per RFC-0002 stack block). For now they land in the plan payload and are echoed back; a future release writes them into `.ship/config.yml` and triggers the bootstrap adapters (RFC-0004).

```bash
npx @elmundi/ship-cli init --yes \
  --agents cursor,codex --tracker linear --ci gh-actions --preset web-app
```

### 4. Apply with confirmation (recommended)

Interactive run prints the plan and asks **Apply these changes? [y/N]**:

```bash
npx @elmundi/ship-cli init
```

After you confirm **`y`**, it writes/updates the files above. Injected content tells agents to **resolve Ship artifacts before use** via the CLI: **search → fetch → record `kind:id@version`** workflow, **`shipctl docs fetch`** for documentation paths, **`shipctl pattern|tool|workflow|collection`** for catalog bodies, and **`shipctl docs feedback`** for safe retro notes.

### 5. Non-interactive (CI or scripts)

Only after you are happy with **`--dry-run`**:

```bash
npx @elmundi/ship-cli init --yes
```

**`--force`** replaces blocks that were already injected (same marker). Without **`--force`**, existing injections are skipped.

## Init flow

`shipctl init` is the primary adoption entrypoint. It composes four steps in one
command:

1. **Config** — creates `.ship/config.yml` (via `DEFAULT_CONFIG`), seeds an
   anonymous telemetry id, writes `.ship/state.json`, and appends
   `.ship/cache/` to `.gitignore`. Existing configs are respected and only
   updated with the flags/doctor proposal.
2. **Telemetry** — prompts once for opt-in (default **OFF**). Non-TTY runs and
   `--yes` default to OFF. `--telemetry on|off|ask` overrides the prompt.
3. **Doctor (no network)** — runs the adapter detect-layer to propose
   `tracker / ci / agents / language` values for gaps the user didn't set
   explicitly via flags. Detection never overrides explicit flags.
4. **Sync** — calls `syncArtifacts()` to pull the derived collection artifacts
   (`collection/agent-rules-<agent>`, `collection/preset-<preset>`, optionally
   `collection/adoption-playbook`) into `.ship/cache/`.

Optional post-steps:

- **`--copy-rules`** — for every agent in `stack.agents`, reads the cached
  `agent-rules-<agent>` artifact, extracts `install_target` + `marker` from the
  front-matter, and writes (or upserts) the rules file at the target path.
  A `<!-- ship-cli: installed-from collection/agent-rules-<agent>@<version> -->`
  footer is appended so re-runs can detect the installed version. Downgrades /
  different versions are skipped unless `--force` is passed.
- **`--bootstrap`** — renders CI + tracker + secrets scaffolding. v1 special-
  cases `mobile-app + gh-actions + linear` (writes `.github/workflows/ship-pilot.yml`
  skeleton, `.ship/labels.yml`, and a `# --- ship-managed ---` block appended to
  `.env.example`). All other combinations emit `SHIP_BOOTSTRAP_PLAN.md` with a
  TODO checklist pointing at the preset artifact for full details.
- **`--copy-playbook`** — fetches `collection/adoption-playbook` when
  published; a 404 is silently skipped (does not fail the command).

### Three primary invocations

**MVP (dry-run preview, recommended first run):**

```bash
shipctl init --dry-run --agents cursor --preset adoption-minimum --tracker none --ci manual
```

**Pilot (apply, no scaffolding):**

```bash
shipctl init --yes \
  --agents cursor,claude-md \
  --preset web-app --tracker linear --ci gh-actions \
  --copy-rules --telemetry off
```

**Full bootstrap (mobile-app, with scaffolding skeletons):**

```bash
shipctl init --bootstrap --yes \
  --agents cursor,claude-md,codex \
  --tracker linear --ci gh-actions --preset mobile-app \
  --copy-rules --telemetry off
```

### Full flag surface

```
shipctl init
  [--yes] [--force] [--dry-run] [--cwd DIR] [--json]
  [--agents cursor,codex,claude-md]  # preferred, csv
  [--tracker linear]                  # linear|jira|github-issues|…|none
  [--ci gh-actions]                   # gh-actions|gitlab-ci|…|manual
  [--preset mobile-app]               # web-app|api-backend|mobile-app|…
  [--language ts]                     # ts|js|py|go|rust|…|multi
  [--channel stable|edge]             # override api.channel
  [--copy-rules]                      # install agent-rules-<agent> files
  [--copy-playbook]                   # fetch adoption-playbook into cache
  [--bootstrap]                       # render CI/tracker scaffolding from preset
  [--telemetry on|off|ask]            # override the interactive prompt
```

### Example output

```
Ship init complete
-----------------
Config:    .ship/config.yml
Agents:    cursor, claude-md
Tracker:   linear
CI:        gh-actions
Preset:    mobile-app
Channel:   stable
Telemetry: off

Installed rules:
  - .cursor/rules/ship-artifacts-protocol.mdc (from collection/agent-rules-cursor@1.0.0) [wrote]
  - CLAUDE.md (from collection/agent-rules-claude-md@1.0.0) [wrote]

Bootstrap (preset=mobile-app):
  - wrote SHIP_BOOTSTRAP_PLAN.md
  - wrote .github/workflows/ship-pilot.yml
  - wrote .ship/labels.yml
  - appended .env.example

Next:
  shipctl sync              # keep artifacts fresh
  shipctl verify            # check tracker labels, CI secrets, rules markers
  shipctl feedback draft    # submit improvement idea
```

## Commands (quick reference)

| Command | Role |
|--------|------|
| **`shipctl init`** | Inject agent-facing rules / sections with your **`SHIP_API_BASE`** (or **`--base-url`**). |
| **`shipctl search …`** | Vector search over methodology corpus (`POST /search`). |
| **`shipctl docs fetch …`**, **`shipctl docs feedback …`** | Documentation file fetch and retro feedback (`POST /fetch` with `path`, `POST /feedback`). |
| **`shipctl pattern\|tool\|workflow\|collection`** **`list` \| `show` \| `fetch` \| `search`** | Artifact bodies; hosted mode uses the same API (including **`fetch`** via `POST /fetch` with `kind` + `id` + optional `version`). Plural aliases (`patterns`, `tools`, …) work. |

**Maintainers / full Ship checkout:** if the current directory (or **`SHIP_REPO`**) is inside the Ship monorepo, **`list` / `show` / `fetch`** for catalogs can read manifests from **disk** instead of HTTP. **`shipctl search`** always uses HTTP.

Run **`shipctl help`** for full usage.

## Doctor

**`shipctl doctor`** inspects a repository using the pluggable adapter registry
(`cli/lib/adapters/`) and proposes a best-guess Ship stack. It runs every
tracker / CI / language / agent adapter's `detect(cwd)` hook, scores the
findings, and infers a preset from repository structure (e.g. `pubspec.yaml`
or `react-native` in deps → `mobile-app`; `packages/` or `pnpm-workspace.yaml`
→ `monorepo`). Nothing is ever written without **`--write-inventory`**.

```bash
shipctl doctor                                       # human report
shipctl doctor --json                                # machine-readable
shipctl doctor --cwd /path/to/other-repo             # inspect elsewhere
shipctl doctor --write-inventory                     # persist .ship/inventory.json
```

Example output (trimmed):

```text
Ship doctor — inspecting /path/to/your-product

Tracker:     linear (0.95) · evidence: .env (LINEAR_API_KEY), package.json (@linear/sdk)
CI:          gh-actions (1.00) · evidence: .github/workflows/ (3 workflow(s))
Language:    ts (1.00) · evidence: tsconfig.json (present)
Agents:      cursor (1.00), claude-md (1.00)

Inferred preset:  web-app (evidence: next.config.ts)

Existing Ship artifacts:
  .ship/config.yml       missing
  .ship/cache/           missing
  .ship/inventory.json   missing
  .cursor/rules/ship-*   missing

Recommendations:
  1. shipctl config init
  2. shipctl init --bootstrap --tracker linear --ci gh-actions --agents cursor,claude-md --preset web-app
  3. shipctl sync
  4. shipctl verify
```

Passing **`--write-inventory`** persists the findings to `.ship/inventory.json`
so that `shipctl init --bootstrap` can pick up the inferred stack without
re-running detection.

## Config & Sync

Ship stores local state under **`.ship/`**. Methodology bodies never live in your
repo git history — `shipctl sync` caches them in `.ship/cache/`, which is
`.gitignore`d by default.

### `.ship/` layout

```
.ship/
├── config.yml                 # RFC-0002 schema; committed
├── state.json                 # last_sync_at, last_manifest_hash; gitignored
├── cache/                     # per-repo artifact cache (gitignored)
│   ├── pattern/<id>@<v>/
│   │   ├── ARTIFACT.md        # full body (frontmatter + content), per RFC-0005
│   │   └── .meta.json         # source, sha256, fetched_at, etc.
│   ├── tool/<id>@<v>/…
│   ├── workflow/<id>@<v>/…
│   ├── collection/<id>@<v>/…
│   └── doc/…
├── telemetry-outbox.jsonl     # buffered telemetry events (gitignored)
└── feedback-drafts/           # feedback draft markdowns (gitignored)
```

### `shipctl config`

```bash
shipctl config init               # bootstrap .ship/config.yml + state.json + cache/
shipctl config path               # print absolute path to config.yml
shipctl config show               # pretty-print effective YAML
shipctl config get <dotted.key>   # e.g. shipctl config get api.channel
shipctl config set <k> <value>    # atomic write, validates before saving
shipctl config validate           # exit 10 on invalid enum / bad URL / bad pin key
```

Value parsing for `config set`:

- Bare `true|false|null` → booleans / null.
- `-?\d+(.\d+)?` → number.
- `[a,b,c]` → array of strings (quotes optional).
- Anything else → string.

Dotted keys under `artifacts.pins` preserve the embedded slash:
`artifacts.pins.pattern/role-developer`.

```bash
shipctl config set stack.agents [cursor,codex]
shipctl config set api.channel edge
shipctl config set artifacts.pins.pattern/role-developer 1.4.2
```

### `shipctl sync`

```bash
shipctl sync                         # pull latest for this stack
shipctl sync --check-only            # report changes without writing cache
shipctl sync --dry-run               # --check-only + planned HTTP calls
shipctl sync --only pattern:role-developer [--only tool:gh-actions]
shipctl sync --channel edge
shipctl sync --force-unpin           # temporarily ignore version pins
```

Summary format:

```
up_to_date: 12
updated:     3
skipped_pin: 2
deprecated:  1 (…)
yanked:      0
failed:      0
```

Pins are honoured: an entry whose manifest version does not satisfy the pin is
reported as `skipped_pin` unless `--force-unpin` is set. After a successful
sync, `.ship/state.json` records `last_sync_at` and `last_manifest_hash`.

> Methodology docs never live in your repo. `shipctl sync` caches them in
> `.ship/cache/`, sealed by `content_sha256` from the Ship manifest.

## Telemetry & Feedback

Anonymous telemetry is **opt-in and OFF by default** (RFC-0003). Nothing leaves
the repo until you explicitly flip the switch with `shipctl telemetry on`.
Events are first buffered to `.ship/telemetry-outbox.jsonl`; flushing to
`POST /telemetry` is a deliberate, retry-safe step.

```bash
# inspect current state
shipctl telemetry status                 # share=..., anonymous_id=..., outbox_pending=N

# opt in, with a narrow scope, non-interactively
shipctl telemetry on --scope artifact_usage,improvement_drafts --yes

# look at what's queued locally before sending
shipctl telemetry buffer --limit 10

# send any queued events (batches of 100); succeeded lines are removed
shipctl telemetry flush
shipctl telemetry flush --dry-run        # preview only

# data rights: export or delete by anonymous_id
shipctl telemetry export --out telemetry.json
shipctl telemetry delete-my-data         # interactive confirmation required

# rotate identity (server treats the previous id as a separate adopter)
shipctl telemetry reset-id

# fully disable
shipctl telemetry off
```

Allowed event types: `artifact.fetch`, `artifact.use`, `artifact.sync`,
`feedback.submit`, `doctor.result`. Payload keys in the RFC-0003 denylist
(`path, code, diff, branch, remote, email`) are stripped client-side before
anything is appended to the outbox.

Feedback is always drafted locally as a markdown file before it is sent
anywhere:

```bash
# create a draft
shipctl feedback draft --kind pattern --id role-developer --version 1.4.2 \
  --title "Missing mobile preview step" \
  --summary "Evidence checklist misses mobile preview" \
  --recommendation "Add a bullet under Evidence"

# review / edit (uses $EDITOR)
shipctl feedback list
shipctl feedback show .ship/feedback-drafts/2026-04-17-11-30-15-pattern-role-developer.md
shipctl feedback edit .ship/feedback-drafts/2026-04-17-11-30-15-pattern-role-developer.md

# submit → POST /feedback → GitHub issue URL; draft moves to sent/
shipctl feedback submit .ship/feedback-drafts/2026-04-17-11-30-15-pattern-role-developer.md --yes
```

Submission requires `kind`, `id`, `title`, and `summary`; missing fields fail
with exit 1 (nothing sent). When `telemetry.share=true` and
`scope.improvement_drafts=true`, a `feedback.submit` event is appended to the
telemetry outbox on successful submission.

## New & Verify

### `shipctl new <name>`

Scaffolds a fresh repo with the Ship wiring already in place: creates the
target directory, runs `git init -q`, drops a minimal `README.md`, seeds
`.ship/config.yml` via `shipctl config init`, applies the provided stack
flags via `shipctl config set`, and (when `--agents` is supplied) runs
`shipctl init --yes --copy-rules …` to install the agent rule files.

```bash
shipctl new pharma-pilot \
  --preset mobile-app --tracker linear --ci gh-actions \
  --agents cursor,claude,codex --yes
cd pharma-pilot
shipctl verify --no-network
```

Common flags:

- `--here` — initialize in the current directory instead of creating `<name>/`.
- `--preset / --tracker / --ci / --agents / --language / --channel` —
  forwarded to `config set` + `init`.
- `--yes` — non-interactive (required for CI / dry-run).
- `--dry-run` — describe the plan without touching disk.
- `--json` — machine-readable summary of created files.

### `shipctl verify`

Post-adoption liveness check. A collection of independent checks under
`cli/lib/verify/checks/` grouped by category:

- **local** — `.ship/config.yml` present, `.gitignore` excludes
  `.ship/cache/`, agent rule files have the
  `<!-- ship-cli: artifacts-protocol v1 -->` marker and `installed-from`
  footer, cached artifacts match their `.meta.json` sha256, bootstrap
  scaffolding (mobile-app + gh-actions + linear) carries
  `ship-managed` markers.
- **config** — `stack.*` enum re-validation, declared agents have
  on-disk signals.
- **network** (skip with `--no-network`) — `/health` (or `/patterns` as a
  fallback) reachable, local cache matches the channel catalog aggregated
  across `/patterns`, `/tools`, `/workflows`, `/collections`, Linear labels
  exist (needs `LINEAR_API_KEY`), every `${{ secrets.X }}` reference in
  gh-actions workflows is declared in `.env.example`.

Exit `0` when no check returned `fail`; warnings do not fail.

```bash
shipctl verify                       # full run
shipctl verify --no-network          # skip HTTP + Linear calls
shipctl verify --check rules-markers,cache-integrity
shipctl verify --severity warn       # hide pass rows
shipctl verify --json                # { checks:[…], summary:{…}, exit_code }
```

## Versioning

`shipctl --version` (or `shipctl version`) prints the running release. The
version is part of every outbound `User-Agent` header so the methodology API
can correlate adoption metrics with the client release.

This package follows the **monorepo-wide** Ship version: a single `VERSION`
file at the repo root drives `cli/package.json`, `landing/package.json`,
`backend/app/main.py`, and the root `package.json` in lockstep via
`scripts/version.mjs` (`npm run version:bump -- patch|minor|major|x.y.z`).
See the root [`README.md`](../README.md#versioning--releases) for the full
release recipe.

## Publishing (maintainers)

The GitHub Action **Publish @elmundi/ship-cli to npm** is triggered by either
the unified `v<x.y.z>` tag (preferred — bumps every component in lockstep) or
the legacy `cli-v<x.y.z>` tag (escape hatch for CLI-only patches). It runs
`scripts/version.mjs check` before publishing so an out-of-sync tree never
ships to npm.

```bash
npm run version:bump -- minor          # 0.10.0 → 0.11.0, syncs everything
git commit -am "release v$(cat VERSION)"
git tag "v$(cat VERSION)"
git push --follow-tags                 # publish workflow picks up v0.11.0
```

Repository secret **`NPM_TOKEN`** is required. Publish from the monorepo
root: **`npm publish -w @elmundi/ship-cli`** — not `npm publish --prefix cli`
(the root package is private).
