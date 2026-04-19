/**
 * Lightweight pre-parser for documentation markdown:
 *
 *   - Pull the H1 (`# Title`) out so the page can render it as a hero.
 *   - Pull the first plain paragraph after the H1 as the lede.
 *   - Build a flat list of H2 anchors for the right-rail mini-TOC.
 *
 * Returns the original markdown with the H1 + lede stripped (we render those
 * separately in React); everything else flows through ReactMarkdown unchanged
 * so admonitions, tables, snippets, etc. still work.
 */

export type DocsPageParts = {
  title: string | null;
  lede: string | null;
  body: string;
  toc: { id: string; text: string }[];
};

export function slugifyHeading(text: string): string {
  return text
    .toLowerCase()
    .replace(/`/g, "")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
}

const slugify = slugifyHeading;

function stripInlineMd(text: string): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .trim();
}

export function parseDocsPage(source: string): DocsPageParts {
  const lines = source.replace(/\r\n/g, "\n").split("\n");

  let title: string | null = null;
  let titleLineIdx = -1;
  for (let i = 0; i < lines.length; i++) {
    const m = /^#\s+(.+?)\s*$/.exec(lines[i]);
    if (m) {
      title = stripInlineMd(m[1]);
      titleLineIdx = i;
      break;
    }
  }

  let lede: string | null = null;
  let ledeStart = -1;
  let ledeEnd = -1;
  if (titleLineIdx >= 0) {
    let i = titleLineIdx + 1;
    /* Skip blank lines after the title. */
    while (i < lines.length && lines[i].trim() === "") i++;
    if (i < lines.length) {
      const first = lines[i];
      const isHeading = /^#{1,6}\s/.test(first);
      const isBlock =
        first.startsWith(":::") ||
        first.startsWith("!!!") ||
        first.startsWith("```") ||
        first.startsWith(">") ||
        first.startsWith("|") ||
        first.startsWith("- ") ||
        first.startsWith("* ") ||
        /^\d+\.\s/.test(first);
      if (!isHeading && !isBlock) {
        ledeStart = i;
        let j = i;
        while (j < lines.length && lines[j].trim() !== "") j++;
        ledeEnd = j;
        lede = lines.slice(ledeStart, ledeEnd).join(" ").trim();
      }
    }
  }

  /* Build the body without the title line and without the lede paragraph;
   * preserve everything else byte-for-byte so tables / snippets are intact. */
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (i === titleLineIdx) continue;
    if (ledeStart >= 0 && i >= ledeStart && i < ledeEnd) continue;
    out.push(lines[i]);
  }
  /* Trim leading blank lines. */
  let body = out.join("\n").replace(/^\s+/, "");

  /* TOC: every H2 in the surviving body, ignoring blocks inside fenced code. */
  const toc: { id: string; text: string }[] = [];
  const seen = new Set<string>();
  let inFence = false;
  for (const line of body.split("\n")) {
    if (/^```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = /^##\s+(.+?)\s*$/.exec(line);
    if (m) {
      const text = stripInlineMd(m[1]);
      let id = slugify(text);
      let n = 2;
      while (seen.has(id)) {
        id = `${slugify(text)}-${n++}`;
      }
      seen.add(id);
      toc.push({ id, text });
    }
  }

  return { title, lede, body, toc };
}
