import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import { ButtonPrimary } from "@/components/ui";
import { WorkspaceEntryPicker } from "@/components/workspace-entry-picker";
import { WorkspaceHome } from "@/components/workspace-home";
import {
  type ApiActivatedRepo,
  type ApiOpsDashboard,
  ApiHttpError,
  ApiUnavailableError,
  getInboxCounts,
  getMe,
  getOpsDashboard,
  isApiConfigured,
  listActivatedRepos,
  listInboxItems,
  listWorkspaces,
} from "@/lib/api/client";
import type { InboxCountsResponse, InboxListResponse } from "@/lib/inbox-types";
import type { ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
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
    return renderDownState("SHIP_API_URL is not set on this deployment.");
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2F&reason=session_expired");

  // Check if user's email is pending (sentinel pattern from Auth0 email-less login)
  try {
    const user = await getMe(token);
    if (
      user.email.startsWith("pending+") &&
      user.email.endsWith("@no-email.local")
    ) {
      redirect("/complete-profile?next=%2F");
    }
  } catch {
    // Ignore errors fetching user profile; proceed to load workspaces
  }

  const skipWizard = params.skipWizard === "1";

  let list: ApiWorkspace[];
  try {
    list = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2F");
    }
    return renderDownState(
      err instanceof Error ? err.message : "Could not load workspaces.",
    );
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
  if (result === "unauthorized") redirect("/login?next=%2F&reason=session_expired");
  if (result === "down") return renderDownState();

  if (!skipWizard && isGreenfieldWorkspace(result)) {
    return renderGreenfieldWelcome(result);
  }

  return renderWorkspaceHome(result);
}

type LiveContext = {
  workspace: ApiWorkspace;
  allWorkspaces: ApiWorkspace[];
  data: ApiOpsDashboard;
  repos: ApiActivatedRepo[];
  /** Inbox preview for "What needs you" — top items + counts. Best-effort:
   *  if the call fails we degrade to empty arrays so the rest of the
   *  dashboard still renders. */
  inboxItems: InboxListResponse | null;
  inboxCounts: InboxCountsResponse | null;
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
    const [data, repos, inboxItems, inboxCounts] = await Promise.all([
      getOpsDashboard(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
      listInboxItems(workspace.id, { statuses: ["new"], limit: 5 }, token).catch(
        () => null as InboxListResponse | null,
      ),
      getInboxCounts(workspace.id, token).catch(
        () => null as InboxCountsResponse | null,
      ),
    ]);
    return { workspace, allWorkspaces: list, data, repos, inboxItems, inboxCounts };
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
      <WorkspaceHome
        summary={data}
        repos={ctx.repos}
        workspaceId={workspace.id}
        inboxItems={ctx.inboxItems}
        inboxCounts={ctx.inboxCounts}
      />
    </AppShell>
  );
}

function renderDownState(details?: string) {
  return (
    <AppShell title="Workspace home">
      <ApiUnavailable scope="workspace" details={details} />
    </AppShell>
  );
}

const SETUP_STEPS: { title: string; body: string }[] = [
  {
    title: "Connect GitHub",
    body: "Install the Ship GitHub App on the org or account that owns the repos.",
  },
  {
    title: "Activate a repo",
    body: "Pick a repository so Ship can land its bundle and watch the activity.",
  },
  {
    title: "Bind a tracker",
    body: "Connect Linear or use GitHub Issues so work has a record of intent.",
  },
  {
    title: "Set policies and seed knowledge",
    body: "Define what agents may do and the context they may use.",
  },
  {
    title: "Review the dashboard and Inbox",
    body: "Watch what runs, resolve decisions, keep evidence near the work.",
  },
];

function renderGreenfieldWelcome(ctx: LiveContext) {
  const { workspace, allWorkspaces } = ctx;
  const onboardingHref = `/onboarding?step=github&ws=${encodeURIComponent(workspace.id)}`;
  return (
    <AppShell
      title="Welcome"
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={toAppShellWorkspaces(allWorkspaces)}
      actions={
        <ButtonPrimary>
          <Link href={onboardingHref}>Continue setup →</Link>
        </ButtonPrimary>
      }
    >
      <div className="mx-auto max-w-3xl space-y-10">
        <header className="space-y-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
            Welcome to {workspace.name}
          </p>
          <h1 className="font-display text-3xl font-bold leading-tight text-white">
            Five steps to make the workspace earn its keep.
          </h1>
          <p className="text-sm text-white/55">
            You can leave and come back any time — your progress persists.
          </p>
        </header>
        <ol className="space-y-6">
          {SETUP_STEPS.map((step, idx) => (
            <li key={step.title} className="flex gap-5">
              <span className="font-display text-2xl font-bold text-aqua tabular-nums">
                {idx + 1}
              </span>
              <div>
                <p className="text-base font-semibold text-white">{step.title}</p>
                <p className="mt-1 text-sm leading-relaxed text-white/55">
                  {step.body}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </div>
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

