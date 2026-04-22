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

import { NewPolicyForm } from "./policy-form";

/**
 * "New policy" form (RFC-0008 §G, mirror-lane MVP).
 *
 * Server wrapper loads the workspace + the request-mode catalog
 * (filtered to lane-capable patterns) so the client form can focus
 * on the pattern picker + cadence fields. Input defaults are
 * applied server-side after creation, so this page stays thin.
 */

export const dynamic = "force-dynamic";

export default async function NewPolicyPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="New policy" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to create policies."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Ffleet%2Fpolicy%2Fnew");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Fpolicy%2Fnew");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let patterns: ApiCatalogPattern[] = [];
  try {
    patterns = await listCatalogPatterns({ mode: "lane", token });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Fpolicy%2Fnew");
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="New policy"
      kicker="fleet"
      actions={
        <Link
          href="/fleet/policy"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Policies
        </Link>
      }
    >
      <NewPolicyForm workspaceId={workspace.id} patterns={patterns} />
    </AppShell>
  );
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="New policy" kicker="fleet">
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
