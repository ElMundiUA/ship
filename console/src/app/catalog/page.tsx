import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import { ScopePill } from "@/components/scope-pill";
import {
  type ResolvedScope,
  resolveScopeFromSearch,
} from "@/lib/scope";

// Reads cookies + env at runtime; never cache between sessions.
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
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listAllArtifacts,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import type { ApiArtifact, ApiArtifactKind, ApiUser } from "@/lib/api/types";
import { artifacts as mockArtifacts, relativeTime, workspaces } from "@/lib/mock/cloud";

const FALLBACK_WS = workspaces[0];

type Row = {
  id: string;
  kind: ApiArtifactKind;
  name: string;
  summary: string;
  version: string;
  channel: string;
  source: "global" | "workspace" | "project";
  sourceRepoId?: string;
  overrides?: "global" | "workspace" | "project";
  updatedAt: string;
  tags: string[];
};

type CatalogData = {
  source: "live" | "mock";
  workspace: { id?: string; slug: string; name: string };
  rows: Row[];
  repos: { id: string; full_name: string }[];
  me: ApiUser | null;
  reason?: string;
};

async function loadCatalog(): Promise<CatalogData> {
  if (!isApiConfigured()) {
    return mockData("backend not configured (SHIP_API_URL unset)");
  }
  const token = await getSessionToken();
  if (!token) {
    return mockData("not signed in — showing demo data");
  }
  try {
    const wss = await listWorkspaces(token);
    if (wss.length === 0) {
      return mockData("no workspaces yet — finish onboarding first");
    }
    const ws = wss[0];
    const [all, repos, me] = await Promise.all([
      listAllArtifacts(ws.id),
      listActivatedRepos(ws.id, token).catch(() => []),
      getMe(token).catch(() => null as ApiUser | null),
    ]);
    return {
      source: "live",
      workspace: { id: ws.id, slug: ws.slug, name: ws.name },
      rows: all.map(toRow),
      repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
      me,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return mockData(`backend error: ${msg}`);
  }
}

function mockData(reason: string): CatalogData {
  // Surface "overrides" by collapsing duplicate ids: workspace > global, project > workspace.
  const winners = new Map<string, (typeof mockArtifacts)[number]>();
  for (const a of mockArtifacts) {
    const cur = winners.get(a.id);
    const rank = (s: (typeof a)["source"]) =>
      s === "project" ? 3 : s === "workspace" ? 2 : 1;
    if (!cur || rank(a.source) > rank(cur.source)) winners.set(a.id, a);
  }
  return {
    source: "mock",
    workspace: { slug: FALLBACK_WS.slug, name: FALLBACK_WS.name },
    rows: [...winners.values()].map((a) => ({
      id: a.id,
      kind: a.kind,
      name: a.name,
      summary: a.summary,
      version: a.version,
      channel: a.channel,
      source: a.source,
      overrides: a.overrides,
      updatedAt: a.updatedAt,
      tags: a.tags,
    })),
    repos: [],
    me: null,
    reason,
  };
}

function toRow(a: ApiArtifact & { _kind?: ApiArtifactKind }): Row {
  // Best-effort kind derivation: when listAllArtifacts merges kinds, it stamps
  // `_kind` on each entry. Otherwise infer from `path` (artifacts/<plural>/...).
  let kind: ApiArtifactKind = a._kind ?? "pattern";
  if (!a._kind && typeof a.path === "string") {
    const m = a.path.match(/artifacts\/(patterns|tools|workflows|collections)\//);
    if (m) {
      const plural = m[1];
      kind =
        plural === "patterns"
          ? "pattern"
          : plural === "tools"
            ? "tool"
            : plural === "workflows"
              ? "workflow"
              : "collection";
    }
  }
  return {
    id: a.id,
    kind,
    name: a.title ?? a.id,
    summary: a.summary ?? "",
    version: a.version ?? "—",
    channel: a.channel ?? "stable",
    source: a.effective_source,
    sourceRepoId: a.source_repo_id,
    updatedAt: a.updated_at ?? new Date().toISOString(),
    tags: a.tags ?? [],
  };
}

export default async function CatalogPage({
  searchParams,
}: {
  searchParams: Promise<{
    scope?: string;
    repo_id?: string;
    project_id?: string;
  }>;
}) {
  const params = await searchParams;
  const scope = resolveScopeFromSearch(params);
  const data = await loadCatalog();
  // Phase 4b: repo scope filters the merged catalog by
  // ``source_repo_id`` — every artifact row that carries a concrete
  // repo of origin (project-scope pins, workspace-authored entries
  // sourced from a specific repo). Rows without a ``source_repo_id``
  // (global catalog, workspace-level catalogs not attached to a
  // single repo) stay visible so the repo-scope view still shows
  // the ambient catalog the repo inherits. User scope has no
  // catalog analog today, so it falls back to full.
  const scopedRows =
    scope.kind === "repo" && scope.repoId
      ? data.rows.filter(
          (r) => !r.sourceRepoId || r.sourceRepoId === scope.repoId,
        )
      : data.rows;
  const counts = {
    total: scopedRows.length,
    global: scopedRows.filter((r) => r.source === "global").length,
    workspace: scopedRows.filter((r) => r.source === "workspace").length,
    project: scopedRows.filter((r) => r.source === "project").length,
  };
  const ws = data.workspace;

  const scopePill =
    data.source === "live" ? (
      <ScopePill
        workspaceName={ws.name}
        repos={data.repos}
        me={
          data.me
            ? {
                id: data.me.id,
                email: data.me.email,
                display_name: data.me.display_name,
              }
            : null
        }
      />
    ) : undefined;

  return (
    <AppShell
      kicker={`${ws.name} · catalog`}
      title="Artifact catalog"
      scopePill={scopePill}
      actions={
        <>
          <Link
            href="/catalog/pull-requests"
            className="inline-flex items-center gap-1.5 rounded-full border border-coral/40 bg-coral/10 px-3 py-1.5 text-xs font-bold text-coral hover:bg-coral/20"
          >
            4 PRs to review
          </Link>
          <ButtonGhost>Add repo</ButtonGhost>
          <ButtonPrimary>+ New artifact</ButtonPrimary>
        </>
      }
    >
      {data.source === "live" ? (
        <LiveBanner workspace={ws.slug} />
      ) : (
        <MockBanner reason={data.reason} />
      )}

      {scope.kind === "user" ? (
        <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[12px] text-amber-100/85">
          Catalog is workspace-wide; there&apos;s no per-user overlay
          (yet). Pick a repo scope to see the ambient catalog plus
          that repo&apos;s project-level overrides, or stay on
          workspace for the full picture.
        </div>
      ) : null}

      {scope.kind === "repo" && scope.repoId ? (
        <div className="mb-4 rounded-lg border border-aqua/30 bg-aqua/5 px-3 py-2 text-[12px] text-aqua/85">
          Showing {catalogScopeLabel(scope, data.repos)} — global /
          workspace rows are included because every repo inherits
          them. Project-scope rows from other repos are hidden.
        </div>
      ) : null}

      <div className="mb-5 flex flex-wrap items-center gap-2">
        <SourcePill label="All" count={counts.total} active />
        <SourcePill tone="global" label="Global" count={counts.global} />
        <SourcePill tone="workspace" label="Workspace" count={counts.workspace} />
        <SourcePill tone="project" label="Project" count={counts.project} />
        <span className="ml-auto inline-flex items-center gap-2 text-xs text-white/50">
          <input
            placeholder="Filter by name, tag, group…"
            className="w-64 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-white outline-none placeholder:text-white/35 focus:border-aqua/40"
          />
          <select className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-white outline-none">
            <option>All kinds</option>
            <option>Pattern</option>
            <option>Tool</option>
            <option>Workflow</option>
            <option>Collection</option>
          </select>
        </span>
      </div>

      <Card padded={false} className="overflow-hidden">
        <table className="min-w-full text-sm">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="px-4 py-2.5 text-left font-semibold">Artifact</th>
              <th className="px-4 py-2.5 text-left font-semibold">Source</th>
              <th className="px-4 py-2.5 text-left font-semibold">Version</th>
              <th className="px-4 py-2.5 text-left font-semibold">Updated</th>
              <th className="px-4 py-2.5 text-left font-semibold">Tags</th>
              <th className="px-4 py-2.5 text-right font-semibold">Use</th>
            </tr>
          </thead>
          <tbody>
            {scopedRows.map((a) => (
              <tr
                key={`${a.kind}:${a.id}:${a.source}`}
                className="border-t border-white/5 transition hover:bg-white/[0.025]"
              >
                <td className="px-4 py-3 align-top">
                  <div className="flex items-center gap-2">
                    <Badge tone="neutral">{a.kind}</Badge>
                    <Link
                      href={`/catalog/${encodeURIComponent(a.id)}`}
                      className="font-semibold text-white hover:text-aqua"
                    >
                      {a.name}
                    </Link>
                  </div>
                  <p className="mt-1 line-clamp-1 max-w-[44ch] text-xs text-white/55">
                    {a.summary}
                  </p>
                </td>
                <td className="px-4 py-3 align-top">
                  <Badge
                    tone={
                      a.source === "workspace"
                        ? "workspace"
                        : a.source === "project"
                          ? "project"
                          : "global"
                    }
                  >
                    {a.source}
                  </Badge>
                  {a.overrides && (
                    <div className="mt-1 text-[10px] text-white/45">
                      overrides <span className="text-white/65">{a.overrides}</span>
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 align-top">
                  <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-aqua/95">
                    {a.version}
                  </code>
                  <div className="mt-1 text-[10px] uppercase tracking-widest text-white/35">
                    {a.channel}
                  </div>
                </td>
                <td className="px-4 py-3 align-top text-xs text-white/55">
                  {relativeTime(a.updatedAt)}
                </td>
                <td className="px-4 py-3 align-top">
                  <div className="flex flex-wrap gap-1">
                    {a.tags.slice(0, 3).map((t) => (
                      <span
                        key={t}
                        className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] text-white/65"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3 text-right align-top">
                  <div className="inline-flex flex-col items-end gap-1.5">
                    <CopyShellctl id={a.id} kind={a.kind} wsSlug={ws.slug} />
                  </div>
                </td>
              </tr>
            ))}
            {scopedRows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-sm text-white/55">
                  No artifacts in any enabled source. Connect a workspace repo or
                  toggle on the global catalog under{" "}
                  <Link href="/settings" className="text-aqua hover:underline">
                    workspace settings
                  </Link>
                  .
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      <Card className="mt-6">
        <CardHeader
          title="How resolution works"
          subtitle="When the same id appears in multiple sources, the higher-priority layer wins."
        />
        <ResolutionDiagram wsName={ws.name} />
      </Card>
    </AppShell>
  );
}

function catalogScopeLabel(
  scope: ResolvedScope,
  repos: { id: string; full_name: string }[],
): string {
  if (scope.kind === "repo" && scope.repoId) {
    const r = repos.find((x) => x.id === scope.repoId);
    return r ? r.full_name : "selected repo";
  }
  return "workspace";
}

function SourcePill({
  label,
  count,
  tone,
  active,
}: {
  label: string;
  count: number;
  tone?: "global" | "workspace" | "project";
  active?: boolean;
}) {
  const ringByTone =
    tone === "workspace"
      ? "ring-aqua/40"
      : tone === "project"
        ? "ring-lilac/40"
        : "ring-white/30";
  const dotByTone =
    tone === "workspace"
      ? "bg-aqua"
      : tone === "project"
        ? "bg-lilac"
        : "bg-white/60";
  return (
    <button
      className={
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition " +
        (active
          ? `border-white/30 bg-white/[0.08] text-white ring-1 ${ringByTone}`
          : "border-white/10 bg-white/[0.03] text-white/65 hover:border-white/25 hover:text-white")
      }
    >
      <span className={"h-1.5 w-1.5 rounded-full " + dotByTone} />
      {label}
      <span className="rounded-full bg-white/10 px-1.5 py-px text-[10px] text-white/65">
        {count}
      </span>
    </button>
  );
}

function CopyShellctl({
  id,
  kind,
  wsSlug,
}: {
  id: string;
  kind: string;
  wsSlug: string;
}) {
  const cmd = `shipctl ${kind} fetch ${id} --workspace ${wsSlug}`;
  return (
    <code className="rounded-md border border-white/10 bg-black/30 px-2 py-1 font-mono text-[10px] text-aqua/90">
      {cmd}
    </code>
  );
}

function ResolutionDiagram({ wsName }: { wsName: string }) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
      <Layer
        tone="global"
        title="Global"
        body="Public Ship monorepo · 24 patterns · 8 tools · 5 workflows · 16 collections"
      />
      <Layer
        tone="workspace"
        title="Workspace"
        body={`${wsName} · workspace-authored entries override global on id collision`}
      />
      <Layer
        tone="project"
        title="Project"
        body="Per-project pins win over both layers — useful for stack-specific overrides"
      />
    </div>
  );
}

function Layer({
  tone,
  title,
  body,
}: {
  tone: "global" | "workspace" | "project";
  title: string;
  body: string;
}) {
  const accent =
    tone === "global"
      ? "from-white/15 via-white/5 to-transparent"
      : tone === "workspace"
        ? "from-aqua/30 via-aqua/10 to-transparent"
        : "from-lilac/30 via-lilac/10 to-transparent";
  return (
    <div className="relative overflow-hidden rounded-xl border border-white/10 bg-white/[0.025] p-4">
      <div
        aria-hidden
        className={"absolute inset-x-0 top-0 h-1 bg-gradient-to-r " + accent}
      />
      <Badge tone={tone}>{title}</Badge>
      <p className="mt-2 text-xs leading-snug text-white/65">{body}</p>
    </div>
  );
}
