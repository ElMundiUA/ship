# Configuration

This page is the field reference for `.ship/config.yml` and a directory listing for everything `shipctl` writes under `.ship/`. It tells you **what each field is** and **what it controls**; for command flags see [/cli](/cli), for "how do I…" recipes see [Operating](/docs/operating), and for the vocabulary every field name assumes (artifact, kind, channel, pin, install_target, …) read [Concepts](/docs/concepts) first. The normative spec is [RFC-0002](/docs/protocol/rfc-0002-shipctl-config); when this page and the RFC disagree, the implementation in `cli/lib/config/` wins.

## On-disk layout (`.ship/`)

`.ship/` always lives at the repository root. `shipctl` walks upward from the current working directory looking for `.ship/config.yml`; nothing else makes a directory "the ship root". Everything in the table below is relative to that root.

| Path | Tracked? | Purpose | Created by | Cleaned by |
|------|----------|---------|------------|------------|
| `.ship/config.yml` | committed | The single config file. Owns `version`, `api`, `stack`, `artifacts.pins`, `cache`, `telemetry`. | `shipctl init` (or `shipctl config init`) | never (manual delete) |
| `.ship/state.json` | gitignored | Mutable runtime state: `last_sync_at`, `last_manifest_hash`, `outbox_pending_count`. Kept next to `config.yml` to keep the committed file diff-clean. | `shipctl init`, written by `shipctl sync` | manual delete; recreated on next `sync` |
| `.ship/cache/` | gitignored by default · committed when `cache.vcs_tracked: true` | Root of the artifact body cache. Holds one folder per cached `<kind>/<id>@<version>`. | `shipctl init` (creates `.gitkeep`); populated by `shipctl sync` | `shipctl sync` removes nothing on its own; manual delete forces a re-fetch |
| `.ship/cache/<kind>/<sanitized-id>@<version>/ARTIFACT.md` | follows `.ship/cache/` | The cached artifact body. `<kind>` is one of `pattern`, `tool`, `workflow`, `collection`, `doc`. Slashes in `<id>` are written as `__`. | `shipctl sync` | overwritten on next `sync` for the same version |
| `.ship/cache/<kind>/<sanitized-id>@<version>/.meta.json` | follows `.ship/cache/` | Sidecar metadata: `kind`, `id`, `version`, `content_sha256`, `updated_at`, `source_url`, `fetched_at`, `channel`. Used by `shipctl verify` to detect drift. | `shipctl sync` | overwritten with the body |
| `.ship/inventory.json` | committed (safe — no secrets) | Snapshot of `shipctl doctor` findings: detected adapters, declared stack, check results. Reviewers diff it on PRs to spot stack drift. | `shipctl doctor --write-inventory` | overwritten on next `doctor` |
| `.ship/telemetry-outbox.jsonl` | gitignored | One JSON envelope per line, buffered when `telemetry.share: true`. Flushed by `shipctl telemetry flush`. | `shipctl sync`, `shipctl feedback`, any command that emits an event | `shipctl telemetry clear` (or auto-cleared after a successful flush) |
| `.ship/feedback-drafts/*.md` | gitignored | One draft per file (`<timestamp>-<kind>-<id>.md`), front-matter + Markdown body. | `shipctl feedback draft` | `shipctl feedback submit` moves the file to `.ship/feedback-drafts/sent/`; manual delete otherwise |
| `.ship/feedback-drafts/sent/` | gitignored (under `.ship/feedback-drafts/`) | Archive of submitted drafts. | `shipctl feedback submit` | manual |
| `.ship/playbooks/<id>@<version>.md` | committed (operator's call) | Optional copy of a playbook collection (e.g. `adoption-playbook`) materialised next to the repo. | `shipctl init --copy-playbook` | manual |
| `SHIP_BOOTSTRAP_PLAN.md` | committed (operator's call) | Markdown summary of what `shipctl init --bootstrap` planned: chosen stack, recommended tools and secrets, TODO checklist. Lives in the **repo root**, not under `.ship/`. | `shipctl init --bootstrap` | overwritten on next `--bootstrap` run |

`shipctl` never writes anywhere else under your repo. Anything else with the `# ship-managed` marker (e.g. agent rules at `.cursor/rules/ship-artifacts-protocol.mdc`, `AGENTS.md` blocks, `.github/workflows/ship-pilot.yml`) is owned by `shipctl init --copy-rules` / `--bootstrap` — those targets are documented in [Concepts → install_target](/docs/concepts) and [/agent-matrix](/docs/agent-matrix).

## `.ship/config.yml` schema

Top-level keys recognised by `cli/lib/config/schema.mjs`:

| Block | Purpose |
|-------|---------|
| `version` | Schema version. Currently `1`. |
| `shipctl_min` | Minimum `shipctl` semver that understands this file. |
| `api` | Where the methodology API lives, which channel to pull from, freshness window, offline behaviour. |
| `stack` | The four-axis description of the repo: tracker, CI, agents, language, preset. |
| `artifacts` | Version pins per artifact, plus the `auto_update` switch. |
| `cache` | Whether `.ship/cache/` is committed. |
| `telemetry` | Anonymous-usage opt-in, anonymous id, scope toggles. |

Unknown top-level keys produce a single warning per command and are otherwise preserved on write (forward-compat). Unknown values for an enum field are a hard error.

### `version` and `shipctl_min`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | `1` | Schema version. Anything other than `1` aborts every command (exit `10`). The field exists so a future schema bump can be migrated explicitly. |
| `shipctl_min` | string | `"0.3.0"` | Minimum `shipctl` semver that understands this file. A CLI older than `shipctl_min` refuses to operate and prints an upgrade hint. |

```yaml
version: 1
shipctl_min: "0.3.0"
```

### `api`

Controls how `shipctl` reaches the methodology API and how aggressively it caches.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_url` | URL string | `"https://ship.elmundi.com"` | Root URL for `/manifest` and `/fetch`. Must parse as a URL or validation fails. |
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

### `artifacts`

Pins (versions you do not want `sync` to drift) and the `auto_update` switch.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pins` | map<string,string> | `{}` | Keys are `<kind>/<id>` (the slash matters; `kind` ∈ `pattern`, `tool`, `workflow`, `collection`, `doc`). Values are an exact semver (`1.4.2`), a major-only prefix (`1`, `1.2`), or a range (`^2.0.0`, `~2.1`). `sync` skips an entry whose upstream version no longer satisfies the pin (counted as `skipped_pin` in the summary). |
| `auto_update` | bool | `true` | When true, `shipctl init` and `shipctl doctor` may run `shipctl sync` without asking. |

There is no `artifacts.disabled` field today; the way to opt out of an artifact is to remove it from the pins map and let `sync` ignore it (only pinned, cached, agent-rules, and preset entries are considered "desired"). Pinning an `<id>` that does not exist on the configured channel is a hard error on `validate` and on any `sync` that tries to use it.

```yaml
artifacts:
  pins:
    pattern/cloud-developer: "1.4.2"
    tool/methodology-api: "~2.1"
    collection/web-application: "^3.0.0"
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

## Resolution order

The effective value for any field is the highest-precedence layer that defines it:

1. Schema default (from `DEFAULT_CONFIG()` in `cli/lib/config/schema.mjs`).
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
| `SHIP_API_BASE` | `api.base_url` | `sync`, `verify`, `feedback`, `telemetry`, the global `--base-url` default |
| `SHIP_CHANNEL` | `api.channel` | `sync` |
| `SHIP_TELEMETRY` | forces `telemetry.share` off when set to `false` | the telemetry outbox |
| `SHIP_API_TOKEN` | adds an `Authorization: Bearer …` header | every HTTP call |
| `SHIP_REPO` | path to a local Ship monorepo for offline `list`/`show`/`fetch` of catalog items | `patterns`, `tools`, `workflows`, `collections`, `manifest-catalog` |
| `SHIP_DEBUG` | when `1`, prints debug lines from the telemetry outbox and the feedback submitter to stderr | telemetry, feedback |

`SHIP_TTL_HOURS`, `SHIP_OFFLINE_OK`, and `SHIP_CACHE_DIR` appear in RFC-0002 but are not yet wired in the CLI; setting them today has no effect. Unknown `SHIP_*` variables are silently ignored.

## Validation

`shipctl config validate` parses `.ship/config.yml` and runs the validator from `cli/lib/config/schema.mjs`. Three things happen:

1. **Shape**: the file must be a YAML mapping; each block must be the expected JSON-ish type (object vs array vs scalar).
2. **Enums**: every `api.channel`, `stack.tracker`, `stack.ci`, `stack.language`, `stack.preset`, `stack.agents[*]` is checked against its frozen list.
3. **Cross-field**: `artifacts.pins` keys match `<kind>/<id>` and values look like a semver or range; `telemetry.anonymous_id` is a UUID v4 (required when `telemetry.share: true`).

Exit codes used by the validator and by every command that runs validation as a side-effect (`init`, `sync`, `doctor`):

| Exit | Meaning |
|------|---------|
| `0` | Config is valid. Warnings (unknown keys) may still be on stderr. |
| `1` | Bad CLI invocation (unknown flag value, missing argument). |
| `10` | Config rejected: missing/wrong `version`, missing required block, type mismatch, unknown enum value. Also raised when `.ship/config.yml` is missing entirely. |
| `20` | Manifest fetch failed during `sync`. |

Common validator messages, with the field they point at:

- `version: expected 1, got "2"` → bump `shipctl` instead of editing the version by hand.
- `api.base_url: not a valid URL (…)` → fix the scheme/host or unset the field to take the default.
- `stack.tracker: "linerar" is not valid. Expected one of: linear, jira, github-issues, azure-boards, clickup, spreadsheet, none` → fix the typo.
- `artifacts.pins["pattern/cloud-developer"]: value must be a semver or range (got "v1.4.2")` → drop the leading `v`.
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

Dotted keys split on `.` for every block except `artifacts.pins`. There the third segment is the **rest of the path**, so the slash inside an artifact key (`<kind>/<id>`) survives:

```bash
shipctl config set artifacts.pins.pattern/cloud-developer 1.4.2
```

This sets `artifacts.pins["pattern/cloud-developer"] = "1.4.2"`. Values are parsed leniently: bare `true`/`false`/`null`, integers and floats, and `[a, b, c]` short-form lists are recognised; everything else is stored as a string. Wrap in single or double quotes to force the string form.

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

`.ship/config.yml` and `.ship/inventory.json` are **not** in this block and are intended to be committed.

## Where to next

For "how do I change channel / pin / unpin / opt-in to telemetry / clear the outbox", see [Operating](/docs/operating). For the CLI flag forms behind every command in this page, see [/cli](/cli). For the normative schema and the migration policy if `version` ever bumps, see [RFC-0002](/docs/protocol/rfc-0002-shipctl-config); the cache layout that backs `.ship/cache/<kind>/…` is specified in [RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2).
