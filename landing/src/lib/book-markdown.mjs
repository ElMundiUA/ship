/**
 * Turn Material-style markdown into something react-markdown + GFM can render.
 * Relative doc links become in-app routes (`/docs/...`, `/book`).
 *
 * Authoritative source. Imported both by the Next app (via the .ts re-export
 * in book-markdown.ts) and by landing/scripts/build-book-pdf.mjs so the PDF
 * looks identical to the rendered /book page.
 */

const HEADING_WITH_ID = /^(#{2,6})\s+(.+?)\s*\{#([^}]+)\}\s*$/;

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}

/** MkDocs `!!! tip` / `!!! note` blocks → blockquote callouts */
export function transformAdmonitions(source) {
  const lines = source.split("\n");
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const open = line.match(/^!!!\s+(\w+)\s+"(.*)"\s*$/);
    if (open) {
      const kind = open[1].toLowerCase();
      const title = open[2];
      i++;
      const body = [];
      if (kind === "note") {
        while (i < lines.length && lines[i].trim() !== "!!!") {
          body.push(lines[i]);
          i++;
        }
        if (i < lines.length && lines[i].trim() === "!!!") i++;
      } else {
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
export function transformHeadingIds(source) {
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
export function rewriteDocLinks(source) {
  return source.replace(/\]\((\.\.\/[^)]+)\)/g, (full, relPath) => {
    const [pathPart, ...hashParts] = relPath.split("#");
    const hash = hashParts.length ? `#${hashParts.join("#")}` : "";
    const normalized = pathPart.replace(/^\.\.\//, "").replace(/^\.\//, "");

    if (normalized === "framework/index.md") {
      return `](/book${hash})`;
    }

    const map = {
      "getting-started/index.md": "/getting-started",
      /* Pre-v0.11 catalog paths that lived under documentation/ moved
       * to top-level routes. /tools and /collections were deleted;
       * links to them should resolve via the default slug logic below. */
      "patterns/index.md": "/patterns",
      /* Docs pages that were renamed/relocated in v0.11. */
      "adoption/index.md": "/docs",
      "adoption/agent-playbook.md": "/docs/operating",
      "adoption/elmundi.md": "/use-cases",
      "adoption/delivery-quality-and-release-process.md": "/docs/operating",
      "adoption/agent-setup-contract.md": "/docs/discovery",
      "adoption/agent-launch-matrix.md": "/docs/agent-matrix",
      "prompts-workflows/index.md": "/patterns",
      "examples/elmundi/index.md": "/use-cases/elmundi",
      "framework/index.md": "/book",
      "rfc/index.md": "/docs/protocol",
      "legal-copyright.md": "/docs/legal",
      "index.md": "/docs",
    };

    const mapped = map[normalized];
    if (mapped !== undefined) {
      return `](${mapped}${hash})`;
    }

    const withoutMd = normalized.replace(/\.md$/, "");
    const slug = withoutMd.endsWith("/index") ? withoutMd.slice(0, -"/index".length) : withoutMd;
    /* `documentation/rfc/` was renamed to `documentation/protocol/` in v0.11. */
    const renamedSlug = slug.startsWith("rfc/") ? `protocol/${slug.slice("rfc/".length)}` : slug;
    const urlPath = renamedSlug === "" ? "/docs" : `/docs/${renamedSlug}`;
    return `](${urlPath}${hash})`;
  });
}

export function rewriteImages(source) {
  return source.replace(/!\[([^\]]*)\]\(\.\.\/diagrams\/([^)]+)\)/g, (_full, alt, file) => {
    return `![${alt}](/diagrams/${file})`;
  });
}

/**
 * Where the ECharts SVG infographics belong in the book.
 *
 * Each entry says: when this exact h3 line appears, insert the
 * companion figure *immediately after the chapter's prose* (we anchor
 * by the next chapter heading or a top-level horizontal rule, which
 * is the simplest stable seam in this book).
 *
 * Charts live in `landing/public/diagrams/charts/` and are produced
 * by `landing/scripts/build-book-charts.mjs`. They use the book's
 * cream/ink palette directly so:
 *   - the print pipeline opts out of SVG colour inversion (see
 *     `figure.book-chart` in build-book-pdf.mjs), and
 *   - the web `/book` page renders them on a paper-toned card so the
 *     warm palette reads correctly against the dark theme (see the
 *     `BookImg` chart branch + `.book-chart` CSS in book.css).
 *
 * The figure is emitted as raw HTML with a markdown image inside so
 * both renderers see the same DOM shape:
 *
 *   <figure class="book-chart">
 *     ![alt](src)
 *     <figcaption>...</figcaption>
 *   </figure>
 */
export const bookChartInsertions = [
  {
    afterHeading: "### Chapter 20.A — What to measure in the morning",
    image: "/diagrams/charts/book-morning-metrics.svg",
    alt: "Five morning-shape signals over a week",
    caption:
      "Fig. 20.A — The five \u201Cshape\u201D signals worth a wall-mounted panel. The shaded band on pick rate marks the healthy quarter; everything outside it is a question, not an alarm.",
  },
  {
    afterHeading: "### Chapter 5 — Throughput must be bounded",
    image: "/diagrams/charts/book-bounded-throughput.svg",
    alt: "Merged outcomes flatten while rework accelerates as WIP grows",
    caption:
      "Fig. 5 — Merged outcomes plateau quickly; rework and duplicate PRs do not. The bounded zone is the part of the curve a tired team can still defend at midnight.",
  },
  {
    afterHeading: "### Chapter 19 — Why \u201Calways on\u201D is a trap",
    image: "/diagrams/charts/book-role-grid.svg",
    alt: "Role grid: at most one delivery role per UTC slot",
    caption:
      "Fig. 19 — One role per slot, by construction. The grid is the calendar a firehose pretends does not exist.",
  },
  {
    afterHeading: "### Chapter 25.A — Where a fix becomes feedback",
    image: "/diagrams/charts/book-elm64-compound.svg",
    alt: "Fifteen identical patches in one calendar day",
    caption:
      "Fig. 25.A — Compound interest, drawn from a real commit log. By patch \u00233 the right move was to escalate the artifact, not to ship patch \u00234.",
  },
  {
    afterHeading: "### Chapter 31.A — The price of a bounded loop",
    image: "/diagrams/charts/book-cost-envelope.svg",
    alt: "Daily agent runs against a bounded envelope",
    caption:
      "Fig. 31.A — The envelope is the contract; spend is the receipt. A day above the line is not a billing event yet \u2014 it is a design conversation.",
  },
];

export function injectBookCharts(source) {
  const lines = source.split("\n");
  const out = [];
  for (let i = 0; i < lines.length; i += 1) {
    out.push(lines[i]);
    const hit = bookChartInsertions.find((c) => lines[i].trim() === c.afterHeading.trim());
    if (!hit) continue;
    /* Walk forward until the *end* of this chapter's prose — the next
     * h3, h2, top-level horizontal rule, OR the raw-HTML form of the
     * same headings (transformHeadingIds emits `<h3 id="..." …>` for
     * headings that carried a `{#anchor}` suffix). */
    let j = i + 1;
    while (j < lines.length) {
      const L = lines[j];
      if (
        /^###\s+/.test(L) ||
        /^##\s+/.test(L) ||
        /^<h[23]\b/.test(L) ||
        L.trim() === "---"
      ) {
        break;
      }
      j += 1;
    }
    /* Emit raw HTML for the entire figure (incl. the <img>) — putting
     * the markdown image syntax inside a block-level <figure> would
     * make both `marked` (PDF) and `react-markdown` (web) treat the
     * image as text, which is why earlier passes printed a stranded
     * `!Caption text` line where the chart should be. The
     * `not-prose` class opts the card out of @tailwindcss/typography
     * spacing on the web; the print pipeline ignores the class. */
    const altAttr = escapeHtml(hit.alt);
    const captionHtml = escapeHtml(hit.caption);
    const figureLines = [
      "",
      `<figure class="book-chart not-prose">`,
      `<img src="${hit.image}" alt="${altAttr}" />`,
      `<figcaption>${captionHtml}</figcaption>`,
      `</figure>`,
      "",
    ];
    for (let k = i + 1; k < j; k += 1) out.push(lines[k]);
    out.push(...figureLines);
    i = j - 1;
  }
  return out.join("\n");
}

export function preprocessBookMarkdown(source) {
  let md = source.replace(/^#\s+The book[^\n]*\n+/, "");
  md = transformAdmonitions(md);
  md = transformHeadingIds(md);
  md = rewriteImages(md);
  md = rewriteDocLinks(md);
  md = md.replace(
    /(!\[System context]\(\/diagrams\/architecture\.svg\)\s*\n+)/,
    "$1\n![SDLC states — lane language for tickets](/diagrams/sdlc-linear-states.svg)\n\n",
  );
  /* Chart injection runs *after* heading-id rewriting so we can match
   * both markdown (`### Chapter …`) and HTML (`<h3 id="…">`) headings
   * as boundaries. Both web (`/book`) and PDF pipelines call this. */
  md = injectBookCharts(md);
  return md.trimStart();
}
