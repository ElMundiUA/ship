import { loadArtifactCatalog, loadArtifactBody, type ArtifactEntry } from "@/lib/artifacts-fs";

export type PatternGroup = "lanes" | "cloud-agent" | "onboarding";

export interface PatternEntry extends ArtifactEntry {
  group: PatternGroup | string;
}

export interface PatternsManifest {
  version: number;
  description: string;
  patterns: PatternEntry[];
}

export function loadPatternsManifest(): PatternsManifest {
  const cat = loadArtifactCatalog("patterns");
  return {
    version: cat.version,
    description: cat.description,
    patterns: cat.entries as PatternEntry[],
  };
}

export function loadPatternMarkdown(relPath: string): string {
  return loadArtifactBody(relPath);
}

export function getPatternById(id: string): PatternEntry | undefined {
  return loadPatternsManifest().patterns.find((p) => p.id === id);
}
