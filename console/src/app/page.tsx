import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import { WorkspaceHome } from "@/components/workspace-home";
import {
  type ApiActivatedRepo,
  type ApiDashboard,
  ApiHttpError,
  ApiUnavailableError,
  getDashboard,
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
  data: ApiDashboard;
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
  const c = ctx.data.counts;
  return (
    c.active_repos === 0 &&
    c.enabled_pipelines === 0 &&
    c.open_pull_requests === 0 &&
    c.runs_last_24h === 0 &&
    ctx.repos.length === 0
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
      getDashboard(workspace.id, token),
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
  const { workspace, data, repos } = ctx;
  return (
    <AppShell
      title="Workspace home"
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      actions={
        <>
          <Link
            href="/settings"
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            CLI tokens
          </Link>
          <Link
            href="/integrations"
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            Integrations
          </Link>
          <ButtonPrimary>
            <Link
              href={`/onboarding?step=repos&ws=${encodeURIComponent(workspace.id)}`}
            >
              Pick more repos →
            </Link>
          </ButtonPrimary>
        </>
      }
    >
      <WorkspaceHome workspace={workspace} repos={repos} summary={data} />
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
  const mockWorkspace = {
    id: "mock-ws",
    name: ws.name,
    slug: ws.org,
  } as unknown as ApiWorkspace;
  const mockRepos: ApiActivatedRepo[] = [
    "helio/api",
    "helio/web",
    "helio/payments",
  ].map(
    (full_name, i) =>
      ({
        id: `mock-${i}`,
        external_id: i,
        full_name,
        default_branch: "main",
        private: i > 0,
        html_url: `https://github.com/${full_name}`,
        description: null,
        activated_at: null,
        provider: "github",
        preset: i === 0 ? "api-backend" : i === 1 ? "web-app" : null,
        installed_bundle_version: i === 2 ? null : 4,
        current_bundle_version: 5,
      }) as ApiActivatedRepo,
  );
  return (
    <AppShell
      title="Workspace home"
      kicker={ws.org}
      actions={
        <>
          <ButtonGhost>Export digest</ButtonGhost>
          <ButtonPrimary>+ Trigger lane</ButtonPrimary>
        </>
      }
    >
      <MockBanner />
      <WorkspaceHome workspace={mockWorkspace} repos={mockRepos} summary={null} />
    </AppShell>
  );
}
