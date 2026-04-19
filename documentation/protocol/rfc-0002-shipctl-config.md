---
rfc: 0002
title: ".ship/config.yml schema"
status: Accepted
created: 2026-04-17
---

# RFC-0002 — `.ship/config.yml` schema

## Summary

Every Ship-enabled repository owns a single standalone `.ship/config.yml` at its root. The schema is identical regardless of language, stack, or tracker: the same file drives a TypeScript monorepo, a Python API, a Swift mobile app, or a Rust CLI. `shipctl` reads this file on every command; no Ship configuration lives elsewhere.

## Location

- Canonical path: `<repo-root>/.ship/config.yml`.
- Sibling state file: `<repo-root>/.ship/state.json`. `state.json` holds runtime values that change between `shipctl` invocations (most importantly `last_sync_at` and `last_manifest_hash`) and is `.gitignore`d by default. Keeping it next to `config.yml` instead of inside it preserves diff sanity on the committed config.
- Never embedded in `package.json`, `pyproject.toml`, `Cargo.toml`, `build.gradle`, `Gemfile`, or any other per-language file.
- Never placed under `tooling/`, `config/`, `scripts/`, or similar — `shipctl` does not search for it.
- Exactly one config per repository. Monorepos configure once at the root; per-package overrides are an open question (see below).

Rationale: one obvious home for Ship config keeps onboarding, doctor checks, and agent prompts deterministic. Tool-specific config files are noisy and opinionated; Ship keeps its own surface separate.

## Full schema

```yaml
version: 1
shipctl_min: "0.3.0"
api:
  base_url: "https://ship.elmundi.com"   # override via SHIP_API_BASE
  channel: "stable"                       # stable | edge
  ttl_hours: 24
  offline_ok: true
stack:
  tracker: "linear"                       # linear|jira|github-issues|spreadsheet|none
  ci: "gh-actions"                        # gh-actions|gitlab-ci|buildkite|circleci|azure|manual
  agents: ["cursor", "codex", "claude"]   # list from supported set
  language: "ts"                          # ts|py|go|rust|java|kotlin|swift|multi
  preset: "web-app"                       # web-app|api-backend|mobile-app|cli|monorepo|adoption-minimum
artifacts:
  pins:                                   # pinned versions; sync won't touch
    pattern/cloud-developer: "1.4.2"
    workflow/scheduled-sdlc-lane: "~2.1"
  auto_update: true                       # init/doctor trigger sync automatically
cache:
  vcs_tracked: false                      # if true, .ship/cache/ is committed
telemetry:
  share: false                            # explicit opt-in from shipctl init
  anonymous_id: "uuid-v4"
  scope:
    artifact_usage: true
    improvement_drafts: true
    errors: false
```

## Field reference

### `version` (required)

Integer, currently `1`. Missing `version` is a hard error. Bumping the schema is governed by the migration policy below; the field exists to make that migration explicit.

### `shipctl_min` (required)

Minimum `shipctl` semver that understands this config. `shipctl` with a lower version refuses to operate and prints an upgrade hint. Allows the community to evolve the CLI without breaking older installations silently.

### `api`

| Key           | Type   | Default                          | Notes                                                                            |
|---------------|--------|----------------------------------|----------------------------------------------------------------------------------|
| `base_url`    | string | `https://ship.elmundi.com`       | Root URL for all HTTP calls. Env `SHIP_API_BASE` overrides.                      |
| `channel`     | enum   | `stable`                         | `stable` or `edge`. Env `SHIP_CHANNEL` overrides.                                |
| `ttl_hours`   | int    | `24`                             | Cache freshness window (see RFC-0001 fetch policy).                              |
| `offline_ok`  | bool   | `true`                           | When `true`, cached artifacts may serve network failures with a warning.         |

### `stack`

All `stack.*` values are enums. Typos MUST fail validation with a message listing known values. New values are added by publishing adapter artifacts (see RFC-0004), not by patching the schema.

| Key        | Allowed values                                                                                   |
|------------|--------------------------------------------------------------------------------------------------|
| `tracker`  | `linear`, `jira`, `github-issues`, `azure-boards`, `clickup`, `spreadsheet`, `none`              |
| `ci`       | `gh-actions`, `gitlab-ci`, `buildkite`, `circleci`, `azure-pipelines`, `jenkins`, `manual`       |
| `agents`   | subset (possibly empty) of: `cursor`, `codex`, `claude`, `claude-md`, `agents-md`, `aider`, `copilot`, `cline`, `continue`, `windsurf`, `zed`, `gemini`, `opencode`, `cursor-cloud`. `[]` means "no agents installed yet" — pure human authorship; `shipctl init` emits a single warning but proceeds. |
| `language` | `ts`, `py`, `go`, `rust`, `java`, `kotlin`, `swift`, `multi`                                     |
| `preset`   | `web-app`, `api-backend`, `mobile-app`, `cli`, `monorepo`, `adoption-minimum`                    |

