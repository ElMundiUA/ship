# Configuration

This page is the field reference for `.ship/config.yml` and a directory listing for everything `shipctl` writes under `.ship/`. It tells you **what each field is** and **what it controls**; for command flags see [/cli](/cli), for "how do I…" recipes see [Operating](/docs/operating), and for the vocabulary every field name assumes (artifact, kind, channel, pin, install_target, lane, …) read [Concepts](/docs/concepts) first. The normative spec is [RFC-0002](/docs/protocol/rfc-0002-shipctl-config) and [RFC-0007](/docs/protocol/rfc-0007-lanes-and-run-agent) — the latter introduced schema v2 and the `lanes:` block. When this page and `cli/lib/config/schema.mjs` disagree, the CLI wins.

## On-disk layout (`.ship/`)

`.ship/` always lives at the repository root. `shipctl` walks upward from the current working directory looking for `.ship/config.yml`; nothing else makes a directory "the ship root". Everything in the table below is relative to that root.

| Path | Tracked? | Purpose | Created by | Cleaned by |
|------|----------|---------|------------|------------|
| `.ship/config.yml` | committed | The single config file. Owns `version`, `api`, `stack`, `agent`, `lanes`, `artifacts.pins`, `cache`, `telemetry`. | `shipctl init` (or `shipctl config init`) | never (manual delete) |
| `.ship/config.yml.bak` | gitignored (operator's call) | Backup written by `shipctl migrate` before it overwrites the v1 file. Keep around until the migration has been reviewed; safe to delete after. | `shipctl migrate` | manual |
| `.ship/shipctl.lock.json` | committed | Lockfile covering every pattern the declared lanes (and any pattern pins) depend on. Records `version` + `content_sha256` + `cached_path` per entry (RFC-0007 §Lockfile). Written by `shipctl sync --lock`. Safe to commit — no secrets, no local paths beyond `.ship/cache/…`. | `shipctl sync --lock` | overwritten on next `sync --lock`; manual delete forces a full re-lock |
| `.ship/state.json` | gitignored | Mutable runtime state: `last_sync_at`, `last_manifest_hash`, `outbox_pending_count`. Kept next to `config.yml` to keep the committed file diff-clean. | `shipctl init`, written by `shipctl sync` | manual delete; recreated on next `sync` |
| `.ship/state/` | committed | Per-lane idempotency markers for `kind: once` lanes: one `<idempotency.key>.json` file per run, recording the pattern sha256 that completed the lane. Per RFC-0007 §Idempotency store markers are expected to be committed so a fresh clone knows a `once` lane is already done; the generated workflow wrapper commits them on success. Backend-store support lands in a future phase and will let teams opt out of the file form. | `shipctl run` (on `kind: once` success) | manual delete; next `run` re-creates the marker |
| `.ship/cache/` | gitignored by default · committed when `cache.vcs_tracked: true` | Root of the artifact body cache. Holds one folder per cached `<kind>/<id>@<version>`. | `shipctl init` (creates `.gitkeep`); populated by `shipctl sync` | `shipctl sync` removes nothing on its own; manual delete forces a re-fetch |
| `.ship/cache/<kind>/<sanitized-id>@<version>/ARTIFACT.md` | follows `.ship/cache/` | The cached artifact body. `<kind>` is one of `pattern`, `tool`, `collection`, `doc`. Slashes in `<id>` are written as `__`. | `shipctl sync` | overwritten on next `sync` for the same version |
| `.ship/cache/<kind>/<sanitized-id>@<version>/.meta.json` | follows `.ship/cache/` | Sidecar metadata: `kind`, `id`, `version`, `content_sha256`, `updated_at`, `source_url`, `fetched_at`, `channel`. Used by `shipctl verify` to detect drift. | `shipctl sync` | overwritten with the body |
| `.ship/inventory.json` | committed (safe — no secrets) | Snapshot of `shipctl doctor` findings: detected adapters, declared stack, check results. Reviewers diff it on PRs to spot stack drift. | `shipctl doctor --write-inventory` | overwritten on next `doctor` |
| `.ship/telemetry-outbox.jsonl` | gitignored | One JSON envelope per line, buffered when `telemetry.share: true`. Flushed by `shipctl telemetry flush`. | `shipctl sync`, `shipctl feedback`, any command that emits an event | `shipctl telemetry clear` (or auto-cleared after a successful flush) |
| `.ship/feedback-drafts/*.md` | gitignored | One draft per file (`<timestamp>-<kind>-<id>.md`), front-matter + Markdown body. | `shipctl feedback draft` | `shipctl feedback submit` moves the file to `.ship/feedback-drafts/sent/`; manual delete otherwise |
| `.ship/feedback-drafts/sent/` | gitignored (under `.ship/feedback-drafts/`) | Archive of submitted drafts. | `shipctl feedback submit` | manual |
| `.ship/playbooks/<id>@<version>.md` | committed (operator's call) | Optional copy of a playbook collection (e.g. `adoption-playbook`) materialised next to the repo. | `shipctl init --copy-playbook` | manual |
| `SHIP_BOOTSTRAP_PLAN.md` | committed (operator's call) | Markdown summary of what `shipctl init --bootstrap` planned: chosen stack, recommended tools and secrets, TODO checklist. Lives in the **repo root**, not under `.ship/`. | `shipctl init --bootstrap` | overwritten on next `--bootstrap` run |

`shipctl` never writes anywhere else under your repo. Anything else with the `# ship-managed` marker (e.g. agent rules at `.cursor/rules/ship-artifacts-protocol.mdc`, `AGENTS.md` blocks, `.github/workflows/ship-<lane>.yml` wrappers) is owned by `shipctl init --copy-rules` / `shipctl lanes install` / `shipctl init --bootstrap` — those targets are documented in [Concepts → install_target](/docs/concepts) and [/agent-matrix](/docs/agent-matrix).

## `.ship/config.yml` schema

Two schema versions are live in parallel:

- **`version: 1`** — the legacy schema shipped through `shipctl` 0.11.x. Still parsed and validated so existing repos see clear warnings instead of silent failures; `shipctl migrate` upgrades them to v2.
- **`version: 2`** — the current schema. Default for new installs (`shipctl init` / `shipctl config init`). Adds the top-level **`agent`** and **`lanes`** blocks (RFC-0007). Requires `shipctl_min >= "0.12.0"`.

A CLI that understands only v1 refuses to read v2 and prints a `shipctl` upgrade hint. A CLI that understands v2 accepts v1 with a deprecation warning and suggests `shipctl migrate`. Unknown top-level keys produce a single warning per command and are otherwise preserved on write (forward-compat). Unknown values for an enum field are a hard error.

Top-level keys recognised in v2 (from `KNOWN_TOP_LEVEL_V2` in `cli/lib/config/schema.mjs`):

| Block | Purpose |
|-------|---------|
| `version` | Schema version (`1` or `2`). See below. |
| `shipctl_min` | Minimum `shipctl` semver that understands this file. |
| `api` | Where the methodology API lives, which channel to pull from, freshness window, offline behaviour. |
| `stack` | The four-axis description of the repo: tracker, CI, agents, language, preset. |
| `agent` | (v2) Per-lane agent-runtime provider overrides. |
| `lanes` | (v2) Map of lane-id → lane definition. The source of truth for `shipctl run` and `shipctl lanes install`. |
| `artifacts` | Version pins per artifact, plus the `auto_update` switch. |
| `cache` | Whether `.ship/cache/` is committed. |
| `telemetry` | Anonymous-usage opt-in, anonymous id, scope toggles. |

### `version` and `shipctl_min`

| Field | Type | Default (v2) | Description |
|-------|------|--------------|-------------|
| `version` | int | `2` | Schema version. Accepted values: `1` (legacy) and `2` (current). Any other value aborts every command (exit `10`). Bump v1 → v2 with `shipctl migrate`; do not hand-edit. |
| `shipctl_min` | string | `"0.12.0"` | Minimum `shipctl` semver that understands this file. v2 requires `>= 0.12.0`. A CLI older than `shipctl_min` refuses to operate and prints an upgrade hint. |

```yaml
version: 2
shipctl_min: "0.12.0"
```

### `api`

Controls how `shipctl` reaches the methodology API and how aggressively it caches.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_url` | URL string | `"https://ship.elmundi.com"` | Root URL for `/manifest` and `/fetch`. Must parse as a URL or validation fails. The CLI appends `/api/methodology` when the URL doesn't already include it. |
| `channel` | enum | `"stable"` | One of `stable`, `edge`. Selects which manifest the cache is reconciled against. |
| `ttl_hours` | number ≥ 0 | `24` | How long a cached entry is considered fresh before `sync` will re-fetch even if the sha matches. |
| `offline_ok` | bool | `true` | When true, `shipctl` may serve cached artifacts on network failure with a warning instead of erroring out. |

```yaml
api:
  base_url: "https://ship.elmundi.com"
  channel: "stable"
  ttl_hours: 24
  offline_ok: true
```

### `stack`

The four-axis description of the repository. Every value is an enum; typos fail validation with the list of accepted values. New values arrive by publishing adapter artifacts (see RFC-0004), not by patching the schema.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tracker` | enum | `"none"` | Issue tracker: `linear`, `jira`, `github-issues`, `azure-boards`, `clickup`, `spreadsheet`, `none`. |
| `ci` | enum | `"manual"` | CI system: `gh-actions`, `gitlab-ci`, `buildkite`, `circleci`, `azure-pipelines`, `jenkins`, `manual`. |
| `agents` | string[] | `[]` | Subset of agent ids: `cursor`, `agents-md`, `claude-md`, `codex`, `copilot`, `aider`, `cline`, `continue`, `windsurf`, `zed`, `gemini`, `opencode`, `cursor-cloud`. Empty list is allowed (warned once during init); `shipctl` proceeds. |
| `language` | enum | `"multi"` | Primary repo language: `ts`, `js`, `py`, `go`, `rust`, `java`, `kotlin`, `swift`, `dart`, `multi`. |
| `preset` | enum | `"adoption-minimum"` | Bundled stack profile: `web-app`, `api-backend`, `mobile-app`, `cli`, `monorepo`, `adoption-minimum`. Drives which `collection/preset-*` is fetched and which bootstrap renderer runs. |

```yaml
stack:
  tracker: "linear"
  ci: "gh-actions"
  agents: ["cursor", "codex"]
  language: "ts"
  preset: "web-app"
```

### `agent` (v2)

Selects which agent runtime executes a lane. `shipctl run` reads this and emits the provider slug on stderr so the reusable workflow can dispatch to the right runtime (Claude Code, Cursor Cloud, Codex, …).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `agent.default.provider` | string (≤64 chars) | `null` | Fallback provider for every lane that does not declare an override. Values are agent slugs like `claude-code`, `cursor-cloud`, `codex`. `null` means "let the workflow choose its default". |
| `agent.overrides.<laneId>.provider` | string (≤64 chars) | unset | Per-lane override. Keys are lane ids and must match `/^[a-z0-9][a-z0-9_-]{0,63}$/`. |

```yaml
agent:
  default:
    provider: "claude-code"
  overrides:
    seed_knowledge:
      provider: "cursor-cloud"
```

### `lanes` (v2) {#lanes}

A **lane** is a triggered execution unit declared under `lanes:`. Each lane is one call to `shipctl run`. The `lanes:` map is the source of truth for `shipctl lanes install` (it materialises one `.github/workflows/ship-<lane>.yml` per entry) and for the Console's `/lanes` page. The operator-first reference — triggers table, cookbook, the Console UI — lives in [Lanes](/docs/lanes). This section is the pure field spec.

Lane ids match `/^[a-z0-9][a-z0-9_-]{0,63}$/`; ids starting with `ship_` are reserved for future built-ins. Anything that fails the regex is a hard error.

**Common keys (any `kind`):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kind` | enum | yes | Trigger discriminator: `once`, `event`, `schedule`. |
| `pattern` | string | one of `pattern` / `patterns` | Single pattern id. Back-compat alias for `patterns: [<id>]`. |
| `patterns` | string[] | one of `pattern` / `patterns` | Canonical multi-pattern form (RFC-0008 C3). Non-empty list; multi-pattern execution lands in C3.2. Mutually exclusive with `pattern`. |
| `pattern_version` | semver string | no | Pin a specific version instead of tracking latest. |
| `permissions` | object | no | Pass-through GitHub Actions `permissions:` block applied to the generated wrapper. |
| `runner` | string | no | Override the default runner label. |
| `timeout_minutes` | int 1–360 | no | Caps the wrapper's `timeout-minutes`. |
| `concurrency.group` | string | if `concurrency` is set | Concurrency group name; required when the block is present. |
| `concurrency.cancel_in_progress` | bool | no | Cancel in-flight runs when a newer one starts. |

**`kind: once` — extra keys:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `idempotency.key` | string `/^[a-z0-9][a-z0-9_.-]{0,127}$/` | yes | Ledger key; duplicate runs with the same key short-circuit to a no-op. |
| `idempotency.store` | enum | no | `file` (default — marker at `.ship/state/<key>.json`) or `backend` (reserved; falls back to `file` today). |
| `idempotency.reset_on` | enum | no | `version-change` (default — re-run when the pattern sha256 changes) or `manual` (only re-run when the marker is deleted). |

**`kind: event` — extra keys:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `on` | enum | yes | One of `pull_request`, `push`, `workflow_run`, `deployment_status`. |
| `when` | object | no | Opaque filter bag forwarded to the reusable workflow (`types: […]`, conclusion gates, etc.). |

**`kind: schedule` — extra keys:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cron` | string | yes | Five-field cron expression (GitHub Actions syntax). |
| `cron_tz` | string | no | IANA timezone for the cron (e.g. `Europe/Kyiv`). Default: UTC. |

Worked example combining all three kinds:

```yaml
lanes:
  pr_review:
    kind: event
    on: pull_request
    pattern: flow-pr-self-review
    permissions:
      contents: read
      pull-requests: write

  daily_retro:
    kind: schedule
    cron: "0 9 * * 1-5"
    cron_tz: "UTC"
    patterns:
      - flow-daily-retro
    concurrency:
      group: ship-daily-retro
      cancel_in_progress: false

  seed_knowledge:
    kind: once
    pattern: onboard-seed-knowledge
    idempotency:
      key: seed-knowledge-v1
      store: file
      reset_on: version-change
```

Any extra keys inside a lane are preserved on write (forward-compat) but surface a warning; validator-rejected shapes are a hard error (exit `10`). For every other operator concern — triggers table, Console UI, migration cookbook — start at [Lanes](/docs/lanes).

### `artifacts`

Pins (versions you do not want `sync` to drift) and the `auto_update` switch.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pins` | map<string,string> | `{}` | Keys are `<kind>/<id>` with `kind ∈ {pattern, tool, collection, doc}`. Values are an exact semver (`1.4.2`), a major-only prefix (`1`, `1.2`), or a range (`^2.0.0`, `~2.1`). `sync` skips an entry whose upstream version no longer satisfies the pin (counted as `skipped_pin` in the summary). |
| `auto_update` | bool | `true` | When true, `shipctl init` and `shipctl doctor` may run `shipctl sync` without asking. |

To stop an artifact from being tracked, remove its pin and let `sync` ignore it — only pinned, cached, agent-rules, and preset entries are considered "desired". Ship deliberately doesn't have a boolean `artifacts.disabled` field: pins are the opt-in list, cache deletion is manual. Pinning an `<id>` that does not exist on the configured channel is a hard error on `validate` and on any `sync` that tries to use it.

```yaml
artifacts:
  pins:
    pattern/role-developer: "1.4.2"
    tool/linear: "~2.1"
    collection/preset-web-app: "^3.0.0"
    doc/onboarding-checklist: "1.0.0"
  auto_update: true
```

### `cache`

A single switch that decides whether the artifact bodies travel with the repo.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `vcs_tracked` | bool | `false` | When true, `.ship/cache/` is committed (you must remove it from `.gitignore` yourself; the default `init` block treats it as ignored). The escape hatch for air-gapped CI that cannot reach the methodology API at build time. |

```yaml
cache:
  vcs_tracked: false
```

### `telemetry`

Anonymous usage telemetry. Default is OFF; `init` only enables it if you explicitly answer "yes" to the prompt or pass `--telemetry on`. Full envelope shape is in RFC-0003.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `share` | bool | `false` | Master switch. When false, no events are written to the outbox at all. |
| `anonymous_id` | string (UUID v4) | generated on first run | Stable per-repo identifier. Required when `share: true`; auto-generated and written back if missing. |
| `scope.artifact_usage` | bool | `true` | Emit `artifact.fetch`, `artifact.use`, `artifact.sync` events. |
| `scope.improvement_drafts` | bool | `true` | Emit `feedback.submit` events. |
| `scope.errors` | bool | `false` | Emit `doctor.result` failure events. |

```yaml
telemetry:
  share: false
  anonymous_id: "9b6d-…uuid-v4"
  scope:
    artifact_usage: true
    improvement_drafts: true
    errors: false
```

The payload-level denylist (`path`, `code`, `diff`, `branch`, `remote`, `email`) is defined in RFC-0003 and enforced by the CLI before events land in the outbox. It is **not** a config field — there is no `telemetry.denylist` key. If you need to petition for extra redactions, file feedback against RFC-0003.

## Resolution order

The effective value for any field is the highest-precedence layer that defines it:

1. Schema default (from `DEFAULT_CONFIG_V2()` in `cli/lib/config/schema.mjs`).
2. `.ship/config.yml` value.
3. Environment variable, when one exists for that field.
4. CLI flag, when the current command exposes one.

Worked example. Suppose `.ship/config.yml` says:

```yaml
api:
  channel: "stable"
```

You run:

```bash
SHIP_CHANNEL=edge shipctl sync --channel stable
```

The CLI flag wins: `sync` runs against `stable`, `SHIP_CHANNEL=edge` is shadowed, the file value never gets a vote. Drop the flag and you get `edge` (env beats file). Unset the env var and you are back to `stable` (the file value). Remove the `channel:` line altogether and the schema default (`stable`) takes over.

Environment variables `shipctl` reads:

| Env var | Overrides | Read by |
|---------|-----------|---------|
| `SHIP_API_BASE` | `api.base_url` | `sync`, `verify`, `feedback`, `telemetry`, `run`, the global `--base-url` default |
| `SHIP_CHANNEL` | `api.channel` | `sync` |
| `SHIP_TELEMETRY` | forces `telemetry.share` off when set to `false` | the telemetry outbox |
| `SHIP_API_TOKEN` | adds an `Authorization: Bearer …` header | every HTTP call |
| `SHIP_REPO` | path to a local Ship monorepo for offline `list`/`show`/`fetch` | `patterns`, `tools`, `collections`, lockfile builder, `shipctl lanes`, `shipctl run` |
| `SHIP_RUN_TOKEN` | short-lived bearer passed to callback endpoints | `shipctl kickoff`, `shipctl callback`, `shipctl run` (when a callback URL is set) |
| `SHIP_DEBUG` | when `1`, prints debug lines from the telemetry outbox and the feedback submitter to stderr | telemetry, feedback |

`SHIP_RUN_TOKEN` is a secret, obtained from the Ship dashboard when it dispatches a run (RFC-0007 / Wizard v2 iter 2). Never commit it; pass it through the GitHub Actions secret store.

`SHIP_TTL_HOURS`, `SHIP_OFFLINE_OK`, and `SHIP_CACHE_DIR` appear in RFC-0002 but are not yet wired in the CLI; setting them today has no effect. Unknown `SHIP_*` variables are silently ignored.

## Validation

`shipctl config validate` parses `.ship/config.yml` and runs the validator from `cli/lib/config/schema.mjs`. Three things happen:

1. **Shape**: the file must be a YAML mapping; each block must be the expected JSON-ish type (object vs array vs scalar).
2. **Enums**: every `api.channel`, `stack.tracker`, `stack.ci`, `stack.language`, `stack.preset`, `stack.agents[*]`, `lanes.*.kind`, `lanes.*.on`, `lanes.*.idempotency.store`, `lanes.*.idempotency.reset_on` is checked against its frozen list.
3. **Cross-field**: `artifacts.pins` keys match `<kind>/<id>` with `kind ∈ {pattern, tool, collection, doc}`; values look like a semver or range; `telemetry.anonymous_id` is a UUID v4 (required when `telemetry.share: true`); every lane id matches the id regex and every lane carries the required per-kind extra fields.

Exit codes used by the validator and by the commands that run validation as a side-effect (`init`, `sync`, `doctor`, `lanes`, `run`):

| Exit | Meaning |
|------|---------|
| `0` | Config is valid (or lane executed / no-op). Warnings (unknown keys, v1 deprecation) may still be on stderr. |
| `1` | Bad CLI invocation (unknown flag value, missing argument). |
| `2` | `shipctl run`-specific: config is v1. Run `shipctl migrate` first. |
| `3` | `shipctl run`: callback POST failed (lane itself may have succeeded). |
| `4` | `shipctl run`: idempotency marker read/write failed. |
| `10` | Config rejected: unsupported `version`, missing required block, type mismatch, unknown enum value, invalid pin key, invalid lane. Also raised by `shipctl run` when a callback URL is set but `SHIP_RUN_TOKEN` is missing. Also raised when `.ship/config.yml` is missing entirely. |
| `20` | Manifest fetch failed during `sync`. |

Common validator messages, with the field they point at:

- `version: unsupported; expected one of 1, 2, got "3"` → bump `shipctl` instead of editing the version by hand.
- `version: config is at v1; run 'shipctl migrate' to upgrade to v2` → deprecation warning (not an error); v1 is still accepted, but `shipctl run` requires v2.
- `api.base_url: not a valid URL (…)` → fix the scheme/host or unset the field to take the default.
- `stack.tracker: "linerar" is not valid. Expected one of: linear, jira, github-issues, azure-boards, clickup, spreadsheet, none` → fix the typo.
- `artifacts.pins["workflow/foo"]: invalid key; expected <kind>/<id> where kind∈{pattern,tool,collection,doc}` → `workflow` is not a valid pin kind; rewrite the pin or remove it. (The `workflow` artifact kind was retired in RFC-0007.)
- `artifacts.pins["pattern/role-developer"]: value must be a semver or range (got "v1.4.2")` → drop the leading `v`.
- `lanes["seed"].idempotency.key: must match /^[a-z0-9][a-z0-9_.-]{0,127}$/` → lowercase + `_.-` only; no spaces.
- `lanes["daily"].cron: must be a 5-field cron expression` → 5 whitespace-separated tokens.
- `telemetry.anonymous_id: required UUID v4 when telemetry.share=true` → run `shipctl init` once to regenerate, or remove `share: true`.

`shipctl init` and `shipctl doctor` also run the validator on every invocation; an invalid config blocks both.

## Editing safely

`shipctl config` is the safe surface; `shipctl config set` round-trips through the schema validator and writes via `.ship/config.yml.tmp` + `rename`, so a SIGKILL mid-edit cannot corrupt the file. Hand-editing the YAML risks (a) leaving the file invalid until the next command runs and surfaces it, (b) introducing comments or formatting that the writer will reflow on the next `set`, and (c) writing an unknown enum value that blocks every subsequent command.

The full surface (see [/cli](/cli) for flags):

| Command | What it does |
|---------|--------------|
| `shipctl config get <key>` | Print the value at the dotted path. |
| `shipctl config set <key> <value>` | Validate, then atomic-write. |
| `shipctl config show` | Print the effective YAML to stdout. |
| `shipctl config validate` | Parse + validate; exit `10` on errors. |
| `shipctl config path` | Print the absolute path of the active `.ship/config.yml`. |
| `shipctl config init` | Create a fresh `.ship/config.yml` + `state.json` + `cache/.gitkeep` from defaults. |

There is no `shipctl config unset` — to drop a key, hand-remove the line in `.ship/config.yml` (or set it to its default via `shipctl config set`) and run `shipctl config validate` to confirm the result.

Dotted keys split on `.` for every block except `artifacts.pins`. There the third segment is the **rest of the path**, so the slash inside an artifact key (`<kind>/<id>`) survives:

```bash
shipctl config set artifacts.pins.pattern/role-developer 1.4.2
```

This sets `artifacts.pins["pattern/role-developer"] = "1.4.2"`. Values are parsed leniently: bare `true`/`false`/`null`, integers and floats, and `[a, b, c]` short-form lists are recognised; everything else is stored as a string. Wrap in single or double quotes to force the string form.

## Default `.gitignore`

`shipctl init` (and `shipctl config init`) appends this block to the repo's `.gitignore`, idempotently — entries already present are left alone:

```
# Ship
.ship/cache/
.ship/telemetry-outbox.jsonl
.ship/feedback-drafts/
.ship/state.json
```

| Entry | Why it is ignored |
|-------|-------------------|
| `.ship/cache/` | Artifact bodies are reproducible from the manifest. Committing them on every team is noisy; flip `cache.vcs_tracked: true` and remove this line for air-gapped CI. |
| `.ship/telemetry-outbox.jsonl` | Per-developer buffered events. Gets flushed and emptied by `shipctl telemetry flush`; never useful in a diff. |
| `.ship/feedback-drafts/` | Personal improvement drafts before they are submitted; they shouldn't show up in PRs. |
| `.ship/state.json` | Mutates on every `sync` (`last_sync_at`, `last_manifest_hash`, `outbox_pending_count`). Keeping it out of git stops `config.yml` diffs from picking up co-located churn. |

`.ship/config.yml`, `.ship/shipctl.lock.json`, `.ship/state/` (idempotency markers), and `.ship/inventory.json` are **not** in this block and are intended to be committed.

## Where to next

For "how do I change channel / pin / unpin / opt-in to telemetry / clear the outbox / upgrade a v1 config", see [Operating](/docs/operating). For the operator-first reference for the `lanes:` block — triggers, cookbook, Console UI — see [Lanes](/docs/lanes). For the new per-user memory buckets and the scope ladder that powers them, see [Knowledge Buckets](/docs/knowledge-buckets). For the CLI flag forms behind every command in this page, see [/cli](/cli). For the normative schema and the migration policy, see [RFC-0002](/docs/protocol/rfc-0002-shipctl-config) and [RFC-0007](/docs/protocol/rfc-0007-lanes-and-run-agent); the cache layout that backs `.ship/cache/<kind>/…` is specified in [RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2).
