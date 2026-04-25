import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/app-shell";

// Reads cookies + fetches per request.
export const dynamic = "force-dynamic";

import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  LiveBanner,
  MockBanner,
} from "@/components/ui";
import {
  type ApiBucket,
  type ApiDistillerRun,
  getBucket,
  getKnowledgeBucket,
  isApiConfigured,
  listBucketArticles,
  listBucketSources,
  listDistillerRuns,
  listWorkspaces,
} from "@/lib/api/client";
import { ApiHttpError } from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import type {
  ApiBucketArticle,
  ApiKnowledgeBucket,
  ApiKnowledgeSource,
} from "@/lib/api/types";
import {
  formatBytes,
  knowledgeBuckets as mockBuckets,
  knowledgeChunks,
  knowledgeDocs as mockDocs,
  relativeTime,
  workspaces,
} from "@/lib/mock/cloud";

import { ConnectorCard } from "./connector-card";
import { UploadCard } from "./upload-card";

const FALLBACK_WS = workspaces[0];

type LiveData = {
  source: "live";
  workspace: { id: string; slug: string; name: string };
  /** Legacy ``repo_files`` mirror body — present only for repo-files buckets. */
  legacy: ApiKnowledgeBucket | null;
  /** Unified bucket row (Phase 5d surface). */
  bucket: ApiBucket | null;
  /** Articles under the unified bucket, if we could load it. */
  articles: ApiBucketArticle[];
  /** Durable source configs and sync state for the unified bucket. */
  sources: ApiKnowledgeSource[];
  /** Distiller run history, newest first. */
  runs: ApiDistillerRun[];
  /** The human-usable title — prefers the unified bucket name. */
  title: string;
};

type MockData = {
  source: "mock";
  workspace: { slug: string; name: string };
  reason: string;
  bucket: (typeof mockBuckets)[number];
};

type Loaded = LiveData | MockData;

async function load(slug: string): Promise<Loaded | "notfound"> {
  if (!isApiConfigured()) {
    return mockFallback(slug, "backend not configured (SHIP_API_URL unset)");
  }
  const token = await getSessionToken();
  if (!token) {
    return mockFallback(slug, "not signed in — showing demo data");
  }
  try {
    const wss = await listWorkspaces(token);
    if (wss.length === 0) {
      return mockFallback(slug, "no workspaces yet — finish onboarding first");
    }
    const ws = wss[0];

    // Fan out the three fetches we need in parallel. Each individually
    // tolerates 404s — a new external-static bucket won't have a legacy
    // row; an old ``.ship/knowledge`` mirror may not yet have made it
    // into the unified bucket table; either could have zero articles
    // or zero runs. The page only hard-404s when *all three* miss.
    const [legacy, bucket, articlesRaw, sourcesRaw, runsRaw] = await Promise.all([
      getKnowledgeBucket(ws.id, slug, token).catch(
        quietly404AsNull<ApiKnowledgeBucket>(),
      ),
      getBucket(ws.id, slug, token).catch(quietly404AsNull<ApiBucket>()),
      listBucketArticles(ws.id, slug, {}, token).catch(
        () => [] as ApiBucketArticle[],
      ),
      listBucketSources(ws.id, slug, token).catch(
        () => [] as ApiKnowledgeSource[],
      ),
      listDistillerRuns(ws.id, slug, { limit: 20, token }).catch(
        () => [] as ApiDistillerRun[],
      ),
    ]);

    if (legacy === null && bucket === null) {
      return mockFallback(slug, "bucket not found in backend — showing demo data");
    }

    const title =
      bucket?.name ?? legacy?.title ?? slug;

    return {
      source: "live",
      workspace: { id: ws.id, slug: ws.slug, name: ws.name },
      legacy,
      bucket,
      articles: articlesRaw,
      sources: sourcesRaw,
      runs: runsRaw,
      title,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return mockFallback(slug, `backend error: ${msg}`);
  }
}

/** Swallow 404s into ``null`` so Promise.all doesn't reject the whole fan-out. */
function quietly404AsNull<T>() {
  return (err: unknown): T | null => {
    if (err instanceof ApiHttpError && err.status === 404) return null;
    throw err;
  };
}

function mockFallback(slug: string, reason: string): MockData | "notfound" {
  const bucket = mockBuckets.find((b) => b.id === slug);
  if (!bucket) return "notfound";
  return { source: "mock", workspace: FALLBACK_WS, reason, bucket };
}

export default async function KnowledgeBucketDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const data = await load(id);
  if (data === "notfound") notFound();
  return data.source === "live" ? <LiveView data={data} /> : <MockView data={data} />;
}

// ---------------------------------------------------------------------------
// Live (backend-connected) view
// ---------------------------------------------------------------------------

