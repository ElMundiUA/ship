# `shipctl` — the local workbench

The console is for the operator. The CLI is for the engineer. `shipctl` is the command-line tool you run locally for setup, sync, validation, and diagnostics. It does not orchestrate workflows, does not touch workspace state, and does not write to the audit log. Most engineers use four or five `shipctl` commands and ignore the rest. This chapter is about those four or five.

## Daily-use commands

The everyday commands fit in one screen:

- `shipctl doctor` — runs a series of cheap checks against the repo: agent rule files installed, config valid, lock file fresh, network reachable, credentials present. Read it like a triage report; treat its warnings as hints, not as failures.

- `shipctl verify` — heavier than doctor: it validates the repo against the published artefact contract, checks marker blocks for drift, and confirms agent rule files match the current `collection/agent-rules-<id>` artifact.

- `shipctl sync` — pulls the latest artefacts and re-installs marker-delimited blocks in agent rule files. Run after a `git pull` if the team has been bumping artefact pins.

- `shipctl config show` — prints `.ship/config.yml` so you can see what is actually loaded.

- `shipctl config validate` — validates `.ship/config.yml` without printing it. Always run this before committing a hand edit.

The cheap habit is `shipctl doctor` after a `git pull`. It takes seconds and catches most drifts before they become Friday incidents.

## Setup commands

The first-time-in-a-repo commands:

- `shipctl init --copy-rules` — bootstrap a repo with Ship's defaults and the agent rule files for the agents named in config (see the agent matrix chapter).

- `shipctl init --agents cursor,codex,claude-md` — install rule files for specific agents without touching the rest of the config.

After init, run `shipctl doctor`. It should be green. If it is not, fix the warnings before doing anything else; init is the easiest moment to keep the repo clean. A red doctor report at this point usually means the workspace integration is not reachable, or a required credential is missing.

## When something is wrong

The flow when a workflow fails locally is the same one the console suggests:

1. `shipctl doctor` — does the repo look healthy? This catches agent rules, config syntax, lock freshness, and basic connectivity.

2. `shipctl config validate` — is the config file legal? This is the schema authority; if it passes, the config itself is sound.

3. `shipctl sync` — are the artefacts and marker blocks current? Sync pulls the latest versions and re-installs them. If agent rules changed upstream, sync fixes it.

4. If those pass, the problem is upstream — the workspace, an integration, or a routine the CLI does not see. Move to the console.

The CLI is intentionally local. It does not run routines, does not change workspace state, and does not write to the audit log. Anything that mutates shared state happens through the console or the API, not from a developer's terminal. If you find yourself reaching for creative flag combinations to automate something, stop. If the console has a button for it, use the button.

## What `shipctl` is NOT

The CLI is not the operator interface. It does not show Inbox items, does not let you bind a tracker, does not approve a PR, does not change a member's role. Those are console moves. Trying to "automate operator work" from a developer's terminal is the same shape as "automate priority calls from a cron job" — the console exists because some decisions need to be human and shared.

The CLI is not a build tool. It does not compile rule files, does not test agent integrations, and does not dry-run workflows. Those are separate concerns. Ship validation happens at the boundary: when you push to the workspace, when a console operator runs a workflow, when the ingestion pipeline sees new rules. Local validation is about config hygiene and artefact freshness, not about whether your integration will work.

A quiet system does fewer surprising things. It leaves traces you can follow without a séance. `shipctl` leans quiet: it runs cheap checks, prints what it found, and stops. Use it after every `git pull`. Read the warnings. When a workflow fails, run the three checks above in order. For symptom-to-fix lookup, see the troubleshooting chapter. For what to do when the workspace says the repo is out of date, see the bundle updates chapter.
