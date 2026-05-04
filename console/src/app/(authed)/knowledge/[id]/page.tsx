/**
 * Knowledge bucket detail — articles list + inline viewer.
 *
 * Stripped down to match the search-first index page (PR #90):
 *
 * - Breadcrumb header.
 * - Bucket title + one-line description.
 * - Articles table, click-through to inline viewer.
 *
 * Retired in this pass: tab row (Articles / Sources / Distiller runs),
 * "Document" markdown card, legacy ``repo_files`` mirror branch (those
 * buckets are dead post-KB-5), CLI sidebar card, "Source repo" sidebar
 * card, ``LiveBanner`` debug strip, scope/source badges in the header,
 * per-bucket Upload article (direct bucket writes are gone — content
 * flows in via import sources only).
 */

import Link from "next/link";
import { notFound } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
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
  return data.source === "live" ? (
    <LiveView data={data} selectedArticleId={selectedArticleId} />
  ) : (
    <UnavailableView data={data} />
  );
}


function LiveView({
  data,
  selectedArticleId,
}: {
  data: LiveData;
  selectedArticleId?: string;
}) {
  const { workspace: ws, bucket, articles } = data;
  const selectedArticle =
    articles.find((article) => article.id === selectedArticleId) ??
    articles.find((article) => article.slug === selectedArticleId) ??
    articles[0] ??
    null;

  return (
    <>
      <PageHeader kicker="knowledge" title={bucket.name} />
      <PageBody>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-white/55">
          <Link href="/knowledge" className="hover:text-white">
            Knowledge
          </Link>
          <span className="text-white/25">/</span>
          <span className="font-mono text-white/65">{bucket.slug}</span>
          <span className="ml-auto text-[11px] text-white/40">
            {articles.length} article{articles.length === 1 ? "" : "s"} · updated{" "}
            {relativeTime(bucket.updated_at)}
          </span>
        </div>

        {bucket.description && (
          <p className="mb-5 max-w-3xl text-sm leading-relaxed text-white/70">
            {bucket.description}
          </p>
        )}

        <ArticlesCard
          articles={articles}
          bucketSlug={bucket.slug}
          selectedArticleId={selectedArticle?.id}
        />
        {selectedArticle && <ArticleViewer article={selectedArticle} />}
      </PageBody>
    </>
  );
}


function ArticlesCard({
  articles,
  bucketSlug,
  selectedArticleId,
}: {
  articles: ApiBucketArticle[];
  bucketSlug: string;
  selectedArticleId?: string;
}) {
  if (articles.length === 0) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.02] px-5 py-10 text-center">
        <p className="text-sm text-white/55">
          No articles in this bucket yet.
        </p>
      </div>
    );
  }
  return (
    <Card padded={false} data-testid="bucket-articles">
      <table className="min-w-full text-sm">
        <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
          <tr>
            <th className="px-4 py-2 text-left font-semibold">Title</th>
            <th className="px-4 py-2 text-left font-semibold">Slug</th>
            <th className="px-4 py-2 text-left font-semibold">v</th>
            <th className="px-4 py-2 text-left font-semibold">Status</th>
            <th className="px-4 py-2 text-left font-semibold">Updated</th>
          </tr>
        </thead>
        <tbody>
          {articles.map((a) => (
            <tr
              key={a.id}
              className={
                a.id === selectedArticleId
                  ? "border-t border-aqua/20 bg-aqua/[0.04]"
                  : "border-t border-white/5"
              }
            >
              <td className="px-4 py-2.5 align-top">
                <Link
                  href={`/knowledge/${encodeURIComponent(bucketSlug)}?article=${encodeURIComponent(a.id)}#article-viewer`}
                  className="font-semibold text-white hover:text-aqua"
                >
                  {a.title}
                </Link>
                {provenanceHint(a) && (
                  <div className="mt-0.5 text-[10px] text-white/45">
                    {provenanceHint(a)}
                  </div>
                )}
              </td>
              <td className="px-4 py-2.5 align-top font-mono text-[11px] text-aqua/85">
                {a.slug}
              </td>
              <td className="px-4 py-2.5 align-top text-xs text-white/65">
                {a.version}
              </td>
              <td className="px-4 py-2.5 align-top">
                <Badge tone={statusTone(a.status)}>{a.status}</Badge>
              </td>
              <td className="px-4 py-2.5 align-top text-xs text-white/55">
                {relativeTime(a.updated_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}


function ArticleViewer({ article }: { article: ApiBucketArticle }) {
  return (
    <Card id="article-viewer" data-testid="article-viewer" className="mt-5">
      <CardHeader
        title={article.title}
        subtitle={
          <span>
            <span className="font-mono">{article.slug}</span> · v{article.version} ·{" "}
            {article.status} · updated {relativeTime(article.updated_at)}
          </span>
        }
      />
      <article className="space-y-4 text-sm leading-relaxed text-white/80">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <h1 className="font-display text-2xl font-bold text-white">
                {children}
              </h1>
            ),
            h2: ({ children }) => (
              <h2 className="font-display text-xl font-bold text-white">
                {children}
              </h2>
            ),
            h3: ({ children }) => (
              <h3 className="font-display text-lg font-bold text-white">
                {children}
              </h3>
            ),
            p: ({ children }) => <p className="text-white/78">{children}</p>,
            a: ({ children, href }) => (
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                className="text-aqua hover:underline"
              >
                {children}
              </a>
            ),
            ul: ({ children }) => (
              <ul className="list-disc space-y-1 pl-5">{children}</ul>
            ),
            ol: ({ children }) => (
              <ol className="list-decimal space-y-1 pl-5">{children}</ol>
            ),
            blockquote: ({ children }) => (
              <blockquote className="border-l-2 border-aqua/50 pl-4 text-white/65">
                {children}
              </blockquote>
            ),
            code: ({ children }) => (
              <code className="rounded bg-white/[0.08] px-1.5 py-0.5 font-mono text-[12px] text-aqua/95">
                {children}
              </code>
            ),
            pre: ({ children }) => (
              <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/35 p-4 text-xs text-white/85">
                {children}
              </pre>
            ),
          }}
        >
          {article.body_md || "_Empty article._"}
        </ReactMarkdown>
      </article>
    </Card>
  );
}


function statusTone(
  status: string,
): "ok" | "warn" | "info" | "neutral" {
  if (status === "published") return "ok";
  if (status === "draft") return "warn";
  if (status === "archived") return "neutral";
  if (status === "superseded") return "info";
  return "neutral";
}


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
  if (kind === "external_static_upload") {
    return typeof p.filename === "string"
      ? `uploaded: ${p.filename}`
      : "uploaded file";
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
