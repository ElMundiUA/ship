import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BookMarkdown } from "@/components/book-content";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { preprocessDocumentationMarkdown } from "@/lib/docs-markdown";
import { repoUrl } from "@/lib/config";
import { getWorkflowById, loadWorkflowMarkdown, loadWorkflowsManifest } from "@/lib/workflows";

export async function generateStaticParams() {
  const { workflows } = loadWorkflowsManifest();
  return workflows.map((w) => ({ slug: w.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  try {
    const w = getWorkflowById(slug);
    if (!w) return { title: "Workflow — Ship" };
    return { title: `${w.title} — Workflows · Ship`, description: w.summary };
  } catch {
    return { title: "Workflow — Ship" };
  }
}

const proseArticle =
  "book-prose prose prose-invert prose-lg max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-coral/70";

export default async function WorkflowDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  let raw: string;
  let wf: NonNullable<ReturnType<typeof getWorkflowById>>;
  try {
    const w = getWorkflowById(slug);
    if (!w) notFound();
    wf = w;
    raw = loadWorkflowMarkdown(w.path);
  } catch {
    notFound();
  }

  const md = preprocessDocumentationMarkdown(raw);

  return (
    <>
      <SiteHeader />
      <main className="book-shell py-14 sm:py-16">
        <nav className="text-sm text-white/50">
          <Link href="/workflows" className="font-semibold text-coral underline-offset-2 hover:underline">
            Workflows
          </Link>
          <span className="mx-2 text-white/25">/</span>
          <span className="text-white/70">{wf.title}</span>
        </nav>
        <header className="mt-8 border-b border-white/10 pb-10">
          <p className="text-xs font-bold uppercase tracking-widest text-coral">{wf.group}</p>
          <h1 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">{wf.title}</h1>
          <p className="mt-4 max-w-3xl text-lg text-white/70">{wf.summary}</p>
          <p className="mt-2 text-sm text-white/45">Source: {wf.path}</p>
          <div className="mt-6 flex flex-wrap gap-2">
            {wf.tags.map((tag) => (
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
              href={`${repoUrl}/blob/main/${wf.path}`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-secondary inline-flex !py-2 !text-sm"
            >
              View on GitHub
            </a>
            <Link href="/workflows#cli" className="btn-secondary inline-flex !py-2 !text-sm">
              CLI reference
            </Link>
            <Link href="/collections" className="btn-secondary inline-flex !py-2 !text-sm">
              Collections
            </Link>
            <Link href="/tools" className="btn-secondary inline-flex !py-2 !text-sm">
              Tools
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
