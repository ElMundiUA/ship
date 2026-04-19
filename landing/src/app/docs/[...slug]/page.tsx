import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { DocsMarkdown } from "@/components/docs-content";
import { preprocessDocumentationMarkdown } from "@/lib/docs-markdown";
import { listDocumentationPages, readDocumentationFile, slugToRelPath } from "@/lib/documentation-fs";
import { parseDocsPage } from "@/lib/docs-page-parts";
import { groupLabelForHref } from "@/lib/docs-nav";

export async function generateStaticParams() {
  const pages = listDocumentationPages();
  return pages.filter((p) => p.slug.length > 0).map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string[] }> }): Promise<Metadata> {
  const { slug } = await params;
  const rel = slugToRelPath(slug);
  if (!rel) return { title: "Docs — Ship" };
  try {
    const raw = readDocumentationFile(rel);
    const first = raw.split("\n").find((l) => l.startsWith("# "));
    const title = first?.replace(/^#\s+/, "").trim() ?? slug.join("/");
    return { title: `${title} — Ship docs` };
  } catch {
    return { title: "Ship docs" };
  }
}

export default async function DocPage({ params }: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await params;
  const rel = slugToRelPath(slug);
  if (!rel) notFound();

  let raw: string;
  try {
    raw = readDocumentationFile(rel);
  } catch {
    notFound();
  }

  const md = preprocessDocumentationMarkdown(raw);
  /* Extract H1 + first paragraph so we can render a proper hero block; pass
   * the rest of the markdown to the renderer untouched. */
  const { title, lede, body, toc } = parseDocsPage(md);

  const href = `/docs/${slug.join("/")}`;
  const kicker = groupLabelForHref(href);
  const hasToc = toc.length >= 3;

  return (
    <main className={hasToc ? "docs-page-with-toc" : "docs-page"}>
      <div className="min-w-0">
        {/* Breadcrumb */}
        <nav className="docs-crumbs" aria-label="Breadcrumb">
          <Link href="/docs">Docs</Link>
          {slug.map((part, i) => (
            <span key={i} className="contents">
              <span className="sep">/</span>
              {i === slug.length - 1 ? (
                <span className="text-white/70">{part}</span>
              ) : (
                <Link href={`/docs/${slug.slice(0, i + 1).join("/")}`}>{part}</Link>
              )}
            </span>
          ))}
        </nav>

        {/* Hero */}
        {title ? (
          <header className="docs-hero">
            <p className="docs-hero-kicker">{kicker}</p>
            <h1 className="docs-hero-title">{title}</h1>
            {lede ? (
              <div className="docs-hero-lede [&>p]:m-0">
                <DocsMarkdown content={lede} />
              </div>
            ) : null}
          </header>
        ) : null}

        {/* Body */}
        <article className="docs-prose">
          <DocsMarkdown content={body} />
        </article>
      </div>

      {/* Right-rail mini-TOC, desktop only, when worth it */}
      {hasToc ? (
        <aside className="docs-toc-rail" aria-label="On this page">
          <div className="docs-toc-sticky">
            <p className="docs-toc-label">On this page</p>
            <nav className="space-y-0.5">
              {toc.map((entry) => (
                <a key={entry.id} href={`#${entry.id}`} className="docs-toc-link">
                  {entry.text}
                </a>
              ))}
            </nav>
          </div>
        </aside>
      ) : null}
    </main>
  );
}
