import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonDanger,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import {
  formatBytes,
  knowledgeBuckets,
  knowledgeChunks,
  knowledgeDocs,
  relativeTime,
  workspaces,
} from "@/lib/mock/cloud";

const ws = workspaces[0];

export default async function KnowledgeBucketDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const bucket = knowledgeBuckets.find((b) => b.id === id);
  if (!bucket) notFound();

  const docs = knowledgeDocs.filter((d) => d.bucketId === id);
  const chunks = knowledgeChunks; // mock: tied to kb_devops, fine to render across

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
      <MockBanner />

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
          tone={bucket.status === "ready" ? "ok" : bucket.status === "indexing" ? "warn" : "err"}
          dot
        >
          {bucket.status}
        </Badge>
        <span className="ml-auto text-[10px] uppercase tracking-widest text-white/40">
          updated {relativeTime(bucket.updatedAt)}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-base leading-relaxed text-white/85">{bucket.summary}</p>

      <section className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Documents" value={bucket.documents.toString()} />
        <Stat label="Chunks" value={bucket.embeddings.toLocaleString()} />
        <Stat label="Total size" value={formatBytes(bucket.totalBytes)} />
        <Stat
          label="Embed model"
          value="text-embedding-3-large"
          mono
        />
      </section>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Search inside this bucket"
            subtitle="Same index your CLI hits via shipctl knowledge fetch"
          />
          <div className="mb-4 flex items-center gap-2">
            <input
              defaultValue="on-call rotation"
              className="flex-1 rounded-full border border-white/10 bg-white/[0.06] px-4 py-2.5 text-sm text-white outline-none focus:border-aqua/40"
            />
            <ButtonPrimary>Search</ButtonPrimary>
          </div>
          <ul className="space-y-3">
            {chunks.map((c) => (
              <li
                key={c.id}
                className="rounded-xl border border-white/10 bg-white/[0.025] p-3 transition hover:border-white/20"
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
                <div className="mt-2 flex items-center gap-2 text-[10px]">
                  <button className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 font-semibold text-white/70 hover:bg-white/[0.08]">
                    Open doc
                  </button>
                  <button className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 font-semibold text-white/70 hover:bg-white/[0.08]">
                    Copy as quote
                  </button>
                  <button className="rounded-full border border-coral/30 bg-coral/[0.08] px-2.5 py-1 font-semibold text-coral hover:bg-coral/15">
                    Hide chunk
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Pipeline status" subtitle="Per-document conversion" />
            <ul className="space-y-2 text-xs">
              <PipelineStep stage="Upload" tone="ok" detail={`${docs.length} files`} />
              <PipelineStep stage="Parse → Markdown" tone="ok" detail="4 ready · 0 in-flight" />
              <PipelineStep stage="Chunk + dedupe" tone="ok" detail="183 chunks total" />
              <PipelineStep
                stage="Embed"
                tone="warn"
                detail="1 in queue (K8s upgrade plan)"
              />
              <PipelineStep
                stage="Index (pgvector)"
                tone="ok"
                detail="ivfflat · 100 lists"
              />
              <PipelineStep
                stage="Failures"
                tone="err"
                detail="1 doc failed parse · Old SRE handbook.pdf"
              />
            </ul>
          </Card>

          <Card>
            <CardHeader title="CLI" />
            <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[11px] text-aqua/90">
{`shipctl knowledge fetch ${bucket.id} \\
  --query "on-call rotation" \\
  --top 5 \\
  --workspace ${ws.slug}`}
            </pre>
          </Card>

          <Card>
            <CardHeader title="Permissions" />
            <ul className="space-y-2 text-xs">
              <li className="flex items-center justify-between">
                <span className="text-white/75">Read</span>
                <Badge tone="ok">workspace · all members</Badge>
              </li>
              <li className="flex items-center justify-between">
                <span className="text-white/75">Upload</span>
                <Badge tone="info">maintainer+</Badge>
              </li>
              <li className="flex items-center justify-between">
                <span className="text-white/75">Re-embed / delete</span>
                <Badge tone="warn">admin+</Badge>
              </li>
            </ul>
          </Card>
        </div>
      </section>

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
              <th className="px-4 py-2 text-left font-semibold">Pages</th>
              <th className="px-4 py-2 text-left font-semibold">Chunks</th>
              <th className="px-4 py-2 text-left font-semibold">Status</th>
              <th className="px-4 py-2 text-left font-semibold">Uploaded</th>
              <th className="px-4 py-2 text-right font-semibold"></th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id} className="border-t border-white/5">
                <td className="px-4 py-3 align-top">
                  <div className="font-semibold text-white">{d.name}</div>
                  <div className="text-[10px] text-white/45">{d.uploadedBy}</div>
                </td>
                <td className="px-4 py-3 align-top text-[11px] uppercase tracking-widest text-white/55">
                  {d.type}
                </td>
                <td className="px-4 py-3 align-top text-xs text-white/65">{formatBytes(d.size)}</td>
                <td className="px-4 py-3 align-top text-xs text-white/65">{d.pages}</td>
                <td className="px-4 py-3 align-top text-xs font-mono text-aqua/85">{d.chunks}</td>
                <td className="px-4 py-3 align-top">
                  <Badge
                    tone={
                      d.status === "ready"
                        ? "ok"
                        : d.status === "embedding"
                          ? "warn"
                          : d.status === "parsing"
                            ? "info"
                            : "err"
                    }
                    dot
                  >
                    {d.status}
                  </Badge>
                </td>
                <td className="px-4 py-3 align-top text-xs text-white/55">
                  {relativeTime(d.uploadedAt)}
                </td>
                <td className="px-4 py-3 text-right align-top">
                  <div className="inline-flex flex-wrap items-center justify-end gap-1.5">
                    <ButtonGhost className="!py-1 !text-[10px]">Re-embed</ButtonGhost>
                    <ButtonDanger className="!py-1 !text-[10px]">Delete</ButtonDanger>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
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

function PipelineStep({
  stage,
  tone,
  detail,
}: {
  stage: string;
  tone: "ok" | "warn" | "err" | "info";
  detail: string;
}) {
  return (
    <li className="flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.02] px-2.5 py-2">
      <div>
        <div className="font-semibold text-white">{stage}</div>
        <div className="text-[10px] text-white/55">{detail}</div>
      </div>
      <Badge tone={tone} dot>
        {tone === "ok" ? "passing" : tone === "warn" ? "queued" : tone === "info" ? "info" : "error"}
      </Badge>
    </li>
  );
}
