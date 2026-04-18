import { loadArtifactCatalog, loadArtifactBody, type ArtifactEntry } from "@/lib/artifacts-fs";

export type ToolGroup = "platform" | "tracker" | "ci" | "e2e" | "agents";

export interface ToolEntry extends ArtifactEntry {
  group: ToolGroup | string;
}

export interface ToolsManifest {
  version: number;
  description: string;
  tools: ToolEntry[];
}

export function loadToolsManifest(): ToolsManifest {
  const cat = loadArtifactCatalog("tools");
  return {
    version: cat.version,
    description: cat.description,
    tools: cat.entries as ToolEntry[],
  };
}

export function loadToolMarkdown(relPath: string): string {
  return loadArtifactBody(relPath);
}

export function getToolById(id: string): ToolEntry | undefined {
  return loadToolsManifest().tools.find((t) => t.id === id);
}
