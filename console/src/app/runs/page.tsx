import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { RunRow } from "@/components/runs/run-row";
import { RunsFiltersControlled } from "@/components/runs/runs-filters-controlled";
import {
  type RunsFiltersOption,
} from "@/components/runs/runs-filters";
import {
  type RunStatus,
  type RunTrigger,
  buildRunsUrl,
  countActiveRunsFilters,
  isRunStatus,
  isRunTrigger,
  parseRunsSearchParams,
} from "@/components/runs/runs-url";
import {
  Card,
  CardHeader,
  EmptyState,
} from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiPipeline,
  type ApiPipelineRun,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listPipelineRuns,
  listPipelines,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * ``/runs`` — outcome-first run list (RFC-0010 / Wave 6 Phase 3
 * tickets P3-04 + P3-06).
 *
 * Replaces the legacy event-style ``RunsView`` (lived under
 * ``/pipelines/runs-view.tsx``) with a single screen that reads as
 * outcomes:
 *
 *   - Each row is a ``RunRow`` keyed off the structured
 *     ``pipeline_runs.outcome`` JSONB (RFC-0010 §RunSummary).
 *   - A filter chip row above the list (``RunsFilters``) lets the
 *     operator slice by play, repo, status, trigger, and the boolean
 *     "has escalations" pivot. State lives in the URL.
 *
 * **Filtering today is FE-side.** The backend ``listPipelineRuns``
 * endpoint is per-pipeline and accepts ``limit`` only — no status /
 * trigger / play filters. We fetch the full window, apply the
 * filter set in JS, and surface the count. Sibling-A's runs-list
 * endpoint (tracked on the planning doc as a follow-up) will let us
 * push these straight to the API; until then the FE pass keeps the
 * UX correct at the cost of slightly larger payloads.
 *
 * **Workspace-wide aggregation.** No ``GET /workspaces/{ws}/runs``
 * exists yet either, so we fan-out: list pipelines → ``Promise.all``
 * the per-pipeline run windows → flatten + sort. This is bounded by
 * ``RUNS_PER_PIPELINE`` (50) which gives us ~250 rows for the pilot
 * tenant's ≤5 pipelines — comfortably enough for the visible window.
 */

export const dynamic = "force-dynamic";

