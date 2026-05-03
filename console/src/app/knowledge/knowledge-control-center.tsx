"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import {
  Badge,
  ButtonGhost,
  Card,
  CardHeader,
  EmptyState,
} from "@/components/ui";
import { cn } from "@/lib/cn";
import type {
  ApiActivatedRepo,
  ApiKnowledgeSearchHit,
  ApiKnowledgeSearchResponse,
} from "@/lib/api/client";
import type { ApiBucketScope, ApiIntegration } from "@/lib/api/types";

import {
  archiveBucketAction,
  updateBucketMetadataAction,
  type UpdateBucketResult,
} from "./actions";
import { KnowledgeImportWizard } from "./import-wizard";
import { NewBucketDialog } from "./new-bucket-dialog";

export type KnowledgeBucketStatus =
  | "Ready"
  | "Indexing"
  | "Failed"
  | "Empty"
  | "Stale";

export type KnowledgeControlBucket = {
  id: string;
  slug: string;
  name: string;
  description: string;
  bucketType: string;
  scope: string;
  sourceKind: string;
  authority: string;
  accessLevel: string;
  freshnessPolicy: string;
  status: KnowledgeBucketStatus;
  articles: number;
  chunks: number;
  sourceCount: number;
  sourceNames: string[];
  lastIndexedAt: string | null;
  updatedAt: string | null;
};

export type KnowledgeControlSource = {
  id: string;
  bucketSlug: string | null;
  bucketName: string;
  kind: string;
  status: string;
  lastSyncedAt: string | null;
  nextSyncAt: string | null;
  lastError: string | null;
  documents: number | null;
  chunks: number | null;
  urlOrPath: string | null;
};

type Props = {
  mode: "live" | "mock";
  workspace: { id?: string; slug: string; name: string };
  reason?: string;
  buckets: KnowledgeControlBucket[];
  sources: KnowledgeControlSource[];
  repos: ApiActivatedRepo[];
  integrations: ApiIntegration[];
  defaultScope: ApiBucketScope;
};

type Tab = "buckets" | "search" | "sources" | "settings";

const STARTER_BUCKETS = [
  "Project Map",
  "Architecture Decisions",
  "Engineering Standards",
  "Runbooks & Operations",
  "Product Knowledge",
  "Source Intelligence",
  "Generated Assets",
  "Security & Access",
  "Integration Playbooks",
  "Data & Domain Glossary",
];

