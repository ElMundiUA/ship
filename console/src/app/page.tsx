import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
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
 *   - greenfield workspace (zero repos / zero runs) → onboarding
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

  const result = await loadLiveContext(token);
  if (result === "unauthorized") redirect("/login?next=%2F");
  if (result === "empty") redirect("/onboarding?step=github");
  if (result === "down") return renderDownState();

  const skipWizard = params.skipWizard === "1";
  if (!skipWizard && isGreenfieldWorkspace(result)) {
    redirect(
      `/onboarding?step=github&ws=${encodeURIComponent(result.workspace.id)}`,
    );
  }

  return renderWorkspaceHome(result);
}

type LiveContext = {
  workspace: ApiWorkspace;
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
): Promise<LiveContext | "empty" | "unauthorized" | "down"> {
  let list: ApiWorkspace[];
  try {
    list = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
  if (list.length === 0) return "empty";

  const workspace = list[0];
  try {
    const [data, repos] = await Promise.all([
      getOpsDashboard(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
    ]);
    return { workspace, data, repos };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
}

function renderWorkspaceHome(ctx: LiveContext) {
  const { workspace, data } = ctx;
  return (
    <AppShell
      title="Workspace home"
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      actions={
        <ButtonPrimary>
          <Link href="/inbox">Review actions →</Link>
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
