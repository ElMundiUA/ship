# @elmundi/ship-cli

`shipctl` is the developer workbench for Ship. Product owners should start in the console and docs; use the CLI when you need local repo setup, config validation, artifact sync, agent rule installation, or reproducible diagnostics.

Published package: `@elmundi/ship-cli`. Binary: `shipctl`.

## Requirements

- Node.js 20+

## Install

```bash
npm install -g @elmundi/ship-cli
# or one-off
npx @elmundi/ship-cli help
```

## When to use the CLI

Use `shipctl` to:

- create or validate `.ship/config.yml`;
- install versioned agent rules into files such as `AGENTS.md`, `CLAUDE.md`, or `.cursor/rules/*.mdc`;
- sync patterns, tools, and collections into `.ship/cache/`;
- verify local repo wiring in CI or before a PR;
- inspect detected stack signals with `doctor`;
- fetch catalog or docs content for agents;
- draft and submit feedback on artifacts;
- run technical routines where the repo-level workflow requires it.

Do not use the CLI as the main product onboarding story. The product setup path is workspace → repo → tracker → knowledge → dashboard/Inbox.

## First local setup

Preview before writing:

```bash
npx @elmundi/ship-cli init --dry-run
```

Apply only after the preview looks right:

```bash
npx @elmundi/ship-cli init --yes \
  --agents cursor,codex \
  --tracker github-issues \
  --ci gh-actions \
  --preset web-app \
  --copy-rules
```

Then check the repo:

```bash
shipctl verify
```

## Core commands

| Command | Use |
| --- | --- |
| `shipctl init` | Create config, infer stack, sync selected artifacts, optionally install agent rules. |
| `shipctl doctor` | Inspect repo signals without changing files unless `--write-inventory` is passed. |
| `shipctl verify` | Check config, cache, agent rules, generated files, and optional network/provider reachability. |
| `shipctl sync` | Refresh catalog artifacts into `.ship/cache/`; `--lock` writes a lockfile. |
| `shipctl config` | Show, validate, get, and set `.ship/config.yml` fields. |
| `shipctl pattern|tool|collection` | List, show, fetch, or search artifact bodies. |
| `shipctl docs fetch` | Fetch documentation content for agent context. |
| `shipctl knowledge init` | Seed starter `.ship/knowledge/*.md` files where supported. |
| `shipctl feedback` | Draft and submit artifact or docs feedback. |
| `shipctl telemetry` | Opt-in usage telemetry controls. |

Run `shipctl help` and `shipctl <command> --help` for the exact flag surface.

## Configuration

The CLI reads `.ship/config.yml` from the repo root. It records stack hints, API settings, artifact pins, telemetry preference, cache behavior, and technical routine wiring.

See [`../documentation/configuration.md`](../documentation/configuration.md) for the maintained field reference.

## Agent rules

`shipctl init --copy-rules` installs marker-delimited blocks into the selected agent targets. Keep custom text outside the markers so re-runs can safely refresh the Ship-owned block.

Typical targets include:

- `.cursor/rules/*.mdc`
- `AGENTS.md`
- `CLAUDE.md`
- `.codex/*`
- `.github/copilot-instructions.md`

Use [`../documentation/agent-matrix.md`](../documentation/agent-matrix.md) for the current agent id table.

## Artifacts

Ship distributes versioned artifacts:

- `pattern` — reusable procedures and prompt bodies;
- `tool` — integration and adapter descriptions;
- `collection` — presets, agent rules, and addenda.

The CLI can read artifacts directly from this monorepo during development or fetch them from the configured Ship API in a product repo.

## Verification in CI

Use `shipctl verify` as the gate. For offline CI, commit the required cache or narrow checks to local categories:

```bash
shipctl verify --no-network
```

Warnings do not fail the command by default; failures do. Treat warnings as review prompts, not as proof the setup is broken.

## Telemetry and feedback

Telemetry is opt-in and off by default. Feedback drafts stay local until explicitly submitted.

```bash
shipctl telemetry off
shipctl feedback draft --kind pattern --id role-developer
```

## Development in this repo

From the repository root:

```bash
npm run shipctl -- help
npm test --prefix cli
```

Catalog commands read `artifacts/**/ARTIFACT.md` directly when run inside this repo. Search and docs fetch still use the configured HTTP API.
