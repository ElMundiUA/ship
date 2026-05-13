#!/usr/bin/env node
/**
 * Build a downloadable PDF of the Ship book.
 *
 * Pipeline:
 *   documentation/framework/index.md
 *     -> sync-book.mjs (already runs in predev/prebuild) -> landing/content/book.md
 *     -> preprocessBookMarkdown (same one used by the /book Next page)
 *     -> stripInAppLinks + rewriteImagesForPrint + buildToc + injectBookCharts
 *     -> md-to-pdf (puppeteer-core + headless chromium)
 *     -> landing/public/book.pdf  (served from /book.pdf)
 *
 * Run from repo root:
 *   npm run book:pdf
 *
 * Or from the landing workspace:
 *   npm run book:pdf --prefix landing
 *
 * The PDF is committed alongside other static assets so the production
 * site does not need a chromium binary at deploy time.
 *
 * Print-design notes (this revision is the "polished book" pass that
 * superseded the v1 dark-cover and v2 cream-with-rivers versions):
 *   - serif body type with proper `lang="en"` so Chrome's automatic
 *     hyphenation finally triggers (previous PDFs had massive justified
 *     rivers because no language was set);
 *   - blockquote/callout title rendered as a small uppercase eyebrow on
 *     its own line, body in roman (not italic) so inline code stays
 *     legible inside Field notes;
 *   - opening paragraph of every part gets a real drop cap;
 *   - SVG ECharts infographics are injected after the chapters that
 *     motivate them — see injectBookCharts() — and rendered with no
 *     colour inversion (the infographic palette already matches the
 *     paper).
 */
import { readFileSync, mkdirSync, statSync, existsSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { mdToPdf } from "md-to-pdf";

import { preprocessBookMarkdown } from "../src/lib/book-markdown.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const landingRoot = join(__dirname, "..");
// landingRoot = apps/landing/  →  repo root is two levels up.
const repoRoot = join(landingRoot, "..", "..");
const srcMd = join(landingRoot, "content", "book.md");
const fallbackSrcMd = join(repoRoot, "documentation", "framework", "index.md");
const destDir = join(landingRoot, "public");
const destPdf = join(destDir, "book.pdf");
const diagramsDir = join(landingRoot, "public", "diagrams");
const tmpHtmlPath = join(destDir, ".book.tmp.html");

if (!existsSync(srcMd)) {
  if (!existsSync(fallbackSrcMd)) {
    console.error("build-book-pdf: neither", srcMd, "nor", fallbackSrcMd, "exist. Run sync-book first.");
    process.exit(1);
  }
  mkdirSync(dirname(srcMd), { recursive: true });
  copyFileSync(fallbackSrcMd, srcMd);
  console.log("build-book-pdf: copied fallback markdown source ->", srcMd);
}

const raw = readFileSync(srcMd, "utf-8");
/* Extract TOC from RAW markdown — preprocessBookMarkdown rewrites
 * `## Title {#id}` into raw <h2 id="..."> HTML, which doesn't match a
 * simple `^##\s+` regex anymore. */
const tocSource = raw;
const processed = preprocessBookMarkdown(raw);

/** Strip in-app cross-links — they collapse to bold so the URL never bleeds onto the page. */
function stripInAppLinks(md) {
  /* The regex used to be `/\[([^\]]+)\]\((\/[^)\s]+)\)/g` which also
   * happily matched the `[alt](/diagrams/foo.svg)` chunk inside an
   * image — `![alt](/diagrams/foo.svg)` — turning the whole thing
   * into `!**alt**` and printing a stranded "!System context" in the
   * PDF. The negative-lookbehind for `!` keeps real images intact. */
  return md.replace(/(?<!\!)\[([^\]]+)\]\((\/[^)\s]+)\)/g, (_full, label, href) => {
    if (href.startsWith("//")) return `[${label}](${href})`;
    return `**${label}**`;
  });
}

/** Rewrite local diagram images so md-to-pdf can render them.
 *
 * md-to-pdf calls page.setContent(html), which leaves the page origin
 * at about:blank. Chromium then refuses to load `file://` resources
 * for security reasons (silent broken image, alt text fallback in the
 * PDF). To avoid that we read each SVG from disk at build time and
 * embed it as a `data:image/svg+xml;base64,…` URL — slightly larger
 * markdown, but the PDF actually shows the picture. */
