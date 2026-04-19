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
  getKnowledgeBucket,
  isApiConfigured,
  listWorkspaces,
} from "@/lib/api/client";
import { ApiHttpError } from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import type { ApiKnowledgeBucket } from "@/lib/api/types";
import {
  formatBytes,
  knowledgeBuckets as mockBuckets,
  knowledgeChunks,
  knowledgeDocs as mockDocs,
  relativeTime,
  workspaces,
} from "@/lib/mock/cloud";

const FALLBACK_WS = workspaces[0];

type LiveData = {
  source: "live";
  workspace: { id: string; slug: string; name: string };
  bucket: ApiKnowledgeBucket;
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
    const bucket = await getKnowledgeBucket(ws.id, slug, token);
    return {
      source: "live",
      workspace: { id: ws.id, slug: ws.slug, name: ws.name },
      bucket,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 404) {
      // Backend says no such bucket. Fall back to mock if a mock entry
      // exists (so the demo flow still works), otherwise hard 404.
      return mockFallback(slug, "bucket not found in backend — showing demo data");
    }
    const msg = err instanceof Error ? err.message : String(err);
    return mockFallback(slug, `backend error: ${msg}`);
  }
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

function LiveView({ data }: { data: LiveData }) {
  const ws = data.workspace;
  const bucket = data.bucket;
  return (
    <AppShell
      kicker={`${ws.name} · knowledge`}
      title={bucket.title}
      actions={<ButtonGhost>Open file in repo</ButtonGhost>}
    >
      <LiveBanner workspace={ws.slug} />

      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-white/55">
        <Link href="/knowledge" className="hover:text-white">
          Knowledge
        </Link>
        <span className="text-white/25">/</span>
        <span className="font-mono text-white/65">{bucket.slug}</span>
        <span className="text-white/25">·</span>
        <Badge tone={bucket.visibility === "project" ? "project" : "workspace"}>
          {bucket.visibility}
        </Badge>
        <span className="ml-auto text-[10px] uppercase tracking-widest text-white/40">
          updated {relativeTime(bucket.updated_at)} · {formatBytes(bucket.size)}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-sm leading-relaxed text-white/75">
        Source file:{" "}
        <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
          {bucket.path}
        </code>
      </p>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Document"
            subtitle="Markdown source — rendered as plain text for now"
          />
          <pre className="max-h-[640px] overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-black/30 px-4 py-3 font-mono text-[12px] leading-relaxed text-white/85">
            {bucket.body || "(empty file)"}
          </pre>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader title="CLI" />
            <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[11px] text-aqua/90">
{`shipctl knowledge fetch ${bucket.slug} \\
  --workspace ${ws.slug}`}
            </pre>
          </Card>

          <Card>
            <CardHeader title="Source repo" />
            <p className="text-xs text-white/65">
              <code className="block break-all rounded bg-white/[0.06] px-2 py-1 font-mono text-[11px] text-aqua/85">
                {bucket.repo_url}
              </code>
              <span className="mt-2 block text-[10px] text-white/45">
                Repo id <span className="font-mono">{bucket.repo_id}</span>
              </span>
            </p>
          </Card>

          <Card>
            <CardHeader title="Embeddings" />
            <p className="text-xs text-white/55">
              Vector indexing arrives in the next milestone. For now, the CLI
              fetches the raw markdown body so agents can include it inline.
            </p>
          </Card>
        </div>
      </section>
    </AppShell>
  );
}

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
