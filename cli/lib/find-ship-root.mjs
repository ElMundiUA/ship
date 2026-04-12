import fs from "node:fs";
import path from "node:path";

const MARKERS = ["workflows/manifest.json", "tools/manifest.json", "collections/manifest.json"];

function markersOk(dir) {
  return MARKERS.every((m) => fs.existsSync(path.join(dir, m)));
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
      throw new Error(`SHIP_REPO=${r} is not the Ship monorepo (expected workflows/, tools/, collections/ manifests).`);
    }
    return r;
  }
  let dir = path.resolve(process.cwd());
  for (;;) {
    if (markersOk(dir)) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(
    "Ship repo root not found: run from inside the ship clone, or set SHIP_REPO to the repository root.",
  );
}
