/**
 * `/settings/agent-roles` — workspace specialist prompt registry.
 *
 * Two-tier model (Phase 2.4):
 *
 * - **Ship defaults** ship as files under
 *   ``backend/app/resources/agent_roles/`` and stay read-only.
 * - **Workspace rows** live in the ``agent_roles`` table. Two
 *   flavours: an *override* (slug shadows a Ship default) or a
 *   *clone* (custom slug + ``base_role_slug`` pointer).
 *
 * Server component: load both lists, hand off to the island for
 * editing, cloning, override, and revert.
 */

import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiAgentRole,
  type ApiAgentRoleDefault,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listShipAgentRoleDefaults,
  listWorkspaceAgentRoles,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  pickWorkspace,
  toAppShellWorkspaces,
} from "@/lib/workspace-scope";

import { AgentRolesList } from "./agent-roles-list";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function AgentRolesPage({
  searchParams,
}: {
  searchParams?: SearchParams;
}) {
  const params = (await (searchParams ?? Promise.resolve({}))) ?? {};
  if (!isApiConfigured()) {
    return (
      <AppShell title="Agent roles" kicker="settings">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to manage workspace agent roles."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fsettings%2Fagent-roles");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fsettings%2Fagent-roles");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const resolved = await getResolvedWorkspaceId(params, workspaces);
  const workspace = pickWorkspace(workspaces, resolved);

  let defaults: ApiAgentRoleDefault[];
  let customs: ApiAgentRole[];
  try {
    [defaults, customs] = await Promise.all([
      listShipAgentRoleDefaults(token),
      listWorkspaceAgentRoles(workspace.id, token),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fsettings%2Fagent-roles");
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Agent roles"
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={toAppShellWorkspaces(workspaces)}
    >
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Specialist prompts agents load when a routine fires. Ship
        ships read-only defaults; you can override one in this
        workspace, or clone it under a new slug to author a custom
        variant. Most teams never touch this — defaults work.
      </p>

      <AgentRolesList
        workspaceId={workspace.id}
        defaults={defaults}
        customs={customs}
      />
    </AppShell>
  );
}

function renderUnavailable(err: unknown) {
  const message =
    err instanceof ApiUnavailableError
      ? "Backend is unreachable right now. Try again in a moment."
      : err instanceof ApiHttpError
        ? `Couldn't load agent roles (HTTP ${err.status}).`
        : "Couldn't load agent roles.";
  return (
    <AppShell title="Agent roles" kicker="settings">
      <Card>
        <CardHeader title="Agent roles unavailable" subtitle={message} />
      </Card>
    </AppShell>
  );
}
