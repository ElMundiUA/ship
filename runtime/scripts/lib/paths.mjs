/**
 * Repository layout: runtime/ (npm package) at repo root, prompts/ and documentation/ siblings.
 */
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** This file: runtime/scripts/lib/paths.mjs */
export const SCRIPTS_DIR = resolve(__dirname, "..");
/** npm package root (dist/, package.json, config/) */
export const RUNTIME_ROOT = resolve(__dirname, "..", "..");
/** Ship repo root (parent of runtime/) */
export const REPO_ROOT = resolve(RUNTIME_ROOT, "..");
export const ENV_PATH = resolve(REPO_ROOT, ".env");
/** Prompts read by cloud-agent-launch.mjs */
export const PROMPTS_CLOUD_AGENT_DIR = resolve(REPO_ROOT, "prompts", "cloud-agent");

export function gitRepoRoot(cwd = RUNTIME_ROOT) {
  try {
    return execFileSync("git", ["rev-parse", "--show-toplevel"], {
      cwd,
      encoding: "utf8",
    }).trim();
  } catch {
    return REPO_ROOT;
  }
}
