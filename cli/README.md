# @elmundi/ship-cli

`shipctl` is the command-line interface to **Ship**. Three jobs:

1. **Bootstrap a repo** so its agents (Cursor, Codex, Claude, Aider, Cline,
   Continue, Windsurf, Zed, Gemini, OpenCode, Copilot, …) can consume Ship
   artifacts the same way every other client does.
2. **Sync the catalog** of `pattern` / `tool` / `collection` artifacts into
   `.ship/cache/` and pin versions for reproducible runs.
3. **Run lanes** (one-shot dispatch + GitHub Actions wrappers) and
   **report Runs** so the operator console can render outcomes and route any
   escalations into the Inbox.

Published as **`@elmundi/ship-cli`** under the [elmundi](https://www.npmjs.com/org/elmundi) org; the binary name is **`shipctl`**.

> **Vocabulary.** The CLI speaks the protocol layer, the operator console
> speaks the product layer. Both are correct; they refer to the same things.
>
> | CLI / YAML / API (literal) | Operator console (prose) |
> |----------------------------|--------------------------|
> | `lanes:` entries in `.ship/config.yml`, `--lane <id>` | **Automations** |
> | `pattern:` artifacts (RFC-0001) | **Plays** |
> | `pipeline_runs` rows + `shipctl callback` payloads | **Runs** |
> | clarifications / improvements / approvals queue | **Inbox** items |
>
> The CLI keeps `lanes:` / `pattern:` / `--lane` literal forever; we are
> never going to break the YAML and flag surface. Help text and prose
> reach for the operator nouns when describing what users see.

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

After you confirm **`y`**, it writes/updates the files above. Injected content tells agents to **resolve Ship artifacts before use** via the CLI: **search → fetch → record `kind:id@version`** workflow, **`shipctl docs fetch`** for documentation paths, **`shipctl pattern|tool|collection`** for catalog bodies, and **`shipctl docs feedback`** for safe retro notes.

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

| Command | Role | Manual |
|---------|------|--------|
| **`shipctl init`** | Bootstrap an existing repo: agent rules, `.ship/config.yml`, optional CI scaffolding. | `documentation/discovery.md` |
| **`shipctl new`** | Greenfield: `git init` + minimal README + `init`. | this README, `New & Verify` |
| **`shipctl doctor`** | Inspect repo, propose stack, optionally write `.ship/inventory.json`. | this README, `Doctor` |
| **`shipctl config`** | Safe edits to `.ship/config.yml` (`init` / `get` / `set` / `validate` / `show` / `path`). | `documentation/configuration.md` |
| **`shipctl search`** | Vector search over docs + prompts (`POST /search`). | `documentation/discovery.md` |
| **`shipctl docs fetch`**, **`shipctl docs feedback`** | Documentation file fetch and retro feedback. | `documentation/discovery.md` |
| **`shipctl pattern\|tool\|collection`** **`list \| show \| fetch \| search`** | Versioned artifact bodies; plural aliases (`patterns`, `tools`, `collections`) work. | `documentation/authoring.md` |
| **`shipctl sync`** | Pull artifacts into `.ship/cache/`; with `--lock` writes `.ship/shipctl.lock.json` covering every Play the declared lanes depend on. | this README, `Config & Sync` |
| **`shipctl run`** | One-shot dispatch entry point. `kind: once` runs locally; other lane kinds are queued for the workspace runner. Reports its terminal status via the callback URL Ship injected. | `documentation/automations.md`, this README |
| **`shipctl lanes`** | Generate / inspect / delete the `.github/workflows/ship-<lane>.yml` thin wrappers (`install` / `list` / `remove`). | `documentation/automations.md`, this README |
| **`shipctl kickoff`** | Print a Play's pattern body for piping into the customer's agent in CI. | `documentation/automations.md` |
| **`shipctl callback`** | Pattern-side: report a Run's terminal status + RunSummary outcome so Ship can render the row and route escalations into the Inbox. | this README |
| **`shipctl knowledge init`** | Open a PR that seeds `.ship/knowledge/*.md` starter buckets. | `documentation/knowledge-buckets.md` |
| **`shipctl telemetry`** | Opt-in anonymous usage events (default OFF). | this README, `Telemetry & Feedback` |
| **`shipctl feedback`** | Local markdown drafts → POST `/feedback` → GitHub issue. | this README |
| **`shipctl verify`** | Post-adoption liveness checks (local + config + network). | this README, `New & Verify` |
| **`shipctl migrate`** | Upgrade `.ship/config.yml` from v1 to v2 (lanes-as-config). | `documentation/configuration.md` |
| **`shipctl help`** | Top-level command list with the same vocabulary callout. | — |

**Maintainers / full Ship checkout:** if the current directory (or **`SHIP_REPO`**) is inside the Ship monorepo, **`list` / `show` / `fetch`** for catalogs can read manifests from **disk** instead of HTTP. **`shipctl search`** always uses HTTP.

Run **`shipctl help`** for the operator-first overview with the same vocabulary callout printed at the top.

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
├── config.yml                 # RFC-0002 schema; committed. `lanes:` entries
│                              # are what the operator console renders as
│                              # Automations.
├── state.json                 # last_sync_at, last_manifest_hash; gitignored
├── shipctl.lock.json          # set by `shipctl sync --lock`; pins every
│                              # pattern the declared lanes depend on
├── cache/                     # per-repo artifact cache (gitignored)
│   ├── pattern/<id>@<v>/
│   │   ├── ARTIFACT.md        # full body (frontmatter + content), per RFC-0005
│   │   └── .meta.json         # source, sha256, fetched_at, etc.
│   ├── tool/<id>@<v>/…
│   ├── collection/<id>@<v>/…
│   └── doc/…
├── knowledge/                 # operator-edited markdown buckets, committed
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
shipctl sync --lock                  # write .ship/shipctl.lock.json after sync
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
sync, `.ship/state.json` records `last_sync_at` and `last_manifest_hash`. With
`--lock`, `shipctl sync` also writes `.ship/shipctl.lock.json` covering every
Play that the declared lanes depend on, so subsequent `shipctl run` invocations
can refuse to drift off the pinned set.

> Methodology docs never live in your repo. `shipctl sync` caches them in
> `.ship/cache/`, sealed by `content_sha256` from the Ship manifest.

## Run

`shipctl run` is the **one-shot dispatch entry point** for an Automation
(YAML key: `lanes:`). What it does depends on the lane's `kind`:

| `kind:` value | Behaviour of `shipctl run` |
|---------------|----------------------------|
| `once` | Executes the lane fully on the local machine (the pattern body is fed to the configured agent; the result is reported back via `shipctl callback`). |
| `lane` / `event` / `schedule` | Refuses to execute locally; the workspace's GitHub Actions runner picks the lane up via `.github/workflows/run-agent.yml`. The CLI exits with a clear message naming the lane id and its kind. |

```bash
# preview which patterns + parameters the lane would dispatch with
shipctl run --lane pr-self-review --dry-run

# fanout: the same pattern over every repo in the workspace
shipctl run --lane fleet-mobile-knowledge-refresh \
  --pattern fleet-knowledge-pack \
  --fanout matrix \
  --trigger event \
  --json

# CI usage: shipctl injects --ship-run-id / --ship-callback-url / --ship-run-token
# automatically; you only set them by hand when running outside the workspace runner
shipctl run --lane release-cut \
  --ship-run-id "$SHIP_RUN_ID" \
  --ship-callback-url "$SHIP_CALLBACK_URL" \
  --ship-run-token "$SHIP_RUN_TOKEN"
```

Important flags:

- `--pattern <id>` — override the lane's default pattern (a composite Play may
  declare several; this lets you target one specifically).
