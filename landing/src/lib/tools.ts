import fs from "node:fs";
import path from "node:path";
import { repoRoot } from "@/lib/repo-path";

export type ToolGroup = "platform" | "tracker" | "ci" | "e2e" | "agents";

export interface ToolEntry {
  id: string;
  title: string;
  summary: string;
  path: string;
  tags: string[];
  group: ToolGroup | string;
}

export interface ToolsManifest {
  version: number;
  description: string;
  tools: ToolEntry[];
}

/** Manifest at repo root `tools/manifest.json`. */
export function toolsManifestPath(): string {
  const p = path.join(repoRoot(), "tools", "manifest.json");
  if (!fs.existsSync(p)) throw new Error("tools/manifest.json not found.");
  return p;
}

export function loadToolsManifest(): ToolsManifest {
  const raw = fs.readFileSync(toolsManifestPath(), "utf8");
  return JSON.parse(raw) as ToolsManifest;
}

export function loadToolMarkdown(relPath: string): string {
  const root = repoRoot();
  const candidate = path.resolve(root, relPath);
  if (!candidate.startsWith(root + path.sep) && candidate !== root) {
    throw new Error("Tool path escapes repository root.");
  }
  if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) {
    throw new Error(`Tool file missing: ${relPath}`);
  }
  return fs.readFileSync(candidate, "utf8");
}

export function getToolById(id: string): ToolEntry | undefined {
  return loadToolsManifest().tools.find((t) => t.id === id);
}
