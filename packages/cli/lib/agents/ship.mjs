/**
 * Ship self-spawn adapter (thesis 6, ELS-241).
 *
 * Mirrors the Cursor / Claude / Codex shape: the runner has already
 * prepared the branch in ``workdir``; this adapter just invokes a
 * NESTED ``shipctl run`` there and returns once it terminates. The
 * nested run picks its own task through the normal engine API and
 * bottoms out spawning the workspace's real coding provider
 * (claude / cursor / codex) — Ship never duplicates the coding agent
 * (thesis 5: spawn + control + inject, never re-implement).
 *
 * Dogfood/debug only: the ``runAgent`` dispatcher refuses
 * ``provider==='ship'`` unless ``SHIP_ALLOW_SELF_SPAWN=true`` (or the
 * caller passes ``allowSelfSpawn``), and every dispatch the nested
 * run triggers passes THROUGH the cascade-depth + per-workspace cap
 * controls server-side (trigger_kind='self_spawn', ELS-242) — a
 * runaway loop terminates via CASCADE_BLOCKED, not by luck.
 *
 * Auth: the nested run needs the same env ``shipctl run`` always
 * needs (``SHIP_API_TOKEN`` et al.) — merged from the caller env.
 * The ``prompt`` (contract uniformity with the other adapters) is
 * exported as ``SHIP_SELF_SPAWN_BRIEF`` for the nested run's context.
 */

import { spawn } from "node:child_process";

/**
 * @param {object} opts
 * @param {string} opts.workdir      repo checkout dir; defaults to process.cwd()
 * @param {string} opts.branchName   branch the nested run works on (already checked out)
 * @param {string} opts.prompt       brief for the nested run (exported via env)
 * @param {Record<string,string>} [opts.env]  extra env vars merged onto process.env
 * @param {(line: string) => void} [opts.onLog] streaming log hook
 * @returns {Promise<{ agentId: string, branchName: string, status: string, exitCode: number }>}
 */
export async function runShipAgent({
  workdir = process.cwd(),
  branchName,
  prompt,
  env = {},
  onLog = (l) => process.stderr.write(`[ship] ${l}\n`),
} = {}) {
  if (!branchName) throw new Error("runShipAgent: branchName required");
  if (!prompt || typeof prompt !== "string") {
    throw new Error("runShipAgent: prompt required");
  }
  if (!(process.env.SHIP_API_TOKEN || env.SHIP_API_TOKEN)) {
    throw new Error("SHIP_API_TOKEN is not set");
  }

  const args = ["run"];

  onLog(`launch shipctl run (nested) branch=${branchName} cwd=${workdir}`);
  const child = spawn("shipctl", args, {
    cwd: workdir,
    env: { ...process.env, ...env, SHIP_SELF_SPAWN_BRIEF: prompt },
    stdio: ["ignore", "inherit", "inherit"],
  });

  const exitCode = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => resolve(code ?? 1));
  });

  const status = exitCode === 0 ? "FINISHED" : "ERRORED";
  onLog(`shipctl terminal: status=${status} exit=${exitCode}`);
  return { agentId: `ship-${branchName}`, branchName, status, exitCode };
}
