"use client";

/**
 * Knowledge — single-page newspaper.
 *
 * Top of the page: a search bar + the export/import actions.
 * Below: two sections — "Recent" (top-10 articles by updated_at)
 * and "Browse by area" (the six buckets as text links).
 *
 * No bordered cards, no metric tiles, no chip-filter row, no fat
 * tables. Reading-column width capped so the layout feels like a
 * blog, not a control panel. Bucket → category page; article →
 * reader page.
 *
 * Real popularity-based ranking is a follow-up — we don't track
 * article views yet, so "Recent" is what we surface for now.
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

  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <div className="flex flex-col gap-3">
        <form onSubmit={runSearch} className="relative">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search workspace knowledge…"
            className="w-full border-b border-white/15 bg-transparent px-1 pb-2 pt-1 text-base text-white placeholder:text-white/30 outline-none transition focus:border-aqua/60"
            aria-label="Search workspace knowledge"
          />
          {(query || isSearching) && (
            <button
              type="button"
              onClick={clearSearch}
              aria-label="Clear search"
              className="absolute right-1 top-1 text-xs text-white/45 hover:text-white"
            >
              clear
            </button>
          )}
        </form>

        <div className="flex items-center gap-3 text-xs">
          <button
            type="button"
            onClick={() => setImportOpen((v) => !v)}
            className={cn(
              "transition",
              importOpen ? "text-aqua" : "text-white/55 hover:text-white",
            )}
          >
            {importOpen ? "Close import" : "Import source"}
          </button>
          <span className="text-white/15">·</span>
          {workspace.id && (
            <a
              href={`/api/knowledge/export?workspaceId=${encodeURIComponent(workspace.id)}`}
              className="text-white/55 transition hover:text-white"
            >
              Export ZIP
            </a>
          )}
        </div>
      </div>

      {pending && (
        <p className="text-xs text-white/45">Searching…</p>
      )}
      {searchError && (
        <p className="text-sm text-coral">{searchError}</p>
      )}

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
        <>
          <RecentSection articles={articles} />
          <BrowseByAreaSection buckets={liveBuckets} />
        </>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------


function RecentSection({ articles }: { articles: KnowledgeArticleRow[] }) {
  if (articles.length === 0) {
    return (
      <SectionHeader
        title="Recent"
        empty="No articles in this workspace yet. Import a source or wait for the harvester to surface drafts."
      />
    );
  }
  return (
    <section className="space-y-4">
      <SectionHeader title="Recent" />
      <ul className="space-y-5">
        {articles.map((article) => (
          <li key={article.id}>
            <ArticleRow article={article} showBucket />
          </li>
        ))}
      </ul>
    </section>
  );
}


function BrowseByAreaSection({ buckets }: { buckets: KnowledgeBucketRow[] }) {
  if (buckets.length === 0) return null;
  return (
    <section className="space-y-4">
      <SectionHeader title="Browse by area" />
      <ul className="divide-y divide-white/5">
        {buckets.map((bucket) => (
          <li key={bucket.slug}>
            <Link
              href={`/knowledge/${encodeURIComponent(bucket.slug)}`}
              className="group flex items-baseline justify-between gap-4 py-3"
            >
              <div className="min-w-0">
                <div className="text-base font-semibold text-white group-hover:text-aqua">
                  {bucket.name}
                </div>
                {bucket.description && (
                  <p className="mt-0.5 line-clamp-1 text-xs text-white/50">
                    {bucket.description}
                  </p>
                )}
              </div>
              <span className="shrink-0 font-mono text-xs text-white/45">
                {bucket.articleCount} article{bucket.articleCount === 1 ? "" : "s"}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}


function SearchResults({
  hits,
  queriedFor,
}: {
  hits: ApiKnowledgeSearchHit[];
  queriedFor: string;
}) {
  if (hits.length === 0) {
    return (
      <SectionHeader
        title={`Results for “${queriedFor}”`}
        empty={queriedFor ? `Nothing matched “${queriedFor}”.` : "No matches."}
      />
    );
  }
  return (
    <section className="space-y-4">
      <SectionHeader title={`Results for “${queriedFor}”`} />
      <ul className="space-y-5">
        {hits.map((hit) => (
          <li key={`${hit.source}:${hit.id}`}>
            <SearchHitRow hit={hit} />
          </li>
        ))}
      </ul>
    </section>
  );
}


// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------


function SectionHeader({
  title,
  empty,
}: {
  title: string;
  empty?: string;
}) {
  return (
    <div className="space-y-2">
      <h2 className="text-[11px] font-bold uppercase tracking-[0.2em] text-white/45">
        {title}
      </h2>
      {empty && <p className="text-sm text-white/55">{empty}</p>}
    </div>
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
      className="group block"
    >
      <div className="text-base font-semibold text-white group-hover:text-aqua">
        {article.title}
      </div>
      {article.snippet && (
        <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-white/60">
          {article.snippet}
        </p>
      )}
      <div className="mt-1 text-[11px] text-white/40">
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
        <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-white/60">
          {hit.snippet}
        </p>
      )}
      <div className="mt-1 text-[11px] text-white/40">
        {hit.bucket_slug && (
          <>
            <span>{hit.bucket_slug}</span>
            <span className="mx-2 text-white/20">·</span>
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
// Import panel — wizard + connected sources list, no Card wrappers
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
      {sources.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-white/45">
            Connected sources
          </h3>
          <ul className="divide-y divide-white/5">
            {sources.map((source) => (
              <li
                key={source.id}
                className="grid grid-cols-[1fr_auto] items-center gap-3 py-2"
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
                  <span className={statusColor(source.status)}>
                    {source.status}
                  </span>
                  <span>
                    {source.lastSyncedAt ? relativeDate(source.lastSyncedAt) : "never"}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
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
