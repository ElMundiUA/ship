import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiCatalogPattern,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listCatalogPatterns,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { NewFleetLaneForm } from "./fleet-lane-form";

/**
 * "New Fleet lane" form (RFC-0008 §G, mirror-lane MVP).
 *
 * Server wrapper loads the workspace + the lane-mode catalog
 * (filtered to lane-capable patterns) so the client form can focus
 * on the pattern picker + cadence fields. Input defaults are
 * applied server-side after creation, so this page stays thin.
 */

export const dynamic = "force-dynamic";

export default async function NewFleetLanePage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="New Fleet lane" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to create Fleet lanes."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Ffleet%2Flanes%2Fnew");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Flanes%2Fnew");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let patterns: ApiCatalogPattern[] = [];
  try {
    patterns = await listCatalogPatterns({
      mode: "lane",
      workspaceId: workspace.id,
      token,
    });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Flanes%2Fnew");
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="New Fleet lane"
      kicker="fleet"
      actions={
        <Link
          href="/fleet/lanes"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Fleet lanes
        </Link>
      }
    >
      <NewFleetLaneForm workspaceId={workspace.id} patterns={patterns} />
    </AppShell>
  );
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="New Fleet lane" kicker="fleet">
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
