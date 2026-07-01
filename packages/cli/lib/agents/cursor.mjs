/**
 * Cursor Agent local-CLI adapter.
 *
 * Runs the official ``cursor-agent`` binary on the GHA runner. The
 * runner prepares the working tree and switches to the target branch
 * *before* this adapter is invoked; we just execute the CLI in
 * ``workdir`` and let it make commits in place. Push + PR creation
 * happen in the workflow step after the adapter returns.
 *
 * Headless flags ('cursor-agent --help'):
 *   -p / --print              non-interactive mode (no TTY)
 *   --output-format text|stream-json
 *   --force / --yolo          allow write/shell tools without prompts
 *   --trust                   skip workspace-trust dialog
 *   --workspace <path>        cwd override (we set it explicitly)
 *
 * Auth: ``CURSOR_API_KEY`` env var (same key used by the cloud API).
 */

import { spawn } from "node:child_process";


/**
 * @param {object} opts
 * @param {string} opts.workdir       repo checkout dir; defaults to process.cwd()
 * @param {string} opts.branchName    branch the agent commits onto (already checked out)
 * @param {string} opts.prompt        full prompt body
 * @param {Record<string,string>} [opts.env]  extra env vars merged onto process.env
 * @param {(line: string) => void} [opts.onLog] streaming log hook
 * @returns {Promise<{ agentId: string, branchName: string, status: string, exitCode: number }>}
 */
export async function runCursorAgent({
  workdir = process.cwd(),
  branchName,
  prompt,
  model = null,
  env = {},
  onLog = (l) => process.stderr.write(`[cursor] ${l}\n`),
} = {}) {
  if (!branchName) throw new Error("runCursorAgent: branchName required");
  if (!prompt || typeof prompt !== "string") {
    throw new Error("runCursorAgent: prompt required");
  }
  if (!(process.env.CURSOR_API_KEY || env.CURSOR_API_KEY)) {
    throw new Error("CURSOR_API_KEY is not set");
  }

  // ``--print`` keeps stdout clean enough to surface in the workflow
  // log, ``--force`` + ``--trust`` are required for non-interactive
  // write/shell operations on a fresh runner without prior workspace
  // trust history. ``--workspace`` pins cwd explicitly so the agent
  // doesn't drift when the parent shell's cwd is mutated.
  //
  // ``--model`` defaults to ``auto`` so workspaces on Cursor's Free
  // plan (where named models are gated — observed on askslayer
  // 2026-05-17: "Named models unavailable Free plans can only use
  // Auto. Switch to Auto or upgrade plans to continue.") still
  // work. Operators on paid plans who want a specific model export
  // ``CURSOR_MODEL`` as a workflow secret and we pass it through.
  // Precedence: per-stage model (from .ship/config.yml) → CURSOR_MODEL → auto.
  const resolvedModel = (model || process.env.CURSOR_MODEL || env.CURSOR_MODEL || "auto").trim();
  const args = [
    "--print",
    "--force",
    "--trust",
    "--workspace",
    workdir,
    "--output-format",
    "text",
    "--model",
    resolvedModel,
    prompt,
  ];

  onLog(
    `launch cursor-agent branch=${branchName} cwd=${workdir} prompt=${prompt.length}b model=${resolvedModel}`,
  );
  const child = spawn("cursor-agent", args, {
    cwd: workdir,
    env: { ...process.env, ...env },
    stdio: ["ignore", "inherit", "inherit"],
  });

  const exitCode = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => resolve(code ?? 1));
  });

  const status = exitCode === 0 ? "FINISHED" : "ERRORED";
  onLog(`cursor-agent terminal: status=${status} exit=${exitCode}`);
  return { agentId: `cursor-agent-${branchName}`, branchName, status, exitCode };
}
