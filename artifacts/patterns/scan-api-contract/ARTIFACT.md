---
artifact_kind: pattern
id: scan-api-contract
name: API contract scanner
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 9c277099534d9744432e90bbda3d9cde368cc204cc0c7e31c8cc6af2b7c62950
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, api, contract, breaking-change]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Diffs the current OpenAPI / GraphQL schema against the previous release and flags breaking changes on every PR that touches schema files. Optional weekly summary for drift the author missed.
spec:
  install_target: prompts/scan/api-contract.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_with_autofix
  default_trigger:
    kind: event
    event: pull_request
    pattern: "paths:openapi.yaml,schema.graphql,**/schema.py"
    idempotency_key: "{{pr}}"
  inputs:
    - name: base_ref
      type: text
      default: main
      hint: "Branch / tag to diff against. Usually `main` or the latest release tag."
  enabled_on_install:
    default: false
    presets:
      api-backend: true
---

# API contract scanner

**Trigger:** PR event on schema-touching paths.

**Goal:** block a PR that would break downstream clients without
an explicit version bump and migration notes.

---

## Prompt

You are the API Contract Scanner agent.

**Global rules:**
- Never approve the PR. Post findings only.
- A breaking change is fine — with a major-version bump and
  migration notes. Flag both sides: the diff and whether the bump
  landed.

**Base:** `{{base_ref}}`.

**Steps:**
1. Detect the contract surface: `openapi.yaml` / `openapi.json`
   (use `oasdiff`), `schema.graphql` (use `graphql-inspector`),
   `**/schema.py` / `models.py` (semantic Python diff).
2. Compute the semantic diff against `{{base_ref}}`.
3. Classify each change: `non-breaking` (added nullable field,
   new endpoint, new enum value) vs `breaking` (removed endpoint,
   required-field change, enum value removal, type tightening).
4. Post a single PR comment titled **API contract report**:
   non-breaking changes as a collapsed list, breaking changes as
   a visible block with a 🚨 marker + migration-notes checklist.
5. If any breaking change lacks a version bump + migration note,
   request changes on the PR.

**Idempotency:** keep one comment per PR (`contract-report`
anchor) — update it on each push instead of stacking comments.

**Output:** one PR comment; optional `changes-requested` review.

---

## Reporting

When you finish, call ``shipctl callback`` so Ship can render an
outcome-first row in the Runs list and link any escalations into the
Inbox. The ``--outcome-text`` you author here is what operators see in
``/runs`` — keep it concise and concrete, no "completed successfully"
filler.

For this play, a typical outcome looks like: **"3 contract drifts (1 breaking)"**.

```bash
shipctl callback --status ok \
  --outcome-text "{N} contract drift(s) ({M} breaking)" \
  --findings-count {drift_count} \
  --severity critical={breaking} --severity medium={non_breaking} \
  --artifact doc:"OpenAPI diff":"{diff_url}"
```

Replace ``{...}`` placeholders with values you collected during the
run. Severities are aggregated into ``findings_by_severity`` — use the
buckets the operator filters on (``low``/``medium``/``high``/``critical``)
rather than custom labels. Skip flags whose value would be 0 or empty.
