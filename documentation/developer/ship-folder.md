# The `.ship/` folder, in plain language

When you activate a repo in Ship, the platform's local state lives in a small folder at the root: `.ship/`. This folder is a contract — the committed parts are read by humans and CI, the ignored parts are local convenience. Knowing which is which prevents the "why is this checked in?" question from derailing code review. This chapter walks you through what lives where, what gets tracked, and what should never be stored there.

## What's in `.ship/`

The committed pieces form the permanent record of your repo's Ship configuration:

- `.ship/config.yml` — Your repo-level Ship configuration: stack definitions (tracker, CI, agents, language, preset), API channel settings, artifact pins, and telemetry opt-in choices. The schema for this file lives in the CLI itself; if this page and the CLI disagree, the CLI wins.
- `.ship/shipctl.lock.json` — Resolved artifact versions and content hashes, committed when generated. Lock files ensure that rollouts stay stable across the team and across time.
- `.ship/knowledge/*.md` — Repo-level knowledge that Ship mirrors into your workspace knowledge surface (see [importing knowledge](/docs/knowledge/importing)).

The local-only pieces support your workflow without cluttering version control:

- `.ship/cache/` — Fetched artifact bodies, available for offline work and faster local operations.
- `.ship/state.json` — Mutable local state for sync and CLI operations; unique to your machine.
- `.ship/feedback-drafts/*.md` — Draft feedback waiting to be submitted; work-in-progress by definition.

Cache, state, and feedback drafts should match the `.gitignore` patterns set by `shipctl init`. They are never committed.

## What's tracked vs. ignored

Your repository tracks these files:

- `.ship/config.yml`
- `.ship/shipctl.lock.json` (when generated)
- `.ship/knowledge/*.md`

Your repository ignores these:

- `.ship/cache/`
- `.ship/state.json`
- `.ship/feedback-drafts/`

When you open a pull request that changes `.ship/config.yml`, the lock file, or knowledge files, reviewers can see those changes the same way they would review any code change. Configuration decisions, artifact updates, and new knowledge all belong in code review. Local cache and state never do.

## Agent rule files outside `.ship/`

Agent rule files themselves do not live in `.ship/`. Instead, they live at canonical paths that each tool expects: `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`, `.github/copilot-instructions.md`, `.codex/SHIP_API.md`, and others. Ship installs and updates marker-delimited blocks inside these files, allowing your custom notes to sit outside the markers and survive subsequent `shipctl sync` operations. This way, you can add context, examples, or local conventions without losing them when Ship updates its own sections.

The full list of supported agents — markers, install targets, install commands — lives on the [roadmap page](/roadmap), which is also where we name what ships at production depth today (Cursor, Claude Code, Codex, Copilot) versus what we're growing toward.

## What never belongs in `.ship/`

Three categories of things should never live in `.ship/`:

Secrets of any kind. API keys, database passwords, OAuth tokens, and private credentials must never be checked in — not even in a subdirectory of `.ship/`. Instead, use your workspace secret store, repository secrets, or the platform secret manager (see [the secrets chapter](/docs/policies/secrets) for policy and tooling).

Personal preferences that don't scale across the team. Your local editor settings, your debug flags, your build cache preferences — those are local config, not committed config. They belong in `.shipctl config local` or your shell profile, not in `.ship/`.

Generated build artifacts that have nothing to do with Ship. Compiled output, Docker images, node_modules, and other transient build products belong in `dist/`, `build/`, `target/`, or whatever pattern your build system uses. They should be ignored, not stored in `.ship/`.

The `.ship/` folder is small on purpose. Most repos converge on a stable, predictable `.ship/` within the first week of use. If `.ship/` is growing with every commit, something is wrong — either the team is accidentally committing cache or state, or someone is treating the folder as a general dumping ground. Watch for this pattern in code review. A healthy `.ship/` stays focused on configuration, locks, and shared knowledge.
