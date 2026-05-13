import { detectAgentTargets } from "../../detect.mjs";

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
  // Phase 2.5 retired the local artifact cache; agent-rule install
  // paths are no longer available via cached frontmatter. Fall back
  // to the heuristic detector only — it covers the common targets
  // (CLAUDE.md, AGENTS.md, .cursor/rules/...) the seed PR writes.
  const detected = new Set(detectAgentTargets(ctx.cwd).map((t) => t.id));
  const missing = declared.filter((agent) => !detected.has(agent));
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
