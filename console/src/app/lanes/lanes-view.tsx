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

import { CoverageView } from "../automations/coverage-view";

import { ActiveCalendar } from "./active-calendar";
import { LibraryCatalog } from "./library-catalog";

/**
 * Reusable Lanes / Automations page body.
 *
 * Lives outside ``page.tsx`` because Next.js page files only allow
 * a fixed set of named exports (``default``, ``dynamic``,
 * ``revalidate``, …) — the page-validator complains if we ship
 * ``LanesView`` from there. Both ``/lanes/page.tsx`` (legacy) and
 * ``/automations/page.tsx`` (new IA, P1-01) import this view and
 * thread their respective ``basePath`` / ``kicker`` / ``title``
 * down so the same body renders with relabeled chrome and the
 * correct deep-link anchors.
 */

type SearchParamsBag = Record<string, string | string[] | undefined>;
type TabId = "active" | "library" | "coverage";

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
  {
    id: "coverage",
    label: "Coverage",
    hint: "Which Plays are configured on which repos. Critical gaps highlighted.",
  },
];

function pickTab(params: SearchParamsBag): TabId {
  const raw = typeof params.tab === "string" ? params.tab : null;
  if (raw === "library") return "library";
  if (raw === "coverage") return "coverage";
  return "active";
}

function pickCoverageFilters(params: SearchParamsBag): {
  category: string | null;
  criticalOnly: boolean;
  hasGaps: boolean;
} {
  const cat = typeof params.category === "string" ? params.category : null;
  const criticalOnly =
    typeof params.critical_only === "string" &&
    params.critical_only.toLowerCase() === "true";
  const hasGaps =
    typeof params.has_gaps === "string" &&
    params.has_gaps.toLowerCase() === "true";
  return { category: cat, criticalOnly, hasGaps };
}

export async function LanesView({
  searchParams,
  basePath,
  kicker,
  title,
}: {
  searchParams?: Promise<SearchParamsBag>;
  basePath: string;
  kicker?: string;
  title: string;
}) {
  const params = (await searchParams) ?? {};
  const loginNext = encodeURIComponent(basePath);

  // Legacy deep-links from pre-redesign (emails, bookmarks) still
  // carry ``?tab=new``. Gracefully funnel them into Library where
  // the author lives now.
  if (typeof params.tab === "string" && params.tab === "new") {
    redirect(`${basePath}?tab=library`);
  }

  const tab = pickTab(params);

  if (!isApiConfigured()) {
    return (
      <AppShell title={title} kicker={kicker}>
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
  if (!token) redirect(`/login?next=${loginNext}`);

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${loginNext}`);
    }
    return renderUnavailable(err, { title, kicker });
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  let lanes: ApiLane[] = [];
  let repos: ApiActivatedRepo[] = [];
  let catalog: ApiLaneCatalogEntry[] = [];
  // Coverage tab only needs activated repos (for the UUID → slug
  // lookup) — skip the lane + lane-catalog fetches to keep its load
  // path lean. The CoverageView fetches its own data via
  // ``listPlaysCoverage``.
  if (tab === "coverage") {
    try {
      repos = await listActivatedRepos(workspace.id, token);
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 401) {
        redirect(`/login?next=${loginNext}`);
      }
      // Coverage degrades gracefully when repos can't load — the
      // tab will fall through to its own MockView.
      repos = [];
    }
  } else {
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
        redirect(`/login?next=${loginNext}`);
      }
      return renderUnavailable(err, { title, kicker });
    }
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
      title={title}
      kicker={kicker}
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
          href="/plays"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          Plays →
        </Link>
      }
    >
      <TabStrip current={tab} basePath={basePath} />

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
          basePath={basePath}
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
          basePath={basePath}
        />
      ) : null}

      {tab === "coverage" ? (
        <CoverageView
          workspaceId={workspace.id}
          repos={sortedRepos}
          basePath={basePath}
          filters={pickCoverageFilters(params)}
          token={token}
        />
      ) : null}
    </AppShell>
  );
}

function TabStrip({ current, basePath }: { current: TabId; basePath: string }) {
  return (
    <div className="mb-5">
      <div className="mb-2 flex flex-wrap items-end gap-1 border-b border-white/10">
        {TAB_META.map((t) => {
          const active = t.id === current;
          const href = t.id === "active" ? basePath : `${basePath}?tab=${t.id}`;
          return (
            <Link
              key={t.id}
              href={href}
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

function renderUnavailable(
  err: unknown,
  labels: { title: string; kicker?: string },
) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title={labels.title} kicker={labels.kicker}>
      <Card>
        <CardHeader
          title={`Couldn't load ${labels.title.toLowerCase()}`}
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