- `--fanout matrix|sequential|concurrent` — only meaningful for fleet-scope
  lanes that target multiple repos.
- `--trigger event|schedule|manual|once` — the trigger the lane was wired for;
  used to choose the payload shape.
- `--offline` — skip every HTTP probe (resolves patterns from `.ship/cache/`
  only); useful for hermetic CI.
- `--dry-run` — print the dispatch plan and exit 0; no agent invocation.

A full Run lifecycle in production looks like: console (or schedule) creates a
`pipeline_run` row → workspace runner is dispatched → runner calls
`shipctl run --lane <id>` for `kind: once` lanes (or invokes the agent
directly for `kind: lane`) → the pattern calls `shipctl callback` with the
RunSummary → console renders the outcome row in `/runs` and any escalations
land in `/inbox`.

## Lanes

`shipctl lanes` manages the **thin GitHub Actions wrappers** that delegate to
the reusable `run-agent.yml` workflow. Each lane in `.ship/config.yml`
generates one `.github/workflows/ship-<lane>.yml` file. The file itself is a
~12-line yaml that does nothing more than `uses: ./.github/workflows/run-agent.yml`
with the right `lane:` input — all the logic lives in the reusable workflow,
so wrappers can be regenerated without touching execution semantics.

```bash
# write workflow files for every lane in config.yml
shipctl lanes install --dry-run
shipctl lanes install --yes

# only one lane (or a few)
shipctl lanes install --only pr-self-review,release-cut

# wire to a specific shipctl version pin
shipctl lanes install --shipctl-version 0.11.2 \
  --owner elmundi --repo ship --ref v0.11.2

# inspect what's on disk vs config.yml
shipctl lanes list --json

# remove generated wrappers (does NOT touch lanes: config)
shipctl lanes remove --dry-run
shipctl lanes remove --only deprecated-lane --yes
```

