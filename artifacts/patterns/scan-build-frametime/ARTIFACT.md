---
artifact_kind: pattern
id: scan-build-frametime
name: Build frametime benchmark
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: d9620bb9573031bd18f393b55122bac15ad984755b5d64bc20f0067b3cbef55d
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, game, performance, benchmark]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Runs a headless benchmark scene nightly and compares frametime (p99 / p95) plus peak memory against a rolling baseline. Files a tracker ticket the moment the trunk build drifts out of frame budget.
category: health_checks
subcategory: performance
critical: false
spec:
  install_target: prompts/scan/build-frametime.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: schedule
    cron: "0 2 * * *"
  inputs:
    - name: benchmark_scene
      type: text
      default: benchmarks/bench_main
      hint: "Scene / level to run headless. Path inside the engine's content root."
    - name: p99_budget_ms
      type: text
      default: "16.6"
      hint: "Worst-case frametime budget (ms). 16.6 ≈ 60 fps floor."
    - name: p95_budget_ms
      type: text
      default: "11.1"
      hint: "p95 frametime budget (ms). 11.1 ≈ 90 fps steady-state target."
    - name: memory_budget_mb
      type: text
      default: "2048"
      hint: "Peak working-set budget in megabytes for the benchmark session."
  enabled_on_install:
    default: false
    presets:
      game: true
---

# Build frametime benchmark

**Trigger:** schedule — nightly 02:00 UTC.

**Goal:** every morning the team knows whether trunk is still
hitting frame budget on the reference hardware — regressions get
a ticket, not a shrug in standup.

---

## Prompt

You are the Build Frametime Benchmark agent.

**Global rules:**
- Never rewrite scene data. Measure + report only.
- Rolling baseline is the 7-day trimmed mean of green runs on
  the same `{{benchmark_scene}}`; one outlier doesn't move it.
- Evidence per finding: commit SHA, scene, pass (CPU / GPU),
  median / p95 / p99 frametime, peak working-set, delta vs
  baseline, delta vs budget.

**Scene:** `{{benchmark_scene}}`. **Budgets:** p99
`{{p99_budget_ms}} ms`, p95 `{{p95_budget_ms}} ms`, memory
`{{memory_budget_mb}} MB`.

**Steps:**
1. Pull the latest green trunk build artefact (headless executable
   + content bundle). Skip the run if no green build exists in the
   last 24 h — file a `build broken` ticket instead.
2. Run `{{benchmark_scene}}` headless with the engine's
   benchmark harness (Unity `-batchmode -nographics` with a
   timeline capture, Unreal `Automation RunTest`, Godot headless
   CLI, or the custom engine's bench runner). Capture:
   - CPU main-thread frametime histogram.
   - GPU frametime histogram (via RenderDoc capture or the
     engine's built-in GPU profiler).
   - Peak working-set (RSS) and VRAM residency.
3. Compute p50 / p95 / p99 frametime, peak memory, and draw-call
   count. Compare against the 7-day rolling baseline and against
   the hard budgets (`{{p99_budget_ms}}` / `{{p95_budget_ms}}` /
   `{{memory_budget_mb}}`).
4. A regression is: p99 worse by > 5 % OR any axis over budget.
   On regression, upsert one ticket titled `Frametime regression
   — <scene>` labelled `lane:frametime` with:
   - Commit range since last green baseline.
   - Per-pass (CPU / GPU) delta table.
   - Captured GPU trace attachment link if available.
   - Suggested suspects (recently-modified assets, shaders,
     gameplay code in the commit range).
5. Close the ticket on the next green run with zero regression;
   update it in place otherwise. Archive historical runs under
   `.ship/cache/frametime/<date>.json` so the 7-day baseline
   stays reproducible.

**Idempotency:** one open ticket per `{{benchmark_scene}}`,
updated in place.

**Output:** 0..1 tracker tickets + lane-run summary with pass /
regression / error counts per scene.
