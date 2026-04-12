import fs from "node:fs";
import path from "node:path";
import { repoRoot } from "@/lib/repo-path";

export type CollectionGroup = "product" | "starter";

export interface CollectionEntry {
  id: string;
  title: string;
  summary: string;
  path: string;
  tags: string[];
  group: CollectionGroup | string;
}

export interface CollectionsManifest {
  version: number;
  description: string;
  collections: CollectionEntry[];
}

export function collectionsManifestPath(): string {
  const p = path.join(repoRoot(), "collections", "manifest.json");
  if (!fs.existsSync(p)) throw new Error("collections/manifest.json not found.");
  return p;
}

export function loadCollectionsManifest(): CollectionsManifest {
  const raw = fs.readFileSync(collectionsManifestPath(), "utf8");
  return JSON.parse(raw) as CollectionsManifest;
}

export function loadCollectionMarkdown(relPath: string): string {
  const root = repoRoot();
  const candidate = path.resolve(root, relPath);
  if (!candidate.startsWith(root + path.sep) && candidate !== root) {
    throw new Error("Collection path escapes repository root.");
  }
  if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) {
    throw new Error(`Collection file missing: ${relPath}`);
  }
  return fs.readFileSync(candidate, "utf8");
}

export function getCollectionById(id: string): CollectionEntry | undefined {
  return loadCollectionsManifest().collections.find((c) => c.id === id);
}