function rewriteImagesForPrint(md) {
  function inline(href) {
    const absolute = join(diagramsDir, href.replace(/^\/diagrams\//, ""));
    if (!existsSync(absolute)) {
      console.warn("build-book-pdf: missing diagram for inlining ->", absolute);
      return null;
    }
    const svg = readFileSync(absolute, "utf-8");
    const base64 = Buffer.from(svg, "utf-8").toString("base64");
    return `data:image/svg+xml;base64,${base64}`;
  }

  /* Markdown image form: `![alt](/diagrams/foo.svg)` */
  let out = md.replace(/!\[([^\]]*)\]\((\/diagrams\/[^)]+)\)/g, (_full, alt, href) => {
    const data = inline(href);
    if (!data) return `![${alt}](file://${join(diagramsDir, href.replace(/^\/diagrams\//, ""))})`;
    return `![${alt}](${data})`;
  });

  /* Raw HTML image form: `<img src="/diagrams/foo.svg" alt="..." />`.
   * The chart figures emitted by `injectBookCharts` use this shape so
   * the markdown parser doesn't strand the image as text inside the
   * surrounding <figure> block. Same Chromium constraint applies —
   * we still need to base64-embed the SVG. */
  out = out.replace(/<img\s+([^>]*?)src="(\/diagrams\/[^"]+)"([^>]*)>/g, (full, before, href, after) => {
    const data = inline(href);
    if (!data) return full;
    return `<img ${before}src="${data}"${after}>`;
  });

  return out;
}

