import fs from "node:fs";
import path from "node:path";
import { repoRoot } from "@/lib/repo-path";

export type WorkflowGroup = "delivery" | "quality" | "operations" | "governance";

export interface WorkflowEntry {
  id: string;
  title: string;
  summary: string;
  path: string;
  tags: string[];
  group: WorkflowGroup | string;
}

export interface WorkflowsManifest {
  version: number;
  description: string;
  workflows: WorkflowEntry[];
}

export function workflowsManifestPath(): string {
  const p = path.join(repoRoot(), "workflows", "manifest.json");
  if (!fs.existsSync(p)) throw new Error("workflows/manifest.json not found.");
  return p;
}

export function loadWorkflowsManifest(): WorkflowsManifest {
  const raw = fs.readFileSync(workflowsManifestPath(), "utf8");
  return JSON.parse(raw) as WorkflowsManifest;
}

export function loadWorkflowMarkdown(relPath: string): string {
  const root = repoRoot();
  const candidate = path.resolve(root, relPath);
  if (!candidate.startsWith(root + path.sep) && candidate !== root) {
    throw new Error("Workflow path escapes repository root.");
  }
  if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) {
    throw new Error(`Workflow file missing: ${relPath}`);
  }
  return fs.readFileSync(candidate, "utf8");
}

export function getWorkflowById(id: string): WorkflowEntry | undefined {
  return loadWorkflowsManifest().workflows.find((w) => w.id === id);
}
