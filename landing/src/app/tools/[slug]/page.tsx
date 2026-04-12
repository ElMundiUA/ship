import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BookMarkdown } from "@/components/book-content";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { preprocessDocumentationMarkdown } from "@/lib/docs-markdown";
import { repoUrl } from "@/lib/config";
import { getToolById, loadToolMarkdown, loadToolsManifest } from "@/lib/tools";

export async function generateStaticParams() {
  const { tools } = loadToolsManifest();
  return tools.map((t) => ({ slug: t.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  try {
    const t = getToolById(slug);
    if (!t) return { title: "Tool — Ship" };
    return { title: `${t.title} — Tools · Ship`, description: t.summary };
  } catch {
    return { title: "Tool — Ship" };
  }
}

const proseArticle =
  "book-prose prose prose-invert prose-lg max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-aqua/70";

export default async function ToolDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  let raw: string;
  let tool: NonNullable<ReturnType<typeof getToolById>>;
  try {
    const t = getToolById(slug);
    if (!t) notFound();
    tool = t;
    raw = loadToolMarkdown(t.path);
  } catch {
    notFound();
  }

  const md = preprocessDocumentationMarkdown(raw);

  return (
    <>
      <SiteHeader />
      <main className="book-shell py-14 sm:py-16">
        <nav className="text-sm text-white/50">
          <Link href="/tools" className="font-semibold text-aqua underline-offset-2 hover:underline">
            Tools
          </Link>
          <span className="mx-2 text-white/25">/</span>
          <span className="text-white/70">{tool.title}</span>
        </nav>
        <header className="mt-8 border-b border-white/10 pb-10">
          <p className="text-xs font-bold uppercase tracking-widest text-sun">{tool.group}</p>
          <h1 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">{tool.title}</h1>
          <p className="mt-4 max-w-3xl text-lg text-white/70">{tool.summary}</p>
          <p className="mt-2 text-sm text-white/45">Source: {tool.path}</p>
          <div className="mt-6 flex flex-wrap gap-2">
            {tool.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-white/15 bg-white/[0.05] px-3 py-1 text-xs font-medium text-white/55"
              >
                {tag}
              </span>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap gap-3">
            <a
              href={`${repoUrl}/blob/main/${tool.path}`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-secondary inline-flex !py-2 !text-sm"
            >
              View on GitHub
            </a>
            <Link href="/tools#cli" className="btn-secondary inline-flex !py-2 !text-sm">
              CLI reference
            </Link>
            <Link href="/patterns" className="btn-secondary inline-flex !py-2 !text-sm">
              Org patterns
            </Link>
          </div>
        </header>
        <article className={`${proseArticle} mt-10`}>
          <BookMarkdown content={md} />
        </article>
      </main>
      <SiteFooter />
    </>
  );
}
