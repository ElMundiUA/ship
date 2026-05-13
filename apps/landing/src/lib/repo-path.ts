import fs from "node:fs";
import path from "node:path";

function hasDocumentationDir(dir: string): boolean {
  const doc = path.join(dir, "documentation");
  try {
    return fs.existsSync(doc) && fs.statSync(doc).isDirectory();
  } catch {
    return false;
  }
}

function resolveByWalkingUp(startDir: string): string | null {
  let dir = path.resolve(startDir);
  for (let i = 0; i < 10; i++) {
    if (hasDocumentationDir(dir)) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

/**
 * Monorepo root (directory that contains `documentation/`).
 * Walks up from `process.cwd()` and from `INIT_CWD` (set by npm when you run scripts from the repo root)
 * so dev servers do not crash when the working directory is not exactly `landing/`.
 */
export function repoRoot(): string {
  const env = process.env.REPO_ROOT?.trim();
  if (env) return path.resolve(env);

  const seeds = new Set<string>();
  seeds.add(process.cwd());
  const init = process.env.INIT_CWD?.trim();
  if (init) seeds.add(init);

  for (const seed of seeds) {
    const found = resolveByWalkingUp(seed);
    if (found) return found;
  }

  throw new Error(
    "Cannot resolve Ship repository root: no documentation/ directory found walking up from process.cwd() or INIT_CWD. " +
      "Run the app from the monorepo, or set REPO_ROOT to the folder that contains documentation/.",
  );
}
