#!/usr/bin/env node
/**
 * Sync shared diagrams from documentation/diagrams/ into landing/public/ so
 * Next can read them at dev/build time. Run automatically via npm predev / prebuild.
 *
 * As of v0.11 the long-form book is authored directly in landing/content/book.md;
 * it is no longer mirrored from documentation/framework/.
 */
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const landingRoot = join(__dirname, "..");
// landingRoot = apps/landing/  →  repo root is two levels up.
const repoRoot = join(landingRoot, "..", "..");

const destSvgDir = join(landingRoot, "public", "diagrams");
const diagramSources = [
  ["architecture.svg", join(repoRoot, "documentation", "diagrams", "architecture.svg")],
  ["sdlc-linear-states.svg", join(repoRoot, "documentation", "diagrams", "sdlc-linear-states.svg")],
];

mkdirSync(destSvgDir, { recursive: true });
for (const [name, srcPath] of diagramSources) {
  const destPath = join(destSvgDir, name);
  copyFileSync(srcPath, destPath);
  console.log("sync-book:", destPath);
}