`stack.agents` is a list, not a single value, because mixed-agent usage is common (a team on Cursor with a CI-side Codex, or Claude in review and Cursor in authoring).

### `artifacts`

| Key           | Type              | Notes                                                                                     |
|---------------|-------------------|-------------------------------------------------------------------------------------------|
| `pins`        | map<string,string>| Keys are `<kind>/<id>`. Values are semver ranges per RFC-0001 pinning rules.              |
| `auto_update` | bool              | When `true`, `shipctl init` and `shipctl doctor` run `shipctl sync` without prompting.    |

`pins` entries are validated against the published manifest: pinning a non-existent artifact is a hard error on save/validate.

### `cache`

| Key           | Type | Notes                                                                                          |
|---------------|------|------------------------------------------------------------------------------------------------|
| `vcs_tracked` | bool | Default `false`. When `true`, `.ship/cache/` is committed to git; useful for air-gapped CI.    |

### `telemetry`

Fully specified in RFC-0003. The config-side surface is:

| Key                          | Type   | Default  | Notes                                                      |
|------------------------------|--------|----------|------------------------------------------------------------|
| `share`                      | bool   | `false`  | Master switch. Default OFF.                                |
| `anonymous_id`               | string | random   | UUID v4. Generated once by `shipctl init`.                 |
| `scope.artifact_usage`       | bool   | `true`   | Emit `artifact.fetch` / `artifact.use` / `artifact.sync`.  |
| `scope.improvement_drafts`   | bool   | `true`   | Emit `feedback.submit` payloads.                           |
| `scope.errors`               | bool   | `false`  | Emit `doctor.result` error telemetry.                      |

## Commands

| Command                                         | Behavior                                                                           |
|-------------------------------------------------|------------------------------------------------------------------------------------|
| `shipctl config get <key>`                      | Prints the value at the dotted path, resolving env/CLI precedence.                 |
| `shipctl config set <key> <value>`              | Writes the YAML value in place, preserving comments; validates before saving.      |
| `shipctl config unset <key>`                    | Removes the key; reverts to schema default on next read.                           |
| `shipctl config validate`                       | Parses the file, enforces enums and required fields, exits non-zero on failure.    |
| `shipctl config show [--effective]`             | Prints the config (`--effective` resolves env + CLI overrides).                    |

Example usage:

```bash
shipctl config get api.channel
shipctl config set api.channel edge
shipctl config set artifacts.pins.pattern/cloud-developer 1.4.2
shipctl config unset artifacts.pins.workflow/scheduled-sdlc-lane
shipctl config validate
shipctl config show --effective
```

Dotted paths for lists index by element name where possible (the `pins` map is indexed by its string key, which includes the slash). When there is no natural key, a numeric index is used (e.g. `stack.agents.0`).

## Precedence

From lowest to highest, the effective value for any field is:

1. Schema default.
2. `.ship/config.yml` value.
3. Environment variable (if one is defined for that field).
4. CLI flag (if the current command exposes one).

Standard environment overrides:

| Env variable          | Overrides              |
|-----------------------|------------------------|
| `SHIP_API_BASE`       | `api.base_url`         |
| `SHIP_CHANNEL`        | `api.channel`          |
| `SHIP_TTL_HOURS`      | `api.ttl_hours`        |
| `SHIP_OFFLINE_OK`     | `api.offline_ok`       |
| `SHIP_TELEMETRY`      | `telemetry.share`      |
| `SHIP_CACHE_DIR`      | cache root (advanced)  |

Unknown `SHIP_*` variables are ignored; they do not cause failures.

## Secrets

- **No secrets in `config.yml`.** The file is committed to git; secret material never belongs there.
- **Secret names only.** When a preset or adapter needs a secret (for example, `LINEAR_API_KEY`), `shipctl` renders a matching entry into `.env.example` during bootstrap and references the name in docs. The actual value lives in `.env.local` or the platform's secret store.
- `shipctl doctor` verifies that every secret name referenced by the active stack has a matching line in `.env.example`; it does not read `.env.local`.

## Validation

`shipctl config validate` runs in three phases:

1. **Shape**: parse YAML, reject unknown types (for example, a list where a string is expected).
2. **Enums**: every `stack.*` enum is checked against the canonical list. A typo produces a message like:
   ```
   stack.tracker: "linerar" is not valid.
   expected one of: linear, jira, github-issues, azure-boards, clickup, spreadsheet, none
   ```
3. **Cross-field**: `artifacts.pins.*` artifacts must exist; `stack.preset` must be compatible with `stack.ci`/`stack.tracker` per the active adapter matrix (see RFC-0004 compatibility).

`shipctl init` runs `validate` before exit. `shipctl doctor` runs it on every invocation.

## Schema versioning

`version: 1` is the only schema today. There are no upgrade migrations to define yet — once schema-breaking field renames or moves arrive in a future RFC, this section spells out the upgrade flow (interactive diff, opt-in confirm, comments preserved). Until then, validation simply rejects unknown `version:` values.

## Gitignore defaults

Recommended baseline produced by `shipctl init`:

```
# Ship
.ship/cache/
.ship/telemetry-outbox.jsonl
.ship/feedback-drafts/
```

Tracked by default:

```
.ship/config.yml
.ship/inventory.json   # produced by shipctl doctor; useful in PRs
```

When `cache.vcs_tracked: true`, `.ship/cache/` is removed from `.gitignore` and the cache directory is committed. This is the air-gapped CI escape hatch; most teams leave it off.

## Examples

### Minimal config

The smallest valid config a fresh `shipctl init --yes --preset adoption-minimum` produces:

```yaml
version: 1
shipctl_min: "0.3.0"
api:
  base_url: "https://ship.elmundi.com"
  channel: "stable"
  ttl_hours: 24
  offline_ok: true
stack:
  tracker: "none"
  ci: "manual"
  agents: ["cursor"]
  language: "multi"
  preset: "adoption-minimum"
artifacts:
  pins: {}
  auto_update: true
cache:
  vcs_tracked: false
telemetry:
  share: false
  anonymous_id: "9b6d...-v4"
  scope:
    artifact_usage: true
    improvement_drafts: true
    errors: false
```

### Web-app config on Linear + GitHub Actions

```yaml
version: 1
shipctl_min: "0.3.0"
api:
  base_url: "https://ship.elmundi.com"
  channel: "stable"
  ttl_hours: 12
  offline_ok: true
stack:
  tracker: "linear"
  ci: "gh-actions"
  agents: ["cursor", "codex"]
  language: "ts"
  preset: "web-app"
artifacts:
  pins:
    pattern/cloud-developer: "1.4.2"
    workflow/scheduled-sdlc-lane: "~2.1"
    collection/web-application: "^3.0.0"
  auto_update: true
cache:
  vcs_tracked: false
telemetry:
  share: true
  anonymous_id: "11a0...-v4"
  scope:
    artifact_usage: true
    improvement_drafts: true
    errors: true
```

### Regulated / air-gapped config

```yaml
version: 1
shipctl_min: "0.3.0"
api:
  base_url: "https://ship.internal.corp"
  channel: "stable"
  ttl_hours: 168   # weekly
  offline_ok: true
stack:
  tracker: "jira"
  ci: "jenkins"
  agents: ["codex"]
  language: "java"
  preset: "api-backend"
artifacts:
  pins:
    pattern/cloud-developer: "1.4.2"
    collection/addendum-pharma: "1.0.0"
  auto_update: false   # reviewed upgrades only
cache:
  vcs_tracked: true    # cache is committed
telemetry:
  share: false
  anonymous_id: "00000000-0000-0000-0000-000000000000"
  scope:
    artifact_usage: false
    improvement_drafts: false
    errors: false
```

## `shipctl init`

The init command is how most adopters produce a `.ship/config.yml`. It runs four steps:

1. **Detect.** Scan the repo for signals (see RFC-0004 detect). Propose a `stack` block with defaults.
2. **Confirm.** Interactive prompts for `tracker`, `ci`, `agents`, `language`, `preset`, `telemetry.share`. Non-interactive mode uses flags.
3. **Generate.** Write `.ship/config.yml`. Comments are inserted exactly as documented above; `shipctl` keeps them in place on subsequent `config set` edits.
4. **Sync.** If `artifacts.auto_update=true`, run `shipctl sync` immediately; otherwise print the command to run later.

Non-interactive flags mirror the config keys:

```bash
shipctl init --yes \
  --tracker linear --ci gh-actions \
  --agents cursor,codex --language ts --preset web-app \
  --telemetry=off
```

## `shipctl doctor`

`shipctl doctor` is the day-two health check. Relevant to the config:

- Confirms `version` and `shipctl_min` are satisfied by the installed CLI.
- Confirms every `stack.*` enum is known.
- Confirms every `artifacts.pins.*` artifact exists on the configured channel.
- Confirms `telemetry.anonymous_id` is a UUID v4 and matches the stored format.
- Confirms `.ship/cache/` exists and is writable (or is tracked in git when `cache.vcs_tracked=true`).
- Confirms `.env.example` references every secret required by the current stack adapters.

Exit is `0` on pass, non-zero when any check fails. A `warn` does not flip exit code; it is printed in yellow.

## Rules

- `version: 1` is mandatory. A missing or unknown `version` aborts every command.
- **Unknown keys warn, do not fail.** Forward compatibility: a newer `shipctl` may write keys older clients do not know; older clients print a single warning per command run and otherwise proceed.
- **Unknown enum values fail.** If `stack.ci: gitlabci` is written instead of `gitlab-ci`, `shipctl` refuses to run and lists the valid enum members.
- **Comments are preserved.** `shipctl config set` must keep existing YAML comments intact; this is why the file is YAML and not JSON.
- **Atomic writes.** Writes go to `.ship/config.yml.tmp` and are renamed into place; a SIGKILL mid-edit does not corrupt the file.
- **No interpolation.** The file is literal YAML. `shipctl` does not expand `${ENV}` inside values; environment overrides are per-key (see Precedence).

## Failure scenarios

| Scenario                                                | `shipctl` behavior                                                     |
|---------------------------------------------------------|------------------------------------------------------------------------|
| Missing `.ship/config.yml`                              | `shipctl init` prompts to create; all other commands exit `10`.        |
| `version:` missing or not `1`                           | Exit `10`. Hint: bump the file or downgrade `shipctl`.                 |
| `shipctl_min` higher than installed version             | Exit `15`. Hint: upgrade the CLI.                                      |
| YAML parse error                                        | Exit `10` with line/column and excerpt.                                |
| Unknown enum value                                      | Exit `10` listing valid values.                                        |
| Unknown top-level key                                   | Warn, continue.                                                        |
| `artifacts.pins.*` references a non-existent artifact   | Exit `13` on `validate` and on any fetch attempting to use the pin.    |
| `stack.agents` empty list                               | Allowed. Warn once ("no agents installed; consider `shipctl init --agents <id>`") and proceed. Validation passes. |
| `telemetry.anonymous_id` missing                        | `shipctl` generates one on next run and writes it back (non-fatal).    |

## Inventory file

Alongside `.ship/config.yml`, `shipctl doctor` produces `.ship/inventory.json` — a machine-readable snapshot of what the current stack looks like to Ship:

```json
{
  "generated_at": "2026-04-17T10:05:13Z",
  "shipctl_version": "0.3.2",
  "config": {
    "preset": "web-app",
    "tracker": "linear",
    "ci": "gh-actions",
    "agents": ["cursor", "codex"]
  },
  "adapters": [
    { "kind": "tool", "id": "gh-actions", "version": "1.2.0", "detected": true },
    { "kind": "tool", "id": "linear", "version": "2.3.0", "detected": true },
    { "kind": "tool", "id": "cursor", "version": "1.1.0", "detected": true }
  ],
  "checks": [
    { "name": "config.schema", "status": "pass" },
    { "name": "pins.resolvable", "status": "pass" },
    { "name": "secrets.env_example", "status": "warn", "detail": "LINEAR_API_KEY missing from .env.example" }
  ]
}
```

Inventory is safe to commit; it contains no secrets and no path information beyond what is in `.ship/config.yml`. Reviewers can diff it in PRs to see when the stack drifts.

## Open questions

- **Multi-workspace / monorepo configs.** Do we need `workspaces:` with per-package overrides (different trackers or agents per package), or is a single root config good enough? Current assumption: single root config; per-package overrides defer to a follow-up RFC.
- **Config schema versions and `shipctl` releases.** How tightly do we couple schema `version` bumps to CLI major versions? A schema bump per CLI major is clean but noisy; a shared version across schema and CLI conflates two lifecycles.
- **Secret helpers.** Should `shipctl` offer a thin "read `.env.local` / platform secret manager" integration, or keep that out of scope entirely?
- **Config locking.** Should `shipctl` take an advisory lock on `.ship/config.yml` during edits to prevent two concurrent CLIs from racing each other?

## Changelog

- 2026-04-17: Initial draft.
- 2026-04-17: Promoted `.ship/state.json` (sibling of `config.yml`, holds `last_sync_at` / `last_manifest_hash`) from open question to main body; explicitly allowed `stack.agents: []` with a warning instead of exit `10`.
