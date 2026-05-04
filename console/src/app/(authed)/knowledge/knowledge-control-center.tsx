"use client";

/**
 * Knowledge index — editorial multi-column.
 *
 * Layout (≥ lg):
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  Search · Import source · Export ZIP                      │
 *   ├──────────────────────────────────┬───────────────────────┤
 *   │  Recent (col-span-8)             │  Browse by area (4)    │
 *   │    Lede article                  │    Sticky bucket nav   │
 *   │    Recent rows (divide-y)        │                        │
 *   ├──────────────────────────────────┴───────────────────────┤
 *   │  Sources (collapsed)                                      │
 *   └──────────────────────────────────────────────────────────┘
 *
 * Below ``lg``: collapses to a single column with the bucket nav
 * lifted above Recent (so the IA is visible without scrolling).
 *
 * Style cues: Linear changelog rhythm + Stripe Press editorial ledes.
 * No bordered cards; sections separated by whitespace + a single
 * hairline rule. ``aqua`` (champagne gold) is reserved for editorial
 * accents (lede hairline, link hover); ``lilac`` for bucket nav;
 * ``coral`` for errors only.
 */

import Link from "next/link";
import { useState, useTransition } from "react";

import { cn } from "@/lib/cn";
import type {
  ApiActivatedRepo,
  ApiKnowledgeSearchHit,
  ApiKnowledgeSearchResponse,
} from "@/lib/api/client";
import type { ApiIntegration } from "@/lib/api/types";

import { KnowledgeImportWizard } from "./import-wizard";


export type KnowledgeBucketRow = {
  id: string;
  slug: string;
  name: string;
  description: string;
  archived: boolean;
  articleCount: number;
};

export type KnowledgeArticleRow = {
  id: string;
  bucketSlug: string;
  bucketName: string;
  slug: string;
  title: string;
  snippet: string;
  updatedAt: string;
};

export type KnowledgeSourceRow = {
  id: string;
  name: string;
  kind: string;
  status: string;
  lastSyncedAt: string | null;
  lastError: string | null;
};

type Props = {
  workspace: { id?: string; slug: string; name: string };
  buckets: KnowledgeBucketRow[];
  articles: KnowledgeArticleRow[];
  sources: KnowledgeSourceRow[];
  repos: ApiActivatedRepo[];
  integrations: ApiIntegration[];
};


