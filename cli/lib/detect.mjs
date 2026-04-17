import fs from "node:fs";
import path from "node:path";

/**
 * @typedef {Object} AgentTarget
 * @property {string} id
 * @property {string} label
 * @property {string[]} paths
 * @property {number} confidence  0..1 — 1 for a definitive marker, 0.5 for directory-only.
 */

/**
 * Detect agent integration targets in a repository.
 *
 * Each detector looks for marker files or directories that uniquely identify a
 * given agent configuration (per RFC-0004 "Detect (signals)"). Returned list is
 * sorted by `confidence` descending so callers (init, doctor, stack emit) can
 * pick the strongest signals first.
 *
 * @param {string} cwd
 * @returns {AgentTarget[]}
 */
export function detectAgentTargets(cwd) {
  const exists = (...p) => fs.existsSync(path.join(cwd, ...p));
  const abs = (...p) => path.join(cwd, ...p);

  /** @type {AgentTarget[]} */
  const targets = [];

  if (exists(".cursor")) {
    targets.push({
      id: "cursor",
      label: "Cursor (`.cursor/` present)",
      paths: [abs(".cursor", "rules", "ship-artifacts-protocol.mdc")],
      confidence: exists(".cursor", "rules") ? 1 : 0.8,
    });
  }

  if (exists("AGENTS.md")) {
    targets.push({
      id: "agents-md",
      label: "OpenAI Codex / generic `AGENTS.md`",
      paths: [abs("AGENTS.md")],
      confidence: 1,
    });
  }

  if (exists("CLAUDE.md")) {
    targets.push({
      id: "claude-md",
      label: "Claude Code `CLAUDE.md`",
      paths: [abs("CLAUDE.md")],
      confidence: 1,
    });
  }

  if (exists(".codex")) {
    targets.push({
      id: "codex",
      label: "Codex config dir (`.codex/`)",
      paths: [abs(".codex", "SHIP_API.md")],
      confidence: 0.8,
    });
  }

  if (exists(".github", "copilot-instructions.md")) {
    targets.push({
      id: "copilot",
      label: "GitHub Copilot instructions",
      paths: [abs(".github", "copilot-instructions.md")],
      confidence: 1,
    });
  }

  const aiderSignals = [".aider.conf.yml", "AIDER.md", ".aider"];
  const aiderHit = aiderSignals.find((p) => exists(p));
  if (aiderHit) {
    targets.push({
      id: "aider",
      label: "Aider (`.aider.conf.yml` present)",
      paths: [abs("AIDER.md")],
      confidence: aiderHit === ".aider" ? 0.5 : 1,
    });
  }

  const clineSignals = [".clinerules", ".rooignore"];
  const clineHit = clineSignals.find((p) => exists(p));
  if (clineHit) {
    targets.push({
      id: "cline",
      label: "Cline/Roo (`.clinerules`)",
      paths: [abs(".clinerules")],
      confidence: 1,
    });
  }

  const continueSignals = [
    path.join(".continue", "config.json"),
    path.join(".continue", "config.yaml"),
    ".continue",
  ];
  const continueHit = continueSignals.find((p) => exists(p));
  if (continueHit) {
    targets.push({
      id: "continue",
      label: "Continue.dev (`.continue/`)",
      paths: [abs(".continue", "ship.md")],
      confidence: continueHit === ".continue" ? 0.5 : 1,
    });
  }

  if (exists(".windsurfrules")) {
    targets.push({
      id: "windsurf",
      label: "Windsurf (`.windsurfrules`)",
      paths: [abs(".windsurfrules")],
      confidence: 1,
    });
  }

  const zedSignals = [path.join(".zed", "settings.json"), ".zed"];
  const zedHit = zedSignals.find((p) => exists(p));
  if (zedHit) {
    targets.push({
      id: "zed",
      label: "Zed AI (`.zed/`)",
      paths: [abs(".zed", "ship.md")],
      confidence: zedHit === ".zed" ? 0.5 : 1,
    });
  }

  const geminiSignals = ["GEMINI.md", ".gemini"];
  const geminiHit = geminiSignals.find((p) => exists(p));
  if (geminiHit) {
    targets.push({
      id: "gemini",
      label: "Gemini CLI (`GEMINI.md`)",
      paths: [abs("GEMINI.md")],
      confidence: geminiHit === ".gemini" ? 0.5 : 1,
    });
  }

  if (exists(".opencode")) {
    targets.push({
      id: "opencode",
      label: "OpenCode (`.opencode/`)",
      paths: [abs(".opencode", "ship.md")],
      confidence: 0.5,
    });
  }

  if (exists(".cursor", "environments.json")) {
    targets.push({
      id: "cursor-cloud",
      label: "Cursor Cloud Agent env",
      paths: [abs(".cursor", "environments.json")],
      confidence: 1,
    });
  }

  return targets.sort((a, b) => b.confidence - a.confidence);
}

/**
 * Fixed catalog of agent ids + default paths used when the user forces
 * an agent via `--agents` or `--only` and the marker is missing.
 * Keeping this in sync with detectAgentTargets() is intentional: the
 * "target file if missing" column in RFC-0004 maps here.
 */
/**
 * Map a raw on-disk agent signal id to the preferred agent id after taking
 * the declared `.ship/config.yml` agents into account. Today this only
 * re-maps `agents-md` → `codex` when config lists `codex`, so doctor and
 * verify can reconcile a Codex-configured repo that only has `AGENTS.md`
 * on disk (the codex rules-render target emits `AGENTS.md`, not
 * `.codex/SHIP_API.md`).
 *
 * @param {string} signalId raw detector id (e.g. "agents-md")
 * @param {string[]=} configuredAgents agents listed in `.ship/config.yml`
 * @returns {string} preferred agent id after reconciliation
 */
export function resolveAgentSignal(signalId, configuredAgents) {
  const configured = Array.isArray(configuredAgents) ? configuredAgents : [];
  if (signalId === "agents-md" && configured.includes("codex")) return "codex";
  return signalId;
}

export const KNOWN_AGENTS = Object.freeze({
  cursor: { label: "Cursor", targetRel: [".cursor", "rules", "ship-artifacts-protocol.mdc"] },
  "agents-md": { label: "AGENTS.md (Codex/generic)", targetRel: ["AGENTS.md"] },
  "claude-md": { label: "Claude Code CLAUDE.md", targetRel: ["CLAUDE.md"] },
  codex: { label: "Codex (.codex/)", targetRel: [".codex", "SHIP_API.md"] },
  copilot: { label: "GitHub Copilot", targetRel: [".github", "copilot-instructions.md"] },
  aider: { label: "Aider", targetRel: ["AIDER.md"] },
  cline: { label: "Cline/Roo", targetRel: [".clinerules"] },
  continue: { label: "Continue.dev", targetRel: [".continue", "ship.md"] },
  windsurf: { label: "Windsurf", targetRel: [".windsurfrules"] },
  zed: { label: "Zed", targetRel: [".zed", "ship.md"] },
  gemini: { label: "Gemini CLI", targetRel: ["GEMINI.md"] },
  opencode: { label: "OpenCode", targetRel: [".opencode", "ship.md"] },
  "cursor-cloud": { label: "Cursor Cloud Agent", targetRel: [".cursor", "environments.json"] },
});