const RUNS_PER_PIPELINE = 50;
const VISIBLE_LIMIT = 100;

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function RunsPage({ searchParams }: PageProps) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const filters = parseRunsSearchParams(params);
  const loginNext = encodeURIComponent("/runs");

  if (!isApiConfigured()) {
    return (
      <AppShell title="Runs" kicker="HISTORY">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load real runs."
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
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let pipelines: ApiPipeline[] = [];
  let repos: ApiActivatedRepo[] = [];
  try {
    [pipelines, repos] = await Promise.all([
      listPipelines(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(
        () => [] as ApiActivatedRepo[],
      ),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${loginNext}`);
    }
    return renderUnavailable(err);
  }

  // Fan-out per-pipeline run fetches. Failures on one pipeline don't
  // sink the whole page — we just drop that pipeline's rows.
  const runResults = await Promise.all(
    pipelines.map(async (p) => {
      try {
        return await listPipelineRuns(workspace.id, p.id, RUNS_PER_PIPELINE, token);
      } catch {
        return [] as ApiPipelineRun[];
      }
    }),
  );

  // Build the page-level lookup tables once so each row render is
  // O(1) instead of O(pipelines) / O(repos).
  const pipelineById = new Map<string, ApiPipeline>(
    pipelines.map((p) => [p.id, p]),
  );
  const repoById = new Map<string, ApiActivatedRepo>(
    repos.map((r) => [r.id, r]),
  );

  const allRuns: ApiPipelineRun[] = runResults.flat();
  // Sort by started_at if present, otherwise created_at — newest first.
  allRuns.sort((a, b) => {
    const ta = new Date(a.started_at ?? a.created_at).getTime();
    const tb = new Date(b.started_at ?? b.created_at).getTime();
    return tb - ta;
  });

  // FE-side filter pass. TODO(P3-?): once sibling-A ships
  // ``GET /v1/workspaces/{ws}/runs?play=&status=&trigger=&has_escalations=``
  // forward the filter state straight to the API and drop this loop.
  const filtered = allRuns.filter((run) => {
    if (filters.play && run.pipeline_id !== filters.play) return false;
    if (filters.repo) {
      const pipe = pipelineById.get(run.pipeline_id);
      if (!pipe || pipe.repo_id !== filters.repo) return false;
    }
    if (filters.statuses.length > 0) {
      const status = run.status;
      const matches = filters.statuses.some((s) => s === status);
      if (!matches) return false;
    }
    if (filters.triggers.length > 0) {
      const trigger = run.trigger;
      const matches = filters.triggers.some((t) => t === trigger);
      if (!matches) return false;
    }
    if (filters.hasEscalations) {
      const escalations = run.outcome?.escalations ?? [];
      if (escalations.length === 0) return false;
    }
    return true;
  });

  const visible = filtered.slice(0, VISIBLE_LIMIT);
  const truncated = filtered.length > VISIBLE_LIMIT;
  const activeFilters = countActiveRunsFilters(filters);

  // Build filter option sets from the (unfiltered) data so the
  // dropdowns always show what's reachable in the current window.
  const playOptions = buildPlayOptions(allRuns, pipelineById);
  const repoOptions = buildRepoOptions(allRuns, pipelineById, repoById);
  const counts = computeFilterCounts(allRuns, pipelineById);

  return (
    <AppShell
      title="Runs"
      kicker="HISTORY"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      scope={{
        repos: repos
          .map((r) => ({ id: r.id, full_name: r.full_name }))
          .sort((a, b) => a.full_name.localeCompare(b.full_name)),
        selectedRepoId: filters.repo,
      }}
      actions={
        <Link
          href="/"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Dashboard
        </Link>
      }
    >
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Every run, ranked newest-first, read as outcomes. Click a row
        for the full artifact + escalation grid; the chip row above
        slices by play, repo, status, trigger, and whether the run
        kicked an escalation into the inbox.
      </p>

      <Card padded={false} className="mb-5 px-4 py-3">
        <RunsFiltersControlled
          value={filters}
          playOptions={playOptions}
          repoOptions={repoOptions}
          counts={counts}
        />
      </Card>

      <RunsList
        visible={visible}
        pipelineById={pipelineById}
        repoById={repoById}
        activeFilters={activeFilters}
        totalInWindow={allRuns.length}
        filteredTotal={filtered.length}
        truncated={truncated}
      />
    </AppShell>
  );
}

function RunsList({
  visible,
  pipelineById,
  repoById,
  activeFilters,
  totalInWindow,
  filteredTotal,
  truncated,
}: {
  visible: ApiPipelineRun[];
  pipelineById: Map<string, ApiPipeline>;
  repoById: Map<string, ApiActivatedRepo>;
  activeFilters: number;
  totalInWindow: number;
  filteredTotal: number;
  truncated: boolean;
}) {
  if (totalInWindow === 0) {
    return (
      <EmptyState
        title="No runs yet"
        body="Activate a repo and trigger a play to see outcomes land here. Runs from manual / scheduled / event triggers all converge into this list."
        action={
          <Link
            href="/plays"
            className="inline-flex items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
          >
            Browse plays →
          </Link>
        }
      />
    );
  }

  if (visible.length === 0) {
    return (
      <EmptyState
        title="No runs match these filters"
        body={
          activeFilters > 0
            ? "Drop a chip or clear all filters to widen the view."
            : "The filtered window came back empty — try a different slice."
        }
        action={
          <Link
            href={buildRunsUrl({
              play: null,
              repo: null,
              statuses: [],
              triggers: [],
              hasEscalations: false,
            })}
            className="inline-flex items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
          >
            Clear filters
          </Link>
        }
      />
    );
  }

  return (
    <Card padded={false}>
      <CardHeader
        className="px-5 pt-5"
        title={`Runs (${filteredTotal}${truncated ? `, showing ${visible.length}` : ""})`}
        subtitle="Newest first. Each row is one pipeline run; click for the per-run artifact grid."
      />
      <ul className="space-y-2 px-3 pb-3">
        {visible.map((run) => {
          const pipeline = pipelineById.get(run.pipeline_id);
          const playLabel = pipeline?.name ?? "Unknown play";
          const repoSlug =
            (pipeline?.repo_id && repoById.get(pipeline.repo_id)?.full_name) ||
            pipeline?.repo_full_name ||
            null;
          return (
            <li key={run.id}>
              <RunRow
                run={run}
                playLabel={playLabel}
                repoSlug={repoSlug}
              />
            </li>
          );
        })}
      </ul>
      {truncated && (
        <div className="border-t border-white/5 px-5 py-3 text-[11px] text-white/45">
          Showing the first {VISIBLE_LIMIT} of {filteredTotal} runs in the
          current window. Tighten the filters to reach older rows; a
          server-side pager lands with the runs-list endpoint follow-up.
        </div>
      )}
    </Card>
  );
}

function buildPlayOptions(
  runs: ApiPipelineRun[],
  pipelineById: Map<string, ApiPipeline>,
): RunsFiltersOption[] {
  const seen = new Map<string, { label: string; hint?: string; count: number }>();
  for (const run of runs) {
    const pipe = pipelineById.get(run.pipeline_id);
    if (!pipe) continue;
    const existing = seen.get(pipe.id);
    if (existing) {
      existing.count += 1;
      continue;
    }
    seen.set(pipe.id, {
      label: pipe.name,
      hint: pipe.kind.replace(/_/g, " "),
      count: 1,
    });
  }
  return [...seen.entries()]
    .map(([value, meta]) => ({ value, ...meta }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function buildRepoOptions(
  runs: ApiPipelineRun[],
  pipelineById: Map<string, ApiPipeline>,
  repoById: Map<string, ApiActivatedRepo>,
): RunsFiltersOption[] {
  const seen = new Map<string, { label: string; count: number }>();
  for (const run of runs) {
    const pipe = pipelineById.get(run.pipeline_id);
    const repoId = pipe?.repo_id;
    if (!repoId) continue;
    const repo = repoById.get(repoId);
    const label = repo?.full_name ?? pipe?.repo_full_name ?? repoId;
    const existing = seen.get(repoId);
    if (existing) {
      existing.count += 1;
      continue;
    }
    seen.set(repoId, { label, count: 1 });
  }
  return [...seen.entries()]
    .map(([value, meta]) => ({ value, ...meta }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function computeFilterCounts(
  runs: ApiPipelineRun[],
  pipelineById: Map<string, ApiPipeline>,
): {
  statuses: Partial<Record<RunStatus, number>>;
  triggers: Partial<Record<RunTrigger, number>>;
  withEscalations: number;
} {
  const statuses: Partial<Record<RunStatus, number>> = {};
  const triggers: Partial<Record<RunTrigger, number>> = {};
  let withEscalations = 0;
  for (const run of runs) {
    if (isRunStatus(run.status)) {
      statuses[run.status] = (statuses[run.status] ?? 0) + 1;
    }
    if (isRunTrigger(run.trigger)) {
      triggers[run.trigger] = (triggers[run.trigger] ?? 0) + 1;
    }
    const escalations = run.outcome?.escalations ?? [];
    if (escalations.length > 0) withEscalations += 1;
  }
  // Suppress the unused warning for ``pipelineById`` — kept on the
  // signature for symmetry with future extensions (e.g. counts per
  // play / per-repo for the dropdown decoration).
  void pipelineById;
  return { statuses, triggers, withEscalations };
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Runs" kicker="HISTORY">
      <Card>
        <CardHeader
          title="Couldn't load runs"
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
