import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import {
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import { WorkspaceEntryPicker } from "@/components/workspace-entry-picker";
import { WorkspaceHome } from "@/components/workspace-home";
import {
  type ApiActivatedRepo,
  type ApiOpsDashboard,
  ApiHttpError,
  ApiUnavailableError,
  getOpsDashboard,
  isApiConfigured,
  listActivatedRepos,
  listWorkspaces,
} from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import { workspaces as mockWorkspaces } from "@/lib/mock/cloud";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  pickWorkspace,
  toAppShellWorkspaces,
  withWorkspaceQuery,
} from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | string[] | undefined };

/**
 * Workspace home (``/``).
 *
 * Phase-1 two-mode shell: ``/`` is **workspace mode** — a landing
 * for workspace-unique primitives (Fleet Requests · Policy ·
 * Adoption · Knowledge graph) and a channel-list view of the
 * activated repos. Per-repo dashboards moved to
 * ``/r/<owner>/<repo>`` and land with real content in PR-4.
 *
 * Auth + onboarding flow is preserved from the old operating
 * dashboard:
 *   - no session → ``/login?next=/``
 *   - no workspace yet → ``/onboarding?step=github``
 *   - more than one workspace and no URL ``?ws=`` or persisted pick → choose
 *     a workspace (avoids sending invitees into the personal JIT shell / default)
 *   - greenfield for the *selected* workspace (zero repos / zero runs) → onboarding
 *   - ``?skipWizard=1`` escape hatch for returning operators.
 */
export default async function CloudHomePage({
  searchParams,
}: {
  searchParams?: Promise<SearchParams>;
}) {
  const params = (await searchParams) ?? {};

  if (!isApiConfigured()) {
    return renderMock();
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2F");

  const skipWizard = params.skipWizard === "1";

  let list: ApiWorkspace[];
  try {
    list = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2F");
    }
    if (err instanceof ApiUnavailableError) {
      return renderDownState();
    }
    return renderDownState();
  }
  if (list.length === 0) {
    redirect("/onboarding?step=github");
  }

  const wsParam = await getResolvedWorkspaceId(params, list);
  if (list.length > 1 && !wsParam) {
    return (
      <WorkspaceGateLayout>
        <WorkspaceEntryPicker workspaces={list} skipWizard={skipWizard} />
      </WorkspaceGateLayout>
    );
  }

  const result = await loadLiveContext(token, list, wsParam);
  if (result === "unauthorized") redirect("/login?next=%2F");
  if (result === "down") return renderDownState();

  if (!skipWizard && isGreenfieldWorkspace(result)) {
    redirect(
      `/onboarding?step=github&ws=${encodeURIComponent(result.workspace.id)}`,
    );
  }

  return renderWorkspaceHome(result);
}

type LiveContext = {
  workspace: ApiWorkspace;
  allWorkspaces: ApiWorkspace[];
  data: ApiOpsDashboard;
  repos: ApiActivatedRepo[];
};

/**
 * A workspace is "greenfield" when the operator has signed in but the
 * backend hasn't seen any wiring yet: zero activated repos, zero
 * pipelines, zero PRs, zero lane runs. Auth0 JIT creates the shell
 * org/workspace on first login, so we can't rely on
 * ``workspaces.length === 0`` anymore to detect "hasn't started setup".
 */
function isGreenfieldWorkspace(ctx: LiveContext): boolean {
  return (
    ctx.repos.length === 0 &&
    ctx.data.system_status.overall_status === "ok" &&
    ctx.data.blockers.length === 0 &&
    ctx.data.work_in_progress.length === 0 &&
    ctx.data.shipped.features_shipped_count === 0 &&
    ctx.data.shipped.fixes_count === 0 &&
    ctx.data.shipped.rollbacks_count === 0
  );
}

