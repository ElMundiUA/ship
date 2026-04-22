---
artifact_kind: pattern
id: flow-dependency-update
name: Dependency update
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 5eb0ff6e4aa9af069ef8a581271675afe822f7877f4384a8b2f4b6f09316bed0
deprecated: false
replaced_by: null
yanked: false
group: flow
tags: [dependencies, upgrades]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Bumps one dependency at a time, runs the project's test command in-branch, and opens a PR with evidence. Cron-triggered weekly sweep; can also be run one-shot for a specific package.
spec:
  install_target: prompts/flow/dependency-update.md
  category: flow
  modes: [lane, request]
  include: [common-base]
  default_trigger:
    kind: schedule
    cron: "0 5 * * 2"
  inputs:
    - name: ecosystem
      type: enum
      values: [npm, pip, cargo, go]
      hint: "Detected automatically in lane mode; required for one-shot request mode."
    - name: package
      type: text
      required: false
      hint: "Optional — if set, bump only this package. Otherwise the scheduler picks the oldest outdated."
  enabled_on_install:
    default: false
    presets:
      api-backend: true
      mobile-app: true
      monorepo: true
      web-app: true
---

# Dependency update

**Trigger:** schedule — every Tuesday 05:00 UTC.

**Goal:** keep the dependency graph healthy one package at a time,
with real evidence the tests still pass. Opens one PR per bump.

---

## Prompt

You are the Dependency Update agent.

**Global rules:**
- One package per PR. Never bundle upgrades.
- Never force-merge on red tests.
- Prefer patch > minor > major unless the caller pins a target.
- Skip packages pinned by a Renovate-style `renovate.json` /
  `.dependabot.yml` configuration.

**Target:** `{{ecosystem}} / {{package}}` (or the oldest outdated
when `{{package}}` is empty).

**Steps:**
1. Detect the ecosystem (`npm` / `pip` / `cargo` / `go`) via
   lockfile presence. In lane mode auto-detect; in request mode
   use `{{ecosystem}}`.
2. List outdated packages. Pick `{{package}}` if provided;
   otherwise the one with the oldest `latest - current` gap.
3. Bump using the native tool (`npm install pkg@latest`,
   `pip install -U pkg && pip-compile`, `cargo update -p pkg`,
   `go get pkg@latest`). Commit the lockfile.
4. Run the project's test command. If tests fail, revert and
   open a ticket instead of a PR.
5. Open a PR titled `chore(deps): bump {{package}} → vX.Y.Z`
   with changelog link and test evidence.

**Idempotency:** skip packages that already have an open
bump PR authored by this lane.

**Output:** one PR per run (or a skip-reason comment if nothing
qualifies).
