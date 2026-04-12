import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { BookMarkdown } from "@/components/book-content";
import { preprocessDocumentationMarkdown } from "@/lib/docs-markdown";
import { listDocumentationPages, readDocumentationFile, slugToRelPath } from "@/lib/documentation-fs";

export async function generateStaticParams() {
  const pages = listDocumentationPages();
  return pages.filter((p) => p.slug.length > 0).map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string[] }> }): Promise<Metadata> {
  const { slug } = await params;
  const rel = slugToRelPath(slug);
  if (!rel) return { title: "Manual — Ship" };
  try {
    const raw = readDocumentationFile(rel);
    const first = raw.split("\n").find((l) => l.startsWith("# "));
    const title = first?.replace(/^#\s+/, "").trim() ?? slug.join("/");
    return { title: `${title} — Ship manual` };
  } catch {
    return { title: "Ship manual" };
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
  const trail = slug.join(" / ");

  return (
    <main className="book-shell py-12 sm:py-16">
      <nav className="text-sm text-white/45">
        <Link className="font-semibold text-aqua hover:underline" href="/docs">
          Manual
        </Link>
        <span className="mx-2 text-white/25">/</span>
        <span className="text-white/65">{trail}</span>
      </nav>
      <article className="book-prose prose prose-invert prose-lg mt-8 max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-aqua/70">
        <BookMarkdown content={md} />
      </article>
    </main>
  );
}
