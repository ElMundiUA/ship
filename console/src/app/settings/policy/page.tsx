/**
 * MIGRATED: /fleet/policy → /settings/policy per RFC-0010 P1-08.
 *
 * Workspace policies — prose standing rules (Workspace policy
 * injection). Plain markdown rules ("Always work via PR", "Never
 * commit secrets") that the backend prepends to the agent system
 * prompt at runtime — both in Navigator chat
 * (``TopicService.assemble_messages``) and in ``shipctl run``
 * stdout (``cli/lib/commands/run.mjs``).
 *
 * The list view is a server component; per-row enable/disable,
 * inline edit and delete live in the client island below.
 *
 * Naming history: ``/fleet/policy`` previously hosted what is now
 * ``/fleet/lanes`` (mirror-lane rules). The "Policy" name was
 * repurposed for the prose-rule injection feature, then moved
 * under Settings as part of the Plays/Inbox redesign.
 */
import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ButtonPrimary, Card, CardHeader } from "@/components/ui";
import {
  type ApiPolicy,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listPolicies,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { PoliciesList } from "./policies-list";

export const dynamic = "force-dynamic";

export default async function PoliciesPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Policy" kicker="settings">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to manage workspace policies."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fsettings%2Fpolicy");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fsettings%2Fpolicy");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let policies: ApiPolicy[];
  try {
    policies = await listPolicies(workspace.id, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fsettings%2Fpolicy");
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Policy"
      kicker="settings"
      actions={
        <Link href="/settings/policy/new">
          <ButtonPrimary>New policy</ButtonPrimary>
        </Link>
      }
    >
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Standing rules injected into the agent system prompt for
        every Navigator chat and every <code>shipctl run</code> in
        this workspace. Use them for invariants like &ldquo;Always
        work via PR&rdquo; or &ldquo;Never commit secrets&rdquo;.
        Disabled rules are kept around but not rendered into the
        prompt.
      </p>

      {policies.length === 0 ? (
        <Card>
          <CardHeader
            title="No policies yet"
            subtitle="Add one to enforce a standing rule across every agent run."
          />
          <div className="mt-3">
            <Link href="/settings/policy/new">
              <ButtonPrimary>New policy</ButtonPrimary>
            </Link>
          </div>
        </Card>
      ) : (
        <PoliciesList workspaceId={workspace.id} policies={policies} />
      )}
    </AppShell>
  );
}

function renderUnavailable(err: unknown) {
  const message =
    err instanceof ApiUnavailableError
      ? err.message
      : err instanceof Error
        ? err.message
        : "Unknown error";
  return (
    <AppShell title="Policy" kicker="settings">
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle={`Workspace policies are currently unavailable (${message}). Retry in a moment.`}
        />
      </Card>
    </AppShell>
  );
}