export function KnowledgeControlCenter({
  mode,
  workspace,
  reason,
  buckets,
  sources,
  repos,
  integrations,
  defaultScope,
}: Props) {
  const [tab, setTab] = useState<Tab>("buckets");
  const stats = useMemo(() => {
    const lastIndexedAt = maxDate(
      buckets.map((bucket) => bucket.lastIndexedAt ?? bucket.updatedAt),
    );
    return {
      buckets: buckets.length,
      articles: buckets.reduce((sum, bucket) => sum + bucket.articles, 0),
      chunks: buckets.reduce((sum, bucket) => sum + bucket.chunks, 0),
      sources: sources.length || buckets.reduce((sum, bucket) => sum + bucket.sourceCount, 0),
      lastIndexedAt,
    };
  }, [buckets, sources]);

  return (
    <div className="space-y-6">
      {mode === "mock" && reason && (
        <div className="rounded-xl border border-sun/30 bg-sun/5 px-3 py-2 text-xs text-sun/95">
          <span className="mr-2 rounded-full bg-sun/25 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest">
            mock
          </span>
          <span className="align-middle">
          {reason}
          </span>
        </div>
      )}

      <section className="rounded-3xl border border-aqua/20 bg-gradient-to-br from-aqua/10 via-white/[0.04] to-lilac/10 p-5 shadow-card">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <div className="text-[10px] font-bold uppercase tracking-[0.2em] text-aqua/80">
              Workspace memory control center
            </div>
            <h2 className="mt-2 font-display text-2xl font-bold text-white">
              Knowledge for {workspace.name}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-white/70">
              Buckets define where Ship stores project memory, which sources feed
              it, how agents route questions, and which articles become canonical.
              Search is one workflow inside the control center.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ExportButton workspace={workspace} />
            <button
              type="button"
              onClick={() => setTab("sources")}
              className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-bold text-aqua transition hover:bg-aqua/20"
            >
              Import source
            </button>
            {mode === "live" && (
              <NewBucketDialog
                integrations={integrations}
                defaultScope={defaultScope}
              />
            )}
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-5">
          <Metric label="Buckets" value={stats.buckets.toLocaleString()} />
          <Metric label="Articles" value={stats.articles.toLocaleString()} />
          <Metric label="Chunks" value={stats.chunks.toLocaleString()} />
          <Metric label="Sources" value={stats.sources.toLocaleString()} />
          <Metric
            label="Last indexed"
            value={stats.lastIndexedAt ? relativeDate(stats.lastIndexedAt) : "Never"}
          />
        </div>
      </section>

      <div
        role="tablist"
        aria-label="Knowledge areas"
        className="flex flex-wrap items-center gap-1 border-b border-white/10"
      >
        <TabButton active={tab === "buckets"} onClick={() => setTab("buckets")}>
          Buckets
        </TabButton>
        <TabButton active={tab === "search"} onClick={() => setTab("search")}>
          Search
        </TabButton>
        <TabButton active={tab === "sources"} onClick={() => setTab("sources")}>
          Sources
        </TabButton>
        <TabButton active={tab === "settings"} onClick={() => setTab("settings")}>
          Settings
        </TabButton>
      </div>

      {tab === "buckets" && <BucketsTab buckets={buckets} live={mode === "live"} />}
      {tab === "search" && (
        <SearchTab workspaceId={workspace.id} buckets={buckets} repos={repos} />
      )}
      {tab === "sources" && (
        <SourcesTab
          mode={mode}
          sources={sources}
          integrations={integrations}
          repos={repos}
          defaultScope={defaultScope}
        />
      )}
      {tab === "settings" && <SettingsTab buckets={buckets} />}
    </div>
  );
}

function BucketsTab({
  buckets,
  live,
}: {
  buckets: KnowledgeControlBucket[];
  live: boolean;
}) {
  if (buckets.length === 0) {
    return (
      <EmptyState
        title="No knowledge buckets yet"
        body="Knowledge buckets help Ship organize project memory for agents and humans."
        action={
          <div className="flex flex-wrap justify-center gap-2">
            <ButtonGhost>Create starter buckets</ButtonGhost>
            {live && <ButtonGhost>Create custom bucket</ButtonGhost>}
          </div>
        }
      />
    );
  }

  return (
    <section className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
      {buckets.map((bucket) => (
        <BucketCard key={bucket.id} bucket={bucket} live={live} />
      ))}
    </section>
  );
}

