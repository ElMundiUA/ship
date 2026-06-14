import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppShell, AppShellChrome } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import { ConnectAgentCard } from "@/components/connect-agent-card";
import { inboxItemUrl } from "@/components/inbox/inbox-url";
import { WorkspaceEntryPicker } from "@/components/workspace-entry-picker";
import {
  type ApiActivatedRepo,
  ApiHttpError,
  apiFetch,
  getInboxCounts,
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listInboxItems,
  listWorkspaces,
} from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import {
  INBOX_ACTIONABLE_CATEGORIES,
  INBOX_LIST_DEFAULT_STATUSES,
  type InboxListResponse,
} from "@/lib/inbox-types";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  pickWorkspace,
  toAppShellWorkspaces,
} from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | string[] | undefined };

/**
 * Workspace home (``/``) — the OPERATOR HUB (ELS-287, MCP-first rework).
 *
 * One screen answering three questions: is the engine alive (thesis-2
 * residue via /engine-health), is anything waiting on me ("Waiting on
 * you" strip → per-item ``/approve/{id}`` deep-links), and how do I
 * connect my agent (the Connect-your-agent card — the new front door:
 * mint a PAT, copy the ``claude mcp add`` command). Domain views live
 * in Linear (tickets/projects) and GitHub (PRs/CI); the console never
 * grows back dashboards.
 *
 * Auth + onboarding flow preserved from the old dashboard:
 *   - no workspace yet → ``/onboarding?step=github``
 *   - several workspaces and no ``?ws=`` → entry picker
 *   - zero activated repos → onboarding (``?skipWizard=1`` escape hatch)
 */
