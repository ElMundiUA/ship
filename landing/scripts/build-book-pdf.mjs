#!/usr/bin/env node
/**
 * Build a downloadable PDF of the Ship book.
 *
 * Pipeline:
 *   documentation/framework/index.md
 *     -> sync-book.mjs (already runs in predev/prebuild) -> landing/content/book.md
 *     -> preprocessBookMarkdown (same one used by the /book Next page)
 *     -> stripInAppLinks + rewriteImagesForPrint + buildToc
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
 * Print-design notes (intentional, after the v1 dark-cover version was
 * judged unreadable on paper):
 *   - light "book" theme (warm off-white background, near-black ink) so the
 *     output is legible printed and on-screen at 100% zoom;
 *   - exactly one page break per part (h2), nothing per chapter (h3) —
 *     chapters flow on the page like a real book;
 *   - long lines inside `pre` wrap rather than getting clipped;
 *   - generated table-of-contents page derived from h2/h3 headings;
 *   - leading paragraph of every part rendered as a drop-style intro.
 */
import { readFileSync, mkdirSync, statSync, existsSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { mdToPdf } from "md-to-pdf";

import { preprocessBookMarkdown } from "../src/lib/book-markdown.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const landingRoot = join(__dirname, "..");
const repoRoot = join(landingRoot, "..");
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
  return md.replace(/\[([^\]]+)\]\((\/[^)\s]+)\)/g, (_full, label, href) => {
    if (href.startsWith("//")) return `[${label}](${href})`;
    return `**${label}**`;
  });
}