function BucketCard({
  bucket,
  live,
}: {
  bucket: KnowledgeControlBucket;
  live: boolean;
}) {
  const sourceText =
    bucket.sourceNames.length > 0
      ? bucket.sourceNames.slice(0, 2).join(", ")
      : prettySource(bucket.sourceKind);

  return (
    <Card className="relative flex min-h-[22rem] flex-col">
      <div className="flex items-start justify-between gap-3">
        <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-lilac/40 via-aqua/30 to-coral/30 font-display text-xl font-bold text-white">
          {bucketInitial(bucket.name)}
        </div>
        <div className="flex flex-wrap justify-end gap-1.5">
          <Badge tone={statusTone(bucket.status)} dot>
            {bucket.status}
          </Badge>
          <Badge tone={scopeTone(bucket.scope)}>{scopeLabel(bucket.scope)}</Badge>
        </div>
      </div>

      <div className="mt-4 min-w-0">
        <Link
          href={`/knowledge/${encodeURIComponent(bucket.slug)}`}
          className="font-display text-lg font-bold text-white hover:text-aqua"
        >
          {bucket.name}
        </Link>
        <p className="mt-1 line-clamp-3 text-sm leading-relaxed text-white/65">
          {bucket.description || "No description yet. Add purpose and source hints so agents know when to use this memory."}
        </p>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <Badge tone="neutral">{bucket.bucketType}</Badge>
        <Badge tone={authorityTone(bucket.authority)}>
          {bucket.authority}
        </Badge>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 text-[11px]">
        <MiniStat label="Articles" value={bucket.articles.toLocaleString()} />
        <MiniStat label="Chunks" value={bucket.chunks.toLocaleString()} />
        <MiniStat label="Sources" value={bucket.sourceCount.toLocaleString()} />
        <MiniStat
          label="Last indexed"
          value={bucket.lastIndexedAt ? relativeDate(bucket.lastIndexedAt) : "Never"}
        />
      </dl>

      <div className="mt-4 rounded-xl border border-white/10 bg-black/20 p-3 text-xs text-white/60">
        <div className="flex items-center justify-between gap-2">
          <span className="font-semibold text-white/80">Sources</span>
          <span className="font-mono text-aqua/80">{bucket.sourceCount}</span>
        </div>
        <p className="mt-1 truncate">{sourceText}</p>
      </div>

      <div className="mt-auto flex items-center justify-between gap-3 pt-4">
        <Link
          href={`/knowledge/${encodeURIComponent(bucket.slug)}`}
          className="text-sm font-semibold text-aqua hover:underline"
        >
          Open bucket
        </Link>
        {live && <BucketManageMenu bucket={bucket} />}
      </div>
    </Card>
  );
}

function BucketManageMenu({ bucket }: { bucket: KnowledgeControlBucket }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(bucket.name);
  const [description, setDescription] = useState(bucket.description);
  const [result, setResult] = useState<UpdateBucketResult | null>(null);
  const [pending, startTransition] = useTransition();

  function save() {
    setResult(null);
    startTransition(async () => {
      const response = await updateBucketMetadataAction({
        slug: bucket.slug,
        name,
        description,
      });
      setResult(response);
      if (response.ok) {
        setOpen(false);
        router.refresh();
      }
    });
  }

  function archive() {
    setResult(null);
    startTransition(async () => {
      const response = await archiveBucketAction(bucket.slug);
      setResult(response);
      if (response.ok) router.refresh();
    });
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-white/70 hover:border-white/30 hover:text-white"
      >
        Manage
      </button>
    );
  }

  return (
    <div className="absolute inset-x-4 bottom-4 z-10 rounded-2xl border border-white/12 bg-ink/95 p-4 shadow-card">
      <div className="grid gap-2">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white"
        />
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={3}
          className="rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white"
        />
      </div>
      {result && !result.ok && (
        <p className="mt-2 text-xs text-coral">{result.message}</p>
      )}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-white/70"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={archive}
          className="rounded-full border border-coral/40 bg-coral/10 px-3 py-1.5 text-xs font-semibold text-coral disabled:opacity-50"
        >
          Archive
        </button>
        <button
          type="button"
          disabled={pending || !name.trim()}
          onClick={save}
          className="rounded-full bg-aqua px-3 py-1.5 text-xs font-bold text-ink disabled:opacity-50"
        >
          Save
        </button>
      </div>
    </div>
  );
}

