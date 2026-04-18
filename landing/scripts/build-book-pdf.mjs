#!/usr/bin/env node
/**
 * Build a downloadable PDF of the Ship book.
 *
 * Pipeline:
 *   documentation/framework/index.md
 *     -> sync-book.mjs (already runs in predev/prebuild) -> landing/content/book.md
 *     -> preprocessBookMarkdown (same one used by the /book Next page)
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
const processed = preprocessBookMarkdown(raw);

// PDF-specific tweaks the on-screen page does not need:
//   1. Strip in-app cross-links to /docs and /book#... — they are useless
//      on paper and confuse readers (the URL is hidden on most renderers).
//      We keep external https?: links and #anchor jumps, the rest collapse
//      to plain bold so the sentence stays readable.
//   2. Inline-replace remote/relative image references with absolute file://
//      paths so md-to-pdf can find them at print time.
function stripInAppLinks(md) {
  return md.replace(/\[([^\]]+)\]\((\/[^)\s]+)\)/g, (_full, label, href) => {
    if (href.startsWith("//")) return `[${label}](${href})`;
    return `**${label}**`;
  });
}

function rewriteImagesForPrint(md) {
  return md.replace(/!\[([^\]]*)\]\((\/diagrams\/[^)]+)\)/g, (_full, alt, href) => {
    const absolute = join(diagramsDir, href.replace(/^\/diagrams\//, ""));
    return `![${alt}](file://${absolute})`;
  });
}

const printable = rewriteImagesForPrint(stripInAppLinks(processed));

const css = `
  @page { size: A4; margin: 22mm 18mm 22mm 18mm; }
  :root {
    --ink: #e7e9ee;
    --ink-soft: #aab1bd;
    --bg: #0b0d12;
    --bg-block: #11141b;
    --aqua: #2ee6d6;
    --coral: #ff5c6c;
    --lilac: #b18cff;
    --rule: rgba(255,255,255,0.10);
  }
  html, body { background: var(--bg) !important; color: var(--ink) !important; }
  body {
    font-family: "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .pdf-cover {
    page-break-after: always;
    height: 252mm;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 18mm 14mm;
    background:
      radial-gradient(1200px 600px at 100% 0%, rgba(255,92,108,0.18), transparent 60%),
      radial-gradient(900px 600px at 0% 100%, rgba(46,230,214,0.16), transparent 60%),
      linear-gradient(180deg, #0b0d12 0%, #0b0d12 100%);
    border: 1px solid var(--rule);
    border-radius: 6mm;
  }
  .pdf-cover .eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 9pt;
    color: #ffd56b;
  }
  .pdf-cover h1 {
    font-size: 38pt;
    line-height: 1.04;
    font-weight: 800;
    margin: 8mm 0 6mm 0;
    background: linear-gradient(90deg, var(--aqua), var(--lilac), var(--coral));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }
  .pdf-cover .lede {
    color: var(--ink-soft);
    font-size: 13pt;
    max-width: 130mm;
    line-height: 1.45;
  }
  .pdf-cover .meta {
    border-top: 1px solid var(--rule);
    padding-top: 6mm;
    color: var(--ink-soft);
    font-size: 9.5pt;
    display: flex;
    justify-content: space-between;
    gap: 10mm;
  }
  .pdf-cover .meta b { color: var(--ink); }

  h1, h2, h3, h4, h5 { color: #fff; font-weight: 700; }
  h2 { font-size: 18pt; margin: 14mm 0 5mm 0; padding-bottom: 2mm; border-bottom: 1px solid var(--rule); page-break-before: always; }
  h2:first-of-type { page-break-before: auto; }
  h3 { font-size: 13pt; margin: 9mm 0 3mm 0; }
  h4 { font-size: 11pt; margin: 6mm 0 2mm 0; color: var(--aqua); }

  p { margin: 0 0 3.6mm 0; color: var(--ink); }
  strong { color: #fff; }

  ul, ol { padding-left: 6mm; margin: 0 0 4mm 0; }
  li { margin-bottom: 1.6mm; color: var(--ink); }
  li::marker { color: var(--aqua); }

  blockquote {
    margin: 4mm 0;
    padding: 3mm 4mm;
    background: var(--bg-block);
    border-left: 3px solid var(--aqua);
    border-radius: 0 2mm 2mm 0;
    color: var(--ink-soft);
    font-size: 10pt;
  }
  blockquote strong { color: var(--aqua); }
  blockquote p { color: inherit; margin-bottom: 1.5mm; }

  code {
    font-family: "JetBrains Mono", "Menlo", "Consolas", monospace;
    background: rgba(46,230,214,0.10);
    color: #9af3e8;
    padding: 0.4mm 1.2mm;
    border-radius: 1mm;
    font-size: 9.4pt;
  }
  pre {
    background: var(--bg-block);
    border: 1px solid var(--rule);
    border-radius: 2mm;
    padding: 3mm 4mm;
    overflow: hidden;
    page-break-inside: avoid;
  }
  pre code { background: transparent; color: var(--ink); padding: 0; font-size: 9pt; }

  a { color: var(--aqua); text-decoration: none; word-break: break-word; }

  hr { border: none; border-top: 1px solid var(--rule); margin: 8mm 0; }

  table { width: 100%; border-collapse: collapse; margin: 4mm 0; font-size: 9.5pt; }
  th, td { border: 1px solid var(--rule); padding: 2mm 3mm; vertical-align: top; }
  th { background: var(--bg-block); color: #fff; text-align: left; }

  img { max-width: 100%; height: auto; filter: brightness(0.95); }

  .book-heading { color: #fff !important; }
`;

const today = new Date().toISOString().slice(0, 10);

const cover = `
<div class="pdf-cover">
  <div>
    <div class="eyebrow">Ship — long read</div>
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

const body = `${cover}\n\n${printable}`;

const headerTemplate = `<div style="font-size:8pt;color:#9aa1ad;width:100%;padding:0 14mm;display:flex;justify-content:space-between;font-family:Inter,Helvetica,Arial,sans-serif;">
  <span>Ship — Why fences, throughput, legibility</span>
  <span>Edition 2026</span>
</div>`;

const footerTemplate = `<div style="font-size:8pt;color:#9aa1ad;width:100%;padding:0 14mm;display:flex;justify-content:space-between;font-family:Inter,Helvetica,Arial,sans-serif;">
  <span>elmundi.ua/ship</span>
  <span>Page <span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>`;

mkdirSync(destDir, { recursive: true });

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
