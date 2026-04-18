import fs from "node:fs";
import path from "node:path";
import { detectAgentTargets } from "../../detect.mjs";
import { readCachedArtifact } from "../../cache/store.mjs";

export const id = "agents-on-disk";
export const category = "config";
export const description = "Declared stack.agents have detectable signals on disk";

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  const declared = (ctx.config && ctx.config.stack && Array.isArray(ctx.config.stack.agents))
    ? ctx.config.stack.agents
    : [];
  if (!declared.length) {
    return { status: "skip", detail: "stack.agents is empty" };
  }
  const detected = new Set(detectAgentTargets(ctx.cwd).map((t) => t.id));
  const missing = [];
  for (const agent of declared) {
    if (detected.has(agent)) continue;
    // Second chance: the cached agent-rules artifact may declare a custom
    // install_target (e.g. codex -> AGENTS.md) that the heuristic detector
    // doesn't recognise. Treat a present install_target file as "signal".
    let fm = null;
    try {
      fm = readCachedArtifact(ctx.cwd, "collection", `agent-rules-${agent}`);
    } catch {
      fm = null;
    }
    const topLevel = fm && fm.fm && typeof fm.fm.install_target === "string"
      ? fm.fm.install_target.trim()
      : "";
    const nested = fm && fm.spec && typeof fm.spec.install_target === "string"
      ? fm.spec.install_target.trim()
      : "";
    const target = topLevel || nested;
    if (target && fs.existsSync(path.join(ctx.cwd, target))) {
      detected.add(agent);
      continue;
    }
    missing.push(agent);
  }
  if (missing.length) {
    return {
      status: "warn",
      detail: `no on-disk signal for declared agents: ${missing.join(", ")}`,
      data: { missing, detected: [...detected] },
    };
  }
  return {
    status: "pass",
    detail: `${declared.length} declared agent(s) have on-disk signals`,
    data: { detected: [...detected] },
  };
}
