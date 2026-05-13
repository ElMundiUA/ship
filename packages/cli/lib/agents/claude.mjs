/**
 * Anthropic Claude Code CLI local adapter.
 *
 * Runs the ``claude`` binary (from ``@anthropic-ai/claude-code``) on
 * the GHA runner. Mirrors the Cursor / Codex shape: the runner has
 * already prepared the branch in ``workdir``; the adapter just
 * invokes the CLI in non-interactive mode and returns once it
 * terminates.
 *
 * Output format is ``stream-json`` (not plain ``text``) so we can
 * parse the trailing ``result`` event for token usage + cost. With
 * ``text`` the CLI dumped only the agent's final reply and we lost
 * the Anthropic usage numbers entirely — operators were burning
 * unknown amounts per tick because we had no visibility. The
 * adapter still surfaces the agent's prose to stderr line-by-line
 * for readable CI logs; usage lands as a single ``[claude] usage:``
 * summary at the end and as a structured object on the return
 * value (caller — ``run.mjs`` — can ship it to the audit row).
 *
 * Headless flags ('claude --help'):
 *   -p / --print                                 non-interactive, print + exit
 *   --output-format text|json|stream-json
 *   --verbose                                    required with stream-json
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
import { createInterface } from "node:readline";


/**
 * @param {object} opts
 * @param {string} opts.workdir      repo checkout dir; defaults to process.cwd()
 * @param {string} opts.branchName   branch the agent commits onto (already checked out)
 * @param {string} opts.prompt       full prompt body
 * @param {Record<string,string>} [opts.env]  extra env vars merged onto process.env
 * @param {(line: string) => void} [opts.onLog] streaming log hook
 * @returns {Promise<{
 *   agentId: string,
 *   branchName: string,
 *   status: string,
 *   exitCode: number,
 *   usage: null | {
 *     input_tokens: number,
 *     cache_read_input_tokens: number,
 *     cache_creation_input_tokens: number,
 *     output_tokens: number,
 *     total_cost_usd: number,
 *     num_turns: number,
 *     duration_ms: number,
 *   },
 * }>}
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
    "stream-json",
    "--verbose",
    prompt,
  ];

  onLog(`launch claude branch=${branchName} cwd=${workdir} prompt=${prompt.length}b`);
  const child = spawn("claude", args, {
    cwd: workdir,
    env: { ...process.env, ...env },
    // Pipe stdout so we can parse stream-json events. Stderr stays
    // inherited so Claude's own warnings still surface live.
    stdio: ["ignore", "pipe", "inherit"],
  });

  let finalUsage = null;

  const rl = createInterface({ input: child.stdout, crlfDelay: Infinity });
  rl.on("line", (line) => {
    if (!line.trim()) return;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      // Stream-json should always be JSON. If it isn't, surface the
      // raw line rather than swallowing it — easier to debug a
      // broken upstream than silent data loss.
      process.stderr.write(`[claude] (non-json) ${line}\n`);
      return;
    }
    if (event.type === "assistant" && event.message?.content) {
      // Surface the agent's reply prose so the CI log stays
      // readable. ``text`` mode used to do this for free; in
      // ``stream-json`` we have to re-emit it ourselves.
      for (const part of event.message.content) {
        if (part.type === "text" && typeof part.text === "string") {
          process.stderr.write(part.text);
          if (!part.text.endsWith("\n")) process.stderr.write("\n");
        } else if (part.type === "tool_use" && part.name) {
          // Tool calls at full fidelity drown the log; a one-line
          // marker keeps the trace navigable without the noise.
          process.stderr.write(`[claude] tool: ${part.name}\n`);
        }
      }
      return;
    }
    if (event.type === "result") {
      finalUsage = extractUsage(event);
      return;
    }
    // ``system`` (init / hooks), ``user`` (tool results),
    // ``rate_limit_event`` — silent in the workflow log; the
    // ``[claude] usage:`` summary at the end captures everything
    // the operator needs to debug a tick.
  });

  const exitCode = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code) => resolve(code ?? 1));
  });
  // ``readline`` close is async w.r.t. ``exit`` — wait for it so we
  // don't miss the trailing ``result`` event when claude flushes
  // stdout right before exiting.
  await new Promise((resolve) => rl.once("close", resolve));

  if (finalUsage) {
    onLog(formatUsageLine(finalUsage));
  } else {
    onLog("usage: <none captured>");
  }
  const status = exitCode === 0 ? "FINISHED" : "ERRORED";
  onLog(`claude terminal: status=${status} exit=${exitCode}`);
  return {
    agentId: `claude-${branchName}`,
    branchName,
    status,
    exitCode,
    usage: finalUsage,
  };
}


/**
 * Project the ``result`` event into the stable shape consumers (the
 * runner + the audit row) read. Claude's payload carries both a
 * top-level ``total_cost_usd`` and a nested ``usage`` block; pick
 * the fields we care about and drop the rest so we don't pin an
 * upstream schema we don't control.
 */
export function extractUsage(resultEvent) {
  const usage = resultEvent.usage || {};
  return {
    input_tokens: integerOrZero(usage.input_tokens),
    cache_read_input_tokens: integerOrZero(usage.cache_read_input_tokens),
    cache_creation_input_tokens: integerOrZero(
      usage.cache_creation_input_tokens,
    ),
    output_tokens: integerOrZero(usage.output_tokens),
    total_cost_usd: numberOrZero(resultEvent.total_cost_usd),
    num_turns: integerOrZero(resultEvent.num_turns),
    duration_ms: integerOrZero(resultEvent.duration_ms),
  };
}


export function formatUsageLine(u) {
  // One-liner that fits in a workflow log row. Cost is the operator-
  // facing headline number; the token columns let them attribute
  // spend (high cache_read = stable prompt, high output = chatty
  // reply, high cache_create = first turn / new context).
  const cost = u.total_cost_usd.toFixed(4);
  return (
    `usage: input=${u.input_tokens} ` +
    `cache_read=${u.cache_read_input_tokens} ` +
    `cache_create=${u.cache_creation_input_tokens} ` +
    `output=${u.output_tokens} ` +
    `cost=$${cost} ` +
    `turns=${u.num_turns} ` +
    `dur=${(u.duration_ms / 1000).toFixed(1)}s`
  );
}


function integerOrZero(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.trunc(value);
  }
  return 0;
}


function numberOrZero(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return 0;
}
