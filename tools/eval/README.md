# Pipeline eval layer

Scoring the **output** of every agent bundle against a rubric, with
two judges in parallel (Claude Sonnet 4.6 + GPT-5-mini). Sits on top
of the process tests in `e2e/tests/pipeline-*.wired.spec.ts` — those
prove the pipeline ran end-to-end; this layer scores whether what
the agent actually produced is shippable.

## Layout

```
tools/eval/
├── rubrics/                 # one markdown rubric per bundle
│   ├── planning.md
│   ├── decomposition.md
│   ├── dev.md
│   ├── validation.md
│   └── self-heal.md
├── judges/
│   ├── base.py              # JudgeRequest / JudgeResult / TokenBreakdown
│   ├── claude.py            # Anthropic SDK + ephemeral prompt cache
│   └── openai_judge.py      # OpenAI SDK + auto prompt cache
├── prices.py                # USD/M-token table (re-check on price changes)
├── judge.py                 # CLI: pick run-id, score every artifact, print cost
├── runs/<run-id>/*.json     # e2e dumps the agent's input + output here
├── results/<run-id>/*.json  # judge writes per-call verdicts here
└── metrics.jsonl            # append-only log: (run_id, routine, judge, score, cost)
```

## Capture → score loop

1. **Run the pipeline e2e suite** with a fixed run-id so artifacts land in
   one folder:

   ```bash
   EVAL_RUN_ID=2026-05-15-trial-1 \
   E2E_RUN_PIPELINE=1 \
   E2E_SHIP_API_BASE=http://127.0.0.1:8100 \
   E2E_PIPELINE_WORKSPACE_ID=... \
   E2E_PIPELINE_PO_TOKEN=ship_pat_... \
   E2E_PIPELINE_DEV_TOKEN=ship_pat_... \
   E2E_PIPELINE_REPO=ElMundiUA/ship-e2e-pipeline \
   E2E_SHIPCTL_BIN=/Users/.../packages/cli/bin/shipctl.mjs \
   E2E_PIPELINE_GH_TOKEN=$(gh auth token) \
   CURSOR_API_KEY=... \
     npx playwright test \
       pipeline-planning.wired.spec.ts \
       pipeline-decomposition.wired.spec.ts \
       pipeline-dev.wired.spec.ts \
       pipeline-validation.wired.spec.ts \
       pipeline-self-heal.wired.spec.ts \
       --workers=1 --timeout=1800000
   ```

   Each test calls `dumpArtifact(<routine>, …)` which writes
   `tools/eval/runs/$EVAL_RUN_ID/<routine>.json` with the agent's
   input + output. If `EVAL_RUN_ID` is unset, a timestamped folder
   is used.

2. **Run the judge** against that run-id:

   ```bash
   ANTHROPIC_API_KEY=... OPENAI_API_KEY=... \
     PYTHONPATH=. .venv/bin/python tools/eval/judge.py \
       --run-id 2026-05-15-trial-1
   ```

   Output prints per-routine scores from both judges + a Ship
   verdict (ship / split / hold) + the total cost line.

3. **Inspect the rationales**:

   ```bash
   cat tools/eval/results/2026-05-15-trial-1/planning.claude.json
   cat tools/eval/results/2026-05-15-trial-1/planning.gpt.json
   ```

4. **Tune the role prompt** in
   `apps/backend/app/resources/agent_roles/<role>.md`, then re-run
   from step 1 (with a fresh run-id) to compare scores.

## Cost

One pipeline run (5 bundles × 2 judges) is **≈ $0.07-0.09** at
current Anthropic + OpenAI pricing:

- **Claude Sonnet 4.6**: ~$0.07 (prompt cache helps on re-runs, 51%
  hit on the baseline above)
- **GPT-5 mini**: ~$0.02 (automatic cache; 96% hit on second
  scoring of the same routine)

Re-scoring the same artifacts within 5 min (Anthropic cache TTL) is
roughly half-price. Production usage where one rubric scores many
artifacts per day amortises the cache write cost.

## Adding a rubric

1. Drop a markdown file in `tools/eval/rubrics/<routine>.md`.
2. Register the routine → rubric mapping in
   `tools/eval/judge.py:ROUTINE_TO_RUBRIC`.
3. Wire `dumpArtifact("<routine>", …)` into the relevant pipeline
   e2e test.

Rubrics MUST instruct the model to return strict JSON in a specific
shape — the judge runner parses `score`, `would_ship`, and prints
`breakdown` from the parsed object. Both judges fall back to first-
JSON-object extraction if the model wraps in fences or trails prose.

## Adding a judge

1. New module under `tools/eval/judges/<provider>.py` exporting
   `run(req: JudgeRequest) -> JudgeResult`.
2. Map its usage shape to `TokenBreakdown` (the runner sums costs
   across providers uniformly).
3. Add it to `tools/eval/judge.py:JUDGES`.
4. Add its model + prices to `tools/eval/prices.py`.

## Threshold + would_ship

Default threshold per rubric is **70 / 100**. Rubrics override via
their final-JSON `would_ship` field — judges can boolean-combine
score + penalty conditions (e.g. "would_ship is true iff score >= 70
AND no `rewrite_brief` penalty").

The runner doesn't fail on `hold` — it just reports. Wire the
`would_ship` field into CI gates yourself if you want a regression
shield.
