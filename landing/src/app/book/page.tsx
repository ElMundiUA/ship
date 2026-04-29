import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { BookMarkdown } from "@/components/book-content";
import { BookHero } from "@/components/book-hero";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { preprocessBookMarkdown } from "@/lib/book-markdown";

export const metadata: Metadata = {
  title: "The book — Ship",
  description: "Why fences, throughput, and legibility matter — long narrative in the same Ship landing UI.",
};

export default function BookPage() {
  const bookPath = path.join(process.cwd(), "content", "book.md");
  if (!fs.existsSync(bookPath)) {
    return (
      <>
        <SiteHeader />
        <main className="mx-auto max-w-xl px-4 pb-24 pt-28 text-center">
          <h1 className="font-display text-2xl font-bold text-white">Book source not synced</h1>
          <p className="mt-4 text-white/65">
            Expected source file: <code className="text-aqua">landing/content/book.md</code>. Run from repo root with{" "}
            <code className="text-aqua">npm run landing:dev</code> or{" "}
            <code className="text-aqua">npm run landing:build</code> after restoring that file.
          </p>
        </main>
        <SiteFooter />
      </>
    );
  }

  const raw = fs.readFileSync(bookPath, "utf-8");
  const md = preprocessBookMarkdown(raw);

  return (
    <>
      <SiteHeader />
      <main>
        <BookHero />

        <div className="book-shell py-14 sm:py-16">
          <article className="book-prose prose prose-invert prose-lg max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-aqua/70 prose-hr:hidden">
            <BookMarkdown content={md} />
          </article>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
