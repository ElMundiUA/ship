import { loadArtifactCatalog, loadArtifactBody, type ArtifactEntry } from "@/lib/artifacts-fs";

export type WorkflowGroup = "delivery" | "quality" | "operations" | "governance";

export interface WorkflowEntry extends ArtifactEntry {
  group: WorkflowGroup | string;
}

export interface WorkflowsManifest {
  version: number;
  description: string;
  workflows: WorkflowEntry[];
}

export function loadWorkflowsManifest(): WorkflowsManifest {
  const cat = loadArtifactCatalog("workflows");
  return {
    version: cat.version,
    description: cat.description,
    workflows: cat.entries as WorkflowEntry[],
  };
}

export function loadWorkflowMarkdown(relPath: string): string {
  return loadArtifactBody(relPath);
}

export function getWorkflowById(id: string): WorkflowEntry | undefined {
  return loadWorkflowsManifest().workflows.find((w) => w.id === id);
}