/** Slugify exactly the way the page renderer / TOC anchors do — keep us in sync. */
function slugify(text) {
  return String(text)
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/[^\w\s-]+/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

/**
 * Build a two-column TOC of parts (h2) and their chapters (h3).
 * Returns HTML that the `## Contents` placeholder is replaced with.
 */
function extractToc(md) {
  const lines = md.split("\n");
  const parts = [];
  let inFence = false;
  let current = null;

  for (const line of lines) {
    if (/^```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;

    const h2 = line.match(/^##\s+(.+?)(?:\s*\{#([^}]+)\})?\s*$/);
    if (h2) {
      const text = h2[1].trim();
      const id = h2[2] || slugify(text);
      current = { text, id, chapters: [] };
      parts.push(current);
      continue;
    }

    const h3 = line.match(/^###\s+(.+?)(?:\s*\{#([^}]+)\})?\s*$/);
    if (h3 && current) {
      const text = h3[1].trim();
      const id = h3[2] || slugify(text);
      current.chapters.push({ text, id });
    }
  }

  return parts;
}

const toc = extractToc(tocSource);

/* Build the TOC as <div class="pdf-toc-row"> rows. We deliberately avoid <table>/<tr>
 * because marked (md-to-pdf's parser) recognizes <table> as a block element but then
 * silently drops the inner <tr>/<td> content when the table is not in markdown-table
 * syntax. <div> rows with display:flex give us the same two-column look without
 * fighting the parser. */
const tocBlocks = [];
toc.forEach((part, i) => {
  tocBlocks.push(
    `<div class="pdf-toc-row pdf-toc-part">` +
      `<div class="pdf-toc-num">Part ${i + 1}</div>` +
      `<div class="pdf-toc-title">${escapeHtml(part.text)}</div>` +
      `</div>`,
  );
  for (const ch of part.chapters) {
    tocBlocks.push(
      `<div class="pdf-toc-row pdf-toc-ch">` +
        `<div class="pdf-toc-num"></div>` +
        `<div class="pdf-toc-title">${escapeHtml(ch.text)}</div>` +
        `</div>`,
    );
  }
});
/* marked treats consecutive HTML elements on a single line as one block, then re-parses
 * children as markdown — which silently drops our inline <div> rows. Splitting each row
 * onto its own line, separated by blank lines, makes marked treat every row as a fresh
 * block-level HTML element and pass it through verbatim. */
/* No explicit pdf-toc-pagebreak div — the very next h2 in the body
 * already triggers `page-break-before: always`, and adding a second
 * break would leave a blank page between the TOC and the Prologue. */
const tocHtml = [
  `<div class="pdf-toc">`,
  ``,
  `<h2 class="pdf-toc-h2">Contents</h2>`,
  ``,
  ...tocBlocks.flatMap((row) => [row, ``]),
  `</div>`,
].join("\n");

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* Chart injection now lives in the shared `preprocessBookMarkdown`
 * helper (book-markdown.mjs) so the web `/book` page and this PDF
 * pipeline pick the same insertion points. By the time we reach the
 * `processed` value above, the chart figures are already inlined.
 * The PDF-only `rewriteImagesForPrint` step then base64-embeds the
 * referenced SVGs because Chromium refuses `file://` images when the
 * document is loaded via `page.setContent()`. */
const printable = rewriteImagesForPrint(stripInAppLinks(processed));

const css = `
  /* Page geometry trick to keep the cream "paper" colour edge-to-edge.
   *
   * Chromium does NOT extend body backgrounds into the page margin
   * areas during PDF print (and \`@page { background }\` is unsupported).
   * If we leave the default 22mm margins the text sits on a cream
   * rectangle surrounded by a hard white frame — exactly what we
   * don't want.
   *
   * Workaround: zero the LEFT/RIGHT @page margins so the body fills
   * the sheet horizontally and recreate the visual side gutter via
   * body padding. Keep the TOP/BOTTOM @page margins (26mm / 20mm)
   * because that's where Puppeteer paints the running header/footer
   * — and those templates carry their own cream backgrounds so the
   * top/bottom bands print cream too. The cover render injects a
   * \`@page { margin: 0 }\` override on top of this rule so it can
   * occupy the entire sheet without leaving a header gutter.
   */
  @page { size: A4; margin: 26mm 0 20mm 0; }

  /* Cover-only render uses zero margin (set via Puppeteer pdf_options) so
   * we don't need an @page :first override here. The !important wins
   * over the body padding rule below — without it the 18mm side
   * padding pushes the 210mm-wide cover card off the page and forces
   * an overflow onto a second page. */
  body.book-cover-only {
    margin: 0 !important;
    padding: 0 !important;
    width: 210mm !important;
    height: 297mm !important;
    overflow: hidden !important;
  }
  body.book-cover-only > * { max-width: 210mm; }

  /* Palette switched from a cream paper to plain white per editorial
   * decision — books print on white perfectly well, and the cream
   * tint was creating a perceptual seam between the body and the
   * Puppeteer-rendered running header/footer bands (those bands paint
   * over a fixed margin area; if their colour is even one ΔE off,
   * the reader sees a "sticker" line). White on white kills that
   * problem at the source. The warm quote/code tints stay as soft
   * neutrals so callouts and inline code still feel like *paper*
   * artefacts and not transplanted web chips. */
  :root {
    --paper: #ffffff;
    --paper-soft: #f6f3eb;
    --ink: #1b1d24;
    --ink-soft: #4d525c;
    --ink-faint: #7b818d;
    --rule: #d9d2c3;
    --rule-faint: #ebe5d6;
    --accent: #a4451b;
    --accent-soft: #b96b3f;
    --accent-pale: #e8c9a8;
    --code-bg: #f1ebda;
    --code-ink: #2b2c33;
    --quote-bg: #f6f1e3;
    --quote-bar: #c98a4f;
  }

  html, body {
    background: var(--paper) !important;
    color: var(--ink) !important;
  }
  body {
    /* The visible side gutter the reader sees is body padding now;
     * @page sets margin: 26mm 0 20mm 0 so the cream body bleeds to
     * the page edge horizontally. The 18mm side padding pulls the
     * text column back into a comfortable measure. Top/bottom are
     * handled by the @page rule (Puppeteer paints the running
     * header / footer in those bands). */
    padding-left: 18mm;
    padding-right: 18mm;
    box-sizing: border-box;
    /* Charter / Iowan Old Style ship as system fonts on macOS+Linux
     * containers used by the build runner; the fallback chain below
     * keeps the PDF readable when the build runs on a slim image
     * (puppeteer-core grabs whatever DejaVu Serif equivalent is
     * available). */
    font-family: "Charter", "Iowan Old Style", "Source Serif Pro", "PT Serif",
                 "Georgia", "DejaVu Serif", "Times New Roman", serif;
    font-size: 10.6pt;
    line-height: 1.55;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
    text-rendering: optimizeLegibility;
    font-feature-settings: "kern" 1, "liga" 1, "onum" 1, "calt" 1;
    /* hyphenation needs the language to be set on a parent — the
     * cover script also adds <html lang="en"> via a meta hack below. */
    hyphens: auto;
    -webkit-hyphens: auto;
    -webkit-hyphenate-limit-before: 3;
    -webkit-hyphenate-limit-after: 3;
    -webkit-hyphenate-limit-chars: 7 3 3;
    -webkit-hyphenate-limit-lines: 2;
  }

  /* ---------------- COVER ---------------- */
  .pdf-cover {
    page-break-after: always;
    height: 297mm; /* full A4 — first page has @page :first { margin: 0 } */
    width: 210mm;
    padding: 30mm 24mm 24mm 24mm;
    background:
      radial-gradient(900px 520px at 100% 0%, rgba(164, 69, 27, 0.10), transparent 62%),
      radial-gradient(720px 480px at 0% 100%, rgba(201, 138, 79, 0.10), transparent 62%),
      var(--paper);
    color: var(--ink);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    box-sizing: border-box;
    position: relative;
  }
  .pdf-cover::after {
    content: "";
    position: absolute;
    inset: 18mm 12mm;
    border: 0.6pt solid var(--rule);
    border-radius: 1.5mm;
    pointer-events: none;
  }
  .pdf-cover .cover-top { position: relative; z-index: 2; }
  .pdf-cover .cover-bottom { position: relative; z-index: 2; }
  .pdf-cover .eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.32em;
    font-size: 8.5pt;
    color: var(--accent);
    font-weight: 700;
    font-family: "Inter", "Helvetica Neue", "DejaVu Sans", sans-serif;
  }
  .pdf-cover .cover-mark {
    margin-top: 4mm;
    width: 18mm;
    height: 0;
    border-top: 1.4pt solid var(--accent);
  }
  .pdf-cover h1 {
    font-family: "Iowan Old Style", "Charter", "PT Serif", "Georgia", serif;
    font-size: 56pt;
    line-height: 1.0;
    font-weight: 700;
    margin: 14mm 0 8mm 0;
    color: var(--ink);
    letter-spacing: -0.012em;
    border-bottom: none;
  }
  .pdf-cover .lede {
    color: var(--ink-soft);
    font-size: 13.5pt;
    max-width: 140mm;
    line-height: 1.5;
    font-style: italic;
    text-align: left;
  }
  .pdf-cover .lede em { color: var(--ink); font-style: normal; font-weight: 600; }
  .pdf-cover .meta {
    border-top: 0.6pt solid var(--rule);
    padding-top: 6mm;
    color: var(--ink-soft);
    font-size: 9.5pt;
    display: flex;
    justify-content: space-between;
    gap: 10mm;
    font-family: "Inter", "Helvetica Neue", sans-serif;
    font-style: normal;
    text-align: left;
  }
  .pdf-cover .meta b { color: var(--ink); font-weight: 700; }
  .pdf-cover .meta .meta-block { display: flex; flex-direction: column; gap: 1mm; }
  .pdf-cover .meta .meta-block span:first-child {
    text-transform: uppercase;
    letter-spacing: 0.2em;
    font-size: 7.5pt;
    color: var(--ink-faint);
  }

  /* ---------------- TOC ----------------
   * Earlier revisions used display:flex / display:table for the TOC
   * rows — both made Chromium misposition the first row of every
   * continuation page (the row ended up inside the running-header
   * reservation and the reader saw clipped letter tops). Both flex
   * and table pagination paths in headless Chromium have known long
   * standing layout bugs around @page margin reservations.
   *
   * The fix that holds is the simplest possible CSS: each row is a
   * normal block, and the chapter-number column is an inline-block
   * prefix glued to the title via negative-margin indentation. Plain
   * blocks page-break perfectly because they go through the normal
   * flow path that the print engine has been tested on for years. */
  .pdf-toc {
    display: block;
    page-break-after: always;
  }
  .pdf-toc-h2 {
    page-break-before: auto !important;
    border-top: none;
    border-bottom: 0.6pt solid var(--rule);
    padding-bottom: 4mm;
    margin: 0 0 8mm 0;
    font-size: 28pt;
    font-weight: 700;
    text-align: left;
  }
  .pdf-toc-row {
    display: block;
    padding: 0.7mm 0;
    page-break-inside: avoid;
  }
  .pdf-toc-row.pdf-toc-part {
    margin-top: 5mm;
    padding-bottom: 1.8mm;
    border-bottom: 0.4pt solid var(--rule-faint);
  }
  .pdf-toc-row.pdf-toc-part:first-of-type { margin-top: 0; }
  .pdf-toc-row.pdf-toc-ch { padding-left: 22mm; }
  .pdf-toc-row .pdf-toc-num {
    display: inline-block;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 8pt;
    font-family: "Inter", "Helvetica Neue", sans-serif;
    color: var(--accent);
    font-weight: 700;
    vertical-align: baseline;
  }
  .pdf-toc-row.pdf-toc-part .pdf-toc-num { margin-right: 6mm; min-width: 16mm; }
  .pdf-toc-row .pdf-toc-title {
    display: inline;
  }
  .pdf-toc-row.pdf-toc-part .pdf-toc-title {
    font-family: "Iowan Old Style", "Charter", "Georgia", serif;
    font-size: 13pt;
    font-weight: 700;
    color: var(--ink);
  }
  .pdf-toc-row.pdf-toc-ch .pdf-toc-title {
    color: var(--ink-soft);
    font-size: 9.6pt;
    line-height: 1.45;
    font-family: "Charter", "Iowan Old Style", "Georgia", serif;
  }
  .pdf-toc-pagebreak { page-break-after: always; height: 0; visibility: hidden; }

  /* ---------------- HEADINGS ---------------- */
  h1, h2, h3, h4, h5, h6 {
    color: var(--ink);
    font-family: "Iowan Old Style", "Charter", "PT Serif", "Georgia", serif;
    font-weight: 700;
    letter-spacing: -0.005em;
    text-align: left;
    hyphens: manual;
    -webkit-hyphens: manual;
  }
  h1 { font-size: 22pt; margin: 0 0 6mm 0; }
  /* h2 always starts a new page (page-break-before: always). The
   * 6mm padding-top makes sure the cap height of the heading never
   * lands inside Chromium's running-header reservation — even with
   * a 1-2 mm rounding error in the print pipeline the title still
   * has clear air above it. The earlier revision sat the title
   * flush against the content-area top and the reader saw clipped
   * letter tops poking out from under the header band. */
  h2 {
    font-size: 26pt;
    margin: 0 0 8mm 0;
    padding: 6mm 0 4mm 0;
    border-bottom: 1.2pt solid var(--accent);
    page-break-before: always;
    color: var(--ink);
    line-height: 1.15;
    letter-spacing: -0.01em;
  }
  h2::before {
    content: "";
    display: block;
    width: 16mm;
    height: 0;
    border-top: 0.8pt solid var(--accent);
    margin-bottom: 4mm;
    opacity: 0.55;
  }
  h3 {
    font-size: 14.5pt;
    margin: 9mm 0 3mm 0;
    color: var(--ink);
    page-break-after: avoid;
    line-height: 1.25;
  }
  h4 {
    font-size: 11.4pt;
    margin: 6mm 0 2mm 0;
    color: var(--accent);
    text-transform: none;
    letter-spacing: 0;
    page-break-after: avoid;
  }
  h5, h6 {
    font-size: 10.2pt;
    margin: 5mm 0 2mm 0;
    color: var(--ink-soft);
    font-style: italic;
    page-break-after: avoid;
  }

  /* ---------------- TEXT ---------------- */
  p {
    margin: 0 0 3.4mm 0;
    color: var(--ink);
    text-align: justify;
    text-justify: inter-word;
    word-spacing: -0.01em;
    hyphens: auto;
    -webkit-hyphens: auto;
    orphans: 3;
    widows: 3;
  }
  /* The opening paragraph of every part / chapter just gets a small
   * size + leading bump for "lede" feel. Earlier revisions decorated
   * it with a drop cap (::first-letter) and a small-caps first line
   * (::first-line); reader feedback was that both were too loud — the
   * drop cap appeared on every chapter (not just the Prologue) and
   * the small-caps first line collided with inline code, since
   * monospace glyphs aren't designed for small-caps substitution and
   * ended up looking double-sized inside an otherwise petite line.
   * Restraint reads as confidence here. */
  h2 + p,
  h3 + p {
    font-size: 11.2pt;
    line-height: 1.6;
    color: var(--ink);
  }
  strong { color: var(--ink); font-weight: 700; }
  em { color: var(--ink); font-style: italic; }

  /* ---------------- LISTS ---------------- */
  ul, ol { padding-left: 6.5mm; margin: 0 0 4mm 0; }
  li { margin-bottom: 1.6mm; color: var(--ink); }
  li::marker { color: var(--accent); }
  li p { text-align: left; }
  ul ul, ol ol, ul ol, ol ul { margin: 1.5mm 0; }

  /* ---------------- BLOCKQUOTES (callouts from MkDocs admonitions) ----------------
   * The previous version rendered the title (e.g. "**Note — Field note**")
   * inline with the body, which produced rivers + stranded inline code in
   * the same paragraph. Now: the first <p> with a leading <strong> becomes
   * a small uppercase eyebrow on its own line, the rest of the body sits
   * in roman with rag-right alignment so inline code never gets stretched
   * across a justified line. */
  blockquote {
    margin: 5mm 0;
    padding: 4mm 6mm 4mm 5.5mm;
    background: var(--quote-bg);
    border-left: 2.4pt solid var(--quote-bar);
    border-radius: 0 1.4mm 1.4mm 0;
    color: var(--ink);
    font-size: 9.8pt;
    line-height: 1.55;
    page-break-inside: avoid;
    box-shadow: inset 0 0 0 0.3pt rgba(201, 138, 79, 0.18);
    /* md-to-pdf wraps content in a GitHub-markdown stylesheet that
     * italicises blockquote text by default. Field notes read better
     * upright, so we force normal style on the whole subtree and
     * re-enable italic only inside <em>. */
    font-style: normal;
  }
  blockquote p {
    color: inherit;
    margin-bottom: 1.8mm;
    text-align: left;
    hyphens: auto;
    -webkit-hyphens: auto;
    font-style: inherit;
  }
  blockquote em { font-style: italic; }
  blockquote p:last-child { margin-bottom: 0; }
  /* If the very first paragraph in a blockquote starts with a <strong>,
   * lift that strong run into an eyebrow and break onto a new line for
   * the body. We emit a tiny CSS hack: the <strong> renders as an
   * inline-block uppercase eyebrow, and we use a ::after with content
   * "\\A" + white-space: pre to force a newline visually. */
  blockquote > p:first-child > strong:first-child {
    display: block;
    font-family: "Inter", "Helvetica Neue", sans-serif;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 8pt;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 2mm;
    padding-bottom: 1.4mm;
    border-bottom: 0.4pt solid var(--quote-bar);
  }
  blockquote code {
    font-style: normal;
    background: rgba(164, 69, 27, 0.08);
    color: var(--ink);
    padding: 0.3mm 1.2mm;
    border-radius: 1mm;
    font-size: 8.6pt;
    word-break: break-word;
    overflow-wrap: anywhere;
    hyphens: none;
    -webkit-hyphens: none;
  }

  /* ---------------- CODE ----------------
   * Inline code got visually huge in earlier renders because md-to-pdf
   * ships a bundled markdown.css that bumps every contextual code
   * selector (h1..h6 code, p code, li code, pre code) to font-size:
   * 1.2em. That compound selector wins on specificity over a plain
   * single-element code declaration, so we repeat the same list and
   * pin the size to a slightly smaller value than the body (8.6pt vs
   * 10.6pt) — monospace x-height is taller than Charter, so matching
   * the body point size still reads as oversized. We also sync the
   * line-height with the paragraph and clamp the line-box so a long
   * token (a feat(ci) commit subject, an issue key, etc.) wraps as
   * plain words instead of stretching the host line. */
  p code, li code, td code, h1 code, h2 code, h3 code, h4 code, h5 code, h6 code,
  code {
    font-family: "JetBrains Mono", "SF Mono", "Menlo", "DejaVu Sans Mono", "Consolas", monospace;
    background: var(--code-bg);
    color: var(--code-ink);
    padding: 0.2mm 1.1mm;
    border-radius: 1mm;
    font-size: 8.6pt;
    line-height: inherit;
    vertical-align: baseline;
    border: none;
    word-break: break-word;
    overflow-wrap: anywhere;
    hyphens: none;
    -webkit-hyphens: none;
    white-space: normal;
    /* keep code upright inside paragraphs marked italic by markdown */
    font-style: normal;
  }
  pre {
    background: var(--code-bg);
    border: 0.4pt solid var(--rule);
    border-radius: 1.6mm;
    padding: 3mm 4mm;
    page-break-inside: avoid;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-wrap: anywhere;
    margin: 4mm 0;
  }
  pre code {
    background: transparent;
    color: var(--code-ink);
    padding: 0;
    font-size: 8.6pt;
    line-height: 1.45;
  }

  a { color: var(--accent); text-decoration: none; word-break: break-word; }
  a:visited { color: var(--accent); }

  hr { border: none; border-top: 0.4pt solid var(--rule); margin: 8mm 0; }

  /* ---------------- TABLES ---------------- */
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 5mm 0;
    font-size: 9.4pt;
    page-break-inside: avoid;
    border-top: 0.6pt solid var(--ink);
    border-bottom: 0.6pt solid var(--ink);
  }
  th, td {
    border: none;
    border-bottom: 0.3pt solid var(--rule);
    padding: 2.2mm 3mm;
    vertical-align: top;
    text-align: left;
  }
  tr:last-child td { border-bottom: none; }
  th {
    background: transparent;
    color: var(--ink);
    font-weight: 700;
    font-family: "Inter", "Helvetica Neue", sans-serif;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-size: 8.2pt;
    border-bottom: 0.6pt solid var(--ink);
  }

  /* ---------------- IMAGES ---------------- */
  img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 4mm auto;
    /* Site SVGs are designed for the dark theme; invert them to read
     * on cream paper. Per-figure overrides below opt out. */
    filter: invert(1) hue-rotate(180deg) brightness(0.95) saturate(0.85);
  }

  /* ---------------- BOOK CHARTS (ECharts SVGs) ----------------
   * These are designed in the printed palette already, so we suppress
   * the global SVG inversion and frame each chart like a real
   * scholarly figure. */
  figure.book-chart {
    margin: 8mm 0;
    padding: 5mm 5mm 4mm 5mm;
    background: var(--paper);
    border: 0.4pt solid var(--rule);
    border-radius: 1.6mm;
    page-break-inside: avoid;
    box-shadow: 0 0 0 0.6pt rgba(155, 117, 67, 0.05) inset;
  }
  figure.book-chart img {
    filter: none;
    margin: 0 auto 3mm auto;
    max-width: 100%;
  }
  figure.book-chart figcaption {
    font-family: "Charter", "Iowan Old Style", "PT Serif", "Georgia", serif;
    font-size: 9pt;
    line-height: 1.5;
    color: var(--ink-soft);
    text-align: left;
    border-top: 0.3pt solid var(--rule);
    padding-top: 2.4mm;
    margin-top: 1mm;
    font-style: italic;
  }
  figure.book-chart figcaption::first-letter { font-weight: 700; color: var(--accent); }

  .book-heading { color: var(--ink) !important; font-family: "Iowan Old Style", "Charter", "Georgia", serif; }
`;

const today = new Date().toISOString().slice(0, 10);

const cover = `
<div class="pdf-cover">
  <div class="cover-top">
    <div class="eyebrow">Ship · Long read · Edition 2026</div>
    <div class="cover-mark"></div>
    <h1>Why Ship</h1>
    <p class="lede">
      Fences, throughput, legibility \u2014 and the boring discipline that makes an
      agentic SDLC something operators can <em>sleep through</em>. Forty short chapters,
      lettered sub-chapters on artifacts, metrics, evals, the improvement loop,
      regulated overlays, costs, agent PR review and onboarding, plus a closing
      Manifesto.
    </p>
  </div>
  <div class="cover-bottom">
    <div class="meta">
      <span class="meta-block"><span>Edition</span><b>2026</b></span>
      <span class="meta-block"><span>Built</span><b>${today}</b></span>
      <span class="meta-block"><span>Source</span><b>documentation/framework/index.md</b></span>
      <span class="meta-block"><span>Online</span><b>ship.elmundi.com</b></span>
    </div>
  </div>
</div>
`;

/* Two-pass render so the cover gets a clean, header-less first page.
 * Puppeteer's `displayHeaderFooter` ignores `@page :first { margin: 0 }`
 * for the running templates, so the only reliable way to keep the
 * cover free of the title strip + page number is to render it as its
 * own PDF and concatenate with the rest via `pdfunite`.
 *
 * The shared CSS above sets `@page { margin: 26mm 0 20mm 0 }` for the
 * interior so Puppeteer can paint the running header/footer in the
 * top/bottom bands. The cover doesn't want those bands at all, so we
 * inline-override `@page` to zero margins for this render only. */
const coverDoc = `<style>@page { margin: 0 !important; }</style>\n${cover}`;
const bodyDoc = `${tocHtml}\n\n${printable}`;

/* Header/footer templates paint an opaque white band edge-to-edge.
 * White matches the (now also white) body paper exactly, so the
 * page reads as uniformly white — but the band is *opaque*, which
 * is critical: headless Chromium occasionally lays out body content
 * (especially the first row of a TOC continuation page or any
 * element following a flex/table page-break) a few millimeters
 * higher than the @page top margin reservation. A transparent
 * header would let that stray content peek through and the reader
 * would see clipped letter tops along the top edge of the page.
 * The opaque white band cleans that up: any overflow gets painted
 * over by the band's background, leaving only the small Inter
 * running text visible.
 *
 * The inner `padding:0 18mm` lines the running text up with the
 * 18mm body padding so the title and page number sit above/below
 * the column of prose. */
const headerTemplate = `<div style="background:#ffffff;width:100%;height:100%;-webkit-print-color-adjust:exact;print-color-adjust:exact;display:flex;align-items:flex-end;padding-bottom:8mm;font-family:Inter,Helvetica,Arial,sans-serif;">
  <div style="width:100%;padding:0 18mm;display:flex;justify-content:space-between;font-size:7.5pt;color:#7b818d;letter-spacing:0.04em;">
    <span style="text-transform:uppercase;letter-spacing:0.18em;">Ship \u00B7 Why fences, throughput, legibility</span>
    <span>Edition 2026</span>
  </div>
</div>`;

const footerTemplate = `<div style="background:#ffffff;width:100%;height:100%;-webkit-print-color-adjust:exact;print-color-adjust:exact;display:flex;align-items:flex-start;padding-top:7mm;font-family:Inter,Helvetica,Arial,sans-serif;">
  <div style="width:100%;padding:0 18mm;display:flex;justify-content:space-between;font-size:7.5pt;color:#7b818d;">
    <span style="font-style:italic;">ship.elmundi.com</span>
    <span>\u2014 <span class="pageNumber"></span> \u2014</span>
  </div>
</div>`;

mkdirSync(destDir, { recursive: true });

if (process.env.DEBUG_BOOK_HTML === "1") {
  const fs = await import("node:fs");
  const debug = await mdToPdf({ content: `${coverDoc}\n\n${bodyDoc}` }, { dest: destPdf, css, as_html: true });
  if (debug && debug.content) {
    /* Prepend a real <html lang="en"> wrapper so Chrome's hyphenator
     * picks the right language; md-to-pdf wraps the body in a stub
     * page that already has <html lang="en"> on most versions, but on
     * older builds the lang attr is missing and rivers come back. */
    const wrapped = debug.content.replace(/<html\b/, '<html lang="en"');
    fs.writeFileSync(tmpHtmlPath, wrapped);
    console.log("build-book-pdf: wrote debug HTML to", tmpHtmlPath);
    process.exit(0);
  }
}

const sharedLaunch = {
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--lang=en-US"],
};

/* ---- pass 1: cover only, no header/footer, zero margin ---- *
 * IMPORTANT: do NOT pass `preferCSSPageSize: true` here. With it,
 * Puppeteer adopts both the size AND margins from `@page { margin:
 * 22mm 0 22mm 0 }` (defined in the shared CSS for the interior),
 * which leaves a 22mm white band above/below the cover and forces
 * the bottom meta strip onto a second page. Letting Puppeteer
 * supply the size + margin: 0 keeps the cover edge-to-edge on a
 * single A4 sheet. */
const coverPdf = join(destDir, ".book.cover.pdf");
const coverResult = await mdToPdf(
  { content: coverDoc },
  {
    dest: coverPdf,
    css,
    body_class: ["book-print", "book-cover-only"],
    document_title: "Ship — cover",
    pdf_options: {
      format: "A4",
      printBackground: true,
      displayHeaderFooter: false,
      margin: { top: "0", bottom: "0", left: "0", right: "0" },
    },
    launch_options: sharedLaunch,
  },
);
if (!coverResult) {
  console.error("build-book-pdf: failed to render cover PDF");
  process.exit(1);
}

/* ---- pass 2: TOC + chapters, with running header/footer ---- */
const interiorPdf = join(destDir, ".book.interior.pdf");
const interiorResult = await mdToPdf(
  { content: bodyDoc },
  {
    dest: interiorPdf,
    css,
    body_class: ["book-print"],
    document_title: "Ship — Why Ship",
    pdf_options: {
      format: "A4",
      printBackground: true,
      preferCSSPageSize: true,
      displayHeaderFooter: true,
      headerTemplate,
      footerTemplate,
      /* Left/right are 0 so the cream body bleeds to the page edge;
       * the visual side margin is body padding (see CSS above). The
       * top/bottom values give the running header/footer a band to
       * paint a cream rectangle in. */
      margin: { top: "26mm", bottom: "20mm", left: "0", right: "0" },
    },
    launch_options: sharedLaunch,
  },
);
if (!interiorResult) {
  console.error("build-book-pdf: failed to render interior PDF");
  process.exit(1);
}

/* ---- concatenate cover + interior with poppler's pdfunite ---- */
const { spawnSync } = await import("node:child_process");
const merged = spawnSync("pdfunite", [coverPdf, interiorPdf, destPdf], {
  stdio: "inherit",
});
if (merged.status !== 0) {
  console.error("build-book-pdf: pdfunite failed (exit", merged.status, "). Is poppler installed?");
  process.exit(1);
}

/* tidy up the per-pass intermediates so the public/ folder stays clean.
 * `KEEP_BOOK_INTERMEDIATES=1` short-circuits the cleanup so we can
 * inspect either pass when debugging cover/header layout issues. */
const fs = await import("node:fs");
if (process.env.KEEP_BOOK_INTERMEDIATES !== "1") {
  for (const tmp of [coverPdf, interiorPdf]) {
    try {
      fs.unlinkSync(tmp);
    } catch {
      /* ignore */
    }
  }
}

if (existsSync(tmpHtmlPath)) {
  try {
    statSync(tmpHtmlPath);
  } catch {
    /* ignore */
  }
}

const { size } = statSync(destPdf);
console.log(`build-book-pdf: wrote ${destPdf} (${(size / 1024).toFixed(1)} KB)`);
