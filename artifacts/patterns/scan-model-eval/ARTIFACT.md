---
artifact_kind: pattern
id: scan-model-eval
name: Model eval gate
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-22T00:00:00+00:00"
content_sha256: 011d4a56bf71fdfbffa5c9620774eff0a028d15438ca528389c61da29aedba03
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, ml, model-eval, regression]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Runs a golden-dataset evaluation on every model-touching PR and blocks merges that regress accuracy / F1 / ROC-AUC beyond the configured threshold. Keeps model quality from silently drifting.
spec:
  install_target: prompts/scan/model-eval.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: event
    event: pull_request
    pattern: "paths:**/models/**,**/*.onnx,**/*.pt,**/*.pkl,**/*.safetensors,**/train.py"
    idempotency_key: "{{pr}}"
  inputs:
    - name: dataset_ref
      type: text
      required: true
      hint: "Dataset version / ref — dataset registry id, S3 path, or DVC hash."
    - name: regression_threshold_pct
      type: text
      default: "1.0"
      hint: "Percentage drop in the primary metric that blocks the PR."
    - name: primary_metric
      type: text
      default: accuracy
      hint: "Metric name to gate on (accuracy / f1 / roc_auc / rmse / bleu / …)."
  enabled_on_install:
    default: false
    presets:
      ml-project: true
---

# Model eval gate

**Trigger:** PR event on model / training paths.

**Goal:** block a PR that regresses the primary metric by more
than `{{regression_threshold_pct}}` percentage points on the
golden dataset.

---

## Prompt

You are the Model Eval Gate agent.

**Global rules:**
- Never approve the PR. Post findings only.
- Golden dataset is pinned by `{{dataset_ref}}` — do not
  substitute.
- Evidence per finding: metric name, base value, PR value,
  delta, sample size, seed.

**Dataset ref:** `{{dataset_ref}}`. **Primary metric:**
`{{primary_metric}}`. **Threshold:**
`{{regression_threshold_pct}}` pp.

**Steps:**
1. Resolve `{{dataset_ref}}` → concrete dataset snapshot.
   Abort if the snapshot is missing or the hash doesn't match.
2. Build the PR candidate model — run the repo's
   `train` / `export` step that produces the artefact.
3. Build the base candidate from the PR's base ref (skip if the
   base ref's artefact is already cached in the model
   registry).
4. Evaluate both candidates on the pinned dataset with the same
   seed; compute the full metric suite, not just the primary.
5. Compare PR vs base: per-metric delta. Gate on
   `(base[primary_metric] - pr[primary_metric]) >
   regression_threshold_pct`.
6. Post a single PR comment titled **Model eval report** with
   the metric × (base / PR / delta) table, seed, sample size,
   and a link to both artefact versions.
7. Request changes on the PR when the primary metric regresses.

**Idempotency:** one comment per PR (`model-eval-report`
anchor).

**Output:** one PR comment + optional `changes-requested`
review.
