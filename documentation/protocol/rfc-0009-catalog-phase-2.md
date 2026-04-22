---
rfc: 0009
title: "Catalog Phase-2 — beyond web & backend"
status: Draft
created: 2026-04-22
supersedes_in_part: []
follows: 0008
---

# RFC-0009 — Catalog Phase-2: beyond web & backend

## Abstract

RFC-0008 landed the catalog reform (naming, modes, expansion pack of
10 Phase-1 patterns) and retired `DefaultPipelineSpec`. The result is
a catalog of 31 patterns that serves the web + API-backend flavour
well but leaves every other application shape (mobile, desktop,
hardware, ML, games, infra/SRE, regulated industries) on the
free-form Requests fallback.

This RFC adds **50 new patterns** across 8 packs, introduces **7 new
presets** (`mobile-app-deep`, `desktop-app`, `firmware`, `ml-project`,
`platform`, `regulated`, `game`), and ships them in 4 rollout waves.
After Phase-2 the catalog is 81 patterns; every shipping application
shape can reach Day-1 ship-readiness by installing a preset alone,
without a single hand-rolled prompt.

Naming, modes, triggers, `enabled_on_install`, and `lane_workflow`
resolution are inherited verbatim from RFC-0008 — this RFC is strictly
additive and introduces no new metadata.

## Implementation status

| Phase | Scope                                                     | Status  |
|-------|-----------------------------------------------------------|---------|
| Wave 1 | Cross-cutting quality (7 patterns)                       | shipped — 2026-04-22 |
| Wave 2 | Mobile (8) + ML (7) + `mobile-app-deep` / `ml-project` presets | shipped — 2026-04-22 |
| Wave 3 | Infra/SRE (8) + Compliance (5) + `platform` / `regulated` presets | planned |
| Wave 4 | Desktop (5) + Hardware (6) + Games (4) + `desktop-app` / `firmware` / `game` presets | planned |

