import type { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { BlogMarkdown } from "@/components/blog-content";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { preprocessBlogMarkdown } from "@/lib/blog-markdown";
import {
  type ChangelogEntry,
  formatChangelogDate,
  listChangelogEntries,
} from "@/lib/changelog";
import { repoUrl } from "@/lib/config";
import { resolveMetadataBase } from "@/lib/site-url";

export const metadata: Metadata = {
  title: "Changelog — Ship",
  description:
    "What we shipped, when. Releases for Ship — the open-source delivery workspace for AI-assisted engineering.",
  alternates: { canonical: "/changelog" },
};

function isEmbedUrl(url: string): boolean {
  return /loom\.com|youtube\.com|youtu\.be|vimeo\.com/.test(url);
}

function HeroMedia({ entry }: { entry: ChangelogEntry }) {
  if (entry.heroVideo && isEmbedUrl(entry.heroVideo)) {
    return (
      <div className="mt-10 overflow-hidden rounded-2xl border border-white/10 bg-black/40">
        <div className="aspect-video w-full">
          <iframe
            src={entry.heroVideo}
            className="h-full w-full"
            title={entry.heroAlt || entry.title}
            allow="autoplay; fullscreen; picture-in-picture"
            allowFullScreen
          />
        </div>
      </div>
    );
  }
  if (entry.heroImage) {
    return (
      <div className="mt-10 overflow-hidden rounded-2xl border border-white/10 bg-black/40">
        <Image
          src={entry.heroImage}
          alt={entry.heroAlt || entry.title}
          width={2400}
          height={1350}
          className="h-auto w-full"
          priority={false}
        />
      </div>
    );
  }
  return null;
}

function PRPills({ prs }: { prs: number[] }) {
  if (prs.length === 0) return null;
  return (
    <div className="mt-10 border-t border-white/10 pt-6">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Pull requests
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {prs.map((n) => (
          <a
            key={n}
            href={`${repoUrl}/pull/${n}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 font-mono text-[12px] text-white/55 transition hover:border-aqua/40 hover:bg-aqua/10 hover:text-aqua"
          >
            #{n}
          </a>
        ))}
      </div>
    </div>
  );
}

function EntryCard({ entry, isLast }: { entry: ChangelogEntry; isLast: boolean }) {
  const body = preprocessBlogMarkdown(entry.body);
  return (
    <article
      id={entry.slug}
      className="relative scroll-mt-28 pb-20 sm:pb-24"
    >
      <header>
        <div className="flex items-center gap-3 text-[11px] font-bold uppercase tracking-[0.22em]">
          <Link
            href={`#${entry.slug}`}
            className="font-mono text-white/45 transition hover:text-aqua"
          >
            <time dateTime={entry.date}>{formatChangelogDate(entry.date)}</time>
          </Link>
          {entry.kicker ? (
            <>
              <span className="h-1 w-1 rounded-full bg-white/25" aria-hidden />
              <span className="text-aqua/85">{entry.kicker}</span>
            </>
          ) : null}
        </div>
        <h2 className="font-display mt-4 text-3xl font-bold leading-[1.08] tracking-tight text-white sm:text-4xl lg:text-[2.625rem]">
          {entry.title}
        </h2>
        {entry.summary ? (
          <p className="mt-5 max-w-3xl text-lg leading-relaxed text-white/70 sm:text-xl">
            {entry.summary}
          </p>
        ) : null}
      </header>

      <HeroMedia entry={entry} />

      <div className="blog-prose prose prose-invert prose-lg mt-10 max-w-none prose-p:text-white/80 prose-p:leading-relaxed prose-strong:text-white prose-h3:font-display prose-h3:text-2xl prose-h3:font-bold prose-h3:tracking-tight prose-hr:hidden">
        <BlogMarkdown content={body} />
      </div>

      <PRPills prs={entry.prs} />

      {!isLast ? (
        <div
          className="mt-20 h-px w-full bg-gradient-to-r from-transparent via-white/15 to-transparent sm:mt-24"
          aria-hidden
        />
      ) : null}
    </article>
  );
}

export default function ChangelogPage() {
  const entries = listChangelogEntries();
  const siteUrl = resolveMetadataBase().toString().replace(/\/$/, "");

  return (
    <>
      <SiteHeader />
      <main className="pb-24 pt-28 sm:pb-32 sm:pt-32">
        {/* Page-wide background flourish */}
        <div
          className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[36rem] bg-[radial-gradient(ellipse_70%_50%_at_50%_0%,rgba(207,169,107,0.10),transparent_70%)]"
          aria-hidden
        />

        <div className="mx-auto max-w-3xl px-4 sm:px-6">
          <header className="mb-14 sm:mb-20">
            <p className="text-[11px] font-bold uppercase tracking-[0.3em] text-sun">
              Changelog
            </p>
            <h1 className="font-display mt-3 text-4xl font-bold leading-[1.05] tracking-tight text-white sm:text-5xl lg:text-[3.5rem]">
              <span className="bg-gradient-to-r from-coral via-sun to-aqua bg-clip-text text-transparent">
                What we shipped,
              </span>{" "}
              when.
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-relaxed text-white/65 sm:text-xl">
              Releases for Ship — the open-source delivery workspace for
              AI-assisted engineering. The deeper thinking behind each beat
              lives in{" "}
              <Link href="/blog" className="text-aqua underline-offset-4 hover:underline">
                Ship Log
              </Link>
              ; this page is the dry timeline.
            </p>
          </header>

          {entries.length === 0 ? (
            <p className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 text-sm text-white/55">
              No changelog entries yet. Add a Markdown file under{" "}
              <code className="font-mono">landing/content/changelog/</code>.
            </p>
          ) : (
            <div>
              {entries.map((entry, idx) => (
                <EntryCard
                  key={entry.slug}
                  entry={entry}
                  isLast={idx === entries.length - 1}
                />
              ))}
            </div>
          )}
        </div>
      </main>
      <SiteFooter />
      {entries.length > 0 ? (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "ItemList",
              itemListElement: entries.map((e, i) => ({
                "@type": "ListItem",
                position: i + 1,
                url: `${siteUrl}/changelog#${e.slug}`,
                name: e.title,
              })),
            }),
          }}
        />
      ) : null}
    </>
  );
}