/** Rewrite local diagram images to absolute file:// paths so md-to-pdf can find them. */
function rewriteImagesForPrint(md) {
  return md.replace(/!\[([^\]]*)\]\((\/diagrams\/[^)]+)\)/g, (_full, alt, href) => {
    const absolute = join(diagramsDir, href.replace(/^\/diagrams\//, ""));
    return `![${alt}](file://${absolute})`;
  });
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
const tocHtml = [
  `<div class="pdf-toc">`,
  ``,
  `<h2 class="pdf-toc-h2">Contents</h2>`,
  ``,
  ...tocBlocks.flatMap((row) => [row, ``]),
  `</div>`,
  ``,
  `<div class="pdf-toc-pagebreak"></div>`,
].join("\n");

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const printable = rewriteImagesForPrint(stripInAppLinks(processed));

const css = `
  @page { size: A4; margin: 22mm 18mm 22mm 18mm; }

  :root {
    --paper: #fbf8f1;
    --ink: #1b1d24;
    --ink-soft: #4d525c;
    --ink-faint: #7b818d;
    --rule: #d9d2c3;
    --rule-faint: #ebe5d6;
    --accent: #a4451b;
    --accent-soft: #b96b3f;
    --code-bg: #f1ece0;
    --code-ink: #1f2128;
    --quote-bg: #f4efe1;
    --quote-bar: #c98a4f;
  }

  html, body {
    background: var(--paper) !important;
    color: var(--ink) !important;
  }
  body {
    font-family: "Charter", "Iowan Old Style", "Source Serif Pro", "Georgia", "Times New Roman", serif;
    font-size: 10.8pt;
    line-height: 1.55;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
    text-rendering: optimizeLegibility;
  }

  /* ---------------- COVER ---------------- */
  .pdf-cover {
    page-break-after: always;
    height: 252mm;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 22mm 16mm 18mm 16mm;
    background: var(--paper);
    border: 1px solid var(--rule);
    border-radius: 4mm;
    position: relative;
  }
  .pdf-cover::before {
    content: "";
    position: absolute;
    inset: 0;
    background:
      radial-gradient(900px 500px at 100% 0%, rgba(164, 69, 27, 0.08), transparent 60%),
      radial-gradient(700px 500px at 0% 100%, rgba(201, 138, 79, 0.08), transparent 60%);
    border-radius: 4mm;
    pointer-events: none;
  }
  .pdf-cover .eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.22em;
    font-size: 8.5pt;
    color: var(--accent);
    font-weight: 700;
    font-family: "Inter", "Helvetica Neue", sans-serif;
  }
  .pdf-cover h1 {
    font-family: "Iowan Old Style", "Charter", "Georgia", serif;
    font-size: 46pt;
    line-height: 1.02;
    font-weight: 700;
    margin: 10mm 0 6mm 0;
    color: var(--ink);
    letter-spacing: -0.01em;
  }
  .pdf-cover .lede {
    color: var(--ink-soft);
    font-size: 13pt;
    max-width: 130mm;
    line-height: 1.5;
    font-style: italic;
  }
  .pdf-cover .meta {
    border-top: 1px solid var(--rule);
    padding-top: 6mm;
    color: var(--ink-soft);
    font-size: 9.5pt;
    display: flex;
    justify-content: space-between;
    gap: 10mm;
    font-family: "Inter", "Helvetica Neue", sans-serif;
    font-style: normal;
  }
  .pdf-cover .meta b { color: var(--ink); font-weight: 700; }

  /* ---------------- TOC ---------------- */
  .pdf-toc { page-break-after: always; }
  .pdf-toc-h2 {
    page-break-before: auto !important;
    border-top: none;
    border-bottom: 1px solid var(--rule);
    padding-bottom: 4mm;
    margin: 0 0 8mm 0;
    font-size: 26pt;
  }
  .pdf-toc-row {
    display: flex;
    align-items: baseline;
    gap: 4mm;
    page-break-inside: avoid;
  }
  .pdf-toc-row.pdf-toc-part {
    margin-top: 5mm;
    padding-bottom: 1.5mm;
    border-bottom: 1px solid var(--rule-faint);
  }
  .pdf-toc-row.pdf-toc-part:first-of-type { margin-top: 0; }
  .pdf-toc-row.pdf-toc-ch {
    padding: 0.4mm 0 0.4mm 22mm;
  }
  .pdf-toc-row .pdf-toc-num {
    flex: 0 0 18mm;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 8pt;
    font-family: "Inter", "Helvetica Neue", sans-serif;
    color: var(--accent);
    font-weight: 700;
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
  h1, h2, h3, h4, h5 {
    color: var(--ink);
    font-family: "Iowan Old Style", "Charter", "Georgia", serif;
    font-weight: 700;
    letter-spacing: -0.005em;
  }
  h1 { font-size: 22pt; margin: 0 0 6mm 0; }
  h2 {
    font-size: 20pt;
    margin: 0 0 6mm 0;
    padding-bottom: 3mm;
    border-bottom: 2px solid var(--accent);
    page-break-before: always;
    color: var(--ink);
  }
  h2:first-of-type, .pdf-cover + h2, .pdf-toc + h2 { /* parts after cover/toc still page-break */ }
  h3 {
    font-size: 14pt;
    margin: 8mm 0 3mm 0;
    color: var(--ink);
    page-break-after: avoid;
  }
  h4 {
    font-size: 11.5pt;
    margin: 6mm 0 2mm 0;
    color: var(--accent);
    text-transform: none;
    letter-spacing: 0;
    page-break-after: avoid;
  }
  h5, h6 {
    font-size: 10.5pt;
    margin: 5mm 0 2mm 0;
    color: var(--ink-soft);
    font-style: italic;
    page-break-after: avoid;
  }

  /* ---------------- TEXT ---------------- */
  p {
    margin: 0 0 3.6mm 0;
    color: var(--ink);
    text-align: justify;
    hyphens: auto;
    -webkit-hyphens: auto;
    orphans: 2;
    widows: 2;
  }
  /* First paragraph after a part heading gets a small drop-cap-ish lede look. */
  h2 + p {
    font-size: 11.4pt;
    line-height: 1.6;
    color: var(--ink);
  }
  h2 + p::first-line { font-variant: small-caps; letter-spacing: 0.04em; }
  strong { color: var(--ink); font-weight: 700; }
  em { color: var(--ink); }

  /* ---------------- LISTS ---------------- */
  ul, ol { padding-left: 6.5mm; margin: 0 0 4mm 0; }
  li { margin-bottom: 1.6mm; color: var(--ink); }
  li::marker { color: var(--accent); }
  ul ul, ol ol, ul ol, ol ul { margin: 1.5mm 0; }

  /* ---------------- BLOCKQUOTES (callouts from MkDocs admonitions) ---------------- */
  blockquote {
    margin: 4mm 0;
    padding: 3.5mm 5mm;
    background: var(--quote-bg);
    border-left: 3px solid var(--quote-bar);
    border-radius: 0 2mm 2mm 0;
    color: var(--ink);
    font-size: 10pt;
    page-break-inside: avoid;
  }
  blockquote strong { color: var(--accent); }
  blockquote p { color: inherit; margin-bottom: 1.8mm; text-align: left; }
  blockquote p:last-child { margin-bottom: 0; }

  /* ---------------- CODE ---------------- */
  code {
    font-family: "JetBrains Mono", "SF Mono", "Menlo", "Consolas", monospace;
    background: var(--code-bg);
    color: var(--code-ink);
    padding: 0.4mm 1.4mm;
    border-radius: 1mm;
    font-size: 9.2pt;
    word-break: break-word;
  }
  pre {
    background: var(--code-bg);
    border: 1px solid var(--rule);
    border-radius: 2mm;
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
    font-size: 8.8pt;
    line-height: 1.45;
  }

  a { color: var(--accent); text-decoration: none; word-break: break-word; }

  hr { border: none; border-top: 1px solid var(--rule); margin: 8mm 0; }

  /* ---------------- TABLES ---------------- */
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 4mm 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
  }
  th, td {
    border: 1px solid var(--rule);
    padding: 2mm 3mm;
    vertical-align: top;
  }
  th {
    background: var(--rule-faint);
    color: var(--ink);
    text-align: left;
    font-weight: 700;
  }

  /* ---------------- IMAGES ---------------- */
  img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 4mm auto;
    /* SVG diagrams from the dark site invert nicely against the cream paper. */
    filter: invert(1) hue-rotate(180deg) brightness(0.95) saturate(0.85);
  }

  .book-heading { color: var(--ink) !important; font-family: "Iowan Old Style", "Charter", "Georgia", serif; }
`;

const today = new Date().toISOString().slice(0, 10);

const cover = `
<div class="pdf-cover">
  <div>
    <div class="eyebrow">Ship — Long read</div>
    <h1>Why Ship</h1>
    <p class="lede">
      Fences, throughput, legibility — and the boring discipline that makes an agentic SDLC
      something operators can sleep through. Forty short chapters, lettered sub-chapters on
      artifacts, metrics, evals, the improvement loop, regulated overlays, costs, agent PR
      review and onboarding, plus a closing Manifesto.
    </p>
  </div>
  <div class="meta">
    <span><b>Edition</b> · 2026</span>
    <span><b>Built</b> · ${today}</span>
    <span><b>Source</b> · documentation/framework/index.md</span>
  </div>
</div>
`;

const body = `${cover}\n\n${tocHtml}\n\n${printable}`;

const headerTemplate = `<div style="font-size:8pt;color:#7b818d;width:100%;padding:0 14mm;display:flex;justify-content:space-between;font-family:Inter,Helvetica,Arial,sans-serif;">
  <span style="letter-spacing:0.04em;">Ship — Why fences, throughput, legibility</span>
  <span>Edition 2026</span>
</div>`;

const footerTemplate = `<div style="font-size:8pt;color:#7b818d;width:100%;padding:0 14mm;display:flex;justify-content:space-between;font-family:Inter,Helvetica,Arial,sans-serif;">
  <span>elmundi.ua/ship</span>
  <span>Page <span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>`;

mkdirSync(destDir, { recursive: true });

if (process.env.DEBUG_BOOK_HTML === "1") {
  const fs = await import("node:fs");
  const debug = await mdToPdf({ content: body }, { dest: destPdf, css, as_html: true });
  if (debug && debug.content) {
    fs.writeFileSync(tmpHtmlPath, debug.content);
    console.log("build-book-pdf: wrote debug HTML to", tmpHtmlPath);
    process.exit(0);
  }
}

const result = await mdToPdf(
  { content: body },
  {
    dest: destPdf,
    css,
    body_class: ["book-print"],
    pdf_options: {
      format: "A4",
      printBackground: true,
      preferCSSPageSize: true,
      displayHeaderFooter: true,
      headerTemplate,
      footerTemplate,
      margin: { top: "26mm", bottom: "20mm", left: "18mm", right: "18mm" },
    },
    launch_options: {
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    },
  },
);

if (!result) {
  console.error("build-book-pdf: md-to-pdf returned no result");
  process.exit(1);
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
