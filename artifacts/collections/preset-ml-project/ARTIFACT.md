---
artifact_kind: collection
id: preset-ml-project
name: Preset — ML project
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 98abe193a845b62031cdebd2d497eb022e289b2ce8d38fc2b9c3069fc70e49b0
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, ml, ml-project]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for repos that train, evaluate, or serve ML models. Wires model-eval gating, data-drift monitoring, feature-schema diff, training reproducibility smoke tests, bias / fairness scans, model-card drafting, and an ML-aware reviewer.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>, tool/ml/registry]
  optional_tools: [tool/ml/dvc, tool/ml/lakefs, tool/ml/mlflow, tool/ml/wandb, tool/feature-store/feast, tool/feature-store/tecton, tool/data-validation/great-expectations]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: ml-project
  install_target: documentation/collections/preset-ml-project.md
---

# Preset — ML project

## Product shape

A repo whose primary output is one or more trained ML models
plus the surrounding training / eval / inference pipelines. The
preset assumes:

- A golden dataset reference (DVC, LakeFS, or a registry id).
- A reproducible training entry-point (`train.py` or similar).
- A model registry (MLflow, W&B, Vertex, or Databricks) that
  holds shipped artefacts.

Serving (FastAPI inference, batch scoring, stream inference) is
expected to live either in the same repo or in a sibling repo
that points back to this one's registry.

## Lanes & patterns enabled out of the box

- `pr_review` (`flow-pr-self-review`)
- `daily_standup` (`flow-daily-retro`)
- `tech_debt`
- `code_map`
- `scan-model-eval` — per-PR golden-dataset eval gate.
- `scan-training-repro` — repro smoke test on training-config
  changes.
- `scan-data-drift` — daily PSI / KS on feature distributions.
- `scan-feature-schema` — PR gate on feature-store schema
  drift.
- `scan-bias-fairness` — per-PR fairness diff on protected
  slices.
- `flow-model-card` — release-tag model-card drafting.
- `role-ml-reviewer` — ML-aware PR reviewer on training / eval
  / data paths.
- `scan-test-coverage`, `scan-dead-code`, `scan-license-deps`,
  `scan-env-var-catalog` — cross-cutting quality pack (Wave 1)
  pre-enabled.

## SDLC columns the preset expects

Standard Ship flow plus:

- `In eval` — sitting between `In review` and `Ready to merge`,
  held open while `scan-model-eval` runs on a slow GPU queue.
- `Model shipped` — terminal column for release tickets once the
  corresponding model-card PR is merged.

## Label contract (preset-specific)

- `lane:model-eval` · `lane:fairness` · `lane:drift`
- `regression:metric` · `regression:fairness`
- `dataset:ref-stale` · `schema:drift`
- `model:shipped`

Plus the base Ship labels.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user.
- Model registry token (MLflow / W&B / Vertex / Databricks).
- Dataset-registry credentials (DVC remote, LakeFS, or S3).
- Feature-store API token (Feast / Tecton / Databricks), if
  `scan-feature-schema` is kept enabled.
- Crash / error-reporting for serving infra, if the repo hosts
  an inference surface.

## Recommended addendums

- `addendum-pharma` — mandatory for models that touch PHI.
- `addendum-fin` — mandatory for credit / fraud / underwriting
  models.
- `addendum-responsible-ai` (Phase-3) — adds documented review
  cadence for high-impact decisions. Compose on top when the
  model is deployed to a user-facing decision surface.

## Evidence types

- Model eval report on every PR comment.
- Data-drift ticket per feature source, upserted.
- Model card per shipped release tag (PR trail, approvals).
- Repro metric row per smoke run (for audits).
