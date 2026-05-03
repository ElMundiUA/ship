"use client";

/**
 * Knowledge — single-page control surface.
 *
 * Layout, top to bottom:
 *
 *   [Title · Workspace]                       [Export ZIP] [Import source]
 *   [           Big search input                                          ]
 *   [All]  [Architecture decisions]  [Engineering]  ...  (6 buckets max)
 *   [Optional: import panel collapsed below header]
 *   [Articles list — flat, click-through to bucket detail]
 *
 * No metric tiles, no per-bucket cards, no tabs. Buckets are fixed at six
 * after the consolidation, so they fit comfortably as filter chips and
 * the articles surface gets the page real estate.
 */

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";

import { Card, CardHeader } from "@/components/ui";
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
  const [filterSlug, setFilterSlug] = useState<string | null>(null);
  const [searchHits, setSearchHits] = useState<ApiKnowledgeSearchHit[] | null>(null);
  const [searchedFor, setSearchedFor] = useState("");
  const [searchError, setSearchError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [pending, startTransition] = useTransition();

  const articlesByBucket = useMemo(() => {
    const map = new Map<string, number>();
    for (const a of articles) {
      map.set(a.bucketSlug, (map.get(a.bucketSlug) ?? 0) + 1);
    }
    return map;
  }, [articles]);

  const visibleArticles = useMemo(() => {
    if (!filterSlug) return articles;
    return articles.filter((a) => a.bucketSlug === filterSlug);
  }, [articles, filterSlug]);

  const visibleHits = useMemo(() => {
    if (!searchHits) return null;
    if (!filterSlug) return searchHits;
    return searchHits.filter((h) => h.bucket_slug === filterSlug);
  }, [searchHits, filterSlug]);

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
          body: JSON.stringify({
            workspaceId: workspace.id,
            query: q,
            limit: 30,
          }),
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
    <div className="space-y-5">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="font-display text-xl font-bold text-white">Knowledge</h1>
        <span className="text-xs text-white/45">{workspace.name}</span>
        <div className="ml-auto flex items-center gap-2">
          <ExportButton workspace={workspace} />
          <button
            type="button"
            onClick={() => setImportOpen((v) => !v)}
            className={cn(
              "rounded-full border px-3 py-1.5 text-xs font-bold transition",
              importOpen
                ? "border-aqua/60 bg-aqua/15 text-aqua"
                : "border-aqua/40 bg-aqua/10 text-aqua hover:bg-aqua/20",
            )}
          >
            {importOpen ? "Close import" : "Import source"}
          </button>
        </div>
      </header>

      <form onSubmit={runSearch}>
        <div className="relative">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search workspace knowledge…"
            className="w-full rounded-2xl border border-white/15 bg-white/[0.04] px-5 py-3 text-base text-white placeholder:text-white/35 outline-none transition focus:border-aqua/60"
            aria-label="Search workspace knowledge"
          />
          {(query || isSearching) && (
            <button
              type="button"
              onClick={clearSearch}
              aria-label="Clear search"
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full border border-white/10 px-2 py-0.5 text-xs text-white/55 hover:border-white/30 hover:text-white"
            >
              clear
            </button>
          )}
        </div>
      </form>

      <div className="flex flex-wrap gap-2">
        <BucketChip
          active={filterSlug === null}
          label="All"
          count={isSearching ? (searchHits?.length ?? 0) : articles.length}
          onClick={() => setFilterSlug(null)}
        />
        {liveBuckets.map((bucket) => (
          <BucketChip
            key={bucket.slug}
            active={filterSlug === bucket.slug}
            label={bucket.name}
            count={
              isSearching
                ? (searchHits?.filter((h) => h.bucket_slug === bucket.slug).length ?? 0)
                : (articlesByBucket.get(bucket.slug) ?? 0)
            }
            onClick={() =>
              setFilterSlug(filterSlug === bucket.slug ? null : bucket.slug)
            }
          />
        ))}
      </div>

      {importOpen && (
        <ImportPanel
          repos={repos}
          integrations={integrations}
          sources={sources}
        />
      )}

      <section>
        {pending && (
          <p className="mb-2 text-xs text-white/45">Searching…</p>
        )}
        {searchError && (
          <Card className="mb-3 border-coral/40 bg-coral/5">
            <p className="text-sm text-coral">{searchError}</p>
          </Card>
        )}
        {isSearching ? (
          <SearchResults
            hits={visibleHits ?? []}
            queriedFor={searchedFor}
            filtered={filterSlug !== null}
          />
        ) : (
          <ArticlesList
            articles={visibleArticles}
            filterSlug={filterSlug}
            totalCount={articles.length}
          />
        )}
      </section>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Bucket filter chips
// ---------------------------------------------------------------------------


function BucketChip({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition",
        active
          ? "border-aqua/60 bg-aqua/15 text-aqua"
          : "border-white/15 bg-white/[0.04] text-white/70 hover:border-white/30 hover:text-white",
      )}
    >
      <span>{label}</span>
      <span
        className={cn(
          "rounded-full px-1.5 text-[10px] font-bold",
          active ? "bg-aqua/30 text-aqua" : "bg-white/10 text-white/55",
        )}
      >
        {count}
      </span>
    </button>
  );
}


// ---------------------------------------------------------------------------
// Article listing
// ---------------------------------------------------------------------------


