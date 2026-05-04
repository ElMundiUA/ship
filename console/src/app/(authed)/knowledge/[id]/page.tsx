/**
 * Knowledge bucket detail — two modes, multi-column.
 *
 * Category (no ``?article=``):
 *   masthead spans 6xl, then a 12-col body — article list 8 cols /
 *   "Other areas" + "About" sidebar 4 cols. Sidebar collapses below
 *   the list at < lg.
 *
 * Reader (``?article=<id>``):
 *   3-column grid at xl: left meta rail (UPDATED / STATUS / SOURCE
 *   stacked, uppercase labels), prose column capped at ~680px in
 *   the middle, right rail reserved empty for future TOC / related.
 *   Drops to single column under lg.
 *
 * No bordered cards on either surface; section breaks come from
 * whitespace + a single hairline rule.
 */

import Link from "next/link";
import { notFound } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import {
  type ApiBucket,
  ApiHttpError,
  getBucket,
  isApiConfigured,
  listBucketArticles,
  listBuckets,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import type { ApiBucketArticle } from "@/lib/api/types";
import { relativeTime } from "@/lib/format";

export const dynamic = "force-dynamic";


type SiblingBucket = {
  slug: string;
  name: string;
};

type LiveData = {
  source: "live";
  workspace: { id: string; slug: string; name: string };
  bucket: ApiBucket;
  articles: ApiBucketArticle[];
  siblings: SiblingBucket[];
};

type UnavailableData = {
  source: "unavailable";
  reason: string;
};

type Loaded = LiveData | UnavailableData;


async function load(slug: string): Promise<Loaded | "notfound"> {
  if (!isApiConfigured()) {
    return { source: "unavailable", reason: "backend not configured (SHIP_API_URL unset)" };
  }
  const token = await getCachedSessionToken();
  if (!token) {
    return { source: "unavailable", reason: "not signed in" };
  }
  try {
    const wss = await getCachedWorkspaces();
    if (wss.length === 0) {
      return { source: "unavailable", reason: "no workspaces yet — finish onboarding first" };
    }
    const ws = wss[0];

    const [bucket, articles, allBuckets] = await Promise.all([
      getBucket(ws.id, slug, token).catch((err) => {
        if (err instanceof ApiHttpError && err.status === 404) return null;
        throw err;
      }),
      listBucketArticles(ws.id, slug, {}, token).catch(
        () => [] as ApiBucketArticle[],
      ),
      listBuckets(ws.id, { token }).catch(() => [] as ApiBucket[]),
    ]);

    if (bucket === null) return "notfound";

    const siblings: SiblingBucket[] = allBuckets
      .filter((b) => b.slug !== bucket.slug && !b.archived_at)
      .map((b) => ({ slug: b.slug, name: b.name }));

    return {
      source: "live",
      workspace: { id: ws.id, slug: ws.slug, name: ws.name },
      bucket,
      articles,
      siblings,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { source: "unavailable", reason: `backend error: ${msg}` };
  }
}


export default async function KnowledgeBucketDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ article?: string | string[] }>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const selectedArticleId = Array.isArray(query.article)
    ? query.article[0]
    : query.article;
  const data = await load(id);
  if (data === "notfound") notFound();
  if (data.source !== "live") return <UnavailableView data={data} />;

  const selectedArticle = selectedArticleId
    ? data.articles.find((a) => a.id === selectedArticleId) ??
      data.articles.find((a) => a.slug === selectedArticleId) ??
      null
    : null;

  return selectedArticle ? (
    <ReaderView bucket={data.bucket} article={selectedArticle} />
  ) : (
    <CategoryView
      bucket={data.bucket}
      articles={data.articles}
      siblings={data.siblings}
    />
  );
}


// ---------------------------------------------------------------------------
// Category view — masthead + (article list | other areas + about)
// ---------------------------------------------------------------------------


