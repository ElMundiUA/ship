import type { Metadata } from "next";
import Link from "next/link";
import { BookMarkdown } from "@/components/book-content";
import { preprocessDocumentationMarkdown } from "@/lib/docs-markdown";
import { readDocumentationFile } from "@/lib/documentation-fs";

export const metadata: Metadata = {
  title: "Ship manual — Start here",
  description: "Instruction-first SDLC framework: start here, then getting started, tools, and reference org.",
};

export default function DocsHomePage() {
  const raw = readDocumentationFile("index.md");
  const md = preprocessDocumentationMarkdown(raw);

  return (
    <main className="book-shell py-12 sm:py-16">
      <article className="book-prose prose prose-invert prose-lg max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-aqua/70">
        <BookMarkdown content={md} />
      </article>
      <p className="mt-12 text-center text-sm text-white/45">
        This manual ships inside the Next.js site — no separate MkDocs server.
      </p>
      <p className="mt-4 text-center">
        <Link href="/docs/getting-started" className="btn-primary inline-flex">
          Getting started
        </Link>
      </p>
    </main>
  );
}
