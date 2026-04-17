import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { BookMarkdown } from "@/components/book-content";
import { preprocessDocumentationMarkdown } from "@/lib/docs-markdown";
import { repoRoot } from "@/lib/repo-path";

export const metadata: Metadata = {
  title: "shipctl CLI — Ship manual",
  description:
    "shipctl is the unified Ship CLI: init, new, doctor, verify, config, sync, telemetry, feedback, search, docs, pattern/tool/workflow/collection.",
};

function readCliReadme(): string {
  const abs = path.join(repoRoot(), "cli", "README.md");
  if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
    throw new Error(`cli/README.md not found at ${abs}`);
  }
  return fs.readFileSync(abs, "utf8");
}

export default function ShipctlDocsPage() {
  let raw: string;
  try {
    raw = readCliReadme();
  } catch {
    notFound();
  }
  const md = preprocessDocumentationMarkdown(raw);

  return (
    <main className="book-shell py-12 sm:py-16">
      <nav className="text-sm text-white/45">
        <Link className="font-semibold text-aqua hover:underline" href="/docs">
          Manual
        </Link>
        <span className="mx-2 text-white/25">/</span>
        <span className="text-white/65">shipctl CLI</span>
      </nav>
      <article className="book-prose prose prose-invert prose-lg mt-8 max-w-none prose-headings:scroll-mt-28 prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-code:text-aqua/90 prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-md prose-ul:my-5 prose-ol:my-5 prose-li:marker:text-aqua/70">
        <BookMarkdown content={md} />
      </article>
      <p className="mt-12 text-center text-sm text-white/45">
        Source: <code className="font-mono">cli/README.md</code> in the Ship repository.
      </p>
    </main>
  );
}
