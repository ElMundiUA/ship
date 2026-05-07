/**
 * OpenAI Codex CLI local adapter.
 *
 * Runs the official ``codex`` binary (from ``@openai/codex``) on the
 * GHA runner. Mirrors the Cursor / Claude shape: the runner has
 * already prepared the branch in ``workdir``; the adapter just
 * invokes the CLI in non-interactive mode and returns once it
 * terminates.
 *
 * Headless mode is ``codex exec`` per
 * https://developers.openai.com/codex/noninteractive .  ``--full-auto``
 * suppresses the permission prompts, ``--skip-git-repo-check`` lets
 * us run inside a freshly checked-out repo without git-state
 * heuristics intercepting.  Prompt is passed as the trailing
 * positional arg.
 *
 * Auth: ``OPENAI_API_KEY`` env var (Codex also accepts a ChatGPT
 * sign-in but that's interactive; CI uses the API-key path).
 */

import { spawn } from "node:child_process";


/**
 * @param {object} opts
 * @param {string} opts.workdir      repo checkout dir; defaults to process.cwd()
 * @param {string} opts.branchName   branch the agent commits onto (already checked out)
 * @param {string} opts.prompt       full prompt body
 * @param {Record<string,string>} [opts.env]  extra env vars merged onto process.env
 * @param {(line: string) => void} [opts.onLog] streaming log hook
 * @returns {Promise<{ agentId: string, branchName: string, status: string, exitCode: number }>}
 */
export async function runCodexAgent({
  workdir = process.cwd(),
  branchName,
  prompt,
  env = {},
  onLog = (l) => process.stderr.write(`[codex] ${l}\n`),
} = {}) {
  if (!branchName) throw new Error("runCodexAgent: branchName required");
  if (!prompt || typeof prompt !== "string") {
    throw new Error("runCodexAgent: prompt required");
  }
  if (!(process.env.OPENAI_API_KEY || env.OPENAI_API_KEY)) {
    throw new Error("OPENAI_API_KEY is not set");
  }

  const args = [
    "exec",
    "--full-auto",
    "--skip-git-repo-check",
    prompt,
  ];

  onLog(`launch codex exec branch=${branchName} cwd=${workdir} prompt=${prompt.length}b`);
  const child = spawn("codex", args, {
    cwd: workdir,
    env: { ...process.env, ...env },
    stdio: ["ignore", "inherit", "inherit"],
  });

  const exitCode = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => resolve(code ?? 1));
  });

  const status = exitCode === 0 ? "FINISHED" : "ERRORED";
  onLog(`codex terminal: status=${status} exit=${exitCode}`);
  return { agentId: `codex-${branchName}`, branchName, status, exitCode };
}
