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
import {
  resolveAutomateBannerData,
  type AutomateBannerData,
} from "@/components/runs/automate-banner";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiCatalogPattern,
  type ApiLane,
  type ApiRunEscalation,
  ApiHttpError,
  ApiUnavailableError,
  getRunDetail,
  isApiConfigured,
  listCatalogPatterns,
  listLanes,
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

  // RFC-0010 P4-04: resolve the "Automate this run" banner. Both
  // fetches are best-effort — if the catalog or lanes endpoint
  // hiccups we just skip the banner (fail-closed per the ticket's
  // edge-case list).
  const automateBanner = await resolveAutomateBannerForRun({
    workspaceId: workspace.id,
    token,
    run: detail.run,
    pipeline: detail.pipeline,
  });

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
        automateBanner={automateBanner}
      />
    </AppShell>
  );
}

/**
 * Best-effort fetch of the catalog + lane projection needed to
 * render the P4-04 banner. Returns ``null`` whenever any input is
 * missing or the underlying calls error — the resolver itself has
 * the same fail-closed contract, but we short-circuit early when
 * we can to avoid wasted requests (e.g. failed/manual=false runs
 * never need the lane fetch).
 *
 * The lane fetch is scoped to the run's repo when known — that
 * keeps the response small and uses the indexed ``repo_id`` filter
 * instead of paginating the full workspace lane list.
 */
async function resolveAutomateBannerForRun({
  workspaceId,
  token,
  run,
  pipeline,
}: {
  workspaceId: string;
  token: string;
  run: Parameters<typeof resolveAutomateBannerData>[0]["run"];
  pipeline: Parameters<typeof resolveAutomateBannerData>[0]["pipeline"];
}): Promise<AutomateBannerData | null> {
  // Cheap pre-checks: skip the network fan-out entirely when the
  // resolver couldn't possibly say "yes" anyway.
  if (run.status !== "succeeded") return null;
  if (run.trigger !== "manual") return null;
  const repoId = pipeline?.repo_id ?? null;
  if (!repoId) return null;

  const [patternsResult, lanesResult] = await Promise.allSettled([
    listCatalogPatterns({ workspaceId, token }),
    listLanes(workspaceId, { repoId, token }),
  ]);

  const patterns: ApiCatalogPattern[] =
    patternsResult.status === "fulfilled" ? patternsResult.value : [];
  const lanes: ApiLane[] =
    lanesResult.status === "fulfilled" ? lanesResult.value : [];

  // Catalog failure → no banner. Lane failure leaves ``lanes = []``,
  // so the resolver can still emit the wizard variant — we only lose
  // the "already automated" downgrade. That's the lesser evil: the
  // banner is more useful than nothing, and the wizard CTA's worst
  // case is a duplicate-prevention check inside the (future) wizard.
  if (patternsResult.status === "rejected") return null;

  return resolveAutomateBannerData({ run, pipeline, patterns, lanes });
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
