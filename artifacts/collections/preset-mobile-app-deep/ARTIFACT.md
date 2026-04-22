---
artifact_kind: collection
id: preset-mobile-app-deep
name: Preset — Mobile application (deep)
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 3855567b5f748fdc707059abb3d069e6a18e2ae3c0f35d0d23e5c9ecc6626b3f
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, mobile-app, mobile-app-deep]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Mobile-app preset with the full deep pack — adds crash-rate monitoring, permissions audit, store-metadata validator, i18n gap sweeps, beta-distribution flow, store-submission flow, and a native-code reviewer on top of the base mobile-app preset.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, tool/ci-mobile/fastlane-eas, collection/agent-rules-<agent>]
  optional_tools: [tool/e2e/detox, tool/e2e/maestro, tool/devicefarm/browserstack, tool/devicefarm/saucelabs, tool/ota/expo-updates, tool/flags/launchdarkly, tool/crash/crashlytics, tool/crash/sentry]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: mobile-app-deep
  install_target: documentation/collections/preset-mobile-app-deep.md
---

# Preset — Mobile application (deep)

## Product shape

Same core as `preset-mobile-app` (iOS / Android / RN with store
review, OTA updates, device-farm E2E) but enables every mobile
pattern in the catalog by default. Use this preset when a repo
is *the* mobile surface for a product team and you want the full
post-install / release-review / i18n machinery wired from day one.

Pick `preset-mobile-app` (the thin preset) when the mobile repo
is incidental or when you want to opt in to store-level patterns
one at a time.

## Lanes & patterns enabled out of the box

Carried from `preset-mobile-app`:

- `pr_review` (`flow-pr-self-review`)
- `daily_standup` (`flow-daily-retro`)
- `tech_debt`
- `code_map`
- `scan-app-size-budget`
- `scan-mobile-crash-rate`

Added by the deep pack:

- `scan-store-metadata` — release-tag metadata validator.
- `scan-permissions-audit` — stealth / undocumented
  permissions.
- `scan-localization-gap` — weekly i18n sweep, ticket per
  locale.
- `role-mobile-reviewer` — native-code reviewer on Swift /
  Obj-C / Kotlin / Java PRs.
- `flow-store-submission` — one-shot request; packages, signs
  and dispatches to store review.
- `flow-beta-distribution` — release-branch promotion to
  TestFlight / Play closed testing.
- `scan-a11y`, `scan-performance-budget`, `scan-test-coverage`,
  `scan-dead-code`, `scan-license-deps`, `role-designer` —
  cross-cutting quality pack (Wave 1) pre-enabled.

## Required secrets (generic names)

Same as `preset-mobile-app`, plus (if the corresponding lane is
kept enabled):

- Crashlytics service account or Sentry DSN + auth token.
- Device-farm token (Browserstack / Sauce), if the beta lane
  includes device runs.
- Localization platform API key (Lokalise / Crowdin / i18n
  file), if the i18n sweep writes through a service instead of
  reading repo files.

## Addendums

Same as `preset-mobile-app` — `addendum-pharma` and
`addendum-fin` compose cleanly on top.
