import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/app-shell";

// Reads cookies + fetches per request; never cache between sessions.
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
  getArtifactById,
  isApiConfigured,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import type { ApiArtifact, ApiArtifactDetail } from "@/lib/api/types";
import {
  artifactReadmes,
  artifactVersions,
  artifacts as mockArtifacts,
  relativeTime,
  workspaces,
} from "@/lib/mock/cloud";

const FALLBACK_WS = workspaces[0];

type LiveData = {
  source: "live";
  workspace: { id: string; slug: string; name: string };
  detail: ApiArtifactDetail;
};

type MockData = {
  source: "mock";
  workspace: { slug: string; name: string };
  reason: string;
  matches: typeof mockArtifacts;
};

type Loaded = LiveData | MockData;

async function load(id: string): Promise<Loaded | "notfound"> {
  if (!isApiConfigured()) {
    return mockFallback(id, "backend not configured (SHIP_API_URL unset)");
  }
  const token = await getSessionToken();
  if (!token) {
    return mockFallback(id, "not signed in — showing demo data");
  }
  try {
    const wss = await listWorkspaces(token);
    if (wss.length === 0) {
      return mockFallback(id, "no workspaces yet — finish onboarding first");
    }
    const ws = wss[0];
    const detail = await getArtifactById(ws.id, id);
    if (detail === null) {
      return "notfound";
    }
    return {
      source: "live",
      workspace: { id: ws.id, slug: ws.slug, name: ws.name },
      detail,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return mockFallback(id, `backend error: ${msg}`);
  }
}

function mockFallback(id: string, reason: string): MockData | "notfound" {
  const matches = mockArtifacts.filter((a) => a.id === id);
  if (matches.length === 0) return "notfound";
  return {
    source: "mock",
    workspace: { slug: FALLBACK_WS.slug, name: FALLBACK_WS.name },
    reason,
    matches,
  };
}

export default async function ArtifactDetailPage({
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
  const { workspace: ws, detail } = data;
  const winner = detail;
  const layers = detail.layers ?? [];
  const kind = guessKind(detail);

  return (
    <AppShell
      kicker={`${ws.name} · catalog`}
      title={winner.title ?? winner.id}
      actions={
        <>
          <ButtonGhost>Open in repo</ButtonGhost>
          <ButtonPrimary>Use in project</ButtonPrimary>
        </>
      }
    >
      <LiveBanner workspace={ws.slug} />

      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-white/55">
        <Link href="/catalog" className="hover:text-white">
          Catalog
        </Link>
        <span className="text-white/25">/</span>
        <Badge tone="neutral">{kind}</Badge>
        <span className="font-mono text-white/65">{winner.id}</span>
        <span className="text-white/25">·</span>
        <Badge tone={toneFor(winner.effective_source)}>
          effective: {winner.effective_source}
        </Badge>
        {layers.length > 1 && (
          <span className="text-[10px] text-white/45">
            overrides {layers[1].effective_source}
          </span>
        )}
        <span className="ml-auto text-[10px] uppercase tracking-widest text-white/40">
          v{winner.version ?? "—"} · {winner.channel ?? "stable"}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-base leading-relaxed text-white/85">
        {winner.summary || "No summary in ARTIFACT.md frontmatter."}
      </p>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="README"
            subtitle={`Rendered from ${winner.path}`}
          />
          {detail.readme.trim().length > 0 ? (
            <pre className="max-h-[640px] overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-black/30 px-4 py-3 font-mono text-[12px] leading-relaxed text-white/85">
              {detail.readme}
            </pre>
          ) : (
            <p className="text-sm text-white/55">
              ARTIFACT.md has no body — only frontmatter.
            </p>
          )}
          {winner.tags.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-1.5">
              {winner.tags.map((t) => (
                <span
                  key={t}
                  className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] text-white/65"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Resolution layers"
              subtitle="Where this id lives across enabled sources"
            />
            <ol className="space-y-2 text-xs">
              {layers.map((layer, i) => (
                <li
                  key={`${layer.effective_source}-${layer.version ?? i}`}
                  className={
                    "flex items-start gap-2 rounded-lg border p-2.5 " +
                    (i === 0
                      ? "border-aqua/35 bg-aqua/[0.05]"
                      : "border-white/10 bg-white/[0.02] opacity-70")
                  }
                >
                  <Badge tone={toneFor(layer.effective_source)}>
                    {layer.effective_source}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-white">
                      {layer.title ?? layer.id}
                    </div>
                    <div className="text-[10px] text-white/45">
                      v{layer.version ?? "—"} · {layer.channel ?? "stable"}
                      {layer.updated_at && ` · ${relativeTime(layer.updated_at)}`}
                    </div>
                  </div>
                  {i === 0 && (
                    <span className="rounded-full bg-aqua/20 px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua">
                      effective
                    </span>
                  )}
                </li>
              ))}
            </ol>
          </Card>

          <Card>
            <CardHeader title="Quick CLI" />
            <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[11px] text-aqua/90">
{`shipctl ${kind} fetch ${winner.id} \\
  --workspace ${ws.slug}${winner.version ? ` \\\n  --version ${winner.version}` : ""}`}
            </pre>
          </Card>

          {winner.deprecated && (
            <Card>
              <CardHeader title="Deprecated" />
              <p className="text-xs text-coral/90">
                This artifact is marked deprecated in its frontmatter. New
                installs are discouraged; existing pins keep working.
              </p>
            </Card>
          )}
        </div>
      </section>
    </AppShell>
  );
}

function MockView({ data }: { data: MockData }) {
  const ws = data.workspace;
  const matches = data.matches;
  const rank = (s: (typeof matches)[number]["source"]) =>
    s === "project" ? 3 : s === "workspace" ? 2 : 1;
  const winner = matches.slice().sort((a, b) => rank(b.source) - rank(a.source))[0];
  const layers = matches.slice().sort((a, b) => rank(b.source) - rank(a.source));

  const versions = artifactVersions[winner.id] ?? [];
  const readme = artifactReadmes[winner.id];

  return (
    <AppShell
      kicker={`${ws.name} · catalog`}
      title={winner.name}
      actions={
        <>
          <ButtonGhost>Open in repo</ButtonGhost>
          <ButtonPrimary>Use in project</ButtonPrimary>
        </>
      }
    >
      <MockBanner reason={data.reason} />

      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-white/55">
        <Link href="/catalog" className="hover:text-white">
          Catalog
        </Link>
        <span className="text-white/25">/</span>
        <Badge tone="neutral">{winner.kind}</Badge>
        <span className="font-mono text-white/65">{winner.id}</span>
        <span className="text-white/25">·</span>
        <Badge
          tone={
            winner.source === "workspace"
              ? "workspace"
              : winner.source === "project"
                ? "project"
                : "global"
          }
        >
          effective: {winner.source}
        </Badge>
        {winner.overrides && (
          <span className="text-[10px] text-white/45">
            overrides {winner.overrides}
          </span>
        )}
        <span className="ml-auto text-[10px] uppercase tracking-widest text-white/40">
          v{winner.version} · {winner.channel}
        </span>
      </div>

      <p className="mb-6 max-w-3xl text-base leading-relaxed text-white/85">
        {winner.summary}
      </p>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="README"
            subtitle="Rendered from artifacts/{kind}s/{id}/ARTIFACT.md"
          />
          {readme ? (
            <div className="space-y-5 text-sm leading-relaxed text-white/80">
              <p>{readme.intro}</p>
              <div>
                <h4 className="mb-2 font-display text-xs font-bold uppercase tracking-widest text-aqua/85">
                  Usage
                </h4>
                <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[12px] text-aqua/90">
{readme.usage}
                </pre>
              </div>
            </div>
          ) : (
            <p className="text-sm text-white/55">
              No README rendered for this mock entry yet.
            </p>
          )}
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Resolution layers" subtitle="Where this id lives across sources" />
            <ol className="space-y-2 text-xs">
              {layers.map((l, i) => (
                <li
                  key={`${l.source}-${l.version}`}
                  className={
                    "flex items-start gap-2 rounded-lg border p-2.5 " +
                    (i === 0
                      ? "border-aqua/35 bg-aqua/[0.05]"
                      : "border-white/10 bg-white/[0.02] opacity-70")
                  }
                >
                  <Badge
                    tone={
                      l.source === "workspace"
                        ? "workspace"
                        : l.source === "project"
                          ? "project"
                          : "global"
                    }
                  >
                    {l.source}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-white">{l.name}</div>
                    <div className="text-[10px] text-white/45">
                      v{l.version} · {l.channel} · {relativeTime(l.updatedAt)}
                    </div>
                  </div>
                  {i === 0 && (
                    <span className="rounded-full bg-aqua/20 px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua">
                      effective
                    </span>
                  )}
                </li>
              ))}
            </ol>
          </Card>

          <Card>
            <CardHeader title="Quick CLI" />
            <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[11px] text-aqua/90">
{`shipctl ${winner.kind} fetch ${winner.id} \\
  --workspace ${ws.slug} \\
  --version ${winner.version}`}
            </pre>
          </Card>
        </div>
      </section>

      {versions.length > 0 && (
        <Card className="mt-8">
          <CardHeader
            title="Version history"
            subtitle={`${versions.length} versions · git source of truth, this is the index view`}
          />
          <ul className="space-y-3">
            {versions.map((v) => (
              <li
                key={v.version}
                className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.025] p-4 lg:flex-row lg:items-center lg:justify-between"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <code className="rounded-md bg-white/[0.06] px-2 py-0.5 font-mono text-[12px] font-bold text-aqua/95">
                      {v.version}
                    </code>
                    <Badge
                      tone={v.channel === "stable" ? "ok" : v.channel === "beta" ? "warn" : "info"}
                    >
                      {v.channel}
                    </Badge>
                    <span className="text-[10px] uppercase tracking-widest text-white/45">
                      {relativeTime(v.releasedAt)}
                    </span>
                  </div>
                  <p className="mt-1.5 text-sm text-white/75">{v.notes}</p>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </AppShell>
  );
}

function toneFor(source: string): "global" | "workspace" | "project" {
  return source === "workspace"
    ? "workspace"
    : source === "project"
      ? "project"
      : "global";
}

function guessKind(detail: ApiArtifact): string {
  if (typeof detail.path === "string") {
    const m = detail.path.match(/artifacts\/(patterns|tools|collections)\//);
    if (m) {
      const plural = m[1];
      return plural === "patterns"
        ? "pattern"
        : plural === "tools"
          ? "tool"
          : "collection";
    }
  }
  return "artifact";
}
