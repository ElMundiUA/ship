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

import { archiveImportSourceAction } from "./actions";
import { KnowledgeImportWizard } from "./import-wizard";


export type KnowledgeTopicViewRow = {
  topicTag: string;
  title: string;
  claimCount: number;
  renderedByModel: string | null;
  lastRenderedAt: string;
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
  topicViews: KnowledgeTopicViewRow[];
  sources: KnowledgeSourceRow[];
  repos: ApiActivatedRepo[];
  integrations: ApiIntegration[];
};


export function KnowledgeControlCenter({
  workspace,
  topicViews,
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

  const isSearching = searchHits !== null;

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
          workspaceId={workspace.id}
        />
      )}

      {isSearching ? (
        <SearchResults hits={searchHits ?? []} queriedFor={searchedFor} />
      ) : (
        <div className="grid grid-cols-1 gap-x-12 gap-y-12 lg:grid-cols-12 2xl:gap-x-16">
          <section className="space-y-8 lg:col-span-8 2xl:col-span-9">
            <SectionKicker tone="aqua">
              Topics
              {topicViews.length > 0 && (
                <span className="ml-2 text-white/40">
                  · {topicViews.length}
                </span>
              )}
            </SectionKicker>
            {topicViews.length === 0 ? (
              <EmptyTopics hasSources={sources.length > 0} />
            ) : (
              <ul className="divide-y divide-white/5">
                {topicViews.map((view) => (
                  <li key={view.topicTag} className="py-5 first:pt-6">
                    <TopicViewRow view={view} />
                  </li>
                ))}
              </ul>
            )}
          </section>

          {sources.length > 0 && (
            <aside className="space-y-3 lg:col-span-4 lg:sticky lg:top-24 lg:self-start 2xl:col-span-3">
              <SectionKicker tone="muted">Sources</SectionKicker>
              <ConnectedSources sources={sources} />
            </aside>
          )}
        </div>
      )}
    </div>
  );
}


function TopicViewRow({ view }: { view: KnowledgeTopicViewRow }) {
  const isAuto = view.renderedByModel && view.renderedByModel !== "deterministic";
  return (
    <Link
      href={`/knowledge/topics/${encodeURIComponent(view.topicTag)}`}
      className="group block"
    >
      <h3 className="text-base font-medium text-white transition-colors group-hover:text-aqua">
        {view.title}
      </h3>
      <p className="mt-1 text-xs text-white/45">
        <span className="font-mono">{view.topicTag}</span>
        <span className="mx-2">·</span>
        <span>
          {view.claimCount} claim{view.claimCount === 1 ? "" : "s"}
        </span>
        <span className="mx-2">·</span>
        <span>{relativeDate(view.lastRenderedAt)}</span>
        {!isAuto && (
          <>
            <span className="mx-2">·</span>
            <span className="text-coral/70">deterministic fallback</span>
          </>
        )}
      </p>
    </Link>
  );
}


function EmptyTopics({ hasSources }: { hasSources: boolean }) {
  return (
    <div className="space-y-3 text-sm text-white/55">
      <p>
        No topics rendered yet. The claim-graph pipeline groups extracted
        atomic facts by ``topic_tag`` once at least three claims land per
        topic.
      </p>
      {!hasSources ? (
        <p>
          Connect an import source (top right) so the extractor has
          something to read.
        </p>
      ) : (
        <p>
          Sources are connected — wait for the next ``*/20`` extractor tick
          and the ``15,45`` topic-render tick. If nothing appears within
          an hour, the pipeline is broken upstream and you should ping
          ops.
        </p>
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
  workspaceId,
}: {
  repos: ApiActivatedRepo[];
  integrations: ApiIntegration[];
  sources: KnowledgeSourceRow[];
  workspaceId: string | undefined;
}) {
  return (
    <section className="space-y-6 border-l border-aqua/30 pl-6">
      <KnowledgeImportWizard
        integrations={integrations}
        repos={repos}
        defaultScope="workspace"
        workspaceId={workspaceId}
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
        <ConnectedSourceRow key={source.id} source={source} />
      ))}
    </ul>
  );
}


function ConnectedSourceRow({ source }: { source: KnowledgeSourceRow }) {
  const [archived, setArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  if (archived) return null;

  function archive() {
    if (!confirm(`Archive “${source.name}”? It will stop syncing and disappear from this list.`)) {
      return;
    }
    setError(null);
    startTransition(async () => {
      const result = await archiveImportSourceAction(source.id);
      if (result.ok) {
        setArchived(true);
      } else {
        setError(result.message);
      }
    });
  }

  return (
    <li className="grid grid-cols-[1fr_auto_auto] items-center gap-3 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-white">
            {source.name}
          </span>
          <span className="text-[11px] uppercase tracking-widest text-white/40">
            {source.kind}
          </span>
        </div>
        {(source.lastError || error) && (
          <p className="mt-0.5 line-clamp-1 text-[11px] text-coral">
            {error ?? source.lastError}
          </p>
        )}
      </div>
      <div className="flex flex-col items-end text-[11px] text-white/45">
        <span className={statusColor(source.status)}>{source.status}</span>
        <span>
          {source.lastSyncedAt ? relativeDate(source.lastSyncedAt) : "never"}
        </span>
      </div>
      <button
        type="button"
        disabled={pending}
        onClick={archive}
        className="rounded-full border border-white/10 px-2.5 py-1 text-[11px] text-white/55 transition hover:border-coral/40 hover:text-coral disabled:cursor-not-allowed disabled:opacity-50"
        data-testid={`source-archive-${source.id}`}
      >
        {pending ? "Archiving…" : "Archive"}
      </button>
    </li>
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
