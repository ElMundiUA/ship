/**
 * Anthropic Claude Code CLI local adapter.
 *
 * Runs the ``claude`` binary (from ``@anthropic-ai/claude-code``) on
 * the GHA runner. Mirrors the Cursor / Codex shape: the runner has
 * already prepared the branch in ``workdir``; the adapter just
 * invokes the CLI in non-interactive mode and returns once it
 * terminates.
 *
 * Headless flags ('claude --help'):
 *   -p / --print                                 non-interactive, print + exit
 *   --output-format text|json|stream-json
 *   --dangerously-skip-permissions               required on a fresh runner
 *                                                without prior workspace trust
 *                                                history (CI sandbox)
 *   --add-dir <dirs...>                          extra dirs the agent may
 *                                                touch — we add the workspace
 *                                                explicitly even though it's
 *                                                cwd, so symlink-style repos
 *                                                still resolve
 *
 * Auth: ``ANTHROPIC_API_KEY`` env var. Claude Code also reads a
 * keychain entry on macOS but CI runners don't have it.
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
export async function runClaudeAgent({
  workdir = process.cwd(),
  branchName,
  prompt,
  env = {},
  onLog = (l) => process.stderr.write(`[claude] ${l}\n`),
} = {}) {
  if (!branchName) throw new Error("runClaudeAgent: branchName required");
  if (!prompt || typeof prompt !== "string") {
    throw new Error("runClaudeAgent: prompt required");
  }
  if (!(process.env.ANTHROPIC_API_KEY || env.ANTHROPIC_API_KEY)) {
    throw new Error("ANTHROPIC_API_KEY is not set");
  }

  const args = [
    "--print",
    "--dangerously-skip-permissions",
    "--add-dir",
    workdir,
    "--output-format",
    "text",
    prompt,
  ];

  onLog(`launch claude branch=${branchName} cwd=${workdir} prompt=${prompt.length}b`);
  const child = spawn("claude", args, {
    cwd: workdir,
    env: { ...process.env, ...env },
    stdio: ["ignore", "inherit", "inherit"],
  });

  const exitCode = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => resolve(code ?? 1));
  });

  const status = exitCode === 0 ? "FINISHED" : "ERRORED";
  onLog(`claude terminal: status=${status} exit=${exitCode}`);
  return { agentId: `claude-${branchName}`, branchName, status, exitCode };
}