function SearchTab({
  workspaceId,
  buckets,
  repos,
}: {
  workspaceId?: string;
  buckets: KnowledgeControlBucket[];
  repos: ApiActivatedRepo[];
}) {
  const [query, setQuery] = useState("");
  const [bucketMode, setBucketMode] = useState("all");
  const [selectedBuckets, setSelectedBuckets] = useState<string[]>([]);
  const [repoId, setRepoId] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [authorityFilter, setAuthorityFilter] = useState("");
  const [resultType, setResultType] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [hits, setHits] = useState<ApiKnowledgeSearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const bucketBySlug = useMemo(
    () => new Map(buckets.map((bucket) => [bucket.slug, bucket])),
    [buckets],
  );
  const effectiveSelected =
    bucketMode === "all"
      ? []
      : bucketMode === "recommended"
        ? buckets
            .filter((bucket) =>
              ["Source of truth", "High-confidence reference"].includes(
                bucket.authority,
              ),
            )
            .slice(0, 4)
            .map((bucket) => bucket.slug)
        : selectedBuckets;
  const filteredHits = useMemo(() => {
    if (!hits) return null;
    return hits.filter((hit) => {
      const bucket = hit.bucket_slug ? bucketBySlug.get(hit.bucket_slug) : null;
      if (sourceFilter && bucket?.sourceKind !== sourceFilter) return false;
      if (authorityFilter && bucket?.authority !== authorityFilter) return false;
      if (resultType && hit.source !== resultType) return false;
      return true;
    });
  }, [authorityFilter, bucketBySlug, hits, resultType, sourceFilter]);

  function runSearch(event: React.FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!workspaceId) {
      setError("Search needs a live workspace.");
      return;
    }
    if (!q) return;
    setError(null);
    setHits(null);
    startTransition(async () => {
      try {
        const searches =
          effectiveSelected.length > 0 ? effectiveSelected : [null];
        const responses = await Promise.all(
          searches.map((bucketSlug) =>
            fetch("/api/knowledge/search", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                workspaceId,
                query: q,
                repoId: repoId || null,
                bucketSlug,
                limit: 20,
              }),
            }),
          ),
        );
        const payloads = await Promise.all(
          responses.map(async (response) => ({
            response,
            body: (await response.json()) as
              | ApiKnowledgeSearchResponse
              | { error?: string },
          })),
        );
        const failed = payloads.find((payload) => !payload.response.ok);
        if (failed) {
          setError(
            "error" in failed.body && failed.body.error
              ? failed.body.error
              : `Search failed (HTTP ${failed.response.status}).`,
          );
          setHits([]);
          return;
        }
        const merged = dedupeHits(
          payloads.flatMap((payload) => (payload.body as ApiKnowledgeSearchResponse).hits),
        );
        setHits(merged);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Search failed.");
        setHits([]);
      }
    });
  }

  return (
    <section className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="space-y-4">
        <Card>
          <CardHeader
            title="Search workspace knowledge"
            subtitle="Search all buckets, recommended authoritative buckets, or a manual selection."
          />
          <form onSubmit={runSearch} className="space-y-4">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="How do we deploy staging?"
              className="w-full rounded-xl border border-white/15 bg-white/[0.04] px-4 py-3 text-sm text-white outline-none transition focus:border-aqua/60"
            />

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="Search in">
                <select
                  value={bucketMode}
                  onChange={(event) => setBucketMode(event.target.value)}
                  className="w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white"
                >
                  <option value="all">All buckets</option>
                  <option value="recommended">Recommended buckets</option>
                  <option value="manual">Manual selection</option>
                </select>
              </Field>
              <Field label="Repo boost">
                <select
                  value={repoId}
                  onChange={(event) => setRepoId(event.target.value)}
                  className="w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white"
                >
                  <option value="">No repo boost</option>
                  {repos.map((repo) => (
                    <option key={repo.id} value={repo.id}>
                      {repo.full_name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            {bucketMode === "manual" && (
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {buckets.map((bucket) => (
                  <label
                    key={bucket.slug}
                    className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/75"
                  >
                    <input
                      type="checkbox"
                      checked={selectedBuckets.includes(bucket.slug)}
                      onChange={(event) => {
                        setSelectedBuckets((current) =>
                          event.target.checked
                            ? [...current, bucket.slug]
                            : current.filter((slug) => slug !== bucket.slug),
                        );
                      }}
                    />
                    <span>{bucket.name}</span>
                  </label>
                ))}
              </div>
            )}

            <button
              type="button"
              onClick={() => setAdvancedOpen((value) => !value)}
              className="text-xs font-semibold text-aqua hover:underline"
            >
              {advancedOpen ? "Hide advanced options" : "Show advanced options"}
            </button>

            {advancedOpen && (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <Field label="Source">
                  <select
                    value={sourceFilter}
                    onChange={(event) => setSourceFilter(event.target.value)}
                    className="w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white"
                  >
                    <option value="">Any source</option>
                    {unique(buckets.map((bucket) => bucket.sourceKind)).map((source) => (
                      <option key={source} value={source}>
                        {prettySource(source)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Authority">
                  <select
                    value={authorityFilter}
                    onChange={(event) => setAuthorityFilter(event.target.value)}
                    className="w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white"
                  >
                    <option value="">Any authority</option>
                    {unique(buckets.map((bucket) => bucket.authority)).map((authority) => (
                      <option key={authority} value={authority}>
                        {authority}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Result type">
                  <select
                    value={resultType}
                    onChange={(event) => setResultType(event.target.value)}
                    className="w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-white"
                  >
                    <option value="">Articles and chunks</option>
                    <option value="bucket_article">Articles</option>
                    <option value="kb_chunk">Chunks</option>
                  </select>
                </Field>
              </div>
            )}

            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={pending || !query.trim() || !workspaceId}
                className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2 text-xs font-bold text-ink shadow-glow disabled:cursor-not-allowed disabled:opacity-50"
              >
                {pending ? "Searching..." : "Search"}
              </button>
              {hits && (
                <ButtonGhost onClick={() => setHits(null)}>Clear</ButtonGhost>
              )}
            </div>
          </form>
        </Card>

        {error && (
          <Card className="border-coral/40 bg-coral/5">
            <p className="text-sm text-coral">{error}</p>
          </Card>
        )}

        {filteredHits && (
          <SearchResults hits={filteredHits} bucketBySlug={bucketBySlug} />
        )}
      </div>

      <Card>
        <CardHeader
          title="Routing preview"
          subtitle="How Navigator should decide where to search."
        />
        <div className="space-y-3 text-xs text-white/65">
          <RoutingHint
            bucket="Project Map"
            useFor="where files live, repository structure, service ownership"
            avoid="architectural rationale or deployment procedures"
          />
          <RoutingHint
            bucket="Runbooks & Operations"
            useFor="deploy, rollback, incident, recovery, rotation"
            avoid="product discovery or UX decisions"
          />
          <RoutingHint
            bucket="Architecture Decisions"
            useFor="why the system is designed this way"
            avoid="current operational checklists"
          />
        </div>
      </Card>
    </section>
  );
}

function SearchResults({
  hits,
  bucketBySlug,
}: {
  hits: ApiKnowledgeSearchHit[];
  bucketBySlug: Map<string, KnowledgeControlBucket>;
}) {
  if (hits.length === 0) {
    return (
      <EmptyState
        title="No matches"
        body="Try all buckets, remove filters, or import a more relevant source."
      />
    );
  }
  return (
    <div className="space-y-3">
      {hits.map((hit) => {
        const bucket = hit.bucket_slug ? bucketBySlug.get(hit.bucket_slug) : null;
        return (
          <Card key={`${hit.source}:${hit.id}`}>
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    href={
                      hit.bucket_slug
                        ? `/knowledge/${encodeURIComponent(hit.bucket_slug)}`
                        : "#"
                    }
                    className="font-semibold text-white hover:text-aqua"
                  >
                    {hit.title || bucket?.name || "Knowledge result"}
                  </Link>
                  <Badge tone="neutral">
                    {hit.source === "bucket_article" ? "article" : "chunk"}
                  </Badge>
                  {bucket && <Badge tone="workspace">{bucket.name}</Badge>}
                  {bucket && (
                    <Badge tone={authorityTone(bucket.authority)}>
                      {bucket.authority}
                    </Badge>
                  )}
                </div>
                <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-white/65">
                  {hit.snippet}
                </p>
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-white/45">
                  <span>{bucket ? scopeLabel(bucket.scope) : hit.scope_kind}</span>
                  {hit.repo_full_name && <span>{hit.repo_full_name}</span>}
                  <span>{bucket?.lastIndexedAt ? relativeDate(bucket.lastIndexedAt) : "freshness unknown"}</span>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <span className="font-mono text-xs text-aqua/85">
                  {hit.score.toFixed(3)}
                </span>
                <ButtonGhost>Promote</ButtonGhost>
              </div>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function SourcesTab({
  mode,
  sources,
  integrations,
  repos,
  defaultScope,
}: {
  mode: "live" | "mock";
  sources: KnowledgeControlSource[];
  integrations: ApiIntegration[];
  repos: ApiActivatedRepo[];
  defaultScope: ApiBucketScope;
}) {
  return (
    <div className="space-y-5">
      {mode === "live" && (
        <KnowledgeImportWizard
          integrations={integrations}
          repos={repos}
          defaultScope={defaultScope}
        />
      )}

      <Card padded={false}>
        <CardHeader
          className="px-5 pt-5"
          title="Sources / Ingestion"
          subtitle="Workspace sources are synced once, fingerprinted, analyzed, and routed into the right buckets."
        />
        {sources.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-white/60">
            No source rows recorded yet. Create an upload bucket or import a
            Notion/Confluence root to start ingestion.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
                <tr>
                  <th className="px-4 py-2 text-left font-semibold">Source</th>
                  <th className="px-4 py-2 text-left font-semibold">Bucket</th>
                  <th className="px-4 py-2 text-left font-semibold">Status</th>
                  <th className="px-4 py-2 text-left font-semibold">Last sync</th>
                  <th className="px-4 py-2 text-left font-semibold">Docs</th>
                  <th className="px-4 py-2 text-left font-semibold">Chunks</th>
                </tr>
              </thead>
              <tbody>
                {sources.map((source) => (
                  <tr key={source.id} className="border-t border-white/5">
                    <td className="px-4 py-3 align-top">
                      <div className="font-semibold text-white">{prettySource(source.kind)}</div>
                      {source.urlOrPath && (
                        <div className="mt-1 max-w-md truncate font-mono text-[11px] text-aqua/80">
                          {source.urlOrPath}
                        </div>
                      )}
                      {source.lastError && (
                        <div className="mt-1 text-xs text-coral">{source.lastError}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top">
                      {source.bucketSlug ? (
                        <Link
                          href={`/knowledge/${encodeURIComponent(source.bucketSlug)}`}
                          className="font-semibold text-aqua hover:underline"
                        >
                          {source.bucketName}
                        </Link>
                      ) : (
                        <span className="font-semibold text-white/70">
                          {source.bucketName}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <Badge tone={source.status === "error" ? "err" : "ok"}>
                        {source.status}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 align-top text-xs text-white/60">
                      {source.lastSyncedAt ? relativeDate(source.lastSyncedAt) : "Never"}
                    </td>
                    <td className="px-4 py-3 align-top text-xs text-white/60">
                      {source.documents ?? "unknown"}
                    </td>
                    <td className="px-4 py-3 align-top text-xs text-white/60">
                      {source.chunks ?? "unknown"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Supported source types"
          subtitle="Workspace sources sync into Ship's database and route changed content into the recommended buckets."
        />
        <div className="flex flex-wrap gap-2">
          {[
            "Notion",
            "Confluence",
            "docs repository",
            "website via Firecrawl",
            "uploaded files",
          ].map((source) => (
            <Badge key={source} tone="neutral">
              {source}
            </Badge>
          ))}
        </div>
      </Card>
    </div>
  );
}

function SettingsTab({ buckets }: { buckets: KnowledgeControlBucket[] }) {
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader
          title="Starter bucket setup"
          subtitle="Recommended defaults for a new workspace."
        />
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {STARTER_BUCKETS.map((bucket) => {
            const exists = buckets.some((item) =>
              item.name.toLowerCase().includes(bucket.toLowerCase().slice(0, 12)),
            );
            return (
              <div
                key={bucket}
                className="flex items-center justify-between gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-xs"
              >
                <span className="text-white/75">{bucket}</span>
                <Badge tone={exists ? "ok" : "neutral"}>
                  {exists ? "present" : "optional"}
                </Badge>
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Policies and permissions"
          subtitle="Modeled here now; deeper enforcement belongs to bucket-level backend permissions."
        />
        <div className="space-y-3 text-sm text-white/65">
          <PolicyRow label="Visibility" value="Workspace, team, client, restricted" />
          <PolicyRow label="Permissions" value="Read, write, manage sources, delete, search" />
          <PolicyRow label="Routing hints" value="Use for / do not use for prompts per bucket" />
          <PolicyRow label="Freshness" value="Manual, scheduled, source-change, stale alerts" />
        </div>
      </Card>
    </div>
  );
}

function ExportButton({ workspace }: {
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

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-black/20 p-3">
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div className="mt-1 font-display text-xl font-bold text-white">{value}</div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.025] px-2 py-1.5">
      <div className="text-[9px] font-bold uppercase tracking-widest text-white/40">
        {label}
      </div>
      <div className="mt-0.5 font-semibold text-white">{value}</div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "relative px-3 py-2 text-sm font-semibold transition",
        active
          ? "text-white after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded-full after:bg-aqua"
          : "text-white/55 hover:text-white",
      )}
    >
      {children}
    </button>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-xs text-white/65">
      <span className="mb-1 block font-semibold uppercase tracking-widest text-white/45">
        {label}
      </span>
      {children}
    </label>
  );
}

function RoutingHint({
  bucket,
  useFor,
  avoid,
}: {
  bucket: string;
  useFor: string;
  avoid: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="font-semibold text-white">{bucket}</div>
      <p className="mt-1">
        <span className="text-aqua/90">Use for:</span> {useFor}
      </p>
      <p className="mt-1">
        <span className="text-coral/90">Do not use for:</span> {avoid}
      </p>
    </div>
  );
}

function PolicyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="text-xs font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div className="mt-1">{value}</div>
    </div>
  );
}

function dedupeHits(hits: ApiKnowledgeSearchHit[]): ApiKnowledgeSearchHit[] {
  const byKey = new Map<string, ApiKnowledgeSearchHit>();
  for (const hit of hits) {
    const key = `${hit.source}:${hit.id}`;
    const current = byKey.get(key);
    if (!current || hit.score > current.score) byKey.set(key, hit);
  }
  return Array.from(byKey.values()).sort((a, b) => b.score - a.score);
}

function maxDate(values: Array<string | null | undefined>): string | null {
  const times = values
    .map((value) => (value ? Date.parse(value) : Number.NaN))
    .filter((value) => Number.isFinite(value));
  if (times.length === 0) return null;
  return new Date(Math.max(...times)).toISOString();
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean))).sort();
}

function bucketInitial(name: string): string {
  return name.trim().slice(0, 1).toUpperCase() || "K";
}

function statusTone(status: KnowledgeBucketStatus) {
  if (status === "Ready") return "ok";
  if (status === "Indexing" || status === "Stale") return "warn";
  if (status === "Failed") return "err";
  return "neutral";
}

function scopeTone(scope: string) {
  if (scope === "workspace") return "workspace";
  if (scope === "project" || scope === "repo") return "project";
  return "neutral";
}

function authorityTone(authority: string) {
  if (authority === "Source of truth") return "ok";
  if (authority.includes("Generated") || authority.includes("Temporary")) {
    return "warn";
  }
  return "neutral";
}

function scopeLabel(scope: string): string {
  if (scope === "workspace") return "Workspace";
  if (scope === "repo") return "Repo-specific";
  if (scope === "user") return "Restricted";
  if (scope === "project") return "Project";
  return scope;
}

function prettySource(source: string): string {
  return source.replace(/_/g, " ");
}

function relativeDate(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "Unknown";
  const diff = Date.now() - timestamp;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "just now";
  if (diff < hour) return `${Math.floor(diff / minute)}m ago`;
  if (diff < day) return `${Math.floor(diff / hour)}h ago`;
  return `${Math.floor(diff / day)}d ago`;
}
