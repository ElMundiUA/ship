"use client";

/**
 * Knowledge index — Lighthouse-only surface.
 *
 * The legacy editorial layout (Recent topic-views / Browse by area /
 * Sources sidebar, all backed by the retired internal claim-graph
 * pipeline) has been removed. What remains:
 *
 *   - a search bar that hits the per-workspace Lighthouse engine
 *   - the Lighthouse corpus panel (what the engine actually holds)
 *   - search results when a query is active
 *
 * The internal-index data fetches in ``page.tsx`` were dropped at the
 * same time, so this component no longer takes topic-views / sources
 * / repos / integrations.
 */

import { useState, useTransition } from "react";

import type {
  ApiImporterType,
  ApiKnowledgeCorpus,
  ApiKnowledgeSearchHit,
  ApiKnowledgeSearchResponse,
  ApiWorkspaceImporter,
  ApiWorkspaceImporterIntegration,
} from "@/lib/api/client";

import { ImportersPanel } from "./importers-panel";


type Props = {
  workspace: { id?: string; slug: string; name: string };
  corpus: ApiKnowledgeCorpus | null;
  importers: ApiWorkspaceImporter[];
  importerTypes: ApiImporterType[];
  importerIntegrations: ApiWorkspaceImporterIntegration[];
};


export function KnowledgeControlCenter({
  workspace,
  corpus,
  importers,
  importerTypes,
  importerIntegrations,
}: Props) {
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<ApiKnowledgeSearchHit[] | null>(null);
  const [searchedFor, setSearchedFor] = useState("");
  const [searchError, setSearchError] = useState<string | null>(null);
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

  const isSearching = searchHits !== null;

  return (
    <div className="mx-auto max-w-6xl space-y-12 2xl:max-w-screen-2xl">
      <SearchHeader
        query={query}
        onChange={setQuery}
        onSubmit={runSearch}
        onClear={clearSearch}
        isSearching={isSearching}
      />

      <CorpusSummary corpus={corpus} />

      <ImportersPanel
        importers={importers}
        importerTypes={importerTypes}
        integrations={importerIntegrations}
      />

      {pending && <p className="text-xs text-white/45">Searching…</p>}
      {searchError && <p className="text-sm text-coral">{searchError}</p>}

      {isSearching && (
        <SearchResults hits={searchHits ?? []} queriedFor={searchedFor} />
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Lighthouse corpus summary — what the per-workspace engine holds
// ---------------------------------------------------------------------------


function CorpusSummary({ corpus }: { corpus: ApiKnowledgeCorpus | null }) {
  if (!corpus || !corpus.configured) return null;
  const top = corpus.sources.slice(0, 8);
  const ingested = corpus.last_ingest_at?.slice(0, 10) ?? null;
  const empty = corpus.total_chunks === 0;
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.02] p-4">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-xs uppercase tracking-wide text-aqua/80">
          Lighthouse corpus
        </p>
        <p className="text-xs text-white/45">
          {corpus.total_chunks} chunk{corpus.total_chunks === 1 ? "" : "s"} ·{" "}
          {corpus.total_sources} source{corpus.total_sources === 1 ? "" : "s"}
          {ingested ? ` · last ingest ${ingested}` : ""}
        </p>
      </div>
      {empty ? (
        <p className="mt-3 text-sm text-white/55">
          Engine connected — no documents have been ingested into this
          workspace yet. Once Ship emits its first knowledge document
          (repo intel, resolved clarification, inbox comment), the next
          importer run picks it up and it shows up here.
        </p>
      ) : (
        top.length > 0 && (
          <ul className="mt-3 divide-y divide-white/5 text-sm">
            {top.map((s) => (
              <li
                key={s.source}
                className="flex items-center justify-between gap-3 py-1.5"
              >
                <span className="truncate text-white/70">{s.source}</span>
                <span className="shrink-0 text-xs text-white/40">
                  {s.chunk_count}
                </span>
              </li>
            ))}
          </ul>
        )
      )}
    </section>
  );
}


// ---------------------------------------------------------------------------
// Header — search
// ---------------------------------------------------------------------------


function SearchHeader({
  query,
  onChange,
  onSubmit,
  onClear,
  isSearching,
}: {
  query: string;
  onChange: (v: string) => void;
  onSubmit: (e?: React.FormEvent) => void;
  onClear: () => void;
  isSearching: boolean;
}) {
  return (
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
  );
}


// ---------------------------------------------------------------------------
// Search results
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
      <h2 className="text-[11px] font-bold uppercase tracking-[0.22em] text-aqua/75">
        Results for “{queriedFor}”
      </h2>
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
  const title = hit.title?.trim() || "Result";
  return (
    <div className="space-y-1.5">
      <div className="text-base font-semibold text-white">{title}</div>
      {hit.snippet && (
        <p className="line-clamp-2 text-sm leading-relaxed text-white/55">
          {hit.snippet}
        </p>
      )}
      <div className="flex items-center gap-2 text-[11px] text-white/40">
        <span className="font-mono">{hit.score.toFixed(3)}</span>
      </div>
    </div>
  );
}
