# `shipctl` command reference

`shipctl` is your local engineering workbench—a CLI for bootstrapping, diagnosing, and orchestrating Ship workflows in your repository. It runs entirely on your machine; it does not mutate workspace state on its own. The CLI ships as the `shipctl` binary and via `npx @elmundi/ship-cli <command>`. This page catalogues every command grouped by what you're trying to do, so you can scan and find the verb you need.

## Setup

- **`shipctl init`** — First-time bootstrap of `.ship/` configuration for a repository. Creates the config skeleton, installs agent rule files, and preps the local environment. Common flags: `--copy-rules` (install agent rule files), `--agents <list>` (comma-separated agent IDs).

- **`shipctl bootstrap`** — Internal-leaning sibling of `init`; primarily used during CI provisioning by teams that prefer explicit control over initialization steps.

- **`shipctl sync`** — Pull the latest artefact bodies and re-install marker-delimited blocks in agent rule files. Run this after `git pull` if your team has bumped artefact pins.

## Diagnostics

- **`shipctl doctor`** — Fast health check that reads quickly and reports warnings. Treat warnings as hints, not failures; this is your default first move when something feels wrong.

- **`shipctl verify`** — Heavier validation that checks the repo against the published artefact contract and scans marker blocks for drift. Useful flag: `--check rules-markers` to limit the scan to one specific check.

- **`shipctl config show`** — Print the resolved `.ship/config.yml` to stdout, showing computed values after all defaults and inheritance.

- **`shipctl config validate`** — Validate a hand-edited config file before commit, catching schema and reference errors early.

## Knowledge and docs

- **`shipctl knowledge fetch`** — Pull workspace knowledge into the local cache for offline work or context priming. Other knowledge subcommands exist; use `shipctl knowledge --help` for the full list.

- **`shipctl docs`** — Interact with workspace documentation. Mirrors content into and out of the local repository.

## Catalogue lookups

- **`shipctl patterns`** — List or inspect patterns in the current artefact catalogue, showing available templates and their metadata.

- **`shipctl search <query>`** — Search the catalogue for patterns, tools, collections, and other artefacts by name or description.

- **`shipctl manifest-catalog`** — Print the manifest of the current cached catalogue. The right answer when you want to see "what does the workspace think it has?"

## Authoring

- **`shipctl new <kind>`** — Scaffold a new artefact (a pattern, tool, collection, etc.) into `artifacts/`. Pre-fills the frontmatter and creates a starter template.

## Process and routines (developer-side)

- **`shipctl process`** — Interact with the per-repo process model from the CLI. Most operators use `/process` in the console; the CLI version is for scripting and CI integration.

- **`shipctl lanes`** — Legacy inspector and alias. Use `process` instead in new code.

- **`shipctl kickoff <routine>`** — Start a routine run from the CLI. Useful in CI hooks and automated triggers.

- **`shipctl trigger <event>`** — Fire an event-driven routine manually, passing the event name as an argument.

- **`shipctl run <routine>`** — Generic routine invocation with broader flag surface. Closer to `kickoff` but with more customization options.

## Webhooks and callbacks

- **`shipctl callback`** — Test the webhook callback path against a local listener. Use this when wiring a custom webhook integration to verify HMAC signing and payload shape.

## Maintenance

- **`shipctl migrate`** — Schema migration helper for `.ship/config.yml` when upgrading to a new major CLI version.

- **`shipctl telemetry`** — Show or change telemetry opt-in state for the CLI.

- **`shipctl feedback`** — List or submit local feedback drafts saved under `.ship/feedback-drafts/`.

## Help and discovery

- **`shipctl --help`** — Print the command list and global flags.

- **`shipctl <cmd> --help`** — Print detailed flags and examples for any specific command.

---

Most teams use four to six commands regularly: `init`, `doctor`, `verify`, `sync`, and `config validate` cover 90% of daily needs. The rest exist for specific moments—feedback submission, new artefact scaffolding, CI integration, and schema migration. Don't memorize the list; use `--help`. For deeper context on the CLI's design and workflow, see [the shipctl chapter](/docs/developer/shipctl) in the developer guide.
