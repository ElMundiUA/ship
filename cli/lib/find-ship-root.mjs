import fs from "node:fs";
import path from "node:path";

const MARKER_DIRS = [
  "artifacts/patterns",
  "artifacts/tools",
  "artifacts/collections",
];

function markersOk(dir) {
  return MARKER_DIRS.every((rel) => {
    const abs = path.join(dir, rel);
    try {
      return fs.statSync(abs).isDirectory();
    } catch {
      return false;
    }
  });
}

/**
 * Walk parents from cwd for a directory containing the v2 artifacts/ tree.
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
 * Root of the Ship monorepo (artifacts/<plural>/<id>/ARTIFACT.md present).
 * Set `SHIP_REPO` to an absolute path when not running from inside the tree.
 */
export function findShipRepoRoot() {
  const env = process.env.SHIP_REPO?.trim();
  if (env) {
    const r = path.resolve(env);
    if (!markersOk(r)) {
      throw new Error(
        `SHIP_REPO=${r} is not the Ship monorepo (expected artifacts/{patterns,tools,collections}/ at repo root).`,
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
        `SHIP_REPO=${r} is not the Ship monorepo (expected artifacts/{patterns,tools,collections}/ at repo root).`,
      );
    }
    return r;
  }
  return tryFindShipRepoRootFromWalk();
}
