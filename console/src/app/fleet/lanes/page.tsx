import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ButtonPrimary, Card, CardHeader } from "@/components/ui";
import {
  type ApiFleetLane,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listFleetLanes,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { FleetLanesList } from "./fleet-lanes-list";

/**
 * Fleet lanes — workspace-unique mirror-lane rules (RFC-0008 §G, PR-5).
 *
 * Mirror-lane MVP: "pattern X runs as lane Y nightly on every
 * activated repo unless explicitly excepted". The list view shows
 * a compliance rollup per Fleet lane; the client island underneath
 * handles per-repo opt-out toggles and Fleet-lane deletion without
 * a full page reload.
 *
 * Naming history: this section used to be ``/fleet/policy``. The
 * "Policy" name was repurposed for free-text standing rules
 * injected into agent instructions, so the mirror-lane primitive
 * moved here.
 */

export const dynamic = "force-dynamic";

export default async function FleetLanesPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Fleet lanes" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to wire Fleet lanes."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Ffleet%2Flanes");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Flanes");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let fleetLanes: ApiFleetLane[];
  try {
    fleetLanes = await listFleetLanes(workspace.id, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Flanes");
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Fleet lanes"
      kicker="fleet"
      actions={
        <Link href="/fleet/lanes/new">
          <ButtonPrimary>New Fleet lane</ButtonPrimary>
        </Link>
      }
    >
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Workspace-level rules enforced across all activated repos.
        Each Fleet lane says &ldquo;this pattern runs as this lane on
        every repo&rdquo; — opt out per-repo for the ones that manage
        themselves. Autofix via Navigator lands in a follow-up.
      </p>

      {fleetLanes.length === 0 ? (
        <Card>
          <CardHeader
            title="No Fleet lanes yet"
            subtitle="Create one to mirror a pattern across every activated repo."
          />
          <div className="mt-3">
            <Link href="/fleet/lanes/new">
              <ButtonPrimary>New Fleet lane</ButtonPrimary>
            </Link>
          </div>
        </Card>
      ) : (
        <FleetLanesList workspaceId={workspace.id} fleetLanes={fleetLanes} />
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
    <AppShell title="Fleet lanes" kicker="fleet">
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle={`The Fleet lanes rollup is currently unavailable (${message}). Retry in a moment.`}
        />
      </Card>
    </AppShell>
  );
}
