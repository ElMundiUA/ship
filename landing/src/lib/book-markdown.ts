/**
 * Turn Material-style markdown into something react-markdown + GFM can render.
 * Relative doc links become in-app routes (`/docs/...`, `/book`).
 */

const HEADING_WITH_ID = /^(#{2,6})\s+(.+?)\s*\{#([^}]+)\}\s*$/;

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}

/** MkDocs `!!! tip` / `!!! note` blocks → blockquote callouts */
export function transformAdmonitions(source: string): string {
  const lines = source.split("\n");
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const open = line.match(/^!!!\s+(\w+)\s+"(.*)"\s*$/);
    if (open) {
      const kind = open[1].toLowerCase();
      const title = open[2];
      i++;
      const body: string[] = [];
      if (kind === "note") {
        while (i < lines.length && lines[i].trim() !== "!!!") {
          body.push(lines[i]);
          i++;
        }
        if (i < lines.length && lines[i].trim() === "!!!") i++;
      } else {
        // tip (and others): body lines are indented with 4 spaces until a non-indented non-empty line
        while (i < lines.length) {
          const L = lines[i];
          if (L === "") {
            body.push("");
            i++;
            continue;
          }
          if (L.startsWith("    ")) {
            body.push(L.slice(4));
            i++;
            continue;
          }
          break;
        }
      }
      const label = kind === "tip" ? "Tip" : kind === "note" ? "Note" : kind.charAt(0).toUpperCase() + kind.slice(1);
      out.push("");
      out.push(`> **${label} — ${title}**`);
      for (const b of body) {
        if (b === "") out.push(">");
        else out.push(`> ${b}`);
      }
      out.push("");
      continue;
    }
    out.push(line);
    i++;
  }
  return out.join("\n");
}

/** `## Title {#id}` → raw HTML heading with stable id (jump links in intro still work). */
export function transformHeadingIds(source: string): string {
  return source
    .split("\n")
    .map((line) => {
      const m = line.match(HEADING_WITH_ID);
      if (!m) return line;
      const level = m[1].length;
      const text = m[2].trim();
      const id = m[3];
      const tag = `h${Math.min(6, Math.max(2, level))}`;
      return `<${tag} id="${escapeHtml(id)}" class="book-heading">${escapeHtml(text)}</${tag}>`;
    })
    .join("\n");
}

/** `](../path/to/file.md#hash)` → in-app route (`/docs/...` or `/book`). */
export function rewriteDocLinks(source: string): string {
  return source.replace(/\]\((\.\.\/[^)]+)\)/g, (full, relPath: string) => {
    const [pathPart, ...hashParts] = relPath.split("#");
    const hash = hashParts.length ? `#${hashParts.join("#")}` : "";
    const normalized = pathPart.replace(/^\.\.\//, "").replace(/^\.\//, "");

    if (normalized === "framework/index.md") {
      return `](/book${hash})`;
    }

    const map: Record<string, string> = {
      "getting-started/index.md": "/docs/getting-started",
      "examples/elmundi/index.md": "/docs/examples/elmundi",
      "prompts-workflows/index.md": "/docs/prompts-workflows",
      "tools/index.md": "/docs/tools",
      "index.md": "/docs",
    };

    const mapped = map[normalized];
    if (mapped !== undefined) {
      return `](${mapped}${hash})`;
    }

    const withoutMd = normalized.replace(/\.md$/, "");
    const slug = withoutMd.endsWith("/index") ? withoutMd.slice(0, -"/index".length) : withoutMd;
    const urlPath = slug === "" ? "/docs" : `/docs/${slug}`;
    return `](${urlPath}${hash})`;
  });
}

export function rewriteImages(source: string): string {
  return source.replace(/!\[([^\]]*)\]\(\.\.\/diagrams\/([^)]+)\)/g, (_full, alt, file) => {
    return `![${alt}](/diagrams/${file})`;
  });
}

export function preprocessBookMarkdown(source: string): string {
  let md = source.replace(/^#\s+The book[^\n]*\n+/, "");
  md = transformAdmonitions(md);
  md = transformHeadingIds(md);
  // Images first: `rewriteDocLinks` also matches `](../...)` inside `![alt](...)` and would break diagram src.
  md = rewriteImages(md);
  md = rewriteDocLinks(md);
  // Second shipped diagram — same public path convention as sync-book.mjs
  md = md.replace(
    /(!\[System context]\(\/diagrams\/architecture\.svg\)\s*\n+)/,
    "$1\n![SDLC states — lane language for tickets](/diagrams/sdlc-linear-states.svg)\n\n",
  );
  return md.trimStart();
}
