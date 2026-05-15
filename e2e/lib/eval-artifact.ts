/**
 * Pipeline e2e → eval-judge artifact dump.
 *
 * Every pipeline-*.wired.spec.ts calls ``dumpArtifact()`` after its
 * happy-path assertions. The judge runner (``tools/eval/judge.py``)
 * picks the dumps up by ``$EVAL_RUN_ID`` and scores each routine
 * with Claude + GPT-5 mini.
 *
 * Layout written to disk:
 *
 *     tools/eval/runs/<run-id>/<routine>.json
 *
 * One file per routine per run; running the same routine twice in
 * one run overwrites (which is what we want — we always score the
 * latest pass).
 *
 * Schema of each file:
 *
 *     {
 *       "meta":    { routine, ticket_ref, project_id, run_id,
 *                    duration_ms, agent_provider, captured_at },
 *       "inputs":  { ...routine-specific },
 *       "outputs": { ...routine-specific }
 *     }
 *
 * The judge prompt sees the full dict and references ``inputs.*`` /
 * ``outputs.*`` per the rubric markdown.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";


const REPO_ROOT = resolve(__dirname, "..", "..");


export function evalRunId(): string {
  const explicit = process.env.EVAL_RUN_ID?.trim();
  if (explicit) return explicit;
  // Default to ISO UTC with colons stripped (file-system-safe).
  return new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19) + "Z";
}


export type ArtifactPayload = {
  meta: Record<string, unknown>;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
};


/**
 * Persist one routine's artifact to ``tools/eval/runs/<run-id>/<routine>.json``.
 *
 * Idempotent overwrite. Caller passes the routine *name* used as the
 * filename — the runner's ``ROUTINE_TO_RUBRIC`` map keys off this
 * (e.g. ``"dev_implementation"`` → ``rubrics/dev.md``).
 */
export function dumpArtifact(
  routine: string,
  payload: ArtifactPayload,
): string {
  const runId = evalRunId();
  const path = resolve(
    REPO_ROOT,
    "tools",
    "eval",
    "runs",
    runId,
    `${routine}.json`,
  );
  mkdirSync(dirname(path), { recursive: true });
  const enriched: ArtifactPayload = {
    meta: {
      ...payload.meta,
      routine,
      run_id: runId,
      captured_at: new Date().toISOString(),
    },
    inputs: payload.inputs,
    outputs: payload.outputs,
  };
  writeFileSync(path, JSON.stringify(enriched, null, 2), "utf-8");
  return path;
}
