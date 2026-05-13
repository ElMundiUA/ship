# @elmundi/ship-cli

`shipctl` is the developer workbench for a Ship-connected repo. The console is for the operator; the CLI is for the engineer. It runs locally for setup, sync, validation, and diagnostics — it does not orchestrate workflows, touch workspace state, or write to the audit log. The customer-side CI uses `shipctl trigger` (and the trigger-spawned `shipctl run`) as a thin entry-point that hands work to the workspace runner.

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
- read workspace knowledge buckets from inside an agent run;
- draft and submit feedback on catalog artifacts.

The product setup path is workspace → repo → tracker → knowledge → dashboard/Inbox, all in the console wizard. The CLI does not duplicate that flow.

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
| `shipctl doctor` | Cheap repo health checks: agent rule files installed, config valid, lock fresh, network reachable. |
| `shipctl verify` | Heavier post-adoption checks (artefact contract, marker drift, network/provider reachability). |
| `shipctl sync` | Refresh catalog artifacts into `.ship/cache/`; `--lock` writes a lockfile. |
| `shipctl config` | `show / validate / get / set / init / path` for `.ship/config.yml`. |
| `shipctl init` | Bootstrap `.ship/`, fetch artifacts, install agent rules in an existing repo. |
| `shipctl pattern\|tool\|collection` | List, show, fetch, or search artifact bodies. |
| `shipctl search` | Vector search over docs + prompts. |
| `shipctl knowledge fetch` | Read a workspace bucket's articles + sync state (the agent's read path). |
| `shipctl trigger` | CI entry-point: compute due routines and claim each schedule window in Ship. |
| `shipctl run` | Spawned per routine by `shipctl trigger`: resolve pattern, fetch a ticket if FSM-staged, launch the agent runtime. |
| `shipctl feedback` | Local markdown drafts; submit creates a GitHub issue against a cited artifact. |
| `shipctl telemetry` | Opt-in usage telemetry controls (default OFF). |

Run `shipctl help` and `shipctl <command> --help` for the exact flag surface.

## Configuration

The CLI reads `.ship/config.yml` from the repo root. It records stack hints, API settings, artifact pins, telemetry preference, cache behavior, and `process.routines` wiring.

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

Catalog commands read `artifacts/**/ARTIFACT.md` directly when run inside this repo. Search still uses the configured HTTP API.