export default async function WorkspaceStatusPage({
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

  try {
    const user = await getMe(token);
    if (
      user.email.startsWith("pending+") &&
      user.email.endsWith("@no-email.local")
    ) {
      redirect("/complete-profile?next=%2F");
    }
  } catch {
    // Ignore profile fetch errors; proceed to load workspaces.
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
  const workspace = pickWorkspace(list, wsParam);

  // Hub data: engine health (the thesis-2 residue), the two pivot
  // config scopes, repos for GitHub deep links, and the "Waiting on
  // you" strip (actionable count + top 3 → /approve/{id} links).
  // Everything best-effort — a failed card never blanks the page.
  const [health, autonomy, surface, repos, inboxCounts, waiting] =
    await Promise.all([
      apiFetch<EngineHealth>(
        `/v1/workspaces/${workspace.id}/engine-health`,
        { token },
      ).catch(() => null),
      fetchScopeValue(workspace.id, "autonomy.profile", token),
      fetchScopeValue(workspace.id, "console.surface", token),
      listActivatedRepos(workspace.id, token).catch(
        () => [] as ApiActivatedRepo[],
      ),
      getInboxCounts(workspace.id, token).catch(() => null),
      listInboxItems(
        workspace.id,
        {
          categories: INBOX_ACTIONABLE_CATEGORIES,
          statuses: INBOX_LIST_DEFAULT_STATUSES,
          sort: "priority_desc_created_asc",
          limit: 3,
        },
        token,
      ).catch(() => null as InboxListResponse | null),
    ]);

  if (repos.length === 0 && !skipWizard) {
    redirect("/onboarding?step=github");
  }

  const wsScope = list.length > 1 ? workspace.id : undefined;
  const mcpEndpoint =
    process.env.NEXT_PUBLIC_SHIP_MCP_URL?.trim() ||
    "https://api.ship.elmundi.com/mcp";

  return (
    <AppShellChrome
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      allWorkspaces={toAppShellWorkspaces(list)}
    >
      <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
        <header>
          <h1 className="font-display text-xl font-bold text-white">
            {workspace.name}
          </h1>
          <p className="mt-1 text-sm text-white/55">
            Operator hub — the work itself lives in your agent, Linear and
            GitHub.
          </p>
        </header>

        <ConnectAgentCard workspaceId={workspace.id} mcpEndpoint={mcpEndpoint} />

        <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          <h2 className="text-[11px] font-bold uppercase tracking-widest text-white/55">
            Engine
          </h2>
          {health === null ? (
            <p className="mt-2 text-sm text-white/55">
              Health unavailable (older backend or transient error).
            </p>
          ) : (
            <div className="mt-2 space-y-1 text-sm">
              <p className={health.healthy ? "text-aqua" : "text-coral"}>
                {health.healthy
                  ? "Healthy — no stalled work"
                  : `Stalled: ${health.stalled.length} lock(s) need attention`}
              </p>
              <p className="text-white/55">
                Last dispatch:{" "}
                {health.last_dispatch_at
                  ? new Date(health.last_dispatch_at).toLocaleString()
                  : "never"}{" "}
                · active locks: {health.active_locks} · expired unswept:{" "}
                {health.expired_unswept_locks}
              </p>
              {health.stalled.slice(0, 5).map((s) => (
                <p key={s.lock_key} className="text-xs text-coral/80">
                  {s.lock_key} — {s.reason} ({Math.round(s.age_minutes)}m)
                </p>
              ))}
            </div>
          )}
          <p className="mt-3 text-xs text-white/45">
            Autonomy: <b className="text-white/70">{autonomy ?? "balanced"}</b>{" "}
            · Console surface:{" "}
            <b className="text-white/70">{surface ?? "full"}</b> — change both
            in{" "}
            <Link href="/settings/config" className="text-aqua hover:underline">
              Settings → Config
            </Link>
          </p>
        </section>

        <section
          className="rounded-2xl border border-white/10 bg-white/[0.03] p-5"
          data-testid="waiting-on-you"
        >
          <h2 className="text-[11px] font-bold uppercase tracking-widest text-white/55">
            Waiting on you
            {inboxCounts?.actionable_new ? ` · ${inboxCounts.actionable_new}` : ""}
          </h2>
          {waiting === null ? (
            <p className="mt-2 text-sm text-white/55">
              Couldn&apos;t load the queue right now.
            </p>
          ) : waiting.items.length === 0 ? (
            <p className="mt-2 text-sm text-white/55">
              Nothing waiting — agents working. Approvals and questions land
              in your agent over MCP; items that need this browser show up
              here.
            </p>
          ) : (
            <ul className="mt-2 space-y-2 text-sm">
              {waiting.items.map((item) => (
                <li key={item.id} className="min-w-0">
                  <Link
                    href={inboxItemUrl(item.id, wsScope)}
                    className="text-aqua hover:underline"
                  >
                    {item.title || "(untitled)"} →
                  </Link>{" "}
                  <span className="text-white/45">{item.type}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          <h2 className="text-[11px] font-bold uppercase tracking-widest text-white/55">
            Where the work lives
          </h2>
          <ul className="mt-2 space-y-2 text-sm">
            <li>
              <a
                href="https://linear.app"
                target="_blank"
                rel="noreferrer"
                className="text-aqua hover:underline"
              >
                Linear →
              </a>{" "}
              <span className="text-white/45">
                tickets, projects, agent comments
              </span>
            </li>
            {repos.map((r) => (
              <li key={r.id}>
                <a
                  href={r.html_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-aqua hover:underline"
                >
                  {r.full_name} →
                </a>{" "}
                <span className="text-white/45">PRs, CI, agent branches</span>
              </li>
            ))}
          </ul>
        </section>
        {/* No Chat/Settings CTA row here — the left rail already carries
            both persistently; a second copy on the hub body was a
            duplicate (ELS-301). */}
      </div>
    </AppShellChrome>
  );
}

type EngineHealth = {
  healthy: boolean;
  last_dispatch_at: string | null;
  last_finish_at: string | null;
  active_locks: number;
  expired_unswept_locks: number;
  stalled: {
    lock_key: string;
    reason: string;
    age_minutes: number;
  }[];
};

async function fetchScopeValue(
  workspaceId: string,
  scope: string,
  token: string,
): Promise<string | null> {
  try {
    const detail = await apiFetch<{ current_value?: unknown }>(
      `/v1/workspaces/${workspaceId}/config/${scope}`,
      { token },
    );
    return typeof detail.current_value === "string"
      ? detail.current_value
      : null;
  } catch {
    return null;
  }
}

function renderDownState(details?: string) {
  return (
    <AppShell title="Workspace home">
      <ApiUnavailable scope="workspace" details={details} />
    </AppShell>
  );
}

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
      <main className="px-6 py-10 lg:px-8">{children}</main>
    </div>
  );
}
