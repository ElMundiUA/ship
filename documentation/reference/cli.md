# `shipctl` command reference

`shipctl` is your local engineering workbench—a CLI for bootstrapping, diagnosing, and inspecting a Ship-connected repository. The console is for the operator; the CLI is for the engineer. It runs locally for setup, sync, validation, and diagnostics — it does not orchestrate workflows, touch workspace state, or write to the audit log. The two CI-side verbs (`trigger` and `run`) are thin entry-points the seed workflow uses to hand work to the workspace runner. The CLI ships as the `shipctl` binary and via `npx @elmundi/ship-cli <command>`.

## Setup

- **`shipctl init`** — First-time bootstrap of `.ship/` for an existing repository. Creates the config skeleton, installs agent rule files, and preps the local environment. Common flags: `--copy-rules` (install agent rule files), `--agents <list>` (comma-separated agent IDs).

- **`shipctl sync`** — Pull the latest artefact bodies and re-install marker-delimited blocks in agent rule files. Run this after `git pull` if your team has bumped artefact pins. `--lock` writes `.ship/shipctl.lock.json` for reproducible installs.

## Diagnostics

- **`shipctl doctor`** — Fast health check that reads quickly and reports warnings. Treat warnings as hints, not failures; this is your default first move when something feels wrong.

- **`shipctl verify`** — Heavier validation that checks the repo against the published artefact contract and scans marker blocks for drift. Useful flag: `--check rules-markers` to limit the scan to one specific check.

- **`shipctl config show`** — Print the resolved `.ship/config.yml` to stdout, showing computed values after all defaults and inheritance.

- **`shipctl config validate`** — Validate a hand-edited config file before commit, catching schema and reference errors early.

## Knowledge

- **`shipctl knowledge fetch <bucket-slug>`** — Read a workspace bucket's articles and source sync state. This is the agent's read path during a routine run; bucket authoring + ingestion live server-side.

## Catalogue lookups

- **`shipctl pattern`** / **`shipctl tool`** / **`shipctl collection`** — `list`, `show`, `fetch`, or `search` artifact bodies in the catalogue.

- **`shipctl search <query>`** — Vector search across docs and prompt bodies.

## CI entry-point (used by the seed workflow)

- **`shipctl trigger --event schedule`** — Compute due routines from `.ship/config.yml` and claim each schedule window in Ship. The seed workflow (`ship-trigger-schedule.yml`) fires this on cron and pipes due ids into `shipctl run` per routine.

- **`shipctl run --routine <id>`** — Resolve the pattern, fetch a ticket if the pattern declares an FSM stage, fetch the workspace policy preamble, and launch the configured agent runtime. Spawned per routine by `shipctl trigger` in the seed workflow.

## Telemetry and feedback

- **`shipctl telemetry`** — Show or change telemetry opt-in state for the CLI. Default OFF.

- **`shipctl feedback`** — Local markdown drafts; `submit` creates a GitHub issue against a cited catalogue artifact.

## Help and discovery

- **`shipctl --help`** — Print the command list and global flags.
- **`shipctl <cmd> --help`** — Print detailed flags and examples for any specific command.

---

Most engineers use five commands regularly: `init`, `doctor`, `verify`, `sync`, and `config validate` cover the daily needs. The CI verbs (`trigger`, `run`) are wired by the workspace wizard once and rarely touched after that. For deeper context on the CLI's design and workflow, see [the shipctl chapter](/docs/developer/shipctl) in the developer guide.
