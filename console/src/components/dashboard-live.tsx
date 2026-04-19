import Link from "next/link";

import {
  Badge,
  ButtonGhost,
  Card,
  CardHeader,
  StatTile,
  type BadgeTone,
} from "@/components/ui";
import type {
  ApiDashboard,
  ApiDashboardPullRequest,
  ApiDashboardWorkflowRun,
  ApiPipeline,
  ApiPipelineRun,
} from "@/lib/api/client";

/**
 * Live dashboard rendered when the backend is reachable and the user
 * has at least one workspace with onboarding finished. Mirrors the
 * five default pipelines surfaced by ``GET /v1/workspaces/{ws}/dashboard``
 * plus the recent-PR / recent-run / recent-pipeline-run strips.
 *
 * Pure server component — toggle/run interactions go through the
 * ``/api/dashboard/*`` form handlers so the session token stays in
 * the httpOnly cookie and the page works without JS.
 */

const RUN_REASONS: Record<string, { tone: BadgeTone; label: string }> = {
  ran: { tone: "ok", label: "Pipeline run finished." },
  enabled: { tone: "ok", label: "Pipeline enabled." },
  disabled: { tone: "warn", label: "Pipeline disabled." },
  forbidden: { tone: "err", label: "You need admin to do that." },
  disabled_pipeline: { tone: "warn", label: "Pipeline is off — enable it first." },
  missing: { tone: "err", label: "That pipeline is gone — refresh." },
  api_unavailable: { tone: "err", label: "Backend is unreachable. Try again." },
  unknown: { tone: "err", label: "Something went wrong. Try again." },
};

export function DashboardLive({
  workspaceId,
  workspaceName,
  workspaceSlug,
  data,
  banner,
}: {
  workspaceId: string;
  workspaceName: string;
  workspaceSlug: string;
  data: ApiDashboard;
  banner?: { kind: string; reason: string };
}) {
  const bannerInfo = banner ? RUN_REASONS[banner.reason] ?? null : null;
  const setupComplete = data.counts.active_repos > 0;

  return (
    <>
      {bannerInfo && (
        <div
          className="mb-4 flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm"
          role="status"
        >
          <Badge tone={bannerInfo.tone}>{banner!.kind}</Badge>
          <span className="text-white/80">{bannerInfo.label}</span>
        </div>
      )}

      {!setupComplete && <FinishSetupCallout workspaceId={workspaceId} />}

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Active repos"
          value={String(data.counts.active_repos)}
          hint={`${workspaceName} · ${workspaceSlug}`}
        />
        <StatTile
          label="Enabled pipelines"
          value={`${data.counts.enabled_pipelines} / 5`}
          hint="Default lanes shipped on Day 3"
        />
        <StatTile
          label="Open PRs (last 10)"
          value={String(data.counts.open_pull_requests)}
          hint="Updated by the GitHub webhook"
        />
        <StatTile
          label="Runs · 24h"
          value={String(data.counts.runs_last_24h)}
          hint="Manual + webhook triggers"
        />
      </section>

      <section className="mt-8">
        <h3 className="mb-3 font-display text-base font-bold text-white">
          Pipelines
        </h3>
        <p className="mb-4 text-xs text-white/55">
          Five baked-in lanes auto-created when you activated repos. Toggle
          off to mute, &ldquo;Run now&rdquo; to fire a manual execution.
        </p>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.pipelines.length === 0 && <EmptyPipelines />}
          {data.pipelines.map((p) => (
            <PipelineCard key={p.id} pipeline={p} workspaceId={workspaceId} />
          ))}
        </div>
      </section>

      <section className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Recent pull requests"
            subtitle="Live from your activated GitHub repos"
            action={
              <Link
                href="/catalog"
                className="text-xs font-semibold text-aqua hover:underline"
              >
                Open catalog →
              </Link>
            }
          />
          {data.pull_requests.length === 0 ? (
            <EmptyState text="No PRs cached yet — open one in GitHub and the webhook will land here within a few seconds." />
          ) : (
            <PullRequestsTable rows={data.pull_requests} />
          )}
        </Card>

        <Card>
          <CardHeader
            title="Recent workflow runs"
            subtitle="GitHub Actions, last 10"
          />
          {data.workflow_runs.length === 0 ? (
            <EmptyState text="No workflow runs cached yet." />
          ) : (
            <WorkflowRunsList rows={data.workflow_runs} />
          )}
        </Card>
      </section>

      <section className="mt-8">
        <Card>
          <CardHeader
            title="Recent pipeline runs"
            subtitle="Manual triggers + future webhook fires"
          />
          {data.pipeline_runs.length === 0 ? (
            <EmptyState text='Press "Run now" on any pipeline to see history land here.' />
          ) : (
            <PipelineRunsList
              rows={data.pipeline_runs}
              pipelinesById={pipelineById(data.pipelines)}
            />
          )}
        </Card>
      </section>

      <CliCard />
    </>
  );
}

