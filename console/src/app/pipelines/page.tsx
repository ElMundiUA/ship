import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, ButtonGhost, Card, CardHeader } from "@/components/ui";
import {
  type ApiActivatedRepo,
  ApiHttpError,
  ApiUnavailableError,
  type ApiPipeline,
  isApiConfigured,
  listActivatedRepos,
  listPipelines,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Dedicated Pipelines surface — separate from the dashboard's
 * "Recommended actions" strip. Lays the lanes out as **swimlanes**
 * grouped by repo (the one binding signal we have today): each repo
 * gets a section header and its lanes render as cards underneath.
 *
 * Pilot scope: pipelines without a repo binding fall into a
 * "Default lanes" swimlane so they're never invisible to the
 * operator. When we add per-project / per-team grouping we'll just
 * change the bucket key here without touching the card UI.
 */

export const dynamic = "force-dynamic";

export default async function PipelinesPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Pipelines">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load real pipelines."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fpipelines");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fpipelines");
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
      redirect("/login?next=%2Fpipelines");
    }
    return renderUnavailable(err);
  }

  const sortedRepos = [...repos].sort((a, b) =>
    a.full_name.localeCompare(b.full_name),
  );
  const lanes = groupByRepo(pipelines);

  return (
    <AppShell
      title="Pipelines"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: sortedRepos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: sortedRepos[0]?.id ?? null,
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
      <p className="mb-6 max-w-2xl text-xs text-white/55">
        All lanes Ship knows about, grouped by the repo they fire against.
        Toggle one off to mute it across the workspace; the install action
        opens a PR in the target repo with the starter workflow.
      </p>
      {lanes.length === 0 ? (
        <Card>
          <p className="text-sm text-white/70">
            No pipelines yet. Activate at least one repo on the wizard and
            the default lanes appear automatically.
          </p>
          <Link
            href={`/onboarding?step=repos&ws=${encodeURIComponent(workspace.id)}`}
            className="mt-3 inline-block text-xs font-semibold text-aqua hover:underline"
          >
            Pick repos →
          </Link>
        </Card>
      ) : (
        <div className="space-y-8">
          {lanes.map((lane) => (
            <Swimlane
              key={lane.key}
              title={lane.title}
              subtitle={lane.subtitle}
              repoId={lane.repoId}
              pipelines={lane.pipelines}
              workspaceId={workspace.id}
            />
          ))}
        </div>
      )}
    </AppShell>
  );
}

type Lane = {
  key: string;
  title: string;
  subtitle: string;
  /**
   * The repo this swimlane targets, when there is one. Cards in a
   * bound lane post this on Install / Run so the backend fires
   * against the repo the user visually pulled the card from — even
   * if the pipeline's stored binding had drifted elsewhere.
   */
  repoId: string | null;
  pipelines: ApiPipeline[];
};

function groupByRepo(pipelines: ApiPipeline[]): Lane[] {
  const buckets = new Map<string, Lane>();
  for (const p of pipelines) {
    const key = p.repo_full_name ?? "__unbound__";
    if (!buckets.has(key)) {
      buckets.set(key, {
        key,
        title: p.repo_full_name ?? "Default lanes (no repo bound)",
        subtitle: p.repo_full_name
          ? `${pluralize(0, "lane", "lanes")} firing against ${p.repo_full_name}`
          : "Bind these to a repo so manual runs have somewhere to dispatch.",
        repoId: p.repo_id,
        pipelines: [],
      });
    }
    buckets.get(key)!.pipelines.push(p);
  }
  // Stable order: real repos first (alphabetically), unbound last.
  const lanes = [...buckets.values()];
  lanes.sort((a, b) => {
    if (a.key === "__unbound__") return 1;
    if (b.key === "__unbound__") return -1;
    return a.title.localeCompare(b.title);
  });
  // Rewrite subtitles with the real lane count.
  for (const lane of lanes) {
    if (lane.key !== "__unbound__") {
      const enabled = lane.pipelines.filter((p) => p.enabled).length;
      lane.subtitle = `${enabled}/${lane.pipelines.length} enabled · firing against ${lane.title}`;
    }
  }
  return lanes;
}

function pluralize(n: number, one: string, many: string): string {
  return n === 1 ? `${n} ${one}` : `${n} ${many}`;
}

