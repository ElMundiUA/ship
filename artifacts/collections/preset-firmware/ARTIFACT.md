---
artifact_kind: collection
id: preset-firmware
name: Preset — Firmware / Embedded
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 677b89a999248341c1fa7586e5bfa714aab2dd840fa7852becbd1f8e0fc2c545
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, firmware, embedded, iot, hardware]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for embedded / IoT device teams shipping firmware with a bench lab, hardware revisions, and OTA updates. Wires firmware size budgets, HAL ABI locks, BOM delta review, nightly power profiling, staged OTA rollout planning, and quarterly CE / FCC / UL / PTCRB certification bundle refreshes on top of the cross-cutting quality pack.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>, tool/firmware/toolchain, tool/firmware/platformio]
  optional_tools: [tool/firmware/esp-idf, tool/firmware/zephyr, tool/firmware/renode, tool/firmware/qemu, tool/ota/aws-iot, tool/ota/mender, tool/ota/balena, tool/bench/power-analyzer, tool/bom/octopart, tool/bom/digikey, tool/eda/kicad, tool/eda/altium]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: firmware
  install_target: documentation/collections/preset-firmware.md
---

# Preset — Firmware / Embedded

## Product shape

A repo (or monorepo section) whose primary output is firmware for
physical devices — MCU-class (ESP32 / STM32 / nRF52 / RP2040) or
application processors running Linux-on-a-module — with a bench lab
behind CI, hardware revisions tracked next to the sources, and an
OTA channel delivering updates to a deployed fleet. Bounded context
is **"the fleet"** — what firmware sits on which SKU, at which
revision, under which compliance envelope, with which power
budget.

The preset assumes:

- A cross-compiler toolchain in CI (arm-none-eabi-gcc,
  clang-embedded, xtensa-esp32-elf-gcc) plus a build system
  (PlatformIO, ESP-IDF, Zephyr, or a hand-rolled Make / CMake
  shell).
- A linker-map-friendly build — flash / RAM measurement depends on
  `size` + section maps being produced as artefacts.
- A locked HAL surface (`firmware/abi/HAL.lock`) — pin maps,
  register offsets, IRQ numbers, linker regions — reviewed before
  downstream board-support packs consume them.
- A versioned BOM (`hardware/bom.csv` or KiCad / Altium export)
  with MPNs, qty, reference designators, and — ideally —
  lifecycle + pricing columns.
- A bench rig capturing power traces into
  `hardware/power/profile.json` (or equivalent) on a nightly
  cadence; QEMU / Renode fallback for offline days.
- An OTA backend with per-cohort targeting (AWS IoT Jobs, Azure
  IoT Hub, Mender, Balena, or a custom transport).
- A quarterly regulatory cadence — CE / FCC / UL / PTCRB — with
  evidence living under source control at `compliance/cert/`.
- A tracker the hardware + firmware rotation actually reads
  (Linear / Jira / GitHub Issues).

## Lanes & patterns enabled out of the box

- `pr_review` (`flow-pr-self-review`)
- `daily_standup` (`flow-daily-retro`)
- `tech_debt`
- `code_map`
- `scan-firmware-size` — per-PR flash / RAM budget check across
  every declared MCU target.
- `scan-bom-delta` — per-PR BOM diff with cost / lifecycle /
  single-source flags.
- `scan-hal-abi-lock` — per-PR HAL ABI diff against the signed
  manifest.
- `scan-power-profile` — nightly idle / sleep / active current
  baseline vs budget.
- `flow-ota-channel` — staged rollout planner triggered on
  `refs/tags/fw-v*`.
- `flow-cert-compliance` — quarterly CE / FCC / UL / PTCRB
  evidence bundle refresh.
- `scan-test-coverage`, `scan-dead-code`, `scan-license-deps`,
  `scan-env-var-catalog`, `scan-performance-budget`,
  `role-designer` — cross-cutting quality pack (Wave 1)
  pre-enabled where it makes sense on a firmware repo (coverage,
  dead code, license hygiene, env-var catalog, host-side tool
  perf budgets, companion-app UI review).
- `scan-sbom-drift` — per-release supply-chain diff inherited
  from the Wave 3 infra pack; SBOMs are as important to embedded
  builds as they are to servers.

Compose `preset-platform` on top when the fleet backplane (MQTT
broker, IoT Core, device registry) lives in the same repo, and
`preset-regulated` when the device ships into HIPAA / PCI
environments that need the compliance-artifact flow alongside the
hardware certification bundle.

## SDLC columns the preset expects

Standard Ship flow plus:

- `On bench` — between `In review` and `Done`, holds PRs whose
  firmware change is live on a bench unit but not yet promoted
  past the `bench` cohort.
- `Canary` — parallel state pinned while the OTA canary cohort
  soaks.
- `Rollback ready` — pinned during any OTA rollout that has not
  cleared the `Broad` stage yet.
- `Cert window` — holds the per-quarter certification PR open
  until the compliance lead signs off.

## Label contract (preset-specific)

- `lane:fw-size` · `lane:bom` · `lane:hal-abi` · `lane:power`
- `lane:ota` · `lane:cert-compliance`
- `lifecycle-risk` · `single-source` · `eol` · `nrnd`
- `abi-break` · `abi-bump-required`
- `cohort:bench` · `cohort:canary` · `cohort:early` ·
  `cohort:broad` · `cohort:general`
- `rollback-recommended` · `cert:missing-evidence`

Plus the base Ship labels.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user.
- BOM / pricing API key (Octopart / Digi-Key / Mouser) — read-only.
- OTA transport credentials (AWS IoT Core role, Azure IoT Hub
  SAS, Mender / Balena API token) — scoped to the rollout's
  device groups.
- Bench-rig telemetry token (power analyzer uploader / device
  farm controller).
- Crash / telemetry source token for crash-free and heartbeat
  gates during OTA rollout (Sentry, Memfault, custom).
- Compliance-framework-of-record credentials when the cert
  bundle syncs out (e.g. quality-management or QMS systems).

## Recommended addendums

- `addendum-fin` — when the device participates in payment /
  regulated financial flows (POS hardware, financial PTCRB bands).
- `addendum-pharma` — when the device is a medical / wellness
  product subject to FDA or HIPAA scopes.
- Compose `preset-platform` on top for the backplane side of a
  connected-device product, and `preset-regulated` when the data
  path needs SOC2 / HIPAA / PCI artefact refreshes beside the
  hardware certification bundle.

## Evidence types

- Per-PR firmware-size report comment with per-target budget
  status + top-10 growing symbols.
- Per-PR BOM delta comment with cost / lifecycle / single-source
  block.
- Per-PR HAL ABI report with required vs detected semver bump.
- Nightly power-profile ticket per phase regression, closed when
  the fleet returns to budget.
- Per-tag OTA rollout ticket with stage gates, soak telemetry,
  and a pinned rollback command.
- Per-quarter certification-bundle PR with coverage matrix and
  missing-evidence callouts.
