import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
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

import { ActiveCalendar } from "./active-calendar";
import { LibraryCatalog } from "./library-catalog";

/**
 * ``/lanes`` — the operator's workflow hub.
 *
 * Two tabs, selected via ``?tab=``:
 *
 * - **active** — weekly calendar showing who runs when, plus an
 *   event-driven strip for PR/push lanes. Click a block to see the
 *   pattern description and edit its schedule.
 * - **library** — catalog grid of built-in recipes. Each card can be
 *   added, edited, or removed inline; saving opens a PR against
 *   ``.ship/config.yml``. The last card spawns the custom-lane
 *   author (retires the old "New" tab).
 *
 * Legacy ``?tab=new`` links now redirect to ``library`` — the custom
 * author moved into a card there.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;
type TabId = "active" | "library";

const TAB_META: { id: TabId; label: string; hint: string }[] = [
  {
    id: "active",
    label: "Active",
    hint: "Calendar of scheduled lanes + event-driven triggers for this workspace.",
  },
  {
    id: "library",
    label: "Library",
    hint: "Catalog of lane recipes you can add to a repo's .ship/config.yml.",
  },
];

function pickTab(params: SearchParamsBag): TabId {
  const raw = typeof params.tab === "string" ? params.tab : null;
  if (raw === "library") return "library";
  return "active";
}

export default async function LanesPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParamsBag>;
}) {
  const params = (await searchParams) ?? {};

  // Legacy deep-links from pre-redesign (emails, bookmarks) still
  // carry ``?tab=new``. Gracefully funnel them into Library where
  // the author lives now.
  if (typeof params.tab === "string" && params.tab === "new") {
    redirect("/lanes?tab=library");
  }

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

  // Library tab writes to a specific repo. Default to the first
  // activated one (matches the scope pill's default) so the tab
  // lands on a usable screen for single-repo tenants.
  const requestedRepoId =
    typeof params.repo_id === "string" ? params.repo_id : null;
  const selectedRepo =
    (requestedRepoId
      ? sortedRepos.find((r) => r.id === requestedRepoId)
      : null) ?? sortedRepos[0] ?? null;

  let libraryConfig: ApiRepoConfig | null = null;
  let libraryConfigError: string | null = null;
  // Library tab needs ``.ship/config.yml`` for baseline + optimistic
  // locking. We only fetch when that tab is active to keep Active
  // fast.
  if (tab === "library" && selectedRepo) {
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

  const initialOpenKind =
    typeof params.open === "string" ? params.open : null;

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
        <ActiveCalendar
          workspaceId={workspace.id}
          lanes={lanes}
          repos={sortedRepos}
          catalog={catalog}
        />
      ) : null}

      {tab === "library" ? (
        <LibraryCatalog
          workspaceId={workspace.id}
          selectedRepo={selectedRepo}
          repos={sortedRepos}
          catalog={catalog}
          lanes={lanes}
          config={libraryConfig}
          configError={libraryConfigError}
          initialOpenKind={initialOpenKind}
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

// -------------------------- HELPERS -----------------------------

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
