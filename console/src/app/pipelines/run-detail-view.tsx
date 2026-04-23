/**
 * Reusable run-detail screen.
 *
 * Lives outside ``page.tsx`` so the new ``/runs/[id]`` route can
 * import it after resolving the parent ``pipelineId`` server-side
 * (the Next.js page-validator rejects named exports from page
 * files). Both ``/pipelines/[pipelineId]/runs/[runId]/page.tsx``
 * (legacy) and ``/runs/[id]/page.tsx`` (new IA, P1-01) call this
 * with their own ``basePath`` / breadcrumb labels.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getPipelineRun,
  isApiConfigured,
  listPipelines,
  listWorkspaces,
  type ApiPipeline,
  type ApiPipelineRun,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

function runStatusTone(status: string): "ok" | "err" | "info" | "warn" | "neutral" {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "err";
  if (status === "running" || status === "queued") return "info";
  if (status === "cancelled") return "warn";
  return "neutral";
}

function formatIso(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function durationMs(started: string | null, finished: string | null): string | null {
  if (!started || !finished) return null;
  const a = new Date(started).getTime();
  const b = new Date(finished).getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return null;
  const sec = Math.round((b - a) / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m`;
  return `${Math.round(min / 60)}h`;
}

export async function RunDetailView({
  pipelineId,
  runId,
  wsQuery,
  basePath,
  indexPath,
  indexLabel,
  backHref,
  backLabel,
}: {
  pipelineId: string;
  runId: string;
  wsQuery?: string;
  basePath: string;
  indexPath: string;
  indexLabel: string;
  backHref: string;
  backLabel: string;
}) {
  const loginNext = encodeURIComponent(basePath);

  if (!isApiConfigured()) {
    return (
      <AppShell title="Pipeline run">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load pipeline runs."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) {
    redirect(`/login?next=${loginNext}`);
  }

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

  const fromQuery = wsQuery
    ? workspaces.find((w) => w.id === wsQuery)
    : undefined;
  const workspace = fromQuery ?? workspaces[0];
  if (!workspace) redirect("/onboarding?step=github");

  let pipelines: ApiPipeline[];
  try {
    pipelines = await listPipelines(workspace.id, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${loginNext}`);
    }
    return renderError(err);
  }

  const pipeline = pipelines.find((p) => p.id === pipelineId) ?? null;

  let run: ApiPipelineRun;
  try {
    run = await getPipelineRun(workspace.id, pipelineId, runId, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 404) {
      return (
        <AppShell
          title="Pipeline run"
          workspace={{
            id: workspace.id,
            name: workspace.name,
            slug: workspace.slug,
          }}
        >
          <Card>
            <CardHeader title="Run not found" subtitle="It may have been deleted or the link is stale." />
            <div className="mt-4">
              <Link
                href={backHref}
                className="text-xs font-semibold text-aqua hover:underline"
              >
                {backLabel}
              </Link>
            </div>
          </Card>
        </AppShell>
      );
    }
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${loginNext}`);
    }
    return renderError(err);
  }

  const metrics =
    run.payload &&
    typeof run.payload.metrics === "object" &&
    run.payload.metrics !== null &&
    !Array.isArray(run.payload.metrics)
      ? (run.payload.metrics as Record<string, unknown>)
      : null;
  const note =
    run.payload && typeof run.payload["note"] === "string"
      ? run.payload["note"]
      : null;
  const ghUrl =
    metrics && typeof metrics.gh_html_url === "string"
      ? metrics.gh_html_url
      : null;

  const dur = durationMs(run.started_at, run.finished_at);

  return (
    <AppShell
      title="Pipeline run"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      actions={
        <Link
          href={backHref}
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          {backLabel}
        </Link>
      }
    >
      <div className="mb-6 max-w-2xl text-xs text-white/55">
        <Link href={indexPath} className="font-semibold text-aqua hover:underline">
          {indexLabel}
        </Link>
        <span className="text-white/35"> / </span>
        <span className="text-white/70">
          {pipeline?.name ?? "Pipeline"}{" "}
          <code className="rounded bg-white/[0.06] px-1 py-0.5 text-[10px]">
            {runId.slice(0, 8)}…
          </code>
        </span>
      </div>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={runStatusTone(run.status)} dot>
                {run.status}
              </Badge>
              <span className="text-[11px] uppercase tracking-widest text-white/45">
                {run.trigger}
              </span>
            </div>
            <h1 className="mt-2 font-display text-lg font-bold text-white">
              {pipeline?.name ?? "Pipeline run"}
            </h1>
            {pipeline?.kind && (
              <p className="mt-1 text-[11px] text-white/45">
                {pipeline.kind.replace(/_/g, " ")} ·{" "}
                <code className="text-white/55">{pipeline.workflow_id}</code>
              </p>
            )}
          </div>
        </div>

        <dl className="mt-6 grid gap-3 border-t border-white/10 pt-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-[10px] uppercase tracking-widest text-white/40">
              Started
            </dt>
            <dd className="mt-0.5 text-white/80">{formatIso(run.started_at)}</dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-widest text-white/40">
              Finished
            </dt>
            <dd className="mt-0.5 text-white/80">{formatIso(run.finished_at)}</dd>
          </div>
          {dur && (
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-white/40">
                Duration
              </dt>
              <dd className="mt-0.5 text-white/80">{dur}</dd>
            </div>
          )}
          <div>
            <dt className="text-[10px] uppercase tracking-widest text-white/40">
              Run id
            </dt>
            <dd className="mt-0.5 break-all font-mono text-[11px] text-white/70">
              {run.id}
            </dd>
          </div>
        </dl>

        {run.summary && (
          <div className="mt-4 border-t border-white/10 pt-4">
            <h2 className="text-[10px] uppercase tracking-widest text-white/40">
              Summary
            </h2>
            <p className="mt-2 text-sm text-white/85">{run.summary}</p>
          </div>
        )}

        {note && (
          <div className="mt-4 border-t border-white/10 pt-4">
            <h2 className="text-[10px] uppercase tracking-widest text-white/40">
              Note
            </h2>
            <p className="mt-2 text-sm text-white/85">{note}</p>
          </div>
        )}

        {ghUrl && (
          <div className="mt-4 border-t border-white/10 pt-4">
            <h2 className="text-[10px] uppercase tracking-widest text-white/40">
              GitHub Actions
            </h2>
            <a
              href={ghUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-block text-sm font-semibold text-aqua hover:underline"
            >
              Open workflow run →
            </a>
          </div>
        )}

        {metrics && Object.keys(metrics).length > 0 && (
          <div className="mt-4 border-t border-white/10 pt-4">
            <h2 className="text-[10px] uppercase tracking-widest text-white/40">
              Metrics
            </h2>
            <p className="mb-2 text-[11px] text-white/45">
              From the workflow&apos;s <code className="text-white/55">shipctl callback</code>{" "}
              step (and GitHub reconciliation).
            </p>
            <dl className="space-y-2 rounded-lg border border-white/10 bg-white/[0.02] p-3 text-xs">
              {Object.entries(metrics).map(([k, v]) => (
                <div key={k} className="flex flex-wrap gap-2">
                  <dt className="min-w-[8rem] font-mono text-white/55">{k}</dt>
                  <dd className="min-w-0 flex-1 break-all text-white/80">
                    {formatMetricValue(v)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {run.payload && Object.keys(run.payload).length > 0 && (
          <details className="mt-4 border-t border-white/10 pt-4">
            <summary className="cursor-pointer text-[10px] uppercase tracking-widest text-white/40">
              Raw payload (debug)
            </summary>
            <pre className="mt-3 max-h-64 overflow-auto rounded-lg border border-white/10 bg-ink/80 p-3 text-[10px] leading-relaxed text-white/70">
              {JSON.stringify(run.payload, null, 2)}
            </pre>
          </details>
        )}
      </Card>
    </AppShell>
  );
}

function formatMetricValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
    return String(v);
  }
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function renderError(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Pipeline run">
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