function LiveView({ data }: { data: LiveData }) {
  const { workspace: ws, legacy, bucket, articles, sources, runs, title } = data;

  // Upload is meaningful for any bucket except ``repo_files`` — those
  // mirror ``.ship/knowledge`` from git and should stay authoritative
  // in the repo. All other source kinds accept UTF-8 uploads through
  // the same Distiller path (including ``agent_memory``, connectors,
  // ``promoted``, etc.).
  const canUpload = bucket !== null && bucket.source_kind !== "repo_files";

  // Connector card only for connector_proxy buckets; it renders the
  // stored integration handle + "Sync now" button.
  const isConnector =
    bucket !== null && bucket.source_kind === "connector_proxy";
  const connectorRef = (bucket?.source_ref ?? null) as {
    integration_kind?: unknown;
    resource_ref?: unknown;
  } | null;
  const connectorKind =
    typeof connectorRef?.integration_kind === "string"
      ? connectorRef.integration_kind
      : null;
  const connectorResourceRef =
    connectorRef?.resource_ref &&
    typeof connectorRef.resource_ref === "object" &&
    !Array.isArray(connectorRef.resource_ref)
      ? (connectorRef.resource_ref as Record<string, unknown>)
      : {};

  const scopeKind = bucket?.scope_kind ?? (legacy?.visibility ?? "workspace");
  const sourceKind = bucket?.source_kind ?? "repo_files";

  return (
    <AppShell
      kicker={`${ws.name} · knowledge`}
      title={title}
      actions={
        legacy ? <ButtonGhost>Open file in repo</ButtonGhost> : undefined
      }
    >
      <LiveBanner workspace={ws.slug} />

      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-white/55">
        <Link href="/knowledge" className="hover:text-white">
          Knowledge
        </Link>
        <span className="text-white/25">/</span>
        <span className="font-mono text-white/65">
          {bucket?.slug ?? legacy?.slug}
        </span>
        <span className="text-white/25">·</span>
        <Badge tone={scopeKind === "project" ? "project" : "workspace"}>
          {scopeKind}
        </Badge>
        <Badge tone="neutral">{sourceKind.replace("_", " ")}</Badge>
        {legacy && (
          <span className="ml-auto text-[10px] uppercase tracking-widest text-white/40">
            updated {relativeTime(legacy.updated_at)} · {formatBytes(legacy.size)}
          </span>
        )}
        {!legacy && bucket && (
          <span className="ml-auto text-[10px] uppercase tracking-widest text-white/40">
            updated {relativeTime(bucket.updated_at)} ·{" "}
            {bucket.summary_count} article
            {bucket.summary_count === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {legacy && (
        <p className="mb-6 max-w-3xl text-sm leading-relaxed text-white/75">
          Source file:{" "}
          <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
            {legacy.path}
          </code>
        </p>
      )}

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          {legacy && (
            <Card>
              <CardHeader
                title="Document"
                subtitle="Markdown source — rendered as plain text for now"
              />
              <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-black/30 px-4 py-3 font-mono text-[12px] leading-relaxed text-white/85">
                {legacy.body || "(empty file)"}
              </pre>
            </Card>
          )}

          <ArticlesCard articles={articles} />
        </div>

        <div className="space-y-5">
          {isConnector && bucket && connectorKind && (
            <ConnectorCard
              workspaceId={ws.id}
              slug={bucket.slug}
              integrationKind={connectorKind}
              resourceRef={connectorResourceRef}
            />
          )}

          {canUpload && bucket && (
            <UploadCard
              workspaceId={ws.id}
              slug={bucket.slug}
              bucketName={bucket.name}
            />
          )}

          {bucket && <SourcesCard sources={sources} />}

          <RunsCard runs={runs} />

          <Card>
            <CardHeader title="CLI" />
            <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[11px] text-aqua/90">
{`shipctl knowledge fetch ${bucket?.slug ?? legacy?.slug} \\
  --workspace ${ws.slug}`}
            </pre>
          </Card>

          {legacy && (
            <Card>
              <CardHeader title="Source repo" />
              <p className="text-xs text-white/65">
                <code className="block break-all rounded bg-white/[0.06] px-2 py-1 font-mono text-[11px] text-aqua/85">
                  {legacy.repo_url}
                </code>
                <span className="mt-2 block text-[10px] text-white/45">
                  Repo id <span className="font-mono">{legacy.repo_id}</span>
                </span>
              </p>
            </Card>
          )}
        </div>
      </section>
    </AppShell>
  );
}

function ArticlesCard({ articles }: { articles: ApiBucketArticle[] }) {
  if (articles.length === 0) {
    return (
      <Card data-testid="bucket-articles-empty">
        <CardHeader
          title="Articles"
          subtitle="Articles appear here once the Distiller accepts new content."
        />
        <p className="text-xs text-white/60">
          Upload a file on the right, or run{" "}
          <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
            POST /v1/workspaces/&lt;ws&gt;/buckets/&lt;slug&gt;/distill
          </code>{" "}
          to seed the bucket.
        </p>
      </Card>
    );
  }
  return (
    <Card padded={false} data-testid="bucket-articles">
      <CardHeader
        className="px-5 pt-5"
        title={`Articles · ${articles.length}`}
        subtitle="Latest revisions first (superseded + archived hidden by default)."
      />
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
            <tr key={a.id} className="border-t border-white/5">
              <td className="px-4 py-2.5 align-top">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-white">{a.title}</span>
                  {a.overrides_workspace_article_id && (
                    <Badge tone="warn" dot>
                      overrides workspace canonical
                    </Badge>
                  )}
                </div>
                {provenanceHint(a) && (
                  <div className="mt-0.5 text-[10px] text-white/45">
                    {provenanceHint(a)}
                  </div>
                )}
                {a.overrides_workspace_article_id &&
                  a.overrides_workspace_bucket_slug && (
                    <div className="mt-0.5 text-[10px]">
                      <Link
                        href={`/knowledge/${encodeURIComponent(a.overrides_workspace_bucket_slug)}`}
                        className="text-aqua/80 hover:underline"
                      >
                        View workspace canonical →
                      </Link>
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

function statusTone(
  status: ApiBucketArticle["status"],
): "ok" | "warn" | "neutral" | "err" {
  switch (status) {
    case "published":
      return "ok";
    case "draft":
      return "warn";
    case "superseded":
      return "neutral";
    case "archived":
      return "err";
    default:
      return "neutral";
  }
}

function provenanceHint(article: ApiBucketArticle): string | null {
  const p = article.provenance;
  if (!p || typeof p !== "object") return null;
  const kind = typeof p.kind === "string" ? p.kind : null;
  if (kind === "pr_merged") {
    const num = p.pr_number;
    const author = p.author;
    return typeof num === "number" || typeof author === "string"
      ? `from PR #${num ?? "?"} by @${author ?? "?"}`
      : "from merged PR";
  }
  if (kind === "external_static_upload") {
    return typeof p.filename === "string"
      ? `uploaded: ${p.filename}`
      : "uploaded file";
  }
  if (kind === "connector_proxy") {
    const connector =
      typeof p.connector_kind === "string" ? p.connector_kind : null;
    return connector ? `synced from ${connector}` : "synced from connector";
  }
  if (kind === "repo_files" && typeof p.path === "string") {
    return `from ${p.path}`;
  }
  if (kind === "agent_memory" && typeof p.thread_id === "string") {
    return `packed from thread ${p.thread_id.slice(0, 8)}…`;
  }
  return null;
}

function SourcesCard({ sources }: { sources: ApiKnowledgeSource[] }) {
  if (sources.length === 0) {
    return (
      <Card data-testid="bucket-sources-empty">
        <CardHeader title="Sources" subtitle="No source rows recorded yet." />
      </Card>
    );
  }

  return (
    <Card padded={false} data-testid="bucket-sources">
      <CardHeader
        className="px-5 pt-5"
        title="Sources"
        subtitle="Durable sync state for this bucket"
      />
      <ul className="divide-y divide-white/5">
        {sources.map((source) => (
          <li key={source.id} className="px-5 py-3">
            <div className="flex items-center gap-2">
              <Badge tone={source.status === "error" ? "err" : "neutral"}>
                {source.status}
              </Badge>
              <span className="font-mono text-[11px] text-aqua/85">
                {source.kind}
              </span>
              <span className="ml-auto text-[10px] text-white/45">
                {source.last_synced_at
                  ? `synced ${relativeTime(source.last_synced_at)}`
                  : "not synced"}
              </span>
            </div>
            {source.last_error && (
              <p className="mt-2 text-xs text-coral/90">{source.last_error}</p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function RunsCard({ runs }: { runs: ApiDistillerRun[] }) {
  if (runs.length === 0) {
    return (
      <Card data-testid="bucket-runs-empty">
        <CardHeader
          title="Distiller runs"
          subtitle="No ingest activity yet."
        />
      </Card>
    );
  }
  return (
    <Card padded={false} data-testid="bucket-runs">
      <CardHeader
        className="px-5 pt-5"
        title="Distiller runs"
        subtitle="Newest first · last 20"
      />
      <ul className="divide-y divide-white/5">
        {runs.map((r) => (
          <li key={r.id} className="flex items-center gap-2 px-5 py-2">
            <Badge tone={runTone(r)}>{r.decision ?? r.status}</Badge>
            <span className="font-mono text-[10px] uppercase tracking-widest text-white/50">
              {classifierLabel(r)}
            </span>
            <span className="ml-auto text-[10px] text-white/45">
              {relativeTime(r.created_at)}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function runTone(
  run: ApiDistillerRun,
): "ok" | "warn" | "neutral" | "err" {
  if (run.status === "failed" || run.decision === "error") return "err";
  if (run.decision === "new" || run.decision === "update") return "ok";
  if (run.decision === "skip") return "neutral";
  return "warn";
}

function classifierLabel(run: ApiDistillerRun): string {
  const refs = run.output_refs as Record<string, unknown>;
  const cls = refs.classifier;
  if (cls && typeof cls === "object") {
    const name = (cls as { name?: unknown }).name;
    if (typeof name === "string") return name;
  }
  return "stub";
}

// ---------------------------------------------------------------------------
// Mock view (unchanged — demo data for disconnected deployments)
// ---------------------------------------------------------------------------

function MockView({ data }: { data: MockData }) {
  const ws = data.workspace;
  const bucket = data.bucket;
  const docs = mockDocs.filter((d) => d.bucketId === bucket.id);

  return (
    <AppShell
      kicker={`${ws.name} · knowledge`}
      title={bucket.name}
      actions={
        <>
          <ButtonGhost>Re-embed bucket</ButtonGhost>
          <ButtonPrimary>+ Upload</ButtonPrimary>
        </>
      }
    >
      <MockBanner reason={data.reason} />

      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-white/55">
        <Link href="/knowledge" className="hover:text-white">
          Knowledge
        </Link>
        <span className="text-white/25">/</span>
        <span className="font-mono text-white/65">{bucket.id}</span>
        <span className="text-white/25">·</span>
        <Badge tone={bucket.visibility === "project" ? "project" : "workspace"}>
          {bucket.visibility}
        </Badge>
        <Badge
          tone={
            bucket.status === "ready"
              ? "ok"
              : bucket.status === "indexing"
                ? "warn"
                : "err"
          }
          dot
        >
          {bucket.status}
        </Badge>
        <span className="ml-auto text-[10px] uppercase tracking-widest text-white/40">
          updated {relativeTime(bucket.updatedAt)}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-base leading-relaxed text-white/85">
        {bucket.summary}
      </p>

      <section className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Documents" value={bucket.documents.toString()} />
        <Stat label="Chunks" value={bucket.embeddings.toLocaleString()} />
        <Stat label="Total size" value={formatBytes(bucket.totalBytes)} />
        <Stat label="Embed model" value="text-embedding-3-large" mono />
      </section>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Search inside this bucket"
            subtitle="Same index your CLI hits via shipctl knowledge fetch"
          />
          <ul className="space-y-3">
            {knowledgeChunks.map((c) => (
              <li
                key={c.id}
                className="rounded-xl border border-white/10 bg-white/[0.025] p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 text-xs text-white/60">
                    <span className="font-semibold text-white/80">{c.docName}</span>
                    <span className="ml-2 text-white/40">page {c.page}</span>
                  </div>
                  <span className="font-mono text-[10px] text-aqua/85">
                    score {c.score.toFixed(2)}
                  </span>
                </div>
                <p className="mt-1.5 text-sm leading-snug text-white/80">{c.excerpt}</p>
              </li>
            ))}
          </ul>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader title="CLI" />
            <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[11px] text-aqua/90">
{`shipctl knowledge fetch ${bucket.id} \\
  --query "on-call rotation" \\
  --workspace ${ws.slug}`}
            </pre>
          </Card>
        </div>
      </section>

      {docs.length > 0 && (
        <Card className="mt-8" padded={false}>
          <CardHeader
            className="px-5 pt-5"
            title="Documents"
            subtitle="Source files retained alongside the parsed Markdown for re-embedding"
          />
          <table className="min-w-full text-sm">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Document</th>
                <th className="px-4 py-2 text-left font-semibold">Type</th>
                <th className="px-4 py-2 text-left font-semibold">Size</th>
                <th className="px-4 py-2 text-left font-semibold">Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id} className="border-t border-white/5">
                  <td className="px-4 py-3 align-top">
                    <div className="font-semibold text-white">{d.name}</div>
                  </td>
                  <td className="px-4 py-3 align-top text-[11px] uppercase tracking-widest text-white/55">
                    {d.type}
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-white/65">{formatBytes(d.size)}</td>
                  <td className="px-4 py-3 align-top text-xs text-white/55">
                    {relativeTime(d.uploadedAt)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </AppShell>
  );
}

function Stat({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <Card>
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">{label}</div>
      <div className={"mt-1 font-display text-xl font-bold text-white " + (mono ? "font-mono text-base" : "")}>
        {value}
      </div>
    </Card>
  );
}
