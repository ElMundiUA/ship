import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiLane,
  type ApiLaneCatalogEntry,
  type ApiRepoConfig,
  ApiHttpError,
  ApiUnavailableError,
  getRepoConfig,
  isApiConfigured,
  listActivatedRepos,
  listLaneCatalog,
  listLanes,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { CustomLaneAuthor } from "./custom-author";
import { LibraryEditor } from "./library-editor";

/**
 * Live `/lanes` surface — the operator's workflow hub.
 *
 * Three tabs (selected via ``?tab=``):
 *
 * - **active** — the projection of customer-declared
 *   ``.ship/config.yml`` lanes, grouped by repo. Backed by
 *   ``listLanes`` which reads the DB table populated by
 *   ``sync_lanes_for_repo`` (webhook + manual refresh).
 * - **library** — the catalog of built-in lane specs plus
 *   workspace-authored ones. Phase 1 is read-only; Phase 2 adds
 *   enable/disable toggles + cron editor + "Save → open PR".
 * - **new** — custom lane author. Phase 1 is a placeholder;
 *   Phase 3 wires the form to ``POST /lanes/propose``.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;
type TabId = "active" | "library" | "new";

const TAB_META: { id: TabId; label: string; hint: string }[] = [
  {
    id: "active",
    label: "Active",
    hint: "Lanes currently declared in this workspace's .ship/config.yml files.",
  },
  {
    id: "library",
    label: "Library",
    hint: "Lane recipes you can add to a repo's config.",
  },
  {
    id: "new",
    label: "New",
    hint: "Author a custom lane — agent, trigger, schedule.",
  },
];

function pickTab(params: SearchParamsBag): TabId {
  const raw = typeof params.tab === "string" ? params.tab : null;
  if (raw === "library" || raw === "new") return raw;
  return "active";
}

export default async function LanesPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParamsBag>;
}) {
  const params = (await searchParams) ?? {};
  const tab = pickTab(params);

  if (!isApiConfigured()) {
    return (
      <AppShell title="Lanes">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load lanes from .ship/config.yml."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Flanes");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Flanes");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  // Every tab needs the repo list (for scope pill + "which repos
  // have which lanes"). We fetch lanes + catalog in parallel —
  // both are workspace-scoped and cheap. The catalog endpoint
  // never 401s so don't let its failure take out the whole page.
  let lanes: ApiLane[] = [];
  let repos: ApiActivatedRepo[] = [];
  let catalog: ApiLaneCatalogEntry[] = [];
  try {
    [lanes, repos, catalog] = await Promise.all([
      listLanes(workspace.id, { token }),
      listActivatedRepos(workspace.id, token).catch(
        () => [] as ApiActivatedRepo[],
      ),
      listLaneCatalog(token).catch(() => [] as ApiLaneCatalogEntry[]),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Flanes");
    }
    return renderUnavailable(err);
  }

  const sortedRepos = [...repos].sort((a, b) =>
    a.full_name.localeCompare(b.full_name),
  );
  const banner = pickBanner(params);

  // The Library editor is single-repo by design. ``?repo_id=`` picks
  // which one; default to the first activated repo (matches the
  // scope pill's initial selection) so the tab lands on a useful
  // screen without an extra click for single-repo tenants.
  const requestedRepoId =
    typeof params.repo_id === "string" ? params.repo_id : null;
  const selectedRepo =
    (requestedRepoId
      ? sortedRepos.find((r) => r.id === requestedRepoId)
      : null) ?? sortedRepos[0] ?? null;

  let libraryConfig: ApiRepoConfig | null = null;
  let libraryConfigError: string | null = null;
  // Both Library and New tabs need the current ``.ship/config.yml`` —
  // Library for baseline/diff, New for the base_sha optimistic lock
  // and the "this lane id is already taken" check.
  const needsConfig = (tab === "library" || tab === "new") && !!selectedRepo;
  if (needsConfig && selectedRepo) {
    try {
      libraryConfig = await getRepoConfig(workspace.id, selectedRepo.id, token);
    } catch (err) {
      if (err instanceof ApiHttpError) {
        libraryConfigError = `Couldn't load .ship/config.yml (HTTP ${err.status}).`;
      } else {
        libraryConfigError = "Couldn't load .ship/config.yml.";
      }
    }
  }

  return (
    <AppShell
      title="Lanes"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      scope={{
        repos: sortedRepos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: sortedRepos[0]?.id ?? null,
      }}
      actions={
        <Link
          href="/requests"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          Requests →
        </Link>
      }
    >
      <TabStrip current={tab} />

      {banner ? (
        <Card className="mb-6 border-white/10">
          <p className="text-xs text-white/75">{banner}</p>
        </Card>
      ) : null}

      {tab === "active" ? (
        <ActiveView
          workspaceId={workspace.id}
          lanes={lanes}
          repos={sortedRepos}
        />
      ) : null}

      {tab === "library" ? (
        <LibraryView
          workspaceId={workspace.id}
          selectedRepo={selectedRepo}
          repos={sortedRepos}
          catalog={catalog}
          lanes={lanes}
          config={libraryConfig}
          configError={libraryConfigError}
        />
      ) : null}

      {tab === "new" ? (
        <NewView
          workspaceId={workspace.id}
          selectedRepo={selectedRepo}
          repos={sortedRepos}
          config={libraryConfig}
          configError={libraryConfigError}
        />
      ) : null}
    </AppShell>
  );
}

function TabStrip({ current }: { current: TabId }) {
  return (
    <div className="mb-5">
      <div className="mb-2 flex flex-wrap items-end gap-1 border-b border-white/10">
        {TAB_META.map((t) => {
          const active = t.id === current;
          return (
            <Link
              key={t.id}
              href={t.id === "active" ? "/lanes" : `/lanes?tab=${t.id}`}
              className={
                "-mb-px inline-flex items-center gap-2 rounded-t-md border-b-2 px-3 py-2 text-xs font-semibold transition " +
                (active
                  ? "border-aqua text-white"
                  : "border-transparent text-white/55 hover:text-white/85")
              }
            >
              {t.label}
            </Link>
          );
        })}
      </div>
      <p className="text-xs text-white/55">
        {TAB_META.find((t) => t.id === current)?.hint}
      </p>
    </div>
  );
}

// -------------------------- ACTIVE -----------------------------

function ActiveView({
  workspaceId,
  lanes,
  repos,
}: {
  workspaceId: string;
  lanes: ApiLane[];
  repos: ApiActivatedRepo[];
}) {
  const groups = groupLanes(lanes, repos);

  return (
    <>
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Every lane Ship sees in this workspace&apos;s{" "}
        <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
          .ship/config.yml
        </code>{" "}
        files, grouped by repo. Sync re-pulls the YAML; pushes to the
        default branch auto-sync via webhook.
      </p>

      {lanes.length === 0 ? (
        <Card>
          <CardHeader
            title="No lanes discovered yet"
            subtitle="Lanes show up here the moment .ship/config.yml lands on a repo's default branch."
          />
          <div className="mt-4 text-xs text-white/60">
            <p>Quick start:</p>
            <ol className="mt-1 ml-5 list-decimal space-y-1">
              <li>
                Open the{" "}
                <Link
                  href="/lanes?tab=library"
                  className="text-aqua hover:underline"
                >
                  Library
                </Link>{" "}
                tab and pick a recipe.
              </li>
              <li>
                Or run{" "}
                <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
                  shipctl init
                </code>{" "}
                in the repo to hand-author a config.
              </li>
              <li>
                Push to the default branch, or click &ldquo;Sync now&rdquo;
                after you&apos;ve activated the repo.
              </li>
            </ol>
          </div>
          {repos.length > 0 ? (
            <div className="mt-4 space-y-2">
              {repos.map((repo) => (
                <SyncButton
                  key={repo.id}
                  workspaceId={workspaceId}
                  repoId={repo.id}
                  label={`Sync ${repo.full_name}`}
                />
              ))}
            </div>
          ) : null}
        </Card>
      ) : (
        <div className="space-y-8">
          {groups.map((group) => (
            <LaneGroup
              key={group.repoId}
              group={group}
              workspaceId={workspaceId}
            />
          ))}
        </div>
      )}
    </>
  );
}

type LaneGroupModel = {
  repoId: string;
  repoFullName: string;
  lanes: ApiLane[];
  syncedAt: string | null;
};

function groupLanes(lanes: ApiLane[], repos: ApiActivatedRepo[]): LaneGroupModel[] {
  const byRepo = new Map<string, LaneGroupModel>();
  for (const lane of lanes) {
    const existing = byRepo.get(lane.repo_id);
    if (existing) {
      existing.lanes.push(lane);
      if (
        !existing.syncedAt ||
        (lane.synced_at && lane.synced_at > existing.syncedAt)
      ) {
        existing.syncedAt = lane.synced_at;
      }
    } else {
      byRepo.set(lane.repo_id, {
        repoId: lane.repo_id,
        repoFullName: lane.repo_full_name,
        lanes: [lane],
        syncedAt: lane.synced_at,
      });
    }
  }
  const active = new Set(repos.map((r) => r.id));
  const groups = [...byRepo.values()];
  groups.sort((a, b) => {
    const aActive = active.has(a.repoId) ? 0 : 1;
    const bActive = active.has(b.repoId) ? 0 : 1;
    if (aActive !== bActive) return aActive - bActive;
    return a.repoFullName.localeCompare(b.repoFullName);
  });
  for (const g of groups) {
    g.lanes.sort((a, b) => a.lane_id.localeCompare(b.lane_id));
  }
  return groups;
}

function LaneGroup({
  group,
  workspaceId,
}: {
  group: LaneGroupModel;
  workspaceId: string;
}) {
  return (
    <section>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3 border-b border-white/10 pb-2">
        <div className="min-w-0">
          <h3 className="font-display text-sm font-bold tracking-wide text-white">
            {group.repoFullName}
          </h3>
          <p className="text-[11px] text-white/45">
            {group.lanes.length}{" "}
            {group.lanes.length === 1 ? "lane declared" : "lanes declared"}
            {group.syncedAt
              ? ` · synced ${formatRelative(group.syncedAt)}`
              : ""}
          </p>
        </div>
        <SyncButton
          workspaceId={workspaceId}
          repoId={group.repoId}
          label="Sync now"
        />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {group.lanes.map((lane) => (
          <LaneCard key={lane.id} lane={lane} />
        ))}
      </div>
    </section>
  );
}

function LaneCard({ lane }: { lane: ApiLane }) {
  const lastRunLabel = lane.last_run_at
    ? `${lane.last_run_status ?? "run"} · ${formatRelative(lane.last_run_at)}`
    : "no runs yet";
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">{lane.kind}</Badge>
        {lane.enabled ? null : <Badge tone="neutral">disabled</Badge>}
        {lane.kind === "schedule" && lane.cron ? (
          <Badge tone="neutral">
            cron <code className="ml-1">{lane.cron}</code>
          </Badge>
        ) : null}
      </div>
      <h4 className="mt-2 font-display text-sm font-bold text-white">
        {lane.lane_id}
      </h4>
      <p className="mt-0.5 text-[11px] text-white/55">
        pattern ·{" "}
        {lane.pattern ? (
          <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
            {lane.pattern}
          </code>
        ) : (
          <span className="text-white/45">(none)</span>
        )}
      </p>
      <div className="mt-3">
        <Badge tone={lastRunTone(lane.last_run_status)} dot>
          {lastRunLabel}
        </Badge>
      </div>
      <div className="mt-4 flex items-center justify-between gap-2">
        <Link
          href={`/lanes/${lane.id}`}
          className="text-xs font-semibold text-white/70 hover:text-white"
        >
          Details →
        </Link>
        <code className="rounded bg-white/[0.04] px-1.5 py-0.5 text-[10px] text-white/45">
          ship-{lane.lane_id}.yml
        </code>
      </div>
    </Card>
  );
}

function SyncButton({
  workspaceId,
  repoId,
  label,
}: {
  workspaceId: string;
  repoId: string;
  label: string;
}) {
  return (
    <form action="/api/dashboard/sync-lanes" method="POST">
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="repo" value={repoId} />
      <button
        type="submit"
        className="rounded border border-white/15 bg-white/[0.05] px-3 py-1 text-[11px] font-semibold text-white/85 hover:border-white/30 hover:text-white"
      >
        {label}
      </button>
    </form>
  );
}

// -------------------------- LIBRARY -----------------------------

function LibraryView({
  workspaceId,
  selectedRepo,
  repos,
  catalog,
  lanes,
  config,
  configError,
}: {
  workspaceId: string;
  selectedRepo: ApiActivatedRepo | null;
  repos: ApiActivatedRepo[];
  catalog: ApiLaneCatalogEntry[];
  lanes: ApiLane[];
  config: ApiRepoConfig | null;
  configError: string | null;
}) {
  if (catalog.length === 0) {
    return (
      <Card>
        <CardHeader
          title="Library is empty"
          subtitle="No built-in lane recipes are exposed by the backend yet. Check that the /v1/catalog/lanes endpoint is deployed."
        />
      </Card>
    );
  }

  if (!selectedRepo) {
    return (
      <Card>
        <CardHeader
          title="Activate a repo first"
          subtitle="The Library editor rewrites .ship/config.yml on a specific repo. Activate one via onboarding to enable this tab."
        />
        <div className="mt-4">
          <Link
            href="/onboarding?step=github"
            className="inline-flex rounded border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
          >
            Open onboarding →
          </Link>
        </div>
      </Card>
    );
  }

  const laneSubset = lanes.filter((l) => l.repo_id === selectedRepo.id);

  return (
    <div className="space-y-4">
      {repos.length > 1 ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-white/55">
          <span className="font-semibold">Repo:</span>
          {repos.map((r) => (
            <Link
              key={r.id}
              href={`/lanes?tab=library&repo_id=${encodeURIComponent(r.id)}`}
              className={
                "rounded-full border px-2.5 py-1 font-mono text-[11px] transition " +
                (r.id === selectedRepo.id
                  ? "border-aqua/40 bg-aqua/10 text-aqua"
                  : "border-white/10 bg-white/[0.04] text-white/70 hover:text-white")
              }
            >
              {r.full_name}
            </Link>
          ))}
        </div>
      ) : null}

      {configError ? (
        <Card className="border-coral/25 bg-coral/5">
          <p className="text-xs text-coral">{configError}</p>
        </Card>
      ) : null}

      <LibraryEditor
        workspaceId={workspaceId}
        repoId={selectedRepo.id}
        repoFullName={selectedRepo.full_name}
        catalog={catalog}
        lanes={laneSubset}
        config={config}
      />

      <p className="text-[11px] text-white/45">
        Recipes with no trigger (e.g. resolver-only lanes like{" "}
        <code className="rounded bg-white/[0.06] px-1 py-0.5">code_map</code>)
        are hidden — they don&apos;t live in{" "}
        <code className="rounded bg-white/[0.06] px-1 py-0.5">
          .ship/config.yml
        </code>{" "}
        and are wired by the installer instead.
      </p>
    </div>
  );
}

// -------------------------- NEW TAB -----------------------------

function NewView({
  workspaceId,
  selectedRepo,
  repos,
  config,
  configError,
}: {
  workspaceId: string;
  selectedRepo: ApiActivatedRepo | null;
  repos: ApiActivatedRepo[];
  config: ApiRepoConfig | null;
  configError: string | null;
}) {
  return (
    <div className="space-y-4">
      {configError ? (
        <Card className="border-coral/25 bg-coral/5">
          <p className="text-xs text-coral">{configError}</p>
        </Card>
      ) : null}
      <CustomLaneAuthor
        workspaceId={workspaceId}
        selectedRepo={selectedRepo}
        repos={repos}
        config={config}
      />
    </div>
  );
}

// -------------------------- HELPERS -----------------------------

function lastRunTone(status: string | null): "ok" | "warn" | "err" | "neutral" {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "err";
  if (
    status === "cancelled" ||
    status === "timed_out" ||
    status === "action_required"
  )
    return "warn";
  return "neutral";
}

function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const sec = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}

function pickBanner(params: SearchParamsBag): string | null {
  const reason = typeof params.reason === "string" ? params.reason : null;
  const changed =
    typeof params.changed === "string" ? params.changed : undefined;
  switch (reason) {
    case "synced":
      return `Lanes synced. ${changed ?? "0"} row(s) changed.`;
    case "synced_with_errors":
      return "Lanes synced, but some entries failed to parse. Check the YAML.";
    case "missing_config":
      return "That repo has no .ship/config.yml on its default branch yet.";
    case "github_unreachable":
      return "GitHub rejected the sync request. Retry in a moment.";
    case "forbidden":
      return "Only workspace admins can sync lanes.";
    case "missing":
      return "Repo not found in this workspace.";
    case "api_unavailable":
      return "Backend is unreachable. Sync is temporarily unavailable.";
    case null:
      return null;
    default:
      return reason?.startsWith("http_")
        ? `Sync failed with ${reason}.`
        : null;
  }
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Lanes">
      <Card>
        <CardHeader
          title="Couldn't load lanes"
          subtitle={
            isUnavailable
              ? "Backend is unreachable. Try again in a few seconds."
              : "Something went wrong."
          }
        />
      </Card>
    </AppShell>
  );
}
