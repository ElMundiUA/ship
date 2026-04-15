import fs from "node:fs";
import path from "node:path";

const MARKERS = [
  "workflows/manifest.json",
  "tools/manifest.json",
  "collections/manifest.json",
  "patterns/manifest.json",
];

function markersOk(dir) {
  return MARKERS.every((m) => fs.existsSync(path.join(dir, m)));
}

/**
 * Walk parents from cwd for a directory containing all Ship manifest markers.
 * @returns {string | null}
 */
export function tryFindShipRepoRootFromWalk() {
  let dir = path.resolve(process.cwd());
  for (;;) {
    if (markersOk(dir)) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

/**
 * Root of the Ship monorepo (manifests at repo root).
 * Set `SHIP_REPO` to an absolute path when not running from inside the tree.
 */
export function findShipRepoRoot() {
  const env = process.env.SHIP_REPO?.trim();
  if (env) {
    const r = path.resolve(env);
    if (!markersOk(r)) {
      throw new Error(
        `SHIP_REPO=${r} is not the Ship monorepo (expected tools/, workflows/, collections/, patterns/ manifests at repo root).`,
      );
    }
    return r;
  }
  const walked = tryFindShipRepoRootFromWalk();
  if (walked) return walked;
  throw new Error(
    "Ship repo root not found: run from inside the ship clone, or set SHIP_REPO to the repository root.",
  );
}

/**
 * When `SHIP_REPO` is unset, returns repo root only if cwd is inside the tree; otherwise `null` (use hosted catalog).
 * When `SHIP_REPO` is set, validates and returns that path or throws.
 * @returns {string | null}
 */
export function resolveShipRepoRootForCatalog() {
  const env = process.env.SHIP_REPO?.trim();
  if (env) {
    const r = path.resolve(env);
    if (!markersOk(r)) {
      throw new Error(
        `SHIP_REPO=${r} is not the Ship monorepo (expected tools/, workflows/, collections/, patterns/ manifests at repo root).`,
      );
    }
    return r;
  }
  return tryFindShipRepoRootFromWalk();
}
