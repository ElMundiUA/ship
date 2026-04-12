import fs from "node:fs";
import path from "node:path";

/**
 * @param {string} cwd
 * @returns {{ id: string; label: string; paths: string[] }[]}
 */
export function detectAgentTargets(cwd) {
  /** @type {{ id: string; label: string; paths: string[] }[]} */
  const targets = [];

  const cursorDir = path.join(cwd, ".cursor");
  const cursorRules = path.join(cursorDir, "rules");
  if (fs.existsSync(cursorDir)) {
    targets.push({
      id: "cursor",
      label: "Cursor (`.cursor/` present)",
      paths: [path.join(cursorRules, "ship-methodology-api.mdc")],
    });
  }

  const agents = path.join(cwd, "AGENTS.md");
  if (fs.existsSync(agents)) {
    targets.push({
      id: "agents-md",
      label: "OpenAI Codex / generic `AGENTS.md`",
      paths: [agents],
    });
  }

  const claude = path.join(cwd, "CLAUDE.md");
  if (fs.existsSync(claude)) {
    targets.push({
      id: "claude-md",
      label: "Claude Code `CLAUDE.md`",
      paths: [claude],
    });
  }

  const codexDir = path.join(cwd, ".codex");
  if (fs.existsSync(codexDir)) {
    targets.push({
      id: "codex",
      label: "Codex config dir (`.codex/`)",
      paths: [path.join(codexDir, "SHIP_API.md")],
    });
  }

  const copilot = path.join(cwd, ".github", "copilot-instructions.md");
  if (fs.existsSync(copilot)) {
    targets.push({
      id: "copilot",
      label: "GitHub Copilot instructions",
      paths: [copilot],
    });
  }

  return targets;
}