Each wave is independently mergeable, bumps `BUNDLE_VERSION`, and
is its own Linear epic. Linear tickets are listed in the
[Rollout](#rollout-waves--linear) section.

## Motivation

Three observations:

1. **The Phase-1 catalog is shaped by web+API-backend lanes.** Of
   31 patterns, 0 reference mobile store metadata, 0 reference
   firmware size budgets, 0 reference ML eval or drift. The
   `mobile-app` preset exists but wires only the same web-ish
   lanes to a mobile repo. Teams on other platforms skip Ship or
   hand-roll Requests.
2. **Quality scanners are table-stakes everywhere and we ship none.**
   A11y, performance budget, test coverage, license compatibility,
   dead code, and env-var hygiene are universal concerns that every
   preset benefits from. Shipping these as proper catalog patterns
   lifts every existing user on Day 1 without them changing their
   `.ship/config.yml`.
3. **Compliance / regulated industries never adopt.** SOC2, HIPAA,
   PCI audits, IAM policy drift, audit-log integrity — we have the
   ingredients (role/flow/scan metadata, multi-pattern lanes) but no
   patterns that speak the domain language. A fintech/healthtech
   operator can't find a starting point.

Phase-2 closes all three gaps in a batch large enough to cross the
"usable per domain" threshold but small enough to fit into four
merged PRs.

## Scope

In scope:

- 50 new `ARTIFACT.md` pattern files (full RFC-0008 metadata).
- 7 new preset keys added to `KNOWN_PRESETS` with `enabled_on_install`
  mappings on both new and existing patterns.
- Preset descriptor copy + icons in the Console onboarding wizard.
- Lane workflow resolver wiring: every new pattern picks one of the
  four existing starter YAMLs — **no new starter workflows**.
- Documentation updates: preset matrix in `rfc-0006-cloud-platform-foundations.md`,
  catalog reference in `documentation/catalog/README.md`.

Out of scope (deferred to RFC-0010 or later):

- Tool integrations (`tool-*` patterns) — Datadog, Sentry, Snyk,
  Crashlytics, Firebase, W&B, MLflow, Infracost, Kyverno. These
  show up in Phase-2 patterns as **referenced external tools** with
  a soft dependency check; proper MCP-style integration is a
  separate RFC.
- Per-pattern cost / billing-unit modelling.
- Cross-pattern compositions (composition-of-patterns as a first-class
  object in the UI — today multi-pattern lanes are the answer).
- New starter workflows — Phase-2 does not need any, the four
  existing starters cover the new trigger shapes.

## New presets

`KNOWN_PRESETS` (`backend/app/services/lane_recipes.py`) grows from 7
to 14:

| Preset key | Shape | Anchor lanes | Wave |
|---|---|---|---|
| `web-app` | existing | `pr_review`, `daily_standup`, `tech_debt`, `self_heal` | — |
| `api-backend` | existing | same + `scan-api-contract` | — |
| `mobile-app` | existing | same; becomes *thin* mobile — kept for BC | — |
| `mobile-app-deep` | **new** | mobile-app + `scan-mobile-crash-rate`, `scan-app-size-budget`, `scan-permissions-audit`, `scan-localization-gap`, `flow-store-submission`, `flow-beta-distribution`, `role-mobile-reviewer` | 2 |
| `ml-project` | **new** | `scan-model-eval`, `scan-data-drift`, `scan-training-repro`, `scan-feature-schema`, `flow-model-card`, `role-ml-reviewer` (+ cross-cutting) | 2 |
| `platform` | **new** | `scan-terraform-drift`, `scan-k8s-policy`, `scan-slo-health`, `scan-sbom-drift`, `flow-runbook-freshness`, `flow-oncall-handoff`, `flow-blast-radius` | 3 |
| `regulated` | **new** | `scan-pii-leakage`, `scan-iam-policy-diff`, `scan-audit-log-integrity`, `scan-consent-drift`, `flow-compliance-artifact` (+ cross-cutting + `platform` overlap) | 3 |
| `desktop-app` | **new** | `scan-signing-notarization`, `scan-installer-size`, `scan-os-support-matrix`, `flow-autoupdate-rollout`, `role-desktop-reviewer` | 4 |
| `firmware` | **new** | `scan-firmware-size-budget`, `scan-power-consumption`, `scan-hal-abi-compat`, `scan-safety-invariants`, `scan-bom-drift`, `flow-ota-rollout` | 4 |
| `game` | **new** | `scan-asset-budget`, `scan-telemetry-catalog`, `scan-balance-regression`, `flow-playtest-summary` | 4 |
| `cli`, `monorepo`, `marketing`, `adoption-minimum` | existing | cross-cutting lifts them automatically | — |

The existing `mobile-app` preset is kept intact for backwards
compatibility; `mobile-app-deep` is marketed as the "ship-ready"
tier. A `preset_deprecates` hint surfaces in the wizard so new
installs pick the deep preset.

## Pattern packs

All patterns follow the RFC-0008 metadata schema. Tables below give
id, summary, default trigger, modes, and the new input surface.
`enabled_on_install.presets` values follow from the preset matrix
above.

### Pack A — Cross-cutting quality (7, Wave 1)

Universally useful; flipped on by default for every existing preset
except `adoption-minimum`.

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `scan-a11y` | Runs axe-core / Lighthouse-a11y sweep against configured URLs or preview deployments; blocks PR on new WCAG AA violations. | event `pull_request` paths `['**/*.tsx', '**/*.html']` | `[lane, request]` | `urls: textarea (one per line)`, `wcag_level: enum[A, AA, AAA] (default AA)` |
| `scan-performance-budget` | Lighthouse / Core Web Vitals sweep per route; enforces LCP/CLS/INP budgets; regress on PR. | event `pull_request` | `[lane, request]` | `route_manifest_path: text (default: .ship/perf-routes.json)`, `budget_profile: enum[strict, default, loose]` |
| `scan-test-coverage` | Reads coverage artefact produced by CI; gates patch coverage on changed files vs baseline. | event `pull_request` | `[lane, request]` | `baseline_ref: text (default: main)`, `min_patch_coverage: text (default: 80)` |
| `scan-dead-code` | Detects unused exports (ts-prune / vulture / deadcode), orphan assets, unreachable branches; tickets the top N. | schedule `0 6 * * 2` (weekly Tue 06:00 UTC) | `[lane, request]` | `top_n: text (default: 20)`, `ignore_globs: textarea` |
| `scan-license-deps` | Walks `package.json` / `requirements.txt` / `Cargo.toml`; blocks GPL in MIT projects, copyleft in proprietary. | event `pull_request` paths dep-manifests | `[lane, request]` | `policy: enum[permissive, copyleft-ok, strict] (default permissive)` |
| `scan-env-var-catalog` | Greps code for `process.env.FOO`, `os.getenv("FOO")`; cross-checks against README / `.env.example`; files a tracker ticket for undocumented vars. | event `pull_request` | `[lane, request]` | `env_example_path: text (default: .env.example)` |
| `role-designer` | Reviews UI / design-touching PRs against the design system — token usage, component contracts, responsive breakpoints. | event `pull_request` labels `['design', 'ui']` | `[lane, request]` | `design_system_path: text (default: design-system/)` |

### Pack B — Mobile (8, Wave 2)

`mobile-app-deep` preset gates the full pack; `mobile-app` (legacy
preset) opts in to `scan-app-size-budget` + `scan-mobile-crash-rate`
only.

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `scan-app-size-budget` | Track IPA / APK / AAB size against a per-platform budget; blocks PR on regression > 2 %. | event `pull_request` | `[lane, request]` | `platforms: enum[ios, android, both] (default both)`, `budget_ios_mb: text`, `budget_android_mb: text` |
| `scan-mobile-crash-rate` | Queries Crashlytics / Sentry for crash-free-users regression vs previous release. | schedule `0 */2 * * *` (every 2h) | `[lane, request]` | `provider: enum[crashlytics, sentry] (required)`, `regression_threshold_pct: text (default: 1.0)` |
| `scan-store-metadata` | Validate App Store / Play Store listing (screenshots dims, title/copy length, keywords, age rating) before release. | event `push` on `refs/tags/v*` | `[lane, request]` | `platforms: enum[ios, android, both]` |
| `scan-permissions-audit` | Cross-check `Info.plist` / `AndroidManifest.xml` permissions against actual usage in source; flag unused and undocumented. | event `pull_request` | `[lane, request]` | `rationale_path: text (default: MOBILE_PERMISSIONS.md)` |
| `scan-localization-gap` | Detect strings missing a translation across locales; file per-locale tracker tickets. | schedule `0 7 * * 1` (weekly Mon) | `[lane, request]` | `string_root: text (default: i18n/)`, `locales: textarea` |
| `flow-store-submission` | Package build, verify signing / provisioning, draft submission notes, push to store review. | `—` (request-only) | `[request]` | `platform: enum[ios, android] (required)`, `release_ref: text (default: HEAD)` |
| `flow-beta-distribution` | Promote green build to TestFlight / Firebase App Distribution; notify tester groups. | event `push` on `refs/heads/release/*` | `[lane, request]` | `channel: enum[internal, external, both]`, `release_notes_path: text` |
| `role-mobile-reviewer` | Reviews PRs touching native code (Swift / Obj-C, Kotlin / Java) for platform pitfalls, lifecycle, main-thread violations. | event `pull_request` paths `['**/*.swift', '**/*.kt', '**/*.m', '**/*.mm', '**/*.java']` | `[lane, request]` | `ticket_url: url` |

### Pack C — Data / ML / AI (7, Wave 2)

Gated to `ml-project` preset; Phase-1 patterns (like `role-developer`)
stay in play for anything that still looks like a service.

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `scan-model-eval` | Run golden-dataset eval after every model commit; block PR on accuracy / F1 / ROC-AUC regression. | event `pull_request` paths `['**/models/**', '**/*.onnx', '**/*.pt']` | `[lane, request]` | `dataset_ref: text (required)`, `regression_threshold_pct: text (default: 1.0)` |
| `scan-data-drift` | Monitor feature-distribution drift (PSI / KS test) vs reference window; ticket on breach. | schedule `0 6 * * *` (daily) | `[lane, request]` | `feature_source: text (required)`, `reference_window_days: text (default: 14)` |
| `scan-training-repro` | Verify dataset hash + random seed + code hash + env digest are recorded for every training run. | schedule `0 5 * * *` (daily) | `[lane]` | — |
| `scan-feature-schema` | Diff feature-store schema vs training set / serving contract; flag silent shape drift. | event `pull_request` paths `['**/feature_definitions/**']` | `[lane, request]` | `feature_store: enum[feast, vertex, custom]` |
| `scan-bias-fairness` | Run group-fairness metrics (demographic parity, equal opportunity) on candidate models. | event `pull_request` labels `['model-release']` | `[request]` | `sensitive_attributes: textarea (required)`, `model_ref: text (required)` |
| `flow-model-card` | Draft / refresh model card on model release — intended use, eval results, known limits, ethical considerations. | event `push` on `refs/tags/model-v*` | `[lane, request]` | `model_ref: text (required)`, `audience: enum[internal, external] (default internal)` |
| `role-ml-reviewer` | Reviews ML PRs for leakage, p-hacking, missing eval splits, unpinned dataset versions. | event `pull_request` paths ML roots | `[lane, request]` | `ticket_url: url` |

### Pack D — Infra / SRE / Platform (8, Wave 3)

Gated to `platform` preset; also enabled by default for `monorepo`
(where infra usually lives).

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `scan-terraform-drift` | Diff Terraform state vs real cloud resources; file a ticket when out-of-band changes drift the estate. | schedule `0 4 * * *` (daily 04:00 UTC) | `[lane, request]` | `workspace_list: textarea` |
| `scan-k8s-policy` | Kyverno / OPA / Conftest policy check on every manifest change. | event `pull_request` paths `['**/*.yaml', '**/*.yml']` | `[lane, request]` | `policy_bundle: text (default: policies/)` |
| `scan-cost-delta` | Estimate cost delta of a PR via Infracost or cloud-pricing API; warn when > threshold. | event `pull_request` paths `['**/terraform/**', '**/*.tf']` | `[lane, request]` | `warn_threshold_usd: text (default: 50)`, `block_threshold_usd: text (default: 500)` |
| `scan-slo-health` | Query Prometheus / Datadog for error-budget burn; nudge on-call when burn > 1× for > 10 min. | schedule `*/15 * * * *` (every 15 min) | `[lane]` | `slo_registry_path: text (default: slo/)` |
| `scan-sbom-drift` | Diff SBOM against previous release; catch unexpected transitive dep additions, flag CVE-exposed ones. | event `push` on `refs/tags/v*` | `[lane, request]` | `baseline_ref: text (default: previous-release)` |
| `flow-runbook-freshness` | Cross-check runbooks against the services they describe; nudge when commands rot (executables missing, flags stale). | schedule `0 8 1 * *` (monthly) | `[lane, request]` | `runbook_root: text (default: runbooks/)` |
| `flow-blast-radius` | Estimate blast radius of a PR — services touched, % traffic affected, rollback path; comment on the PR. | event `pull_request` | `[lane, request]` | `service_map_path: text (default: .ship/service-map.json)` |
| `flow-oncall-handoff` | Draft handoff notes at shift change — open incidents, flaky tests, pending rollouts, active toggles. | `—` (request-only) | `[request]` | `shift_from: text (required)`, `shift_to: text (required)` |

### Pack E — Compliance / regulated (5, Wave 3)

Gated to `regulated` preset; `platform` enables a subset
(`scan-iam-policy-diff`, `scan-audit-log-integrity`).

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `scan-pii-leakage` | Grep logs / fixtures / commits for PII patterns (email, phone, SSN, card numbers, addresses); file tickets with redaction hints. | schedule `0 3 * * *` (daily) | `[lane, request]` | `pii_profile: enum[gdpr, hipaa, pci, custom]`, `custom_patterns_path: text` |
| `scan-iam-policy-diff` | Highlight IAM / role / scope changes on every PR touching auth config; annotate with blast-radius. | event `pull_request` paths `['**/iam/**', '**/policies/**', '**/*.tf']` | `[lane, request]` | — |
| `scan-audit-log-integrity` | Verify audit-log chain integrity (hash chain, sequence continuity) against last checkpoint. | schedule `0 * * * *` (hourly) | `[lane]` | `log_source: enum[db, s3, cloudwatch] (required)` |
| `scan-consent-drift` | Check consent-flow coverage against the regulated data map; flag uncovered event types or new processing purposes. | schedule `0 9 * * 1` (weekly Mon) | `[lane, request]` | `data_map_path: text (default: privacy/data-map.yml)` |
| `flow-compliance-artifact` | Refresh SOC2 / HIPAA / PCI artifact bundle (policies, evidence samples, access review logs) for the audit window. | `—` (request-only) | `[request]` | `framework: enum[soc2, hipaa, pci, iso27001] (required)`, `audit_window_start: text (required)`, `audit_window_end: text (required)` |

### Pack F — Desktop (5, Wave 4)

Gated to `desktop-app` preset.

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `scan-signing-notarization` | Verify macOS notarization + Windows Authenticode signing on every release candidate. | event `push` on `refs/tags/v*` | `[lane, request]` | `platforms: enum[macos, windows, both] (default both)` |
| `scan-installer-size` | Track per-platform installer size (dmg / msi / deb / AppImage) against a budget. | event `pull_request` | `[lane, request]` | `budget_mb: text (default: 200)` |
| `scan-os-support-matrix` | Cross-check supported OS list against the CI matrix; flag drift when a supported version stops being tested. | schedule `0 6 * * 1` (weekly Mon) | `[lane, request]` | `support_matrix_path: text (default: SUPPORTED_OS.md)` |
| `flow-autoupdate-rollout` | Stage auto-update to a canary channel, verify telemetry, promote to stable with a rollback plan. | event `push` on `refs/tags/v*` | `[lane, request]` | `canary_pct: text (default: 5)`, `soak_minutes: text (default: 180)` |
| `role-desktop-reviewer` | Reviews native-integration PRs (IPC surface, FS bridges, menu bar, system tray, permissions). | event `pull_request` paths `['**/native/**', '**/*.swift', '**/*.cpp', '**/*.rs']` | `[lane, request]` | `ticket_url: url` |

### Pack G — Hardware / Embedded / IoT / Firmware (6, Wave 4)

Gated to `firmware` preset.

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `scan-firmware-size-budget` | Flash / RAM footprint tracker — opens a ticket if PR pushes over the board budget. | event `pull_request` | `[lane, request]` | `board: text (required)`, `budget_flash_kb: text (required)`, `budget_ram_kb: text (required)` |
| `scan-power-consumption` | Track power draw from test-rig benchmark results; regress on 95th percentile. | schedule `0 2 * * *` (nightly) | `[lane, request]` | `profile: text (required)` |
| `scan-hal-abi-compat` | Diff HAL headers between releases; flag ABI breaks that would fork board-support packages. | event `pull_request` paths `['**/hal/**', '**/*.h']` | `[lane, request]` | `baseline_ref: text (default: main)` |
| `scan-safety-invariants` | Run MISRA-C / CERT-C / Coverity checks; block PR on new safety-class findings. | event `pull_request` | `[lane, request]` | `standard: enum[misra-c-2012, cert-c, iso26262] (required)` |
| `scan-bom-drift` | Hardware BOM vs actual components — catches silent substitution of out-of-stock parts. | schedule `0 9 * * 1` (weekly Mon) | `[lane, request]` | `bom_path: text (default: bom.csv)` |
| `flow-ota-rollout` | Canary firmware OTA with auto-rollback plan + fleet-health checkpoint. | `—` (request-only) | `[request]` | `image_ref: text (required)`, `canary_pct: text (default: 1)`, `fleet_tag: text` |

### Pack H — Games (4, Wave 4)

Gated to `game` preset.

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `scan-asset-budget` | Polygon count, texture memory, draw-call budget per scene / level; regresses on breach. | event `pull_request` paths `['**/assets/**', '**/*.fbx', '**/*.png']` | `[lane, request]` | `budget_profile: text (default: default)` |
| `scan-telemetry-catalog` | Cross-check defined telemetry events vs what's actually emitted from builds. | schedule `0 4 * * *` (nightly) | `[lane, request]` | `catalog_path: text (default: telemetry/events.yml)` |
| `scan-balance-regression` | Run balance-sim against the golden snapshot; flag win-rate / economy drift beyond ±2σ. | event `pull_request` paths `['**/balance/**', '**/config/*.json']` | `[lane, request]` | `sim_runs: text (default: 1000)` |
| `flow-playtest-summary` | Summarise playtest session feedback into a one-pager + tagged follow-ups. | `—` (request-only) | `[request]` | `session_notes_url: url (required)` |

## Lane workflow resolution

Every new pattern picks an existing starter YAML per the
RFC-0008 resolver rules (no new starter workflows are added):

| Pattern family | Starter YAML | Reason |
|---|---|---|
| Any pattern with `default_trigger.event == "pull_request"` | `pr-and-ci-gate` | Needs PR-comment permissions + PR-scoped context. |
| All `scan-*` without a PR trigger | `parallel-audit-lanes` | Fan-out friendly; unified reporting path. |
| `op-*` with `actions: write` need (none in Phase-2) | `pipeline-self-heal` | Not used by Phase-2. |
| Everything else (roles, flows, schedules) | `scheduled-sdlc-lane` | Universal agent-run path. |

Any pattern that needs an override (e.g. `scan-slo-health` because of
its 15-minute cadence and lack of PR context) can set
`spec.lane_workflow` explicitly — for Phase-2 the resolver-chosen
defaults cover every pattern.

## Rollout — waves & Linear

Each wave is one Linear **epic** (`shp-phase2-wave<N>`) with
**one issue per pattern** + **one preset-matrix issue** + **one doc
issue**. The pattern issue spec is identical for every pattern; see
[Per-pattern DoD](#per-pattern-definition-of-done) below.

### Wave 1 — Cross-cutting quality

- **Epic:** "RFC-0009 Wave 1 — Cross-cutting quality pack"
- **Acceptance:** all 7 patterns live under `artifacts/patterns/`,
  every existing preset (`web-app`, `api-backend`, `mobile-app`,
  `monorepo`, `cli`) opts in to `scan-a11y`, `scan-performance-budget`,
  `scan-test-coverage`, `scan-license-deps`, `scan-env-var-catalog`.
  `scan-dead-code` + `role-designer` stay optional.
- **Issues (9):**
  1. `scan-a11y` — implement pattern + prompt + fixtures.
  2. `scan-performance-budget` — implement pattern.
  3. `scan-test-coverage` — implement pattern.
  4. `scan-dead-code` — implement pattern.
  5. `scan-license-deps` — implement pattern.
  6. `scan-env-var-catalog` — implement pattern.
  7. `role-designer` — implement pattern.
  8. Preset matrix update: flip `enabled_on_install.presets` for
     existing presets.
  9. Docs: update `documentation/catalog/README.md`, regenerate
     `BUNDLE_VERSION`, refresh snapshot tests.

### Wave 2 — Mobile + ML  ✅ shipped — 2026-04-22

- **Epic:** "RFC-0009 Wave 2 — Mobile-deep + ML packs"
- **Acceptance:** 15 patterns landed, `mobile-app-deep` and
  `ml-project` added to `KNOWN_PRESETS`, wizard copy updated,
  `mobile-app` stays as the thin legacy preset.
- **Shipped:**
  - Mobile pack (8): `scan-app-size-budget`,
    `scan-mobile-crash-rate`, `scan-store-metadata`,
    `scan-permissions-audit`, `scan-localization-gap`,
    `flow-store-submission`, `flow-beta-distribution`,
    `role-mobile-reviewer`.
  - ML pack (7): `scan-model-eval`, `scan-data-drift`,
    `scan-training-repro`, `scan-feature-schema`,
    `scan-bias-fairness`, `flow-model-card`, `role-ml-reviewer`.
  - Preset wiring: `mobile-app-deep` + `ml-project` in
    `backend.app.services.lane_recipes.KNOWN_PRESETS`;
    `flow-pr-self-review` / `flow-daily-retro` /
    `tech_debt` gated on the new presets.
  - Cross-cutting quality pack patterns (Wave 1) opt the two
    new presets in where relevant.
  - Onboarding wizard: `console/src/app/onboarding/presets.ts`
    and CLI `cli/lib/bootstrap/render.mjs` carry descriptor
    copy + recommended-tool lists for both presets.
  - New preset collection artefacts:
    `artifacts/collections/preset-mobile-app-deep/ARTIFACT.md`
    and `artifacts/collections/preset-ml-project/ARTIFACT.md`.

### Wave 3 — Infra + Compliance

- **Epic:** "RFC-0009 Wave 3 — Platform + Regulated packs"
- **Acceptance:** 13 patterns land, `platform` + `regulated` presets
  added. Cross-preset overlap (`scan-iam-policy-diff`,
  `scan-audit-log-integrity`) enabled on both.
- **Issues (16):**
  1–8. Infra/SRE pack.
  9–13. Compliance pack.
  14. Preset wiring for `platform` + `regulated`.
  15. Wizard copy for two new presets (incl. regulated-industry
      questionnaire gate — ask for framework before install).
  16. Docs: add compliance-framework mapping table in
      `documentation/catalog/README.md`.

### Wave 4 — Desktop + Hardware + Games

- **Epic:** "RFC-0009 Wave 4 — Desktop + Firmware + Game packs"
- **Acceptance:** 15 patterns land, 3 new presets
  (`desktop-app`, `firmware`, `game`), wizard updated.
- **Issues (18):**
  1–5. Desktop pack.
  6–11. Hardware pack.
  12–15. Games pack.
  16. Preset wiring (3 presets).
  17. Wizard copy + icons for 3 new presets.
  18. Docs refresh + `BUNDLE_VERSION` bump + snapshot tests.

Total: 4 epics, 60 Linear issues.

## Per-pattern definition of done

A pattern issue is closed when:

1. `artifacts/patterns/<id>/ARTIFACT.md` exists with full RFC-0008
   metadata populated (`category`, `modes`, `default_trigger`,
   `inputs`, `enabled_on_install`, `spec.include: [common-base]` when
   the pattern has the user-visible pre-amble).
2. `scripts/restamp_artifact_shas.py` was run and the commit
   updates `content_sha256`.
3. Prompt body follows the existing Phase-1 style: one-line trigger,
   goal statement, numbered steps, output contract, fallback.
4. `backend/tests/test_catalog.py` snapshot accepts the new pattern
   (`pytest backend/tests/test_catalog.py -k <id>`).
5. CLI smoke test passes (`shipctl run --pattern <id>
   --dry-run`).
6. `documentation/catalog/README.md` has one row describing the
   pattern in ≤ 120 chars.
7. Preset wiring (if applicable) is added in the same or paired
   commit — no half-wired state on `main`.

## Non-goals

- **Tool integrations (`tool-*` patterns).** `scan-mobile-crash-rate`
  names Crashlytics and Sentry as providers but does *not* wrap their
  APIs; it calls the CI-installed CLIs. A proper tool-integration
  RFC (MCP adapters, secret shape, shared fixtures) is future work.
- **Paid plans / billing gating.** Some Phase-2 patterns (compliance
  artifact refresh, OTA rollout) are enterprise-grade features. This
  RFC ships them as open patterns; the plan tiering conversation is
  decoupled.
- **AI-model-specific tuning.** Patterns specify tasks, not which
  agent to use. Model routing stays in `Agent` configuration.
- **Deprecation of Phase-1 patterns.** Nothing ships a `deprecated:
  true` flag in Phase-2.

## Open questions

1. **`mobile-app` vs `mobile-app-deep`.** Ship them side-by-side
   forever, or schedule `mobile-app` for removal in Phase-3? Current
   proposal: keep both, mark legacy on the wizard card only.
2. **`regulated` preset and industry profile.** Does the wizard ask
   "which framework" (SOC2 / HIPAA / PCI) at install time, and does
   `flow-compliance-artifact` inherit that default? Current
   proposal: yes, stored as a workspace-scoped config key.
3. **Pattern-to-tool soft dependencies.** When `scan-model-eval`
   expects an MLflow artefact but the repo doesn't use MLflow, do we
   skip the pattern at runtime or noisy-fail? Current proposal:
   skip with a visible "not configured" note in the run timeline.
