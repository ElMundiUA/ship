#!/usr/bin/env node
/**
 * Copy Ship "book" source from documentation/ into landing so Next can read it at dev/build time.
 * Run automatically via npm predev / prebuild.
 */
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const landingRoot = join(__dirname, "..");
const repoRoot = join(landingRoot, "..");

const srcMd = join(repoRoot, "documentation", "framework", "index.md");
const destDir = join(landingRoot, "content");
const destMd = join(destDir, "book.md");
const destSvgDir = join(landingRoot, "public", "diagrams");
const diagramSources = [
  ["architecture.svg", join(repoRoot, "documentation", "diagrams", "architecture.svg")],
  ["sdlc-linear-states.svg", join(repoRoot, "documentation", "diagrams", "sdlc-linear-states.svg")],
];

mkdirSync(destDir, { recursive: true });
copyFileSync(srcMd, destMd);
mkdirSync(destSvgDir, { recursive: true });
for (const [name, srcPath] of diagramSources) {
  const destPath = join(destSvgDir, name);
  copyFileSync(srcPath, destPath);
  console.log("sync-book:", destPath);
}
console.log("sync-book:", destMd);
