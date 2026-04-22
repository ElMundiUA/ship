---
artifact_kind: collection
id: preset-desktop-app
name: Preset — Desktop app
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 8fd576a9cff606cc1026f79572996832df95b7637534962554def668787f82cf
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, desktop, electron, tauri, native]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for teams shipping a desktop app (Electron / Tauri / native) to end users on macOS / Windows / Linux. Wires signing + notarization verification, installer-size budgets, OS support-matrix drift, a canary auto-update rollout flow, and a native-surface PR reviewer on top of the cross-cutting quality pack.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>, tool/desktop/codesign, tool/desktop/signtool]
  optional_tools: [tool/desktop/notarytool, tool/desktop/sparkle, tool/desktop/electron-updater, tool/desktop/tauri-updater, tool/desktop/squirrel, tool/desktop/osslsigncode, tool/obs/sentry, tool/obs/crashlytics]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: desktop-app
  install_target: documentation/collections/preset-desktop-app.md
---

# Preset — Desktop app

## Product shape

A desktop application shipped as an installer to end-user
machines — Electron, Tauri, or a fully native Cocoa / Win32 /
GTK app — running on macOS, Windows, and Linux. Bounded context
is **"the installed base"** — which OS versions you promise to
support, what binary you handed them, whether it's signed, and
how the next version reaches them safely.

The preset assumes:

- A CI pipeline that produces signed release artefacts
  (`.dmg` / `.pkg` on macOS, `.exe` / `.msi` on Windows,
  `.deb` / `.AppImage` on Linux) and stages them under a
  predictable `dist/` root per tag.
- An auto-update framework wired up (electron-updater,
  Sparkle, Squirrel, tauri-updater, or a custom server).
- A supported-OS list in source control
  (`SUPPORTED_OS.md` or equivalent) that the support / docs
  pages reference.
- A crash / error telemetry backend the canary rollout can
  read (Sentry, Crashlytics, Bugsnag, or a self-hosted
  equivalent).

## Lanes & patterns enabled out of the box

- `pr_review` (`flow-pr-self-review`)
- `daily_standup` (`flow-daily-retro`)
- `tech_debt`
- `code_map`
- `scan-signing-notarization` — release-tag signing +
  notarization verification (macOS notarization ticket,
  Windows Authenticode).
- `scan-installer-size` — per-PR installer size budget on
  packaging / dist changes.
- `scan-os-support-matrix` — weekly declared-vs-tested drift
  check across platforms.
- `flow-autoupdate-rollout` — canary → stable auto-update
  rollout with telemetry gates and rollback plan.
- `role-desktop-reviewer` — per-PR native-surface review
  (IPC, FS bridges, menu bar, tray, permissions).
- `scan-test-coverage`, `scan-dead-code`, `scan-license-deps`,
  `scan-env-var-catalog`, `scan-a11y`,
  `scan-performance-budget`, `role-designer` — cross-cutting
  quality pack (Wave 1) pre-enabled.

## SDLC columns the preset expects

Standard Ship flow plus:

- `Canary` — between `In review` and `Done`, holds release
  tags that have shipped to the canary channel and are
  soaking against telemetry.
- `Rollback ready` — parallel state pinned while a canary
  soaks; cleared once the ramp to stable completes without
  regression.

## Label contract (preset-specific)

- `lane:signing` · `lane:installer-size` · `lane:os-matrix`
- `lane:autoupdate` · `channel:canary` · `channel:stable` ·
  `channel:rolled-back`
- `ship:blocked` · `privilege-escalation` ·
  `native-surface-change` · `entitlement-change`

Plus the base Ship labels.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user.
- Apple Developer ID certificate + private key + app-specific
  password for `notarytool` (macOS signing / notarization).
- Windows code-signing certificate (EV or OV) plus
  timestamp-authority URL for Authenticode.
- Auto-update feed bucket credentials (S3 / GCS / R2 / CDN
  origin) for each channel.
- Crash / error telemetry backend API key (Sentry / Crashlytics
  / Bugsnag).

## Recommended addendums

- `addendum-fin` — when the desktop app handles regulated
  financial workflows.
- `addendum-pharma` — when the desktop app handles PHI or
  clinical data.
- Compose `preset-platform` on top when the same repo owns
  the update-server / CDN infrastructure.

## Evidence types

- Per-release signing & notarization ticket (one per tag).
- Per-PR installer-size comment with budget verdict.
- Weekly OS support-matrix drift ticket.
- Per-release auto-update rollout ticket with canary
  telemetry checkpoints and rollback plan.
- Per-PR native-surface review with capability-delta summary.
