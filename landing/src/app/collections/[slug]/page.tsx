import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BookMarkdown } from "@/components/book-content";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { preprocessDocumentationMarkdown } from "@/lib/docs-markdown";
import { repoUrl } from "@/lib/config";
import { getCollectionById, loadCollectionMarkdown, loadCollectionsManifest } from "@/lib/collections";

export async function generateStaticParams() {
  const { collections } = loadCollectionsManifest();
  return collections.map((c) => ({ slug: c.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  try {
    const c = getCollectionById(slug);
    if (!c) return { title: "Collection — Ship" };
    return { title: `${c.title} — Collections · Ship`, description: c.summary };
  } catch {
    return { title: "Collection — Ship" };
  }
}

const proseArticle =
  "book-prose prose prose-invert prose-lg max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-lilac/70 prose-table:text-sm";

export default async function CollectionDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  let raw: string;
  let col: NonNullable<ReturnType<typeof getCollectionById>>;
  try {
    const c = getCollectionById(slug);
    if (!c) notFound();
    col = c;
    raw = loadCollectionMarkdown(c.path);
  } catch {
    notFound();
  }

  const md = preprocessDocumentationMarkdown(raw);

  return (
    <>
      <SiteHeader />
      <main className="book-shell py-14 sm:py-16">
        <nav className="text-sm text-white/50">
          <Link href="/collections" className="font-semibold text-lilac underline-offset-2 hover:underline">
            Collections
          </Link>
          <span className="mx-2 text-white/25">/</span>
          <span className="text-white/70">{col.title}</span>
        </nav>
        <header className="mt-8 border-b border-white/10 pb-10">
          <p className="text-xs font-bold uppercase tracking-widest text-lilac">{col.group}</p>
          <h1 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">{col.title}</h1>
          <p className="mt-4 max-w-3xl text-lg text-white/70">{col.summary}</p>
          <p className="mt-2 text-sm text-white/45">Source: {col.path}</p>
          <div className="mt-6 flex flex-wrap gap-2">
            {col.tags.map((tag) => (
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
              href={`${repoUrl}/blob/main/${col.path}`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-secondary inline-flex !py-2 !text-sm"
            >
              View on GitHub
            </a>
            <Link href="/collections#cli" className="btn-secondary inline-flex !py-2 !text-sm">
              CLI reference
            </Link>
            <Link href="/workflows" className="btn-secondary inline-flex !py-2 !text-sm">
              Workflows
            </Link>
            <Link href="/tools" className="btn-secondary inline-flex !py-2 !text-sm">
              Tools
            </Link>
            <Link href="/patterns" className="btn-secondary inline-flex !py-2 !text-sm">
              Patterns
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
