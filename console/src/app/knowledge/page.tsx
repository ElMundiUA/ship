import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import {
  formatBytes,
  knowledgeBuckets,
  knowledgeDocs,
  relativeTime,
  workspaces,
} from "@/lib/mock/cloud";

const ws = workspaces[0];

export default function KnowledgeIndexPage() {
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
      <MockBanner />

      <p className="mb-5 max-w-3xl text-sm text-white/65">
        Drop in PDFs, presentations, Word docs or Markdown — Ship parses them
        to Markdown, embeds the chunks and exposes them to your CLI as if they
        were a versioned artifact:{" "}
        <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
          shipctl knowledge fetch &lt;bucket&gt; --query &quot;…&quot;
        </code>
        .
      </p>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {knowledgeBuckets.map((b) => (
          <Card key={b.id} className="flex flex-col">
            <div className="flex items-start justify-between gap-3">
              <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-lilac/40 via-aqua/30 to-coral/30 text-xl font-bold text-white">
                {emojiFor(b.glyph)}
              </div>
              <Badge
                tone={b.status === "ready" ? "ok" : b.status === "indexing" ? "warn" : "err"}
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
                tone={b.visibility === "project" ? "project" : b.visibility === "workspace" ? "workspace" : "neutral"}
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
          subtitle="Per-document parsing & embedding pipeline"
          action={
            <Link
              href="/knowledge/kb_devops"
              className="text-xs font-semibold text-aqua hover:underline"
            >
              Open bucket →
            </Link>
          }
        />
        <table className="min-w-full text-sm">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="px-3 py-2 text-left font-semibold">Document</th>
              <th className="px-3 py-2 text-left font-semibold">Type</th>
              <th className="px-3 py-2 text-left font-semibold">Pages</th>
              <th className="px-3 py-2 text-left font-semibold">Size</th>
              <th className="px-3 py-2 text-left font-semibold">Status</th>
              <th className="px-3 py-2 text-left font-semibold">Uploaded</th>
            </tr>
          </thead>
          <tbody>
            {knowledgeDocs.map((d) => (
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
                <td className="px-3 py-2.5 align-top">
                  <DocStatus status={d.status} chunks={d.chunks} />
                </td>
                <td className="px-3 py-2.5 align-top text-xs text-white/55">
                  {relativeTime(d.uploadedAt)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </AppShell>
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

function DocStatus({ status, chunks }: { status: string; chunks: number }) {
  if (status === "ready")
    return <Badge tone="ok" dot>{chunks} chunks</Badge>;
  if (status === "embedding")
    return (
      <span className="inline-flex items-center gap-2">
        <Badge tone="warn" dot>embedding</Badge>
        <span className="h-1 w-20 overflow-hidden rounded bg-white/10">
          <span className="block h-full w-2/3 animate-pulse rounded bg-sun" />
        </span>
      </span>
    );
  if (status === "parsing") return <Badge tone="info" dot>parsing → md</Badge>;
  return <Badge tone="err" dot>failed</Badge>;
}

function emojiFor(glyph: string): string {
  switch (glyph) {
    case "devops":
      return "⚙";
    case "security":
      return "🛡";
    case "design":
      return "✦";
    case "compliance":
      return "§";
    default:
      return "◆";
  }
}