function CategoryView({
  bucket,
  articles,
  siblings,
}: {
  bucket: ApiBucket;
  articles: ApiBucketArticle[];
  siblings: SiblingBucket[];
}) {
  return (
    <>
      <PageHeader kicker="knowledge" title={bucket.name} />
      <PageBody>
        <div className="mx-auto max-w-6xl">
          <header className="mb-12 space-y-3">
            <Link
              href="/knowledge"
              className="text-xs text-white/55 hover:text-white"
            >
              ← Knowledge
            </Link>
            <h1 className="font-display text-4xl font-bold leading-tight text-white">
              {bucket.name}
            </h1>
            {bucket.description && (
              <p className="max-w-2xl text-base leading-relaxed text-white/65">
                {bucket.description}
              </p>
            )}
            <p className="text-[11px] uppercase tracking-widest text-white/40">
              {articles.length} article{articles.length === 1 ? "" : "s"}
              <span className="mx-2 text-white/20">·</span>
              updated {relativeTime(bucket.updated_at)}
            </p>
          </header>

          <div className="grid grid-cols-1 gap-x-12 gap-y-12 lg:grid-cols-12">
            <section className="lg:col-span-8">
              <ArticleList articles={articles} bucketSlug={bucket.slug} />
            </section>

            <aside className="space-y-10 lg:col-span-4 lg:sticky lg:top-24 lg:self-start">
              {siblings.length > 0 && (
                <div className="space-y-4">
                  <h2 className="text-[11px] font-bold uppercase tracking-[0.22em] text-lilac/75">
                    Other areas
                  </h2>
                  <ul className="divide-y divide-white/5">
                    {siblings.map((sibling) => (
                      <li key={sibling.slug}>
                        <Link
                          href={`/knowledge/${encodeURIComponent(sibling.slug)}`}
                          className="block py-3 font-display text-sm font-bold uppercase tracking-wider text-white/65 transition hover:text-lilac"
                        >
                          {sibling.name}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="space-y-3">
                <h2 className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/40">
                  About
                </h2>
                <p className="text-sm leading-relaxed text-white/55">
                  Articles in this area are drafted by the synthesiser from
                  harvested notes, then reviewed by an operator before
                  publishing.
                </p>
              </div>
            </aside>
          </div>
        </div>
      </PageBody>
    </>
  );
}


function ArticleList({
  articles,
  bucketSlug,
}: {
  articles: ApiBucketArticle[];
  bucketSlug: string;
}) {
  if (articles.length === 0) {
    return (
      <p className="text-sm text-white/55">No articles in this bucket yet.</p>
    );
  }
  return (
    <ul className="divide-y divide-white/5">
      {articles.map((article) => (
        <li key={article.id} className="py-5 first:pt-0">
          <Link
            href={`/knowledge/${encodeURIComponent(bucketSlug)}?article=${encodeURIComponent(article.id)}`}
            className="group block space-y-1.5"
          >
            <div className="text-base font-semibold text-white group-hover:text-aqua">
              {article.title || article.slug}
            </div>
            {firstParagraph(article.body_md) && (
              <p className="line-clamp-2 text-sm leading-relaxed text-white/55">
                {firstParagraph(article.body_md)}
              </p>
            )}
            <div className="flex items-center gap-2 text-[11px] text-white/40">
              <span>{relativeTime(article.updated_at)}</span>
              {article.status !== "published" && (
                <>
                  <span className="text-white/20">·</span>
                  <span>{article.status}</span>
                </>
              )}
              {provenanceHint(article) && (
                <>
                  <span className="text-white/20">·</span>
                  <span>{provenanceHint(article)}</span>
                </>
              )}
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}


// ---------------------------------------------------------------------------
// Reader — left meta rail + prose + reserved right rail
// ---------------------------------------------------------------------------


function ReaderView({
  bucket,
  article,
}: {
  bucket: ApiBucket;
  article: ApiBucketArticle;
}) {
  const provenance = provenanceHint(article);
  return (
    <>
      <PageHeader kicker="knowledge" title={article.title} />
      <PageBody>
        <div className="mx-auto max-w-6xl">
          <header className="mb-10 space-y-4">
            <Link
              href={`/knowledge/${encodeURIComponent(bucket.slug)}`}
              className="text-xs text-white/55 hover:text-white"
            >
              ← {bucket.name}
            </Link>
            <h1 className="font-display text-3xl font-bold leading-tight tracking-tight text-white md:text-4xl">
              {article.title}
            </h1>
            <p className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-widest text-white/40">
              <span>Updated {relativeTime(article.updated_at)}</span>
              {article.status !== "published" && (
                <>
                  <span className="text-white/20">·</span>
                  <span>{article.status}</span>
                </>
              )}
              {provenance && (
                <>
                  <span className="text-white/20">·</span>
                  <span>{provenance}</span>
                </>
              )}
            </p>
            <div className="border-t border-aqua/30 pt-1" />
          </header>

          <div className="grid grid-cols-1 gap-12 lg:grid-cols-12">
            <aside className="order-2 space-y-6 lg:order-1 lg:col-span-2">
              <RailLabel label="Updated" value={relativeTime(article.updated_at)} />
              <RailLabel label="Status" value={article.status} />
              {provenance && <RailLabel label="Source" value={provenance} />}
              <RailLabel label="Slug" value={article.slug} mono />
              <RailLabel label="Version" value={`v${article.version}`} />
            </aside>

            <article className="order-1 max-w-[680px] lg:order-2 lg:col-span-7">
              <div className="space-y-5 text-[16px] leading-[1.8] text-white/82">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    h1: ({ children }) => (
                      <h2 className="mt-12 font-display text-2xl font-bold text-white">
                        {children}
                      </h2>
                    ),
                    h2: ({ children }) => (
                      <h2 className="mt-10 font-display text-xl font-bold text-white">
                        {children}
                      </h2>
                    ),
                    h3: ({ children }) => (
                      <h3 className="mt-8 font-display text-lg font-bold text-white">
                        {children}
                      </h3>
                    ),
                    p: ({ children }) => (
                      <p className="text-white/82">{children}</p>
                    ),
                    a: ({ children, href }) => (
                      <a
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className="text-aqua underline-offset-4 hover:underline"
                      >
                        {children}
                      </a>
                    ),
                    ul: ({ children }) => (
                      <ul className="list-disc space-y-2 pl-6">{children}</ul>
                    ),
                    ol: ({ children }) => (
                      <ol className="list-decimal space-y-2 pl-6">{children}</ol>
                    ),
                    blockquote: ({ children }) => (
                      <blockquote className="border-l-2 border-aqua/50 pl-5 italic text-white/65">
                        {children}
                      </blockquote>
                    ),
                    code: ({ children }) => (
                      <code className="rounded bg-white/[0.08] px-1.5 py-0.5 font-mono text-[13px] text-aqua/95">
                        {children}
                      </code>
                    ),
                    pre: ({ children }) => (
                      <pre className="overflow-x-auto rounded-lg bg-black/40 p-4 font-mono text-[13px] leading-relaxed text-white/85 ring-1 ring-white/5">
                        {children}
                      </pre>
                    ),
                    hr: () => <hr className="border-white/10" />,
                  }}
                >
                  {article.body_md || "_Empty article._"}
                </ReactMarkdown>
              </div>
            </article>

            <aside
              className="order-3 hidden lg:col-span-3 lg:block"
              aria-hidden="true"
            />
          </div>
        </div>
      </PageBody>
    </>
  );
}


function RailLabel({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="space-y-1">
      <div className="text-[11px] uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div
        className={
          mono
            ? "font-mono text-[12px] text-white/75"
            : "text-sm text-white/75"
        }
      >
        {value}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function provenanceHint(article: ApiBucketArticle): string | null {
  const p = article.provenance;
  if (!p || typeof p !== "object") return null;
  const kind = typeof p.kind === "string" ? p.kind : null;
  if (kind === "pr_merged") {
    const num = typeof p.pr_number === "number" ? p.pr_number : null;
    const author = typeof p.author === "string" ? p.author : null;
    return typeof num === "number" || typeof author === "string"
      ? `from PR #${num ?? "?"} by @${author ?? "?"}`
      : "from merged PR";
  }
  if (kind === "knowledge_import_source") {
    const source = typeof p.import_source_kind === "string" ? p.import_source_kind : null;
    return source ? `harvested from ${source}` : "harvested from import source";
  }
  if (kind === "auto_routed_notes" || kind === "auto_routed_draft") {
    return "synthesised from harvested notes";
  }
  if (kind === "agent_memory" && typeof p.thread_id === "string") {
    return `packed from thread ${p.thread_id.slice(0, 8)}…`;
  }
  return null;
}


function firstParagraph(body: string): string {
  if (!body) return "";
  const text = body.trim();
  if (!text) return "";
  for (const chunk of text.split("\n\n")) {
    const candidate = chunk.replace(/^#+\s+/, "").trim();
    if (candidate) {
      return candidate.length > 200
        ? candidate.slice(0, 199).trimEnd() + "…"
        : candidate;
    }
  }
  return text.length > 200 ? text.slice(0, 199).trimEnd() + "…" : text;
}


function UnavailableView({ data }: { data: UnavailableData }) {
  return (
    <>
      <PageHeader kicker="knowledge" title="Knowledge" />
      <PageBody>
        <ApiUnavailable scope="knowledge" details={data.reason} />
      </PageBody>
    </>
  );
}