export function KnowledgeControlCenter({
  workspace,
  buckets,
  articles,
  sources,
  repos,
  integrations,
}: Props) {
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<ApiKnowledgeSearchHit[] | null>(null);
  const [searchedFor, setSearchedFor] = useState("");
  const [searchError, setSearchError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [pending, startTransition] = useTransition();

  function runSearch(event?: React.FormEvent) {
    event?.preventDefault();
    const q = query.trim();
    if (!q) {
      setSearchHits(null);
      setSearchedFor("");
      setSearchError(null);
      return;
    }
    setSearchError(null);
    startTransition(async () => {
      try {
        const res = await fetch("/api/knowledge/search", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ workspaceId: workspace.id, query: q, limit: 30 }),
        });
        const body = (await res.json()) as
          | ApiKnowledgeSearchResponse
          | { error?: string; code?: string };
        if (!res.ok || !("hits" in body)) {
          const msg =
            ("error" in body && body.error) ||
            `Search failed (HTTP ${res.status}).`;
          setSearchHits([]);
          setSearchedFor(q);
          setSearchError(msg);
          return;
        }
        setSearchHits(body.hits);
        setSearchedFor(body.query || q);
      } catch (err) {
        setSearchHits([]);
        setSearchedFor(q);
        setSearchError(err instanceof Error ? err.message : "Search failed.");
      }
    });
  }

  function clearSearch() {
    setQuery("");
    setSearchHits(null);
    setSearchedFor("");
    setSearchError(null);
  }

  const liveBuckets = buckets.filter((b) => !b.archived);
  const isSearching = searchHits !== null;
  const lede = articles[0] ?? null;
  const rest = articles.slice(1);

  return (
    <div className="mx-auto max-w-6xl space-y-12 2xl:max-w-screen-2xl">
      <SearchHeader
        query={query}
        onChange={setQuery}
        onSubmit={runSearch}
        onClear={clearSearch}
        isSearching={isSearching}
        importOpen={importOpen}
        onToggleImport={() => setImportOpen((v) => !v)}
        workspaceId={workspace.id}
      />

      {pending && <p className="text-xs text-white/45">Searching…</p>}
      {searchError && <p className="text-sm text-coral">{searchError}</p>}

      {importOpen && (
        <ImportPanel
          repos={repos}
          integrations={integrations}
          sources={sources}
        />
      )}

      {isSearching ? (
        <SearchResults hits={searchHits ?? []} queriedFor={searchedFor} />
      ) : (
        <div className="grid grid-cols-1 gap-x-12 gap-y-12 lg:grid-cols-12 2xl:gap-x-16">
          <section className="space-y-8 lg:col-span-8 2xl:col-span-7">
            <SectionKicker tone="aqua">Recent</SectionKicker>
            {lede ? (
              <>
                <LedeArticle article={lede} />
                {rest.length > 0 && (
                  <ul className="divide-y divide-white/5">
                    {rest.map((article) => (
                      <li key={article.id} className="py-5 first:pt-6">
                        <ArticleRow article={article} showBucket />
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="text-sm text-white/55">
                No articles in this workspace yet. Connect an import source or
                wait for the harvester to surface drafts.
              </p>
            )}
          </section>

          <aside className="space-y-6 lg:col-span-4 lg:sticky lg:top-24 lg:self-start 2xl:col-span-3">
            <SectionKicker tone="lilac">Browse by area</SectionKicker>
            <BucketDirectory buckets={liveBuckets} />
          </aside>

          {sources.length > 0 && (
            <section className="space-y-3 lg:col-span-12 2xl:col-span-2 2xl:sticky 2xl:top-24 2xl:self-start">
              <SectionKicker tone="muted">Sources</SectionKicker>
              <ConnectedSources sources={sources} />
            </section>
          )}
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Header — search + actions
// ---------------------------------------------------------------------------


function SearchHeader({
  query,
  onChange,
  onSubmit,
  onClear,
  isSearching,
  importOpen,
  onToggleImport,
  workspaceId,
}: {
  query: string;
  onChange: (v: string) => void;
  onSubmit: (e?: React.FormEvent) => void;
  onClear: () => void;
  isSearching: boolean;
  importOpen: boolean;
  onToggleImport: () => void;
  workspaceId?: string;
}) {
  return (
    <div className="flex flex-col gap-3">
      <form onSubmit={onSubmit} className="relative">
        <input
          type="search"
          value={query}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Search workspace knowledge…"
          className="w-full border-b border-white/15 bg-transparent px-1 pb-3 pt-1 text-lg text-white placeholder:text-white/30 outline-none transition focus:border-aqua/60"
          aria-label="Search workspace knowledge"
        />
        {(query || isSearching) && (
          <button
            type="button"
            onClick={onClear}
            aria-label="Clear search"
            className="absolute right-1 top-2 text-xs text-white/45 hover:text-white"
          >
            clear
          </button>
        )}
      </form>

      <div className="flex items-center gap-3 text-xs">
        <button
          type="button"
          onClick={onToggleImport}
          className={cn(
            "transition",
            importOpen ? "text-aqua" : "text-white/55 hover:text-white",
          )}
        >
          {importOpen ? "Close import" : "Import source"}
        </button>
        <span className="text-white/15">·</span>
        {workspaceId && (
          <a
            href={`/api/knowledge/export?workspaceId=${encodeURIComponent(workspaceId)}`}
            className="text-white/55 transition hover:text-white"
          >
            Export ZIP
          </a>
        )}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Recent — lede + rows
// ---------------------------------------------------------------------------


function LedeArticle({ article }: { article: KnowledgeArticleRow }) {
  return (
    <Link
      href={`/knowledge/${encodeURIComponent(article.bucketSlug)}?article=${encodeURIComponent(article.id)}`}
      className="group block space-y-3 border-b border-aqua/30 pb-8"
    >
      <h3 className="font-display text-2xl font-bold leading-tight text-white group-hover:text-aqua md:text-3xl">
        {article.title}
      </h3>
      {article.snippet && (
        <p className="line-clamp-3 text-base leading-relaxed text-white/65">
          {article.snippet}
        </p>
      )}
      <p className="flex items-center gap-2 text-[11px] uppercase tracking-widest text-white/40">
        <span>{article.bucketName}</span>
        <span className="text-white/20">·</span>
        <span>{relativeDate(article.updatedAt)}</span>
      </p>
    </Link>
  );
}


function ArticleRow({
  article,
  showBucket,
}: {
  article: KnowledgeArticleRow;
  showBucket?: boolean;
}) {
  return (
    <Link
      href={`/knowledge/${encodeURIComponent(article.bucketSlug)}?article=${encodeURIComponent(article.id)}`}
      className="group block space-y-1.5"
    >
      <div className="text-base font-semibold text-white group-hover:text-aqua">
        {article.title}
      </div>
      {article.snippet && (
        <p className="line-clamp-2 text-sm leading-relaxed text-white/55">
          {article.snippet}
        </p>
      )}
      <div className="text-[11px] text-white/40">
        {showBucket && (
          <>
            <span>{article.bucketName}</span>
            <span className="mx-2 text-white/20">·</span>
          </>
        )}
        <span>{relativeDate(article.updatedAt)}</span>
      </div>
    </Link>
  );
}


// ---------------------------------------------------------------------------
// Browse by area — bucket directory
// ---------------------------------------------------------------------------


function BucketDirectory({ buckets }: { buckets: KnowledgeBucketRow[] }) {
  if (buckets.length === 0) return null;
  return (
    <ul className="divide-y divide-white/5">
      {buckets.map((bucket) => (
        <li key={bucket.slug}>
          <Link
            href={`/knowledge/${encodeURIComponent(bucket.slug)}`}
            className="group flex items-baseline justify-between gap-3 py-3"
          >
            <div className="min-w-0 flex-1">
              <div className="font-display text-sm font-bold uppercase tracking-wider text-white/85 group-hover:text-lilac">
                {bucket.name}
              </div>
              {bucket.description && (
                <p className="mt-1 line-clamp-1 text-xs text-white/45">
                  {bucket.description}
                </p>
              )}
            </div>
            <span className="shrink-0 font-mono text-xs text-white/35">
              {bucket.articleCount}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}


// ---------------------------------------------------------------------------
// Search results — same shape as Recent rows, single column inside the grid
// ---------------------------------------------------------------------------


function SearchResults({
  hits,
  queriedFor,
}: {
  hits: ApiKnowledgeSearchHit[];
  queriedFor: string;
}) {
  return (
    <section className="space-y-6">
      <SectionKicker tone="aqua">
        Results for “{queriedFor}”
      </SectionKicker>
      {hits.length === 0 ? (
        <p className="text-sm text-white/55">
          {queriedFor ? `Nothing matched “${queriedFor}”.` : "No matches."}
        </p>
      ) : (
        <ul className="divide-y divide-white/5">
          {hits.map((hit) => (
            <li key={`${hit.source}:${hit.id}`} className="py-5 first:pt-0">
              <SearchHitRow hit={hit} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}


function SearchHitRow({ hit }: { hit: ApiKnowledgeSearchHit }) {
  const title =
    hit.title?.trim() ||
    hit.bucket_slug ||
    (hit.source === "kb_chunk" ? "Repo chunk" : "Article");
  const href =
    hit.source === "bucket_article" && hit.bucket_slug
      ? `/knowledge/${encodeURIComponent(hit.bucket_slug)}?article=${encodeURIComponent(hit.id)}`
      : null;
  const content = (
    <>
      <div className="text-base font-semibold text-white group-hover:text-aqua">
        {title}
      </div>
      {hit.snippet && (
        <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-white/55">
          {hit.snippet}
        </p>
      )}
      <div className="mt-1 flex items-center gap-2 text-[11px] text-white/40">
        {hit.bucket_slug && (
          <>
            <span>{hit.bucket_slug}</span>
            <span className="text-white/20">·</span>
          </>
        )}
        <span className="font-mono">{hit.score.toFixed(3)}</span>
      </div>
    </>
  );
  return href ? (
    <Link href={href} className="group block">
      {content}
    </Link>
  ) : (
    <div className="group block">{content}</div>
  );
}


// ---------------------------------------------------------------------------
// Sources — wizard + connected list, full-width footer
// ---------------------------------------------------------------------------


function ImportPanel({
  repos,
  integrations,
  sources,
}: {
  repos: ApiActivatedRepo[];
  integrations: ApiIntegration[];
  sources: KnowledgeSourceRow[];
}) {
  return (
    <section className="space-y-6 border-l border-aqua/30 pl-6">
      <KnowledgeImportWizard
        integrations={integrations}
        repos={repos}
        defaultScope="workspace"
      />
      {sources.length > 0 && <ConnectedSources sources={sources} />}
    </section>
  );
}


function ConnectedSources({ sources }: { sources: KnowledgeSourceRow[] }) {
  if (sources.length === 0) return null;
  return (
    <ul className="divide-y divide-white/5">
      {sources.map((source) => (
        <li
          key={source.id}
          className="grid grid-cols-[1fr_auto] items-center gap-3 py-3"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-white">
                {source.name}
              </span>
              <span className="text-[11px] uppercase tracking-widest text-white/40">
                {source.kind}
              </span>
            </div>
            {source.lastError && (
              <p className="mt-0.5 line-clamp-1 text-[11px] text-coral">
                {source.lastError}
              </p>
            )}
          </div>
          <div className="flex flex-col items-end text-[11px] text-white/45">
            <span className={statusColor(source.status)}>{source.status}</span>
            <span>
              {source.lastSyncedAt ? relativeDate(source.lastSyncedAt) : "never"}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}


function statusColor(status: string): string {
  if (status === "error") return "text-coral";
  if (status === "syncing") return "text-aqua";
  if (status === "ready") return "text-emerald-400";
  return "text-white/55";
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function SectionKicker({
  children,
  tone,
}: {
  children: React.ReactNode;
  tone: "aqua" | "lilac" | "muted";
}) {
  const toneClass =
    tone === "aqua"
      ? "text-aqua/75"
      : tone === "lilac"
        ? "text-lilac/75"
        : "text-white/40";
  return (
    <h2
      className={cn(
        "text-[11px] font-bold uppercase tracking-[0.22em]",
        toneClass,
      )}
    >
      {children}
    </h2>
  );
}


function relativeDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = Date.parse(value);
  if (Number.isNaN(date)) return "—";
  const diff = Date.now() - date;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < hour) return `${Math.max(1, Math.round(diff / minute))}m ago`;
  if (diff < day) return `${Math.round(diff / hour)}h ago`;
  if (diff < 30 * day) return `${Math.round(diff / day)}d ago`;
  return new Date(date).toLocaleDateString();
}
