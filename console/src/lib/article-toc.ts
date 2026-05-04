/**
 * Server-side TOC parser for markdown article bodies.
 *
 * Walks ``body_md`` line-by-line for ``# / ## / ###`` headings and
 * emits ``{ id, level, text }`` rows. The slug rule is deterministic
 * and shared with the in-prose ``h1``/``h2``/``h3`` ReactMarkdown
 * renderer so TOC links and rendered anchors agree.
 *
 * Fenced code blocks (``` ... ```) are skipped so a literal ``# command``
 * inside a shell snippet can't end up in the outline.
 */

export type TocEntry = {
  id: string;
  level: 1 | 2 | 3;
  text: string;
};

export function slugifyHeading(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

export function parseToc(body: string): TocEntry[] {
  if (!body) return [];
  const out: TocEntry[] = [];
  let inFence = false;
  for (const rawLine of body.split("\n")) {
    const line = rawLine.trimEnd();
    if (line.startsWith("```")) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const match = /^(#{1,3})\s+(.+?)\s*$/.exec(line);
    if (!match) continue;
    const level = match[1].length as 1 | 2 | 3;
    const text = match[2].trim();
    if (!text) continue;
    const id = slugifyHeading(text);
    if (!id) continue;
    out.push({ id, level, text });
  }
  return out;
}


/**
 * Match the slug ReactMarkdown will use for an inline heading. Pulls
 * the visible text out of whatever the renderer hands us as
 * ``children`` — strings, arrays of strings, or wrapped React nodes.
 */
export function headingIdFromChildren(children: unknown): string {
  return slugifyHeading(nodeText(children));
}


function nodeText(node: unknown): string {
  if (node == null) return "";
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (typeof node === "object") {
    const maybe = node as { props?: { children?: unknown } };
    if (maybe.props && "children" in maybe.props) {
      return nodeText(maybe.props.children);
    }
  }
  return "";
}
