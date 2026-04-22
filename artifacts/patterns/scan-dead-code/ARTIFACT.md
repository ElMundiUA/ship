---
artifact_kind: pattern
id: scan-dead-code
name: Dead code scanner
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 18b6e3848d5e0a18aed68eb267efe671c4d8991ef8f4da383b53e0cf3098f9d0
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, dead-code, hygiene]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Weekly sweep for unused exports, unreachable branches and orphan assets. Files the top N findings as tracker tickets so the codebase doesn't accumulate silent dead weight.
spec:
  install_target: prompts/scan/dead-code.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  default_trigger:
    kind: schedule
    cron: "0 6 * * 2"
  inputs:
    - name: top_n
      type: text
      default: "20"
      hint: "Maximum number of findings to file in one run."
    - name: ignore_globs
      type: textarea
      required: false
      hint: "Paths to skip (one glob per line). Generated code, vendored libs, fixtures."
  enabled_on_install:
    default: false
    presets:
      web-app: true
      api-backend: true
      mobile-app-deep: true
      ml-project: true
      platform: true
      regulated: true
      desktop-app: true
      firmware: true
      game: true
      monorepo: true
      cli: true
---

# Dead code scanner

**Trigger:** schedule — weekly Tuesday 06:00 UTC.

**Goal:** surface the top `{{top_n}}` dead-code findings so
unreachable code doesn't keep surviving refactors.

---

## Prompt

You are the Dead Code Scanner agent.

**Global rules:**
- Never delete code. Tickets only.
- A finding is dead when it has zero production callers *and* zero
  test callers. Test-only exports are fine.
- Evidence per finding: file, symbol / line range, tool verdict,
  last-modified commit.

**Ignore globs:**

```
{{ignore_globs}}
```

**Steps:**
1. Detect the ecosystem(s): TS/JS → `ts-prune` or `knip`; Python →
   `vulture` / `deadcode`; Rust → `cargo-udeps`; Go →
   `unused` / `staticcheck`. Run each that applies.
2. Walk the project for orphan static assets (images, JSON
   fixtures, svg) — `.git grep` the filename; zero hits means
   orphan.
3. Filter findings through `ignore_globs`.
4. Rank by `(lines * age_days)` so big-and-old symbols bubble up
   first. Keep the top `{{top_n}}`.
5. Group adjacent findings in the same file into one ticket.
6. File tickets with label `lane:dead-code`, each linking to the
   top 3 evidence lines.

**Idempotency:** before filing, search open issues with label
`lane:dead-code` and the same file path — reuse if exists
(bump severity comment with the latest run date).

**Output:** up to `{{top_n}}` tickets + one summary lane-run
comment.
