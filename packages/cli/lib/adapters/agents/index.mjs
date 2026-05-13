/**
 * Agent detection today lives in `cli/lib/detect.mjs` (synchronous, returns
 * the `AgentTarget[]` shape with `{id, label, paths, confidence}`).
 *
 * This module re-exports it and provides a thin adapter-shaped wrapper
 * (`detectAll`) that yields `{id, present, confidence, evidence}` entries
 * consistent with tracker/ci/language adapters. Full per-agent adapter
 * wrappers (bootstrap/verify) land in a later task.
 */
import { detectAgentTargets, KNOWN_AGENTS } from "../../detect.mjs";

export { detectAgentTargets, KNOWN_AGENTS };

/**
 * @param {string} cwd
 * @returns {Promise<Array<{id:string, present:boolean, confidence:number, evidence:Array}>>}
 */
export async function detectAllAgents(cwd) {
  const targets = detectAgentTargets(cwd);
  return targets.map((t) => ({
    id: t.id,
    present: true,
    confidence: t.confidence,
    evidence: [{ type: "file", where: t.paths[0] || "-", match: t.label }],
  }));
}
