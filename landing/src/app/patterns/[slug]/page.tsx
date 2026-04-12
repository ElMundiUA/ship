import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { repoUrl } from "@/lib/config";
import { getPatternById, loadPatternMarkdown, loadPatternsManifest } from "@/lib/patterns";

export async function generateStaticParams() {
  const { patterns } = loadPatternsManifest();
  return patterns.map((p) => ({ slug: p.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  try {
    const p = getPatternById(slug);
    if (!p) return { title: "Pattern — Ship" };
    return { title: `${p.title} — Org patterns · Ship`, description: p.summary };
  } catch {
    return { title: "Pattern — Ship" };
  }
}

const proseArticle =
  "book-prose prose prose-invert prose-lg max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-aqua/70";

export default async function PatternDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  let md: string;
  let pattern: NonNullable<ReturnType<typeof getPatternById>>;
  try {
    const p = getPatternById(slug);
    if (!p) notFound();
    pattern = p;
    md = loadPatternMarkdown(p.path);
  } catch {
    notFound();
  }

  return (
    <>
      <SiteHeader />
      <main className="book-shell py-14 sm:py-16">
        <nav className="text-sm text-white/50">
          <Link href="/patterns" className="font-semibold text-aqua underline-offset-2 hover:underline">
            Org patterns
          </Link>
          <span className="mx-2 text-white/25">/</span>
          <span className="text-white/70">{pattern.title}</span>
        </nav>
        <header className="mt-8 border-b border-white/10 pb-10">
          <p className="text-xs font-bold uppercase tracking-widest text-lilac">{pattern.group}</p>
          <h1 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">{pattern.title}</h1>
          <p className="mt-4 max-w-3xl text-lg text-white/70">{pattern.summary}</p>
          <div className="mt-6 flex flex-wrap gap-2">
            {pattern.tags.map((tag) => (
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
              href={`${repoUrl}/blob/main/${pattern.path}`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-secondary inline-flex !py-2 !text-sm"
            >
              View on GitHub
            </a>
            <Link href="/patterns#cli" className="btn-secondary inline-flex !py-2 !text-sm">
              CLI reference
            </Link>
          </div>
        </header>
        <article className={`${proseArticle} mt-10`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
        </article>
      </main>
      <SiteFooter />
    </>
  );
}