function Swimlane({
  title,
  subtitle,
  repoId,
  pipelines,
  workspaceId,
}: {
  title: string;
  subtitle: string;
  repoId: string | null;
  pipelines: ApiPipeline[];
  workspaceId: string;
}) {
  return (
    <section>
      <div className="mb-3 flex items-end justify-between gap-3 border-b border-white/10 pb-2">
        <div className="min-w-0">
          <h3 className="font-display text-sm font-bold tracking-wide text-white">
            {title}
          </h3>
          <p className="text-[11px] text-white/45">{subtitle}</p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {pipelines.map((p) => (
          <PipelineLaneCard
            key={p.id}
            pipeline={p}
            // Swimlane repo wins over the pipeline's stored binding: if
            // the user grabs a card from this repo's lane the backend
            // rebinds + dispatches against this repo. ``null`` keeps
            // the "no context" shape for the default-lanes lane so we
            // still fall back to the auto-bind heuristic for legacy
            // seeds.
            repoId={repoId ?? p.repo_id}
            workspaceId={workspaceId}
          />
        ))}
      </div>
    </section>
  );
}

function PipelineLaneCard({
  pipeline,
  repoId,
  workspaceId,
}: {
  pipeline: ApiPipeline;
  repoId: string | null;
  workspaceId: string;
}) {
  const state = pipelineState(pipeline);
  const lastRun = pipeline.last_run_at
    ? `${pipeline.last_run_status ?? "run"} · ${formatRelative(pipeline.last_run_at)}`
    : "no runs yet";
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="info">{pipeline.kind.replace(/_/g, " ")}</Badge>
            <Badge tone={pipeline.enabled ? "ok" : "neutral"}>
              {pipeline.enabled ? "enabled" : "disabled"}
            </Badge>
            {state === "needs-install" && (
              <Badge tone="warn">workflow not installed</Badge>
            )}
            {state === "coming-soon" && (
              <Badge tone="neutral">phase 2 preset</Badge>
            )}
          </div>
          <h4 className="mt-2 font-display text-sm font-bold text-white">
            {pipeline.name}
          </h4>
          <p className="mt-0.5 text-[11px] text-white/55">
            workflow ·{" "}
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
              {pipeline.workflow_id}
            </code>
          </p>
        </div>
      </div>

      <div className="mt-3 text-xs text-white/65">
        <Badge tone={lastRunTone(pipeline.last_run_status)} dot>
          {lastRun}
        </Badge>
      </div>

      <div className="mt-4 flex items-center justify-between gap-2">
        <form
          action="/api/dashboard/toggle-pipeline"
          method="POST"
          className="flex items-center gap-2"
        >
          <input type="hidden" name="ws" value={workspaceId} />
          <input type="hidden" name="pipeline" value={pipeline.id} />
          <input
            type="hidden"
            name="enabled"
            value={pipeline.enabled ? "off" : "on"}
          />
          <ButtonGhost type="submit">
            {pipeline.enabled ? "Disable" : "Enable"}
          </ButtonGhost>
        </form>
        {state === "run-ready" && (
          <form
            action="/api/dashboard/run-pipeline"
            method="POST"
            className="flex items-center gap-2"
          >
            <input type="hidden" name="ws" value={workspaceId} />
            <input type="hidden" name="pipeline" value={pipeline.id} />
            {repoId && (
              <input type="hidden" name="repo_id" value={repoId} />
            )}
            <button
              type="submit"
              disabled={!pipeline.enabled}
              className={
                "inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-bold transition " +
                (pipeline.enabled
                  ? "bg-gradient-to-r from-coral via-lilac to-aqua text-ink shadow-glow hover:brightness-110"
                  : "cursor-not-allowed border border-white/10 bg-white/[0.04] text-white/40")
              }
            >
              Run now
            </button>
          </form>
        )}
        {state === "needs-install" && (
          <form
            action="/api/dashboard/install-pipeline"
            method="POST"
            className="flex items-center gap-2"
          >
            <input type="hidden" name="ws" value={workspaceId} />
            <input type="hidden" name="pipeline" value={pipeline.id} />
            {repoId && (
              <input type="hidden" name="repo_id" value={repoId} />
            )}
            <button
              type="submit"
              className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/[0.08] px-3.5 py-1.5 text-xs font-bold text-aqua hover:bg-aqua/[0.16]"
            >
              Install workflow PR →
            </button>
          </form>
        )}
        {state === "coming-soon" && (
          <button
            type="button"
            disabled
            title="Phase 2 ships a starter workflow for this pipeline kind."
            className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-1.5 text-xs font-bold text-white/40"
          >
            Coming with presets
          </button>
        )}
      </div>
    </Card>
  );
}

function pipelineState(
  p: ApiPipeline,
): "run-ready" | "needs-install" | "coming-soon" {
  if (!p.supports_run) return "coming-soon";
  if (p.workflow_installed === true) return "run-ready";
  return "needs-install";
}

function lastRunTone(status: string | null) {
  if (status === "succeeded") return "ok" as const;
  if (status === "failed") return "err" as const;
  if (status === "running") return "info" as const;
  if (status) return "warn" as const;
  return "neutral" as const;
}

function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const sec = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Pipelines">
      <Card>
        <CardHeader
          title="Couldn't load pipelines"
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
