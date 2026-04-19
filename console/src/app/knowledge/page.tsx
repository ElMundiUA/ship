import Link from "next/link";
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
  isApiConfigured,
  listKnowledgeBuckets,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import type { ApiKnowledgeBucket } from "@/lib/api/types";
import {
  formatBytes,
  knowledgeBuckets as mockBuckets,
  knowledgeDocs as mockDocs,
  relativeTime,
  workspaces,
} from "@/lib/mock/cloud";

const FALLBACK_WS = workspaces[0];

type LiveData = {
  source: "live";
  workspace: { id: string; slug: string; name: string };
  buckets: ApiKnowledgeBucket[];
};

type MockData = {
  source: "mock";
  workspace: { slug: string; name: string };
  reason: string;
};

type Loaded = LiveData | MockData;

async function load(): Promise<Loaded> {
  if (!isApiConfigured()) {
    return { source: "mock", workspace: FALLBACK_WS, reason: "backend not configured (SHIP_API_URL unset)" };
  }
  const token = await getSessionToken();
  if (!token) {
    return { source: "mock", workspace: FALLBACK_WS, reason: "not signed in — showing demo data" };
  }
  try {
    const wss = await listWorkspaces(token);
    if (wss.length === 0) {
      return { source: "mock", workspace: FALLBACK_WS, reason: "no workspaces yet — finish onboarding first" };
    }
    const ws = wss[0];
    const buckets = await listKnowledgeBuckets(ws.id, token);
    return {
      source: "live",
      workspace: { id: ws.id, slug: ws.slug, name: ws.name },
      buckets,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { source: "mock", workspace: FALLBACK_WS, reason: `backend error: ${msg}` };
  }
}

export default async function KnowledgeIndexPage() {
  const data = await load();
  const ws = data.workspace;

  return (
    <AppShell
      kicker={`${ws.name} · knowledge`}
      title="Knowledge buckets"
      actions={
        <>
          <ButtonGhost>Browse global packs</ButtonGhost>
          <ButtonPrimary>+ New bucket</ButtonPrimary>
        </>
      }
    >
      {data.source === "live" ? (
        <LiveBanner workspace={ws.slug} />
      ) : (
        <MockBanner reason={data.reason} />
      )}

      <p className="mb-5 max-w-3xl text-sm text-white/65">
        Each bucket maps to a markdown file under{" "}
        <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
          .ship/knowledge/&lt;slug&gt;.md
        </code>{" "}
        in your workspace or project repo. The CLI reads the same files via{" "}
        <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
          shipctl knowledge fetch &lt;slug&gt;
        </code>
        .
      </p>

      {data.source === "live" ? (
        <LiveBucketGrid buckets={data.buckets} />
      ) : (
        <MockBucketGrid />
      )}
    </AppShell>
  );
}

function LiveBucketGrid({ buckets }: { buckets: ApiKnowledgeBucket[] }) {
  if (buckets.length === 0) {
    return (
      <Card className="text-center">
        <p className="text-sm text-white/70">
          No knowledge buckets yet. Finish onboarding (or run{" "}
          <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
            shipctl knowledge seed
          </code>
          ) to populate{" "}
          <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
            .ship/knowledge/
          </code>{" "}
          in your repo.
        </p>
        <div className="mt-4 flex justify-center gap-2">
          <Link
            href="/onboarding"
            className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-bold text-aqua hover:bg-aqua/20"
          >
            Open onboarding →
          </Link>
        </div>
      </Card>
    );
  }
  return (
    <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {buckets.map((b) => (
        <Card key={`${b.repo_id}-${b.slug}`} className="flex flex-col">
          <div className="flex items-start justify-between gap-3">
            <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-lilac/40 via-aqua/30 to-coral/30 text-xl font-bold text-white">
              {emojiFor(b.slug)}
            </div>
            <Badge tone={b.visibility === "project" ? "project" : "workspace"}>
              {b.visibility}
            </Badge>
          </div>
          <h3 className="mt-3 font-display text-base font-bold text-white">
            {b.title}
          </h3>
          <p className="mt-1 line-clamp-3 text-xs text-white/60">
            {b.excerpt || "No content yet."}
          </p>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-[11px]">
            <Stat k="Size" v={formatBytes(b.size)} />
            <Stat k="Updated" v={relativeTime(b.updated_at)} />
          </dl>
          <div className="mt-auto pt-4">
            <Link
              href={`/knowledge/${encodeURIComponent(b.slug)}`}
              className="font-semibold text-aqua hover:underline"
            >
              Open →
            </Link>
          </div>
        </Card>
      ))}
    </section>
  );
}

function MockBucketGrid() {
  return (
    <>
      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {mockBuckets.map((b) => (
          <Card key={b.id} className="flex flex-col">
            <div className="flex items-start justify-between gap-3">
              <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-lilac/40 via-aqua/30 to-coral/30 text-xl font-bold text-white">
                {emojiFor(b.glyph)}
              </div>
              <Badge
                tone={
                  b.status === "ready"
                    ? "ok"
                    : b.status === "indexing"
                      ? "warn"
                      : "err"
                }
                dot
              >
                {b.status}
              </Badge>
            </div>
            <h3 className="mt-3 font-display text-base font-bold text-white">{b.name}</h3>
            <p className="mt-1 line-clamp-2 text-xs text-white/60">{b.summary}</p>
            <dl className="mt-4 grid grid-cols-3 gap-3 text-[11px]">
              <Stat k="Docs" v={b.documents.toString()} />
              <Stat k="Chunks" v={b.embeddings.toLocaleString()} />
              <Stat k="Size" v={formatBytes(b.totalBytes)} />
            </dl>
            <div className="mt-auto flex items-center justify-between pt-4 text-[11px]">
              <Badge
                tone={
                  b.visibility === "project"
                    ? "project"
                    : b.visibility === "workspace"
                      ? "workspace"
                      : "neutral"
                }
              >
                {b.visibility}
              </Badge>
              <Link
                href={`/knowledge/${b.id}`}
                className="font-semibold text-aqua hover:underline"
              >
                Open →
              </Link>
            </div>
            <div className="mt-3 text-[10px] uppercase tracking-widest text-white/35">
              updated {relativeTime(b.updatedAt)}
            </div>
          </Card>
        ))}
      </section>

      <Card className="mt-8">
        <CardHeader
          title="Recent uploads · DevOps rules · Helio"
          subtitle="Per-document parsing & embedding pipeline (mock)"
        />
        <table className="min-w-full text-sm">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="px-3 py-2 text-left font-semibold">Document</th>
              <th className="px-3 py-2 text-left font-semibold">Type</th>
              <th className="px-3 py-2 text-left font-semibold">Pages</th>
              <th className="px-3 py-2 text-left font-semibold">Size</th>
              <th className="px-3 py-2 text-left font-semibold">Uploaded</th>
            </tr>
          </thead>
          <tbody>
            {mockDocs.map((d) => (
              <tr key={d.id} className="border-t border-white/5">
                <td className="px-3 py-2.5 align-top">
                  <div className="font-semibold text-white">{d.name}</div>
                  <div className="text-[10px] text-white/45">{d.uploadedBy}</div>
                </td>
                <td className="px-3 py-2.5 align-top text-[11px] uppercase tracking-widest text-white/55">
                  {d.type}
                </td>
                <td className="px-3 py-2.5 align-top text-xs text-white/65">{d.pages}</td>
                <td className="px-3 py-2.5 align-top text-xs text-white/65">{formatBytes(d.size)}</td>
                <td className="px-3 py-2.5 align-top text-xs text-white/55">
                  {relativeTime(d.uploadedAt)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.02] px-2 py-1.5">
      <div className="text-[9px] font-bold uppercase tracking-widest text-white/40">{k}</div>
      <div className="mt-0.5 font-semibold text-white">{v}</div>
    </div>
  );
}

function emojiFor(slug: string): string {
  const lower = slug.toLowerCase();
  if (lower.includes("brand")) return "✦";
  if (lower.includes("style") || lower.includes("code")) return "⌘";
  if (lower.includes("test")) return "✓";
  if (lower.includes("devops") || lower.includes("ops")) return "⚙";
  if (lower.includes("security")) return "🛡";
  if (lower.includes("design")) return "✦";
  if (lower.includes("compliance")) return "§";
  return "◆";
}
