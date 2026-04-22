---
artifact_kind: pattern
id: scan-feature-schema
name: Feature schema gate
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: e7873d2f237583b7e6309616dad25ce7bdfc79100864472311081108ada8319f
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, ml, feature-store, schema]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Diffs feature-store schema against the repo's declared schema on every PR. Blocks drift between production feature definitions and the model-training contract.
spec:
  install_target: prompts/scan/feature-schema.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  default_trigger:
    kind: event
    event: pull_request
    pattern: "paths:**/feature_views/**,**/features.yaml,**/features.py,**/schema/**"
    idempotency_key: "{{pr}}"
  inputs:
    - name: feature_store
      type: enum
      values: [feast, tecton, databricks, custom]
      default: feast
      hint: "Feature store provider; 'custom' = use the schema file checked into the repo."
    - name: schema_path
      type: text
      default: schema/features.yaml
      hint: "Repo path that mirrors the live feature-store schema."
  enabled_on_install:
    default: false
    presets:
      ml-project: true
---

# Feature schema gate

**Trigger:** PR event on feature / schema paths.

**Goal:** keep the model's expected feature contract in sync
with the feature store — no silent column renames, no quiet
type coercions.

---

## Prompt

You are the Feature Schema Gate agent.

**Global rules:**
- Never write to the feature store. Read + report only.
- Evidence per finding: feature name, declared type, actual
  type, entity, last-updated timestamp.

**Feature store:** `{{feature_store}}`. **Schema path:**
`{{schema_path}}`.

**Steps:**
1. Load the repo's declared schema from `{{schema_path}}`.
2. Pull the live feature-store schema for the same entities
   (Feast `feast feature-views list`, Tecton API,
   Databricks Feature Store DESCRIBE, or the custom YAML if
   `{{feature_store}} == custom`).
3. Compute three sets:
   - **Missing in store** (declared in repo, not in store).
   - **Missing in repo** (in store, not declared).
   - **Type mismatch** (declared but with a different dtype /
     nullability).
4. For each mismatch, capture the last-updated timestamp of
   the live feature so reviewers can triangulate the
   upstream change.
5. Post a single PR comment titled **Feature schema diff**
   with the three sets as separate blocks; request changes
   when any are non-empty.

**Idempotency:** one comment per PR (`feature-schema-diff`
anchor).

**Output:** one PR comment + optional `changes-requested`.
