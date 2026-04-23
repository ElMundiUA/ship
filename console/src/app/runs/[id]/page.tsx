/**
 * ``/runs/[id]`` — outcome-first run detail surface (RFC-0010
 * Wave 6 / Phase 3 ticket P3-05).
 *
 * Server component that owns auth + data fetching and hands off to
 * the presentational :func:`RunDetail` view. Two parallel fetches:
 *
 *   1. {@link getRunDetail}        — primary; the run row + parent
 *      pipeline. Resolves the parent ``pipelineId`` server-side
 *      because the legacy GET endpoint is keyed by
 *      ``(workspaceId, pipelineId, runId)``.
 *   2. {@link listRunEscalations}  — secondary; the joined
 *      ``run_escalations`` rows. Endpoint isn't shipped yet
 *      (TODO P3-05-BE) — the client returns ``[]`` on 404 so the
 *      detail page degrades gracefully.
 *
 * If escalations error out we still render the rest of the page
 * (the page-level error banner just notes the partial state).
 * Same applies to escalations endpoint not yet existing — the page
 * keeps reading correctly without it.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiRunEscalation,
  ApiHttpError,
  ApiUnavailableError,
  getRunDetail,
  isApiConfigured,
  listRunEscalations,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { RunDetail } from "./run-detail";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ ws?: string }>;
};

export default async function RunDetailPage({
  params,
  searchParams,
}: PageProps) {
  const { id: runId } = await params;
  const { ws: wsQuery } = await searchParams;
  const loginNext = encodeURIComponent("/runs");

  if (!isApiConfigured()) {
    return (
      <AppShell title="Run" kicker="RUN">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load run details."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect(`/login?next=${loginNext}`);

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${loginNext}`);
    }
    return renderError(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace =
    (wsQuery ? workspaces.find((w) => w.id === wsQuery) : undefined) ??
    workspaces[0];
  if (!workspace) redirect("/onboarding?step=github");

  // Two fetches in parallel — see file-level docstring. Each catches
  // independently so a hiccup on one doesn't blow up the other.
  const [runResult, escalationsResult] = await Promise.allSettled([
    getRunDetail(workspace.id, runId, token),
    listRunEscalations(workspace.id, runId, token),
  ]);

  if (runResult.status === "rejected") {
    const err = runResult.reason;
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${loginNext}`);
    }
    return renderError(err, {
      workspaceId: workspace.id,
      workspaceName: workspace.name,
      workspaceSlug: workspace.slug,
    });
  }

  const detail = runResult.value;
  if (!detail) {
    return (
      <AppShell
        title="Run"
        kicker="RUN"
        workspace={{
          id: workspace.id,
          name: workspace.name,
          slug: workspace.slug,
        }}
        actions={
          <Link
            href="/runs"
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            {"\u2190"} All runs
          </Link>
        }
      >
        <Card>
          <CardHeader
            title="Run not found"
            subtitle="No pipeline in this workspace recorded a run with that id in the last 50 runs."
          />
        </Card>
      </AppShell>
    );
  }

  let escalations: ApiRunEscalation[] = [];
  let escalationsError = false;
  if (escalationsResult.status === "fulfilled") {
    escalations = escalationsResult.value;
  } else {
    escalationsError = true;
  }

  return (
    <AppShell
      title="Run"
      kicker="RUN"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      actions={
        <Link
          href="/runs"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          {"\u2190"} All runs
        </Link>
      }
    >
      <RunDetail
        workspaceId={workspace.id}
        workspaceSlug={workspace.slug}
        run={detail.run}
        pipeline={detail.pipeline}
        escalations={escalations}
        escalationsError={escalationsError}
      />
    </AppShell>
  );
}

function renderError(
  err: unknown,
  workspace?: {
    workspaceId: string;
    workspaceName: string;
    workspaceSlug: string;
  },
) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell
      title="Run"
      kicker="RUN"
      workspace={
        workspace
          ? {
              id: workspace.workspaceId,
              name: workspace.workspaceName,
              slug: workspace.workspaceSlug,
            }
          : undefined
      }
    >
      <Card>
        <CardHeader
          title="Couldn't load run"
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
