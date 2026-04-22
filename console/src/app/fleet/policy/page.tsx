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

/**
 * Fleet Policy — workspace-unique rules (RFC-0008 §G, PR-5).
 *
 * Mirror-lane MVP: "pattern X runs as lane Y nightly on every
 * activated repo unless explicitly excepted". The list view shows
 * a compliance rollup per policy; the client island underneath
 * handles per-repo opt-out toggles and policy deletion without a
 * full page reload.
 */

export const dynamic = "force-dynamic";

export default async function FleetPolicyPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Policy" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to wire policies."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Ffleet%2Fpolicy");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Fpolicy");
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
      redirect("/login?next=%2Ffleet%2Fpolicy");
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Policy"
      kicker="fleet"
      actions={
        <Link href="/fleet/policy/new">
          <ButtonPrimary>New policy</ButtonPrimary>
        </Link>
      }
    >
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Workspace-level rules enforced across all activated repos.
        Each mirror policy says "this pattern runs as this lane on
        every repo" — opt out per-repo for the ones that manage
        themselves. Autofix via Navigator lands in a follow-up.
      </p>

      {policies.length === 0 ? (
        <Card>
          <CardHeader
            title="No policies yet"
            subtitle="Create one to mirror a pattern across every activated repo."
          />
          <div className="mt-3">
            <Link href="/fleet/policy/new">
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
    <AppShell title="Policy" kicker="fleet">
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle={`The policies rollup is currently unavailable (${message}). Retry in a moment.`}
        />
      </Card>
    </AppShell>
  );
}
