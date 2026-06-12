/**
 * Agent runtime dispatcher.
 *
 * ## ADAPTER CONTRACT (thesis 5 — enforced, see ELS-245)
 *
 * Ship is a SUPERSTRUCTURE over coding agents: it spawns, controls and
 * injects context — it never re-implements the agent. Every adapter in
 * this directory may couple to its tool through EXACTLY two points:
 *
 *   1. The tool's NON-INTERACTIVE invocation contract (binary + a few
 *      headless flags; prompt in).
 *   2. "The agent commits to the branch Ship checked out" (+ exit code
 *      out).
 *
 * ALL Ship value — agent-roles, policies, .ship/knowledge, Lighthouse
 * retrieval, the autonomy preamble — enters THROUGH the prompt/context.
 * Two patterns are REJECTED by design and linted against
 * (test_agent_adapter_discipline.py):
 *
 *   - Variant A: delivering value via tool-native config (.cursorrules,
 *     per-tool CLAUDE.md, codex config, hooks) — N foreign roadmaps of
 *     maintenance and nowhere to hold the control plane.
 *   - Inversion: Ship becoming an MCP tool the agents call — flips the
 *     control direction; Ship spawns the agent, never the reverse.
 *
 * ## Runtime binding
 *
 * The workspace binds to one local CLI via ``Workspace.agent_provider``
 * (cursor / codex / claude, plus the dogfood-gated ``ship``
 * self-spawn); ``shipctl run`` resolves that binding through
 * ``GET /v1/workspaces/{ws}/agent-provider`` before invoking
 * ``runAgent`` so the runner installs and executes only the right CLI
 * on each tick.
 *
 * Each adapter expects the runner to have already checked out the
 * target branch in ``workdir`` and configured git committer identity.
 * After the adapter returns, the runner pushes the branch and opens
 * a PR via ``gh``. There is no remote service in the loop anymore —
 * the cloud-poll path was removed once Cursor's GitHub App
 * dependency proved fragile (organisation-level App removal silently
 * killed every cron tick).
 */

import { runClaudeAgent } from "./claude.mjs";
import { runCodexAgent } from "./codex.mjs";
import { runCursorAgent } from "./cursor.mjs";
import { runShipAgent } from "./ship.mjs";


const RUNTIMES = {
  cursor: runCursorAgent,
  codex: runCodexAgent,
  claude: runClaudeAgent,
  // Thesis-6 self-spawn (ELS-241): nested ``shipctl run`` for
  // dogfood/debug of the spawn+control loop. Hard-gated below —
  // a misconfigured workspace can never silently fork-bomb; the
  // server-side cascade/cap controls count self_spawn dispatches
  // like any other (ELS-242).
  ship: runShipAgent,
};

export const DEFAULT_PROVIDER = "cursor";


/**
 * Run the configured agent runtime.
 *
 * @param {string} provider — one of ``RUNTIMES``' keys.
 * @param {object} opts — passed straight through to the runtime.
 *   ``opts.allowSelfSpawn`` (or env ``SHIP_ALLOW_SELF_SPAWN=true``)
 *   is required for ``provider==='ship'``.
 * @returns {Promise<{ agentId: string, branchName: string, status: string, exitCode: number }>}
 */
export async function runAgent(provider, opts) {
  if (provider === "ship") {
    const allowed =
      process.env.SHIP_ALLOW_SELF_SPAWN === "true" ||
      Boolean(opts && opts.allowSelfSpawn);
    if (!allowed) {
      throw new Error(
        "agent runtime 'ship' (self-spawn) is dogfood-gated: set " +
          "SHIP_ALLOW_SELF_SPAWN=true (or pass allowSelfSpawn) to enable.",
      );
    }
  }
  const fn = RUNTIMES[provider];
  if (!fn) {
    const known = Object.keys(RUNTIMES).join(", ") || "(none)";
    throw new Error(
      `agent runtime '${provider}' is not wired in this build. Known: ${known}`,
    );
  }
  return fn(opts);
}


export const SUPPORTED_PROVIDERS = Object.freeze(Object.keys(RUNTIMES));
