import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiLane,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listLanes,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Live `/lanes` surface — replaces the previous mock ``/workflows``
 * page with a real projection of customer-declared
 * ``.ship/config.yml`` lanes (RFC-0007 Phase 7).
 *
 * Lanes are grouped by repo (the only grouping dimension today). For
 * each repo we render a card stack showing lane id, kind, pattern
 * deep-link into ``/catalog``, last run status, and a "Sync now"
 * button that re-pulls the YAML.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;

export default async function LanesPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParamsBag>;
}) {
  const params = (await searchParams) ?? {};

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
  let lanes: ApiLane[] = [];
  let repos: ApiActivatedRepo[] = [];
  try {
    [lanes, repos] = await Promise.all([
      listLanes(workspace.id, { token }),
      listActivatedRepos(workspace.id, token).catch(
        () => [] as ApiActivatedRepo[],
      ),
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
  const groups = groupLanes(lanes, sortedRepos);
  const banner = pickBanner(params);

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
          href="/pipelines"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          Pipelines →
        </Link>
      }
    >
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Every lane Ship sees in this workspace&apos;s{" "}
        <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
          .ship/config.yml
        </code>{" "}
        files, grouped by repo. Sync re-pulls the YAML; pushes to the
        default branch auto-sync via webhook.
      </p>

      {banner ? (
        <Card className="mb-6 border-white/10">
          <p className="text-xs text-white/75">{banner}</p>
        </Card>
      ) : null}

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
                Run{" "}
                <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
                  shipctl init
                </code>{" "}
                in the repo.
              </li>
              <li>
                Declare one or more lanes under{" "}
                <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
                  lanes:
                </code>
                .
              </li>
              <li>
                Push to the default branch, or click &ldquo;Sync now&rdquo;
                after you&apos;ve activated the repo.
              </li>
            </ol>
          </div>
          {sortedRepos.length > 0 ? (
            <div className="mt-4 space-y-2">
              {sortedRepos.map((repo) => (
                <SyncButton
                  key={repo.id}
                  workspaceId={workspace.id}
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
              workspaceId={workspace.id}
            />
          ))}
        </div>
      )}
    </AppShell>
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
  // Stable ordering: match the scope pill (alphabetical) and fall
  // back to lanes-only repos at the end.
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
          <Link
            href={`/catalog?q=${encodeURIComponent(lane.pattern)}`}
            className="text-aqua hover:underline"
          >
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
              {lane.pattern}
            </code>
          </Link>
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
