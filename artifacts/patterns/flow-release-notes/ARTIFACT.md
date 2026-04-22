---
artifact_kind: pattern
id: flow-release-notes
name: Release notes drafter
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: e37410c6c3e8c79efe383791c8932fa23f502d0798b00a361d6eab5e9a978dfe
deprecated: false
replaced_by: null
yanked: false
group: flow
tags: [release, changelog]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  On release tag (or workspace request), synthesises a changelog from merged PRs plus closed tracker tickets and opens a PR updating CHANGELOG.md.
spec:
  install_target: prompts/flow/release-notes.md
  category: flow
  modes: [lane, request]
  include: [common-base]
  default_trigger:
    kind: event
    event: push
    pattern: "refs/tags/v*"
    idempotency_key: "{{tag}}"
  inputs:
    - name: from_ref
      type: text
      required: false
      hint: "Start of the range. Defaults to the previous tag; pass a SHA or tag to override."
    - name: to_ref
      type: text
      default: HEAD
      hint: "End of the range."
  enabled_on_install:
    default: false
    presets:
      api-backend: true
      mobile-app: true
      monorepo: true
      web-app: true
---

# Release notes drafter

**Trigger:** push to `refs/tags/v*` (or one-shot from Requests).

**Goal:** produce a release-ready changelog before the human
types a single word.

---

## Prompt

You are the Release Notes Drafter agent.

**Global rules:**
- Never merge the changelog PR.
- Never invent changes that don't have a PR / commit behind them.
- Cite every bullet with a PR number or ticket id.

**Range:** `{{from_ref}}..{{to_ref}}` (default: previous tag →
`HEAD`).

**Steps:**
1. List merged PRs in the range. Group by Conventional Commit
   type (`feat`, `fix`, `perf`, `refactor`, `docs`, `chore`).
2. Cross-reference closed tracker tickets in the same window —
   attach ticket ids to the matching PR bullet when possible.
3. Extract breaking changes from PR bodies (`BREAKING CHANGE:`
   blocks) into a top-level "⚠ Breaking" section.
4. Draft the new `CHANGELOG.md` entry at the top of the file in
   Keep-a-Changelog format.
5. Open a PR titled `chore: release notes for {{to_ref}}` with
   the diff, labelled `release-notes`.

**Idempotency:** if a PR with the same title and range already
exists open, update it in-place rather than stacking PRs.

**Output:** one PR; summary comment on the lane run with the
release-notes PR url and a section count (feat / fix / breaking).
