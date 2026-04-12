import fs from "node:fs";
import path from "node:path";
import { repoRoot } from "@/lib/repo-path";

export type PatternGroup = "lanes" | "cloud-agent" | "onboarding";

export interface PatternEntry {
  id: string;
  title: string;
  summary: string;
  path: string;
  tags: string[];
  group: PatternGroup | string;
}

export interface PatternsManifest {
  version: number;
  description: string;
  patterns: PatternEntry[];
}

/** Manifest at repo root `patterns/manifest.json`. */
export function patternsManifestPath(): string {
  const p = path.join(repoRoot(), "patterns", "manifest.json");
  if (!fs.existsSync(p)) throw new Error("patterns/manifest.json not found.");
  return p;
}

export function loadPatternsManifest(): PatternsManifest {
  const raw = fs.readFileSync(patternsManifestPath(), "utf8");
  return JSON.parse(raw) as PatternsManifest;
}

export function loadPatternMarkdown(relPath: string): string {
  const root = repoRoot();
  const candidate = path.resolve(root, relPath);
  if (!candidate.startsWith(root + path.sep) && candidate !== root) {
    throw new Error("Pattern path escapes repository root.");
  }
  if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) {
    throw new Error(`Pattern file missing: ${relPath}`);
  }
  return fs.readFileSync(candidate, "utf8");
}

export function getPatternById(id: string): PatternEntry | undefined {
  return loadPatternsManifest().patterns.find((p) => p.id === id);
}
