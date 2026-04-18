import fs from "node:fs";
import path from "node:path";
import { repoRoot } from "@/lib/repo-path";

const DOC_DIR = "documentation";

function documentationDir(): string {
  return path.join(repoRoot(), DOC_DIR);
}

/** Relative path from `documentation/` (e.g. `getting-started/index.md`). */
export function slugToRelPath(slug: string[]): string | null {
  const base = slug.join("/");
  const root = documentationDir();
  const candidates =
    base === ""
      ? [path.join(root, "index.md")]
      : [path.join(root, base + ".md"), path.join(root, base, "index.md")];
  for (const abs of candidates) {
    if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
      return path.relative(root, abs).replace(/\\/g, "/");
    }
  }
  return null;
}

export function relPathToSlug(rel: string): string[] {
  const n = rel.replace(/\\/g, "/");
  if (n === "index.md") return [];
  if (n.endsWith("/index.md")) {
    return n.slice(0, -"/index.md".length).split("/").filter(Boolean);
  }
  return n.replace(/\.md$/, "").split("/").filter(Boolean);
}

function shouldSkip(relPath: string): boolean {
  const n = relPath.replace(/\\/g, "/");
  if (n.startsWith("framework/")) return true; /* long-form book lives at /book */
  if (n.startsWith("archive/")) return true;
  if (n.includes("/hooks/") || n.startsWith("hooks/")) return true;
  if (n.includes("/stylesheets/") || n.startsWith("stylesheets/")) return true;
  /* getting-started has its own static React route with the AgentSetupForm wizard; the
   * legacy MkDocs HTML form in documentation/getting-started/index.md does not work
   * outside MkDocs Material and must not be rendered through the catch-all. */
  if (n === "getting-started/index.md") return true;
  if (n.endsWith(".uk.md")) return true;
  return false;
}

export function listDocumentationPages(): { slug: string[]; rel: string }[] {
  const root = documentationDir();
  const out: { slug: string[]; rel: string }[] = [];

  function walk(dir: string) {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const abs = path.join(dir, ent.name);
      const rel = path.relative(root, abs).replace(/\\/g, "/");
      if (shouldSkip(rel)) continue;
      if (ent.isDirectory()) {
        walk(abs);
      } else if (ent.isFile() && ent.name.endsWith(".md")) {
        out.push({ rel, slug: relPathToSlug(rel) });
      }
    }
  }

  walk(root);
  out.sort((a, b) => a.rel.localeCompare(b.rel));
  return out;
}

export function readDocumentationFile(relFromDocumentation: string): string {
  const root = documentationDir();
  const candidate = path.resolve(root, relFromDocumentation);
  if (!candidate.startsWith(root + path.sep) && candidate !== root) {
    throw new Error("Path escapes documentation/");
  }
  if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) {
    throw new Error(`Not found: ${relFromDocumentation}`);
  }
  return fs.readFileSync(candidate, "utf8");
}