async function loadLiveContext(
  token: string,
  list: ApiWorkspace[],
  wsParam: string | undefined,
): Promise<LiveContext | "unauthorized" | "down"> {
  const workspace = pickWorkspace(list, wsParam);
  try {
    const [data, repos] = await Promise.all([
      getOpsDashboard(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
    ]);
    return { workspace, allWorkspaces: list, data, repos };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
}

function renderWorkspaceHome(ctx: LiveContext) {
  const { workspace, data, allWorkspaces } = ctx;
  const multi = allWorkspaces.length > 1;
  return (
    <AppShell
      title="Workspace home"
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={toAppShellWorkspaces(allWorkspaces)}
      actions={
        <ButtonPrimary>
          <Link
            href={withWorkspaceQuery("/inbox", workspace.id, multi)}
          >
            Review actions →
          </Link>
        </ButtonPrimary>
      }
    >
      <WorkspaceHome summary={data} repos={ctx.repos} workspaceId={workspace.id} />
    </AppShell>
  );
}

function renderDownState() {
  return (
    <AppShell title="Workspace home">
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle="The workspace home couldn't load live data."
        />
        <p className="text-sm text-white/70">
          Try again in a few seconds. If this keeps happening, check the
          backend service in your hosting console.
        </p>
      </Card>
    </AppShell>
  );
}

function renderMock() {
  const ws = mockWorkspaces[0];
  return (
    <AppShell
      title="Workspace home"
      kicker={ws.org}
      actions={
        <ButtonPrimary>
          <Link href="/inbox">Review actions →</Link>
        </ButtonPrimary>
      }
    >
      <MockBanner />
      <WorkspaceHome summary={mockOpsDashboard} repos={[]} workspaceId="mock-workspace" />
    </AppShell>
  );
}

/**
 * Strips the main sidebar on purpose: while no ``?ws=`` is set, the default
 * nav would send multi-workspace users to surfaces that still resolve
 * ``list[0]`` and defeat the chooser.
 */
function WorkspaceGateLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-ink text-mist">
      <header className="border-b border-white/10 bg-ink/80 px-6 py-4 lg:px-8">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <span className="font-display text-base font-bold tracking-tight text-white">
            Ship<span className="text-aqua">.</span>
            <span className="ml-1 rounded-md border border-aqua/40 bg-aqua/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua/90">
              cloud
            </span>
          </span>
          <form action="/logout" method="POST">
            <button
              type="submit"
              className="text-[11px] font-semibold text-white/45 hover:text-white"
            >
              Sign out
            </button>
          </form>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl px-6 py-10 lg:px-8">
        <h1 className="font-display text-xl font-bold text-white sm:text-2xl">
          Open a workspace
        </h1>
        <p className="mt-1 text-sm text-white/50">
          Choose which organization to load before the dashboard.
        </p>
        {children}
      </main>
    </div>
  );
}

const mockOpsDashboard: ApiOpsDashboard = {
  system_status: {
    overall_status: "degraded",
    failing_pipelines_count: 1,
    stuck_prs_count: 0,
    broken_automations_count: 1,
    last_deploy: null,
  },
  blockers: [],
  work_in_progress: [
    {
      name: "ENG-2042: Add billing webhook retry",
      status: "review",
      repo: "helio/payments",
      scope: null,
      updated_at: new Date().toISOString(),
      blocker_ref: null,
      href: "https://github.com/helio/payments/pull/42",
      ticket_ref: "ENG-2042",
      tracker: "Linear",
      board_column: "In review",
      active_agent: null,
      pull_request: {
        number: 42,
        href: "https://github.com/helio/payments/pull/42",
      },
    },
    {
      name: "Update onboarding seed bundle",
      status: "in_progress",
      repo: "helio/web",
      scope: "feature",
      updated_at: new Date().toISOString(),
      blocker_ref: null,
      href: "https://github.com/helio/web/pull/12",
      ticket_ref: null,
      tracker: "Linear",
      board_column: "Draft",
      pull_request: {
        number: 12,
        href: "https://github.com/helio/web/pull/12",
      },
    },
  ],
  shipped: {
    features_shipped_count: 2,
    fixes_count: 1,
    rollbacks_count: 0,
    items: [
      {
        name: "PR #39: Fix checkout retries",
        type: "fix",
        repo: "helio/payments",
        href: null,
      },
    ],
  },
  bottlenecks: [],
  automation_health: {
    automation_coverage: null,
    success_rate: 0.82,
    manual_interventions_count: 3,
    failures_count: 1,
  },
  suggested_actions: [],
};