function FinishSetupCallout({ workspaceId }: { workspaceId: string }) {
  const wizardHref = `/onboarding?step=github&ws=${encodeURIComponent(workspaceId)}`;
  return (
    <Card className="mb-6 border-aqua/30 bg-aqua/[0.06]">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Badge tone="info">Setup</Badge>
            <span className="text-xs font-semibold uppercase tracking-wider text-aqua/80">
              3 quick steps · ~2 min
            </span>
          </div>
          <h3 className="mt-2 font-display text-base font-bold text-white">
            Finish wiring Ship into your repos.
          </h3>
          <p className="mt-1 text-xs text-white/65">
            Install the GitHub App, pick the repos Ship can see, and connect a
            tracker. Until then this dashboard stays empty &mdash; there&apos;s
            nothing to stream yet.
          </p>
        </div>
        <Link
          href={wizardHref}
          className="self-start rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2 text-xs font-bold text-ink shadow-glow hover:brightness-110"
        >
          Resume setup →
        </Link>
      </div>
    </Card>
  );
}

function CliCard() {
  return (
    <section className="mt-8">
      <Card>
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <CardHeader
              title="Wire up your CLI"
              subtitle="Mint a Personal Access Token to call the API from shipctl, Cursor, Codex, or Claude Code."
            />
            <pre className="mt-2 overflow-x-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[11px] text-white/85">
              <code>
                {"export SHIP_API_TOKEN=ship_pat_…\n"}
                {"npx @elmundi/ship-cli init --copy-rules"}
              </code>
            </pre>
          </div>
          <Link
            href="/settings"
            className="self-start rounded-full border border-aqua/40 bg-aqua/[0.08] px-4 py-2 text-xs font-bold text-aqua hover:bg-aqua/[0.16]"
          >
            Mint a token →
          </Link>
        </div>
      </Card>
    </section>
  );
}

function pipelineById(rows: ApiPipeline[]): Record<string, ApiPipeline> {
  return Object.fromEntries(rows.map((p) => [p.id, p]));
}

function PipelineCard({
  pipeline,
  workspaceId,
}: {
  pipeline: ApiPipeline;
  workspaceId: string;
}) {
  const lastRunLabel = pipeline.last_run_at
    ? `${pipeline.last_run_status ?? "run"} · ${relativeTime(pipeline.last_run_at)}`
    : "no runs yet";
  const tone: BadgeTone =
    pipeline.last_run_status === "succeeded"
      ? "ok"
      : pipeline.last_run_status === "failed"
        ? "err"
        : pipeline.last_run_status
          ? "warn"
          : "neutral";

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Badge tone="info">{pipeline.kind.replace("_", " ")}</Badge>
            <Badge tone={pipeline.enabled ? "ok" : "neutral"}>
              {pipeline.enabled ? "enabled" : "disabled"}
            </Badge>
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

      <div className="mt-4 flex items-center gap-2 text-xs text-white/65">
        <Badge tone={tone} dot>
          {lastRunLabel}
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

        <form
          action="/api/dashboard/run-pipeline"
          method="POST"
          className="flex items-center gap-2"
        >
          <input type="hidden" name="ws" value={workspaceId} />
          <input type="hidden" name="pipeline" value={pipeline.id} />
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
      </div>
    </Card>
  );
}

function EmptyPipelines() {
  return (
    <Card className="md:col-span-2 xl:col-span-3">
      <p className="text-sm text-white/70">
        No pipelines yet. Finish onboarding (pick at least one repo) and the
        five default lanes will be created automatically.
      </p>
      <Link
        href="/onboarding"
        className="mt-3 inline-block text-xs font-semibold text-aqua hover:underline"
      >
        Open onboarding →
      </Link>
    </Card>
  );
}

function EmptyState({ text }: { text: string }) {
  return <p className="text-sm text-white/60">{text}</p>;
}

