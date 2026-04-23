import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listPipelines,
  listPipelineRuns,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { RunDetailView } from "../../pipelines/run-detail-view";

/**
 * ``/runs/[id]`` — new IA mount point for the per-run detail
 * surface (RFC-0010 §3 / P1-01). Re-uses the legacy
 * :func:`RunDetailView` body; only the breadcrumb + login redirect
 * targets differ.
 *
 * **Fallback strategy.** The legacy detail endpoint is keyed by
 * ``(workspaceId, pipelineId, runId)`` — no
 * "find run by id alone" route exists yet (see
 * ``backend/app/api/v1/routes/`` :: pipelines). The new IA URL only
 * carries ``runId``, so we resolve the parent pipelineId server-
 * side by listing pipelines and scanning each pipeline's recent
 * runs for the matching ``runId``. This is O(pipelines × runs) and
 * fine for the pilot tenant; once the backend exposes
 * ``GET /v1/workspaces/{ws}/runs/{runId}`` (tracked separately) we
 * can replace this loop with a single fetch.
 *
 * If the run isn't found, we fall through to ``RunDetailView`` with
 * a sentinel pipelineId — it will surface the standard "Run not
 * found" 404 card.
 */

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ ws?: string }>;
};

export default async function RunDetailPage({ params, searchParams }: PageProps) {
  const { id: runId } = await params;
  const { ws: wsQuery } = await searchParams;
  const loginNext = encodeURIComponent("/runs");

  if (!isApiConfigured()) {
    return (
      <AppShell title="Run">
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

  // Resolve pipelineId by scanning recent runs across all pipelines.
  // First match wins. ``listPipelineRuns`` is paginated — we ask for
  // the standard window (50) which covers Phase-1's pilot volume.
  let pipelineId: string | null = null;
  try {
    const pipelines = await listPipelines(workspace.id, token);
    for (const p of pipelines) {
      let runs;
      try {
        runs = await listPipelineRuns(workspace.id, p.id, 50, token);
      } catch {
        continue;
      }
      if (runs.some((r) => r.id === runId)) {
        pipelineId = p.id;
        break;
      }
    }
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${loginNext}`);
    }
    return renderError(err);
  }

  if (!pipelineId) {
    return (
      <AppShell
        title="Run"
        kicker="HISTORY"
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
            ← All runs
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

  return (
    <RunDetailView
      pipelineId={pipelineId}
      runId={runId}
      wsQuery={wsQuery}
      basePath="/runs"
      indexPath="/runs"
      indexLabel="Runs"
      backHref="/runs"
      backLabel="← All runs"
    />
  );
}

function renderError(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Run">
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
