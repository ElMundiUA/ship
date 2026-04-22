import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ActiveCalendar } from "@/app/lanes/active-calendar";
import { LibraryCatalog } from "@/app/lanes/library-catalog";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiLane,
  type ApiLaneCatalogEntry,
  type ApiRepoConfig,
  ApiHttpError,
  ApiUnavailableError,
  getRepoConfig,
  isApiConfigured,
  listLaneCatalog,
  listLanes,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext, type RepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";

/**
 * Repo-mode Lanes (``/r/<owner>/<repo>/lanes``).
 *
 * Mirrors the workspace ``/lanes`` page surface-for-surface but
 * ``listLanes`` is pre-filtered to ``repo_id`` and the Library tab
 * hides its repo-switcher (repo is locked by the URL). Banners,
 * ``?tab=new`` redirect, and ``?reason=/?changed=`` copy come over
 * verbatim so deep links stay functional.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;
type TabId = "active" | "library";

const TAB_META: { id: TabId; label: string; hint: string }[] = [
  {
    id: "active",
    label: "Active",
    hint: "Calendar of scheduled lanes + event-driven triggers for this repo.",
  },
  {
    id: "library",
    label: "Library",
    hint: "Catalog of lane recipes you can add to this repo's .ship/config.yml.",
  },
];

function pickTab(params: SearchParamsBag): TabId {
  const raw = typeof params.tab === "string" ? params.tab : null;
  if (raw === "library") return "library";
  return "active";
}

export default async function RepoLanesPage({
  params,
  searchParams,
}: {
  params: Promise<RepoRouteParams>;
  searchParams?: Promise<SearchParamsBag>;
}) {
  const [resolved, sp] = await Promise.all([
    params,
    searchParams ?? Promise.resolve({} as SearchParamsBag),
  ]);
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const basePath = `/r/${slug}/lanes`;

  if (typeof sp.tab === "string" && sp.tab === "new") {
    redirect(`${basePath}?tab=library`);
  }
  const tab = pickTab(sp);

  if (!isApiConfigured()) {
    return (
      <AppShell title="Lanes" kicker={`${slug} · repo`}>
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
  if (!token) redirect(`/login?next=${encodeURIComponent(basePath)}`);

  const result = await resolveRepoContext(token, slug);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(basePath)}`);
  }
  if (result.kind === "down") return renderUnavailable();
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  const ctx = result.ctx;

  let lanes: ApiLane[] = [];
  let catalog: ApiLaneCatalogEntry[] = [];
  try {
    [lanes, catalog] = await Promise.all([
      listLanes(ctx.workspace.id, { token, repoId: ctx.repo.id }),
      listLaneCatalog(token).catch(() => [] as ApiLaneCatalogEntry[]),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(basePath)}`);
    }
    return renderUnavailable(err);
  }

  const banner = pickBanner(sp);

  let libraryConfig: ApiRepoConfig | null = null;
  let libraryConfigError: string | null = null;
  if (tab === "library") {
    try {
      libraryConfig = await getRepoConfig(ctx.workspace.id, ctx.repo.id, token);
    } catch (err) {
      if (err instanceof ApiHttpError) {
        libraryConfigError = `Couldn't load .ship/config.yml (HTTP ${err.status}).`;
      } else {
        libraryConfigError = "Couldn't load .ship/config.yml.";
      }
    }
  }

  const initialOpenKind = typeof sp.open === "string" ? sp.open : null;
  const repoLockedList = [ctx.repo];

  return renderShell(ctx, tab, basePath, banner, (
    <>
      {tab === "active" ? (
        <ActiveCalendar
          workspaceId={ctx.workspace.id}
          lanes={lanes}
          repos={repoLockedList}
          catalog={catalog}
          basePath={basePath}
        />
      ) : null}

      {tab === "library" ? (
        <LibraryCatalog
          workspaceId={ctx.workspace.id}
          selectedRepo={ctx.repo}
          repos={repoLockedList}
          catalog={catalog}
          lanes={lanes}
          config={libraryConfig}
          configError={libraryConfigError}
          initialOpenKind={initialOpenKind}
        />
      ) : null}
    </>
  ));
}

function renderShell(
  ctx: RepoContext,
  tab: TabId,
  basePath: string,
  banner: string | null,
  body: React.ReactNode,
) {
  const { workspace, repo, repos } = ctx;
  return (
    <AppShell
      title="Lanes"
      kicker={`${repo.full_name} · repo`}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repo.id,
      }}
      actions={
        <Link
          href={`/r/${repo.full_name}/requests`}
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          Requests →
        </Link>
      }
    >
      <TabStrip current={tab} basePath={basePath} />

      {banner ? (
        <Card className="mb-6 border-white/10">
          <p className="text-xs text-white/75">{banner}</p>
        </Card>
      ) : null}

      {body}
    </AppShell>
  );
}

function TabStrip({
  current,
  basePath,
}: {
  current: TabId;
  basePath: string;
}) {
  return (
    <div className="mb-5">
      <div className="mb-2 flex flex-wrap items-end gap-1 border-b border-white/10">
        {TAB_META.map((t) => {
          const active = t.id === current;
          return (
            <Link
              key={t.id}
              href={t.id === "active" ? basePath : `${basePath}?tab=${t.id}`}
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
      return "This repo has no .ship/config.yml on its default branch yet.";
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

function renderUnavailable(err?: unknown) {
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
