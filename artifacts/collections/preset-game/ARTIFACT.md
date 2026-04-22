---
artifact_kind: collection
id: preset-game
name: Preset — Game
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 55c7e9973b649c69d287c50af2dfed360fbe2df22df3c8a1d2d596cce8dbce7d
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, game, live-ops, unity, unreal, godot]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for shipping game teams. Wires per-scene asset-budget enforcement on PRs, nightly build-frametime benchmarks, weekly live-ops calendar readiness sweeps, tuning / balance PR review, plus the cross-cutting quality pack so data-tables, art, and code all land behind the same guardrails.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>, tool/engine/<unity|unreal|godot>, tool/scm/git-lfs]
  optional_tools: [tool/engine/unity, tool/engine/unreal, tool/engine/godot, tool/scm/perforce, tool/scm/git-lfs, tool/ci/jenkins, tool/ci/unity-cloud-build, tool/ci/unreal-ugs, tool/qa/testrail, tool/qa/xray, tool/gfx/renderdoc, tool/crash/backtrace, tool/crash/sentry, tool/crash/unity-cloud-diagnostics, tool/l10n/lokalise, tool/l10n/crowdin, tool/l10n/phrase, tool/store/app-store-connect, tool/store/play-console, tool/store/steamworks]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: game
  install_target: documentation/collections/preset-game.md
---

# Preset — Game

## Product shape

A shipping game codebase — Unity, Unreal, Godot, or a custom
engine — where the product is the build plus the live-ops calendar
that keeps it alive. Bounded context is **"the build and its
content"** — playable code, the asset pipeline feeding it, the
data-tables tuning it, and the events scheduled against it.

The preset assumes:

- A game engine project (Unity `Assets/` + `ProjectSettings/`,
  Unreal `Content/` + `Source/`, Godot `project.godot` +
  `addons/`, or a custom engine tree) with a deterministic build
  job.
- An asset pipeline with large-binary tracking — Perforce or
  Git LFS — and a headless content-build step that CI can drive.
- A tracker the game / live-ops producers actually read (Linear /
  Jira / GitHub Issues) — preset lanes open tickets there.
- A nightly or per-PR build capable of running a headless
  benchmark scene on reference hardware.

## Lanes & patterns enabled out of the box

- `pr_review` (`flow-pr-self-review`)
- `daily_standup` (`flow-daily-retro`)
- `tech_debt`
- `code_map`
- `scan-asset-budget` — per-scene texture / mesh / audio /
  shader budgets per platform tier, blocks PRs that regress.
- `scan-build-frametime` — nightly headless frametime + memory
  benchmark vs a rolling 7-day baseline.
- `flow-live-ops-calendar` — weekly readiness sweep over the
  live-ops calendar (branch, loc, rating, store-review,
  telemetry, marketing).
- `role-game-balance-reviewer` — tuning / balance PR reviewer,
  catches power-creep and economy holes in data-tables.
- `scan-dead-code`, `scan-license-deps`, `scan-env-var-catalog`,
  `scan-performance-budget`, `role-designer` — cross-cutting
  quality pack (Wave 1) pre-enabled so engine code, build
  scripts, and tooling benefit from the same guardrails as a
  typical service repo.

## SDLC columns the preset expects

Standard Ship flow plus:

- `Playtest` — between `In review` and `Done`, holds builds
  pending a scheduled playtest session.
- `Cert` — parallel state for builds in console-cert / store-
  review; removed once the platform owner signs off.
- `Soft-launch` — optional column for regional rollouts
  collecting KPI evidence before global ship.

## Label contract (preset-specific)

- `lane:asset-budget` · `lane:frametime` · `lane:liveops` ·
  `lane:balance-review`
- `event:at-risk` · `event:ready` · `event:shipped`
- `content-tag:<event-id>` — applied to PRs contributing to a
  specific live-ops event so the calendar sweep can resolve
  readiness.
- `platform-tier:mobile-low` · `platform-tier:mobile-high` ·
  `platform-tier:console` · `platform-tier:pc` ·
  `platform-tier:switch`
- `tuning-brief-linked` · `unexplained-delta` ·
  `power-creep-risk` · `economy-hole`

Plus the base Ship labels.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user (engine build runner).
- Asset pipeline credentials (Perforce workspace login or
  Git LFS token) scoped to read-only for scans.
- Engine licence / activation token where the engine requires
  seat login on headless runners (Unity licensing server, Unreal
  launcher credentials).
- Crash / telemetry SDK read token (Backtrace API, Sentry auth,
  Unity Cloud Diagnostics) for frametime + crash cross-reference.
- Localization TMS token (Lokalise / Crowdin / Phrase) for the
  live-ops calendar loc-coverage check.
- Store portal tokens (App Store Connect, Play Console,
  Steamworks) for the store-review window check.
- Calendar-source token (Google Calendar API, Notion integration
  secret, or Linear API key) matching the chosen
  `calendar_source` input.

## Recommended addendums

- `addendum-coppa` — when the game targets audiences under 13
  (age-gated content, data-minimisation rules, parental
  consent).
- `addendum-gacha` — when the game ships loot boxes / gacha
  systems subject to drop-rate disclosure laws (JP / KR / CN).
- `addendum-fin` — when real-money trade or currency
  conversion is part of the economy loop.

Compose `preset-regulated` on top when a region's rating /
consent regime pulls the game under SOC2 / HIPAA / PCI scope
(subscription back-end, health / mental-wellness titles).

## Evidence types

- Per-PR asset-budget comment with per-scene × per-tier table.
- Nightly frametime ticket (one per benchmark scene) with
  rolling-baseline delta and GPU-trace link.
- Weekly live-ops readiness digest + per-event ticket with a
  prerequisite checklist.
- Per-PR balance-review comment with outlier deltas + sim KPI
  deltas (when the repo has a balance-sim harness).
- Cross-cutting quality evidence inherited from the Wave 1 pack
  (dead-code tickets, license-deps PR gates, env-var catalog
  tickets, performance-budget PR comments, designer reviews).