function ArticlesList({
  articles,
  filterSlug,
  totalCount,
}: {
  articles: KnowledgeArticleRow[];
  filterSlug: string | null;
  totalCount: number;
}) {
  if (articles.length === 0) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.02] px-5 py-10 text-center">
        <p className="text-sm text-white/55">
          {totalCount === 0
            ? "No articles in this workspace yet. Import a source or wait for the harvester to surface drafts."
            : filterSlug
              ? "No articles in this bucket yet."
              : "No articles match the current filter."}
        </p>
      </div>
    );
  }

  return (
    <ul className="divide-y divide-white/5 rounded-2xl border border-white/10 bg-white/[0.02]">
      {articles.map((article) => (
        <li key={article.id} className="px-5 py-3">
          <Link
            href={`/knowledge/${encodeURIComponent(article.bucketSlug)}?article=${encodeURIComponent(article.id)}#article-viewer`}
            className="group flex flex-col gap-1"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-white group-hover:text-aqua">
                {article.title || article.slug}
              </span>
              <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-semibold text-white/65">
                {article.bucketName}
              </span>
              <span className="ml-auto text-[11px] text-white/45">
                {relativeDate(article.updatedAt)}
              </span>
            </div>
            {article.snippet && (
              <p className="line-clamp-2 text-xs text-white/55">
                {article.snippet}
              </p>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}


// ---------------------------------------------------------------------------
// Search results
// ---------------------------------------------------------------------------


function SearchResults({
  hits,
  queriedFor,
  filtered,
}: {
  hits: ApiKnowledgeSearchHit[];
  queriedFor: string;
  filtered: boolean;
}) {
  if (hits.length === 0) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.02] px-5 py-10 text-center">
        <p className="text-sm text-white/55">
          {queriedFor
            ? filtered
              ? `No matches for "${queriedFor}" in this bucket.`
              : `Nothing matched "${queriedFor}".`
            : "No matches."}
        </p>
      </div>
    );
  }

  return (
    <ul className="divide-y divide-white/5 rounded-2xl border border-white/10 bg-white/[0.02]">
      {hits.map((hit) => {
        const title =
          hit.title?.trim() ||
          hit.bucket_slug ||
          (hit.source === "kb_chunk" ? "Repo chunk" : "Article");
        const href =
          hit.source === "bucket_article" && hit.bucket_slug
            ? `/knowledge/${encodeURIComponent(hit.bucket_slug)}?article=${encodeURIComponent(hit.id)}#article-viewer`
            : null;
        const RowEl = href ? Link : "div";
        return (
          <li key={`${hit.source}:${hit.id}`} className="px-5 py-3">
            <RowEl
              href={href ?? ""}
              className="group flex flex-col gap-1"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-white group-hover:text-aqua">
                  {title}
                </span>
                {hit.bucket_slug && (
                  <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-semibold text-white/65">
                    {hit.bucket_slug}
                  </span>
                )}
                <span
                  className="ml-auto font-mono text-[11px] text-aqua/80"
                  title="match score"
                >
                  {hit.score.toFixed(3)}
                </span>
              </div>
              {hit.snippet && (
                <p className="line-clamp-2 text-xs text-white/55">
                  {hit.snippet}
                </p>
              )}
            </RowEl>
          </li>
        );
      })}
    </ul>
  );
}


// ---------------------------------------------------------------------------
// Import panel — collapsed unless toggled
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
    <div className="space-y-3">
      <KnowledgeImportWizard
        integrations={integrations}
        repos={repos}
        defaultScope="workspace"
      />
      <SourcesTable sources={sources} />
    </div>
  );
}


function SourcesTable({ sources }: { sources: KnowledgeSourceRow[] }) {
  if (sources.length === 0) {
    return null;
  }
  return (
    <Card padded={false}>
      <CardHeader
        className="px-5 pt-5"
        title="Connected sources"
        subtitle="The harvester pulls from these and routes content into buckets."
      />
      <ul className="divide-y divide-white/5">
        {sources.map((source) => (
          <li key={source.id} className="grid grid-cols-[1fr_auto] items-center gap-3 px-5 py-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-semibold text-white">
                  {source.name}
                </span>
                <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-semibold text-white/65">
                  {source.kind}
                </span>
              </div>
              {source.lastError && (
                <p className="mt-0.5 line-clamp-1 text-[11px] text-coral">
                  {source.lastError}
                </p>
              )}
            </div>
            <div className="flex flex-col items-end text-[11px] text-white/55">
              <span className={statusColor(source.status)}>{source.status}</span>
              <span>{source.lastSyncedAt ? relativeDate(source.lastSyncedAt) : "never synced"}</span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}


function statusColor(status: string): string {
  if (status === "error") return "text-coral";
  if (status === "syncing") return "text-aqua";
  if (status === "ready") return "text-emerald-400";
  return "text-white/55";
}


// ---------------------------------------------------------------------------
// Compact export button (kept from the previous version, trimmed)
// ---------------------------------------------------------------------------


function ExportButton({
  workspace,
}: {
  workspace: Props["workspace"];
}) {
  const href = workspace.id
    ? `/api/knowledge/export?workspaceId=${encodeURIComponent(workspace.id)}`
    : undefined;

  return (
    <a
      href={href}
      aria-disabled={!href}
      className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:border-white/30 hover:bg-white/[0.08] aria-disabled:pointer-events-none aria-disabled:opacity-50"
    >
      Export ZIP
    </a>
  );
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
