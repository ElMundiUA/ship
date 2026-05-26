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
  ApiKnowledgeCorpus,
  ApiKnowledgeSearchHit,
  ApiKnowledgeSearchResponse,
} from "@/lib/api/client";
import type { ApiIntegration } from "@/lib/api/types";

import { archiveImportSourceAction } from "./actions";
import { KnowledgeImportWizard } from "./import-wizard";


export type KnowledgeTopicViewRow = {
  topicTag: string;
  title: string;
  snippet: string | null;
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
  corpus: ApiKnowledgeCorpus | null;
};


export function KnowledgeControlCenter({
  workspace,
  topicViews,
  sources,
  repos,
  integrations,
  corpus,
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

      <CorpusSummary corpus={corpus} />

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
      ) : topicViews.length === 0 ? (
        <EmptyTopics hasSources={sources.length > 0} />
      ) : (
        <EditorialLayout topicViews={topicViews} sources={sources} />
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Lighthouse corpus summary (K7) — what the per-workspace engine holds
// ---------------------------------------------------------------------------


function CorpusSummary({ corpus }: { corpus: ApiKnowledgeCorpus | null }) {
  // Render nothing only until Lighthouse is wired. Once configured, we
  // surface the panel even when empty so the operator can see the new
  // engine is live (and that the corpus just hasn't been populated yet).
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
// Editorial layout — Lede + Recent rows + Browse-by-area sidebar
// ---------------------------------------------------------------------------


function EditorialLayout({
  topicViews,
  sources,
}: {
  topicViews: KnowledgeTopicViewRow[];
  sources: KnowledgeSourceRow[];
}) {
  // Topic views arrive sorted by claim_count desc from the API. The
  // first row is the lede (deepest topic = most-attested area in the
  // workspace canon); the next ~12 are the recent stack. Anything
  // past that lives only in the sidebar so the main column stays
  // scannable on a laptop screen.
  const lede = topicViews[0];
  const rest = topicViews.slice(1, 13);
  const areas = clusterTopicsByPrefix(topicViews);

  return (
    <div className="grid grid-cols-1 gap-x-12 gap-y-12 lg:grid-cols-12 2xl:gap-x-16">
      <section className="space-y-8 lg:col-span-8 2xl:col-span-7">
        <SectionKicker tone="aqua">
          Recent
          <span className="ml-2 text-white/40">· {topicViews.length}</span>
        </SectionKicker>
        <LedeTopic view={lede} />
        {rest.length > 0 && (
          <ul className="divide-y divide-white/5">
            {rest.map((view) => (
              <li key={view.topicTag} className="py-5 first:pt-6">
                <TopicRow view={view} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <aside className="space-y-6 lg:col-span-4 lg:sticky lg:top-24 lg:self-start 2xl:col-span-3">
        <SectionKicker tone="lilac">Browse by area</SectionKicker>
        <AreaDirectory areas={areas} />
      </aside>

      {sources.length > 0 && (
        <section className="space-y-3 lg:col-span-12 2xl:col-span-2 2xl:sticky 2xl:top-24 2xl:self-start">
          <SectionKicker tone="muted">Sources</SectionKicker>
          <ConnectedSources sources={sources} />
        </section>
      )}
    </div>
  );
}


function LedeTopic({ view }: { view: KnowledgeTopicViewRow }) {
  const isAuto =
    view.renderedByModel && view.renderedByModel !== "deterministic";
  return (
    <Link
      href={`/knowledge/topics/${encodeURIComponent(view.topicTag)}`}
      className="group block space-y-3 border-b border-aqua/30 pb-8"
    >
      <h3 className="font-display text-2xl font-bold leading-tight text-white group-hover:text-aqua md:text-3xl">
        {view.title}
      </h3>
      {view.snippet && (
        <p className="line-clamp-3 text-base leading-relaxed text-white/65">
          {view.snippet}
        </p>
      )}
      <p className="flex flex-wrap items-center gap-x-2 text-[11px] uppercase tracking-widest text-white/40">
        <span>
          {view.claimCount} claim{view.claimCount === 1 ? "" : "s"}
        </span>
        <span className="text-white/20">·</span>
        <span>{relativeDate(view.lastRenderedAt)}</span>
        {!isAuto && (
          <>
            <span className="text-white/20">·</span>
            <span className="text-coral/70">deterministic fallback</span>
          </>
        )}
      </p>
    </Link>
  );
}


function TopicRow({ view }: { view: KnowledgeTopicViewRow }) {
  const isAuto =
    view.renderedByModel && view.renderedByModel !== "deterministic";
  return (
    <Link
      href={`/knowledge/topics/${encodeURIComponent(view.topicTag)}`}
      className="group block space-y-1.5"
    >
      <div className="text-base font-semibold text-white group-hover:text-aqua">
        {view.title}
      </div>
      {view.snippet && (
        <p className="line-clamp-2 text-sm leading-relaxed text-white/55">
          {view.snippet}
        </p>
      )}
      <div className="text-[11px] text-white/40">
        <span>
          {view.claimCount} claim{view.claimCount === 1 ? "" : "s"}
        </span>
        <span className="mx-2 text-white/20">·</span>
        <span>{relativeDate(view.lastRenderedAt)}</span>
        {!isAuto && (
          <>
            <span className="mx-2 text-white/20">·</span>
            <span className="text-coral/70">deterministic</span>
          </>
        )}
      </div>
    </Link>
  );
}


// ---------------------------------------------------------------------------
// Browse by area — auto-clustered topics
// ---------------------------------------------------------------------------


type Area = {
  key: string;
  label: string;
  topics: KnowledgeTopicViewRow[];
};


function clusterTopicsByPrefix(views: KnowledgeTopicViewRow[]): Area[] {
  // Heuristic: cluster on the first ``-``-separated token of the
  // topic_tag. ``inbox-disposition`` + ``inbox-routing`` group under
  // ``inbox``; standalone tags whose token has no other members go
  // into a synthetic ``Other`` bucket so the sidebar stays compact.
  const buckets = new Map<string, KnowledgeTopicViewRow[]>();
  for (const view of views) {
    const token = view.topicTag.split("-", 1)[0] || view.topicTag;
    const list = buckets.get(token) ?? [];
    list.push(view);
    buckets.set(token, list);
  }
  const areas: Area[] = [];
  const orphans: KnowledgeTopicViewRow[] = [];
  for (const [key, topics] of buckets) {
    if (topics.length === 1) {
      orphans.push(topics[0]);
      continue;
    }
    areas.push({
      key,
      label: humaniseTag(key),
      topics: topics.sort((a, b) => b.claimCount - a.claimCount),
    });
  }
  if (orphans.length > 0) {
    areas.push({
      key: "_other",
      label: "Other",
      topics: orphans.sort((a, b) => b.claimCount - a.claimCount),
    });
  }
  return areas.sort((a, b) => {
    // Bigger clusters first, "_other" pinned to the bottom.
    if (a.key === "_other") return 1;
    if (b.key === "_other") return -1;
    const aTotal = a.topics.reduce((sum, t) => sum + t.claimCount, 0);
    const bTotal = b.topics.reduce((sum, t) => sum + t.claimCount, 0);
    return bTotal - aTotal;
  });
}


function humaniseTag(tag: string): string {
  return tag
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
}


function AreaDirectory({ areas }: { areas: Area[] }) {
  if (areas.length === 0) return null;
  return (
    <ul className="divide-y divide-white/5">
      {areas.map((area) => (
        <li key={area.key} className="py-3 first:pt-0">
          <div className="flex items-baseline justify-between gap-3">
            <div className="font-display text-sm font-bold uppercase tracking-wider text-white/85">
              {area.label}
            </div>
            <span className="shrink-0 font-mono text-xs text-white/35">
              {area.topics.length}
            </span>
          </div>
          <ul className="mt-2 space-y-1.5">
            {area.topics.slice(0, 5).map((topic) => (
              <li key={topic.topicTag}>
                <Link
                  href={`/knowledge/topics/${encodeURIComponent(topic.topicTag)}`}
                  className="group flex items-baseline justify-between gap-3 text-xs text-white/55 hover:text-lilac"
                >
                  <span className="truncate">{topic.title}</span>
                  <span className="shrink-0 font-mono text-white/30">
                    {topic.claimCount}
                  </span>
                </Link>
              </li>
            ))}
            {area.topics.length > 5 && (
              <li className="text-[11px] text-white/30">
                + {area.topics.length - 5} more
              </li>
            )}
          </ul>
        </li>
      ))}
    </ul>
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
