/**
 * Agent runtime dispatcher.
 *
 * The workspace binds to one of the three local CLIs via
 * ``Workspace.agent_provider`` (cursor / codex / claude); ``shipctl
 * run`` resolves that binding before invoking ``runAgent`` so the
 * runner installs and executes only the right CLI on each tick. Per-
 * routine override is still honoured via ``.ship/config.yml``
 * ``agent.overrides.<routine>.provider`` for debug runs.
 *
 * Each adapter expects the runner to have already checked out the
 * target branch in ``workdir`` and configured git committer identity.
 * After the adapter returns, the runner pushes the branch and opens
 * a PR via ``gh``. There is no remote service in the loop anymore —
 * the cloud-poll path was removed in PR-5 once Cursor's GitHub App
 * dependency proved fragile (organisation-level App removal silently
 * killed every cron tick).
 */

import { runClaudeAgent } from "./claude.mjs";
import { runCodexAgent } from "./codex.mjs";
import { runCursorAgent } from "./cursor.mjs";


const RUNTIMES = {
  cursor: runCursorAgent,
  codex: runCodexAgent,
  claude: runClaudeAgent,
};

const DEFAULT_PROVIDER = "cursor";


export function resolveProvider(config, routineId) {
  // Per-routine override on the customer's .ship/config.yml — used
  // for debug pinning ("force this one routine to claude") without
  // touching the workspace binding. The workspace-level default
  // arrives via the API resolver path inside `shipctl run`; this
  // function only handles the config.yml branches.
  const override = config?.agent?.overrides?.[routineId]?.provider;
  if (override) return String(override).toLowerCase();
  const def = config?.agent?.default?.provider;
  if (def) return String(def).toLowerCase();
  return DEFAULT_PROVIDER;
}


/**
 * Run the configured agent runtime.
 *
 * @param {string} provider — one of ``RUNTIMES``' keys.
 * @param {object} opts — passed straight through to the runtime.
 * @returns {Promise<{ agentId: string, branchName: string, status: string, exitCode: number }>}
 */
export async function runAgent(provider, opts) {
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
