import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiCatalogPattern,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listCatalogPatterns,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { FleetRequestForm } from "./fleet-request-form";

/**
 * "New fleet request" form (RFC-0008 §D).
 *
 * Reuses the same catalog (``?mode=request``) the per-repo
 * ``/requests`` page uses so operators don't context-switch between
 * two pattern lists. The form's innovation is the repo multi-select:
 * inputs are entered once and fanned out across the checked repos.
 */

export const dynamic = "force-dynamic";

export default async function NewFleetRequestPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="New fleet request" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to dispatch fleet requests."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Ffleet%2Frequests%2Fnew");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Frequests%2Fnew");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  let repos: ApiActivatedRepo[] = [];
  let patterns: ApiCatalogPattern[] = [];
  try {
    [repos, patterns] = await Promise.all([
      listActivatedRepos(workspace.id, token).catch(
        () => [] as ApiActivatedRepo[],
      ),
      listCatalogPatterns({
        mode: "request",
        workspaceId: workspace.id,
        token,
      }).catch(() => [] as ApiCatalogPattern[]),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Frequests%2Fnew");
    }
    return renderUnavailable(err);
  }

  const sortedRepos = [...repos].sort((a, b) =>
    a.full_name.localeCompare(b.full_name),
  );

  return (
    <AppShell
      title="New fleet request"
      kicker="fleet"
      actions={
        <Link
          href="/fleet/requests"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Fleet requests
        </Link>
      }
    >
      <FleetRequestForm
        workspaceId={workspace.id}
        repos={sortedRepos}
        patterns={patterns}
      />
    </AppShell>
  );
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="New fleet request" kicker="fleet">
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