Notes:

- `lanes install` writes files **only** for lanes with a configured trigger
  (`on.push`, `on.schedule`, `on.workflow_dispatch`). `kind: once` lanes
  intended to run only via `shipctl run` don't get a wrapper.
- The same wrapper covers a fleet-scope lane (one workflow file in your
  pilot repo, fanout happens server-side via the matrix Ship dispatches).
- Removing a wrapper does **not** remove the lane from `.ship/config.yml`;
  to fully retire an Automation, drop the `lanes.<id>` block from the YAML
  and re-run `shipctl lanes install` so the file is reconciled.

## Callback

`shipctl callback` is what a Play's pattern calls to **close the loop on a
Run**. It POSTs a structured payload to the URL Ship injected into the
runner (`SHIP_CALLBACK_URL` env or `--callback-url`) so the operator console
can render an outcome-first row in `/runs` and route any escalations into
`/inbox`.

The flag surface is **protocol-stable** (the workspace API depends on it);
the help is grouped by intent.

### Identity (one of these is required)

```
--run-id <uuid>          # falls back to SHIP_RUN_ID
--callback-url <url>     # falls back to SHIP_CALLBACK_URL
SHIP_RUN_TOKEN=<jwt>     # bearer for the callback URL (CI sets this)
SHIP_API_BASE=<url>      # base for {api}/v1/runs/<id>/callback when only --run-id is set
```

### Status & summary

```
--status ok|fail|cancelled       # required terminal state
--summary "Free text"            # short human readout (kept in addition to outcome)
--metric key=value               # repeatable; persisted as-is
```

### RunSummary outcome (Phase 3)

These map 1:1 to the `outcome:` JSONB column on `pipeline_runs` and to what
the console renders:

```
--outcome-text "Reviewed PR · 3 suggestions · 1 fix applied"
--findings-count 3
--severity high=1 --severity medium=2          # repeatable; map of sev → count
--artifact pr:"Auto-fix: typo":"https://...":  # repeatable; type:title[:ref]
--artifact comment:"Self-review summary":"https://github.com/.../pull/42#…"
--escalation clarification:"agent_low_confidence"   # repeatable; type:reason
--requires-approval                                  # toggle the approval gate
--approval-payload '{"...": "..."}'                  # JSON forwarded to the Inbox item
```

You can also pass the full RunSummary as JSON via:

- `SHIP_RUN_OUTCOME=$JSON_STRING`
- `SHIP_RUN_OUTCOME_FILE=/path/to/outcome.json`

Flags merge on top of the env / file payload (CLI wins).

### Example (canonical pattern recipe)

```bash
shipctl callback --status ok \
  --outcome-text "Reviewed PR · 3 suggestions · 1 fix applied" \
  --findings-count 3 \
  --severity high=1 --severity medium=2 \
  --artifact comment:"PR self-review summary":"https://github.com/elmundi/ship/pull/42#issuecomment-…" \
  --artifact pr:"Auto-fix: typo in README":"https://github.com/elmundi/ship/pull/43"
```

The same `## Reporting` block lives at the bottom of every top-Play pattern
(see `artifacts/patterns/flow-pr-self-review/ARTIFACT.md` for the canonical
example). When you author a new Play, copy that block as the contract you
expect runners to honour.

## Knowledge

`shipctl knowledge init` opens a PR in the target repo that seeds the
`.ship/knowledge/` starter buckets (e.g. `code-style.md`, `ui-runbook.md`).
The PR is intentionally minimal — operators are expected to fill the buckets
in over time.

```bash
# requires SHIP_API_TOKEN; targets the workspace's wired GitHub installation
shipctl knowledge init \
  --workspace 11111111-1111-1111-1111-111111111111 \
  --repo elmundi/ship-pilot \
  --only code-style,ui-runbook \
  --json
```

Behind the scenes, this hits the workspace API which uses the GitHub App
installation to open the PR. The buckets the Plays read at runtime live
under the same `.ship/knowledge/` tree the PR seeds.

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
  across `/patterns`, `/tools`, `/collections`, Linear labels exist (needs
  `LINEAR_API_KEY`), every `${{ secrets.X }}` reference in gh-actions
  workflows is declared in `.env.example`.

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

## Reference

Protocol-stable surfaces:

- **Artifacts protocol** (`POST /search`, `POST /fetch`) — RFC-0001.
- **Stack block + presets + adapters** — RFCs 0002 / 0004 / 0006.
- **Telemetry** — RFC-0003.
- **Operator IA** (Plays / Automations / Runs / Inbox) — RFC-0010.
- **HTTP schemas live next to the source**: `artifacts/tools/methodology-api/ARTIFACT.md`.

Every consumed artifact should be recorded in the PR or commit log as
`<kind>:<id>@<version>` so reviewers can replay what the agent saw.
