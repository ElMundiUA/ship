/**
 * MIGRATED: /fleet/policy/new → /settings/policy/new per RFC-0010 P1-08.
 *
 * "New policy" form for the Workspace policy injection feature.
 *
 * Server wrapper resolves the workspace from the session bearer
 * (no per-policy permissions, member-level write); the client form
 * owns the title/body/sort/enabled fields and round-trips through
 * ``/api/policies``.
 */
import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { NewPolicyForm } from "./policy-form";

export const dynamic = "force-dynamic";

export default async function NewPolicyPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="New policy" kicker="settings">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to create workspace policies."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fsettings%2Fpolicy%2Fnew");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fsettings%2Fpolicy%2Fnew");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  return (
    <AppShell
      title="New policy"
      kicker="settings"
      actions={
        <Link
          href="/settings/policy"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Policies
        </Link>
      }
    >
      <NewPolicyForm workspaceId={workspace.id} />
    </AppShell>
  );
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="New policy" kicker="settings">
      <Card>
        <CardHeader
          title="Couldn't load the form"
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
