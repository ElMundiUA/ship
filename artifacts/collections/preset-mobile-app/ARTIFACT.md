---
artifact_kind: collection
id: preset-mobile-app
name: Preset — Mobile application
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-17T21:15:32.596709+00:00"
content_sha256: 2ded20be901ab4877f64b86ad6827d994494ead70ac8b13bfc0647544541a8e8
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, mobile-app]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for iOS/Android apps with store-review cadence, OTA updates, and device-farm testing. Use when bootstrapping a Ship project that matches this preset shape, when picking a starter set with `shipctl init`, or when the addendums or presets it composes need updating.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, tool/ci-mobile/fastlane-eas, collection/agent-rules-<agent>]
  optional_tools: [tool/e2e/detox, tool/e2e/maestro, tool/devicefarm/browserstack, tool/devicefarm/saucelabs, tool/ota/expo-updates, tool/flags/launchdarkly]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: mobile-app
  install_target: documentation/collections/preset-mobile-app.md
---

# Preset — Mobile application

## Product shape

iOS / Android client — recommended stack is
**Expo + React Native + EAS Build + Fastlane**. Alternatives
(bare React Native with native toolchains, Flutter, native
Swift/Kotlin) are supported; the preset only assumes the CI
can produce signed store-submittable binaries.

Bounded context is **"the install"** — the app has a long-
lived presence on the device, store review gates every
shipped version, and OTA updates patch between stores.

## SDLC columns the preset expects

- `Backlog → Todo → In Progress → In Review → Done`
- `Blocked` as a parallel state.
- Optional terminal-ish `In Store Review` column between
  `Done` and a final `Released` marker: a ticket stays in
  `In Store Review` while Apple / Google hold the build and
  re-opens automatically on rejection.

## Label contract (preset-specific)

- `platform:ios`
- `platform:android`
- `store:review` — build submitted to an app store, awaiting
  verdict.
- `store:rejected` — store-rejected build; needs a fix
  re-submission.
- `ota:update` — change can roll out as an OTA (Expo
  Updates / CodePush) without a new binary.
- `binary:required` — change **cannot** be OTA-only (native
  module, entitlement, plist change).
- `flag:behind` / `flag:exposed` (same as web-app).
- Plus the base Ship labels.

## CI stages (pseudocode)

```
on: pull_request
jobs:
  install:
  lint-typecheck:
  unit:
  preview-build:     # EAS preview build (internal distribution)
  e2e-device:        # Detox on simulators or Maestro on
                     # Browserstack/Sauce devices
  bundle-size:       # track JS bundle + native growth
  doctor:            # shipctl doctor
on: workflow_dispatch / schedule
jobs:
  release-build:     # EAS production build for iOS + Android
  submit-ios:        # Fastlane deliver / EAS submit → TestFlight
  submit-android:    # Fastlane supply / EAS submit → Play internal
```

Store submission is a **separate audit lane** with its own
ticket cadence — it is not merged with the delivery lane
because store review is external and asynchronous.

## Evidence types

- Preview build URL / QR code in the PR body (EAS / TestFlight
  internal / Play internal).
- Device-farm run report (Detox locally, Maestro or
  Browserstack / Sauce in CI).
- Bundle-size diff (JS + native) for each platform.
- Store submission receipt with the build number and
  cross-reference to the ticket.

## Promote gates

`preview-build green → staging submission → external testing
(TestFlight external / Play closed testing) → store review →
release`.

Each gate must be recorded on the ticket with its timestamp
and reviewer. OTA-only changes (label `ota:update`) skip the
store-review gate but still traverse `external testing`.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user.
- EAS / Expo token (or equivalent build-service token).
- Apple App Store Connect API key + team id + bundle id.
- Google Play service-account JSON + package name.
- Code-signing certificates (iOS distribution, Android
  upload key) — stored in the CI secret store, never in the
  repo.
- Device-farm token (Browserstack / Sauce), if used.
- Feature-flag SDK key, if used.

## Recommended addendums

- `addendum-pharma` — mandatory if the app handles PHI (e.g.
  telehealth, medication, patient portals). Adds audit-log
  retention and PHI redaction rules on top of this preset.
- `addendum-fin` — if the app processes payments or regulated
  financial actions.

Store submission cadence is preserved as a separate audit
lane whether or not an addendum is active.
