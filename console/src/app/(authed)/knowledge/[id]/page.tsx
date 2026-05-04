/**
 * Knowledge bucket detail — two modes.
 *
 * - ``?article=<id>``: article reader. Just the markdown body in a
 *   centred reading column, with a "← Back to <category>" link at
 *   the top. No sibling article list, no metadata pills.
 * - default: category page. Bucket title + description, then the
 *   bucket's full article list (no inline viewer — clicks go to the
 *   reader URL).
 *
 * Both modes share the search-first newspaper aesthetic with the
 * index page: no Card wrappers, no tables, ~720px reading column.
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
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import type { ApiBucketArticle } from "@/lib/api/types";
import { relativeTime } from "@/lib/format";

export const dynamic = "force-dynamic";


type LiveData = {
  source: "live";
  workspace: { id: string; slug: string; name: string };
  bucket: ApiBucket;
  articles: ApiBucketArticle[];
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

    const bucket = await getBucket(ws.id, slug, token).catch((err) => {
      if (err instanceof ApiHttpError && err.status === 404) return null;
      throw err;
    });
    if (bucket === null) return "notfound";

    const articles = await listBucketArticles(ws.id, slug, {}, token).catch(
      () => [] as ApiBucketArticle[],
    );

    return {
      source: "live",
      workspace: { id: ws.id, slug: ws.slug, name: ws.name },
      bucket,
      articles,
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
    <CategoryView bucket={data.bucket} articles={data.articles} />
  );
}


// ---------------------------------------------------------------------------
// Category page — bucket title + article list
// ---------------------------------------------------------------------------


function CategoryView({
  bucket,
  articles,
}: {
  bucket: ApiBucket;
  articles: ApiBucketArticle[];
}) {
  return (
    <>
      <PageHeader kicker="knowledge" title={bucket.name} />
      <PageBody>
        <div className="mx-auto max-w-3xl space-y-8">
          <div className="space-y-2">
            <Link
              href="/knowledge"
              className="text-xs text-white/55 hover:text-white"
            >
              ← Knowledge
            </Link>
            <h1 className="font-display text-3xl font-bold text-white">
              {bucket.name}
            </h1>
            {bucket.description && (
              <p className="text-base leading-relaxed text-white/65">
                {bucket.description}
              </p>
            )}
            <p className="text-xs text-white/40">
              {articles.length} article{articles.length === 1 ? "" : "s"}
            </p>
          </div>

          <ArticleList articles={articles} bucketSlug={bucket.slug} />
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
    <ul className="space-y-6">
      {articles.map((article) => (
        <li key={article.id}>
          <Link
            href={`/knowledge/${encodeURIComponent(bucketSlug)}?article=${encodeURIComponent(article.id)}`}
            className="group block"
          >
            <div className="text-base font-semibold text-white group-hover:text-aqua">
              {article.title || article.slug}
            </div>
            {firstParagraph(article.body_md) && (
              <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-white/60">
                {firstParagraph(article.body_md)}
              </p>
            )}
            <div className="mt-1 flex items-center gap-2 text-[11px] text-white/40">
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
// Article reader — Medium-style
// ---------------------------------------------------------------------------


function ReaderView({
  bucket,
  article,
}: {
  bucket: ApiBucket;
  article: ApiBucketArticle;
}) {
  return (
    <>
      <PageHeader kicker="knowledge" title={article.title} />
      <PageBody>
        <article className="mx-auto max-w-3xl space-y-6">
          <div className="space-y-2">
            <Link
              href={`/knowledge/${encodeURIComponent(bucket.slug)}`}
              className="text-xs text-white/55 hover:text-white"
            >
              ← {bucket.name}
            </Link>
            <h1 className="font-display text-3xl font-bold leading-tight text-white">
              {article.title}
            </h1>
            <p className="text-xs text-white/40">
              Updated {relativeTime(article.updated_at)}
              {article.status !== "published" && (
                <>
                  <span className="mx-2 text-white/20">·</span>
                  <span>{article.status}</span>
                </>
              )}
              {provenanceHint(article) && (
                <>
                  <span className="mx-2 text-white/20">·</span>
                  <span>{provenanceHint(article)}</span>
                </>
              )}
            </p>
          </div>

          <div className="prose-reader space-y-5 text-[15px] leading-[1.75] text-white/80">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h1: ({ children }) => (
                  <h1 className="mt-10 font-display text-2xl font-bold text-white">
                    {children}
                  </h1>
                ),
                h2: ({ children }) => (
                  <h2 className="mt-8 font-display text-xl font-bold text-white">
                    {children}
                  </h2>
                ),
                h3: ({ children }) => (
                  <h3 className="mt-6 font-display text-lg font-bold text-white">
                    {children}
                  </h3>
                ),
                p: ({ children }) => (
                  <p className="text-white/80">{children}</p>
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
                  <pre className="overflow-x-auto rounded-lg bg-black/40 p-4 font-mono text-[13px] leading-relaxed text-white/85">
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
      </PageBody>
    </>
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
