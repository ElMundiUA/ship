---
artifact_kind: pattern
id: scan-env-var-catalog
name: Environment variable catalog
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 271516066d5196759c247422d4170590a289ed928fd16c6ef0fb8cc7914ee6d5
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, env, config, hygiene]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Walks the code for env var references, cross-checks against the documented catalog (.env.example / README), and files a tracker ticket for undocumented or unused vars.
spec:
  install_target: prompts/scan/env-var-catalog.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  default_trigger:
    kind: event
    event: pull_request
    pattern: "**"
    idempotency_key: "{{pr}}"
  inputs:
    - name: env_example_path
      type: text
      default: .env.example
      hint: "Canonical catalog of env vars. Missing vars surface as findings."
    - name: readme_path
      type: text
      default: README.md
      hint: "Fallback doc scanned when .env.example is absent."
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
      game: true
      monorepo: true
      cli: true
---

# Environment variable catalog

**Trigger:** PR event.

**Goal:** every env var the code reads should also live in
`{{env_example_path}}` with a short description — no more "works
on my machine" deploys.

---

## Prompt

You are the Env Var Catalog Scanner agent.

**Global rules:**
- Never modify source or the catalog file. Tickets / PR comments
  only.
- Evidence per finding: file, line, extraction snippet, and
  whether the var already appears anywhere in docs.
- Secrets are env vars too — never log values, only names.

**Catalog:** `{{env_example_path}}`. **Fallback doc:**
`{{readme_path}}`.

**Steps:**
1. Grep the codebase for env var references:
   - JS/TS → `process.env.FOO`, `import.meta.env.FOO`.
   - Python → `os.getenv("FOO")`, `os.environ["FOO"]`.
   - Rust → `std::env::var("FOO")`.
   - Go → `os.Getenv("FOO")`.
   - Shell / CI → `$FOO`, `${FOO}` (skip obvious shell builtins).
2. Build the `referenced_vars` set from the scan.
3. Load `{{env_example_path}}`; fallback to scanning
   `{{readme_path}}` for a `## Environment variables` block when
   the file doesn't exist.
4. Compute two diffs:
   - **Undocumented:** `referenced_vars − documented_vars`.
   - **Unused:** `documented_vars − referenced_vars`.
5. On a PR run: post a single **Env var report** comment. On a
   scheduled run (if ever wired as a lane): open a single tracker
   ticket with label `lane:env-catalog` and update in place.
6. Request changes on the PR when at least one *new* undocumented
   var was introduced by the patch.

**Idempotency:** one PR comment or one open ticket (`env-catalog`
anchor), updated on each run.

**Output:** one PR comment (PR mode) or one ticket (lane mode) +
optional `changes-requested` review.