function PullRequestsTable({ rows }: { rows: ApiDashboardPullRequest[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-white/10">
      <table className="min-w-full text-sm">
        <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
          <tr>
            <th className="px-3 py-2 text-left font-semibold">Repo</th>
            <th className="px-3 py-2 text-left font-semibold">PR</th>
            <th className="px-3 py-2 text-left font-semibold">State</th>
            <th className="px-3 py-2 text-left font-semibold">Updated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((pr) => (
            <tr key={pr.id} className="border-t border-white/5 hover:bg-white/[0.02]">
              <td className="px-3 py-2.5 align-top text-xs text-white/65">
                {pr.repo_full_name}
              </td>
              <td className="px-3 py-2.5 align-top">
                <a
                  href={pr.html_url}
                  target="_blank"
                  rel="noreferrer"
                  className="font-semibold text-white hover:text-aqua"
                >
                  #{pr.number} · {pr.title}
                </a>
                {pr.author && (
                  <div className="text-[10px] text-white/40">@{pr.author}</div>
                )}
              </td>
              <td className="px-3 py-2.5 align-top">
                <Badge tone={prTone(pr)} dot>
                  {pr.merged ? "merged" : pr.draft ? "draft" : pr.state}
                </Badge>
              </td>
              <td className="px-3 py-2.5 align-top text-xs text-white/55">
                {pr.updated_at_external
                  ? relativeTime(pr.updated_at_external)
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function prTone(pr: ApiDashboardPullRequest): BadgeTone {
  if (pr.merged) return "ok";
  if (pr.draft) return "neutral";
  if (pr.state === "open") return "info";
  return "warn";
}

function WorkflowRunsList({ rows }: { rows: ApiDashboardWorkflowRun[] }) {
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li
          key={r.id}
          className="rounded-lg border border-white/10 bg-white/[0.02] p-3"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-xs font-semibold text-white">
                {r.name}
              </div>
              <div className="text-[10px] text-white/45">
                {r.repo_full_name}
                {r.head_branch && ` · ${r.head_branch}`}
              </div>
            </div>
            <Badge tone={runTone(r)} dot>
              {r.conclusion ?? r.status}
            </Badge>
          </div>
          <div className="mt-1 text-[10px] text-white/40">
            {r.started_at ? relativeTime(r.started_at) : "—"}
            {r.actor && ` · @${r.actor}`}
          </div>
        </li>
      ))}
    </ul>
  );
}

function runTone(r: ApiDashboardWorkflowRun): BadgeTone {
  if (r.conclusion === "success") return "ok";
  if (r.conclusion === "failure" || r.conclusion === "timed_out") return "err";
  if (r.conclusion === "cancelled") return "warn";
  if (r.status === "in_progress" || r.status === "queued") return "info";
  return "neutral";
}

function PipelineRunsList({
  rows,
  pipelinesById,
}: {
  rows: ApiPipelineRun[];
  pipelinesById: Record<string, ApiPipeline>;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-white/10">
      <table className="min-w-full text-sm">
        <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
          <tr>
            <th className="px-3 py-2 text-left font-semibold">Pipeline</th>
            <th className="px-3 py-2 text-left font-semibold">Trigger</th>
            <th className="px-3 py-2 text-left font-semibold">Status</th>
            <th className="px-3 py-2 text-left font-semibold">When</th>
            <th className="px-3 py-2 text-left font-semibold">Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((run) => {
            const pipeline = pipelinesById[run.pipeline_id];
            return (
              <tr
                key={run.id}
                className="border-t border-white/5 hover:bg-white/[0.02]"
              >
                <td className="px-3 py-2.5 align-top text-xs">
                  <span className="font-semibold text-white">
                    {pipeline?.name ?? "—"}
                  </span>
                  {pipeline?.kind && (
                    <span className="ml-2 text-[10px] uppercase tracking-widest text-white/40">
                      {pipeline.kind.replace("_", " ")}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2.5 align-top text-xs text-white/55">
                  {run.trigger}
                </td>
                <td className="px-3 py-2.5 align-top">
                  <Badge tone={pipelineRunTone(run)} dot>
                    {run.status}
                  </Badge>
                </td>
                <td className="px-3 py-2.5 align-top text-xs text-white/55">
                  {run.started_at ? relativeTime(run.started_at) : "—"}
                </td>
                <td className="px-3 py-2.5 align-top text-xs text-white/65">
                  {run.summary ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function pipelineRunTone(run: ApiPipelineRun): BadgeTone {
  if (run.status === "succeeded") return "ok";
  if (run.status === "failed") return "err";
  if (run.status === "running") return "info";
  return "neutral";
}

/**
 * Tiny relative-time formatter matching the look of the mock's
 * ``relativeTime`` helper. Pure to keep this component server-safe.
 */
function relativeTime(iso: string): string {
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const diffSec = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const min = Math.round(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}
