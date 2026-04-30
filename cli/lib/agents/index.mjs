/**
 * Agent runtime dispatcher.
 *
 * E14 picks the runtime per ``.ship/config.yml`` ``agent.default.provider``
 * (and per-routine override under ``agent.overrides.<routine>``). Today
 * Cursor Cloud is the only one wired in; ``claude`` / ``codex`` slots
 * exist so adding them later is a single new file plus an entry in
 * ``RUNTIMES`` here.
 */

import { runCursorAgent } from "./cursor.mjs";


const RUNTIMES = {
  cursor: runCursorAgent,
  // claude: runClaudeCodeAgent,   // TODO when we add it
  // codex:  runCodexAgent,        // TODO when we add it
};

const DEFAULT_PROVIDER = "cursor";


export function resolveProvider(config, routineId) {
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
 * @returns {Promise<{ agentId: string, branchName: string, status: string, raw: object }>}
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
