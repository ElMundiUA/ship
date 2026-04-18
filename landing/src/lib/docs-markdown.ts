import fs from "node:fs";
import path from "node:path";
import { repoRoot } from "@/lib/repo-path";
import { rewriteDocLinks, rewriteImages, transformAdmonitions, transformHeadingIds } from "@/lib/book-markdown";

const SNIPPET = /^--8<--\s+"([^"]+)"\s*$/gm;

/**
 * Inline MkDocs Material-style `--8<-- "path"` snippets (paths relative to repo root)
 * used by some documentation sources to compose long pages from shared fragments.
 */
export function expandSnippetIncludes(source: string): string {
  const root = repoRoot();
  return source.replace(SNIPPET, (_full, rel: string) => {
    const safe = rel.replace(/\\/g, "/").replace(/^\/+/, "");
    const abs = path.resolve(root, safe);
    if (!abs.startsWith(root + path.sep) && abs !== root) {
      return `\n\n_\[snippet skipped: path outside repo\]_\n\n`;
    }
    if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
      return `\n\n_\[snippet missing: ${safe}\]_\n\n`;
    }
    const body = fs.readFileSync(abs, "utf8");
    return `\n\n${body}\n\n`;
  });
}

/** Full pipeline for markdown under `documentation/` rendered on the Next site. */
export function preprocessDocumentationMarkdown(source: string): string {
  let md = expandSnippetIncludes(source);
  md = transformAdmonitions(md);
  md = transformHeadingIds(md);
  md = rewriteImages(md);
  md = rewriteDocLinks(md);
  return md;
}
