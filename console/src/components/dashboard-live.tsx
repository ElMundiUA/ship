import Link from "next/link";

import {
  Badge,
  Card,
  CardHeader,
  StatTile,
  ToggleSwitch,
  type BadgeTone,
} from "@/components/ui";
import type {
  ApiActivatedRepo,
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

// Must stay in lockstep with ``backend.app.services.default_pipelines.KNOWN_PRESETS``.
// The dashboard picker is intentionally terser than the onboarding wizard's
// — this is a "I know what I'm doing" admin affordance, not the first-time
// decision surface.
const PRESET_CHOICES: { id: string; label: string }[] = [
  { id: "web-app", label: "Web app" },
  { id: "api-backend", label: "API backend" },
  { id: "mobile-app", label: "Mobile app" },
  { id: "cli", label: "CLI / tool" },
  { id: "monorepo", label: "Monorepo" },
  { id: "adoption-minimum", label: "Adoption-minimum" },
];

const RUN_REASONS: Record<string, { tone: BadgeTone; label: string }> = {
  ran: { tone: "ok", label: "Pipeline run finished." },
  dispatched: { tone: "info", label: "Dispatch sent — watch the run land in seconds." },
  enabled: { tone: "ok", label: "Pipeline enabled." },
  disabled: { tone: "warn", label: "Pipeline disabled." },
  forbidden: { tone: "err", label: "You need admin to do that." },
  disabled_pipeline: { tone: "warn", label: "Pipeline is off — enable it first." },
  missing: { tone: "err", label: "That pipeline is gone — refresh." },
  api_unavailable: { tone: "err", label: "Backend is unreachable. Try again." },
  unknown: { tone: "err", label: "Something went wrong. Try again." },
  // Day-4 Phase-1 dispatcher precondition codes
  precondition_workflow_not_installed: {
    tone: "warn",
    label: "Install the workflow PR first — Run now needs the YAML in your repo.",
  },
  precondition_pipeline_not_bound: {
    tone: "warn",
    label:
      "No activated repo to attach this pipeline to. Open the wizard and activate at least one repo, then try again.",
  },
  precondition_kind_not_supported_yet: {
    tone: "neutral",
    label: "This pipeline lane lights up with Phase 2 presets.",
  },
  precondition_github_app_missing: {
    tone: "err",
    label: "GitHub App is gone — reinstall to keep dispatching.",
  },
  precondition_precondition: {
    tone: "warn",
    label: "Precondition failed — refresh and try again.",
  },
  dispatch_failed: { tone: "err", label: "GitHub rejected the dispatch — see audit log." },
  installed: { tone: "ok", label: "Install PR opened — merge it to unlock Run now." },
  bundle_installed: {
    tone: "ok",
    label:
      "Bundle PR opened — merging it wires every preset lane in one go, then knowledge pipelines auto-fire.",
  },
  bundle_preset_required: {
    tone: "warn",
    label:
      "Pick a preset on the repo first (or add one via the onboarding wizard) so we know which workflows to install.",
  },
  bundle_empty_bundle: {
    tone: "warn",
    label:
      "Selected preset has no installable workflows yet — they're still catalog-only. Fall back to per-lane Install for now.",
  },
  bundle_invalid_preset: {
    tone: "err",
    label: "Unknown preset id. The picker only accepts presets in the catalog.",
  },
  bundle_upstream: {
    tone: "err",
    label: "GitHub refused the bundle PR — check the App permissions.",
  },
  back_from_pr: {
    tone: "ok",
    label:
      "Welcome back! If you merged the install PR, refresh in ~30s — the dashboard probes GitHub on a 60s TTL.",
  },
  disconnected: {
    tone: "ok",
    label:
      "Repo disconnected. Lanes + runs wiped; remove the Ship App on GitHub to drop the webhook too.",
  },
  disconnect_confirm_missing: {
    tone: "warn",
    label: "Type \"disconnect\" to confirm — keeps accidental clicks safe.",
  },
  disconnect_missing: {
    tone: "warn",
    label: "Repo already gone — nothing to disconnect.",
  },
  preset_updated: {
    tone: "ok",
    label: "Preset updated. New lanes (if any) are seeded; run them when you're ready.",
  },
  preset_invalid: {
    tone: "warn",
    label: "That preset is not recognised — pick from the dropdown.",
  },
  preset_missing_repo: {
    tone: "warn",
    label: "Repo gone — refresh the dashboard and try again.",
  },
  install_kind_not_supported_yet: {
    tone: "neutral",
    label: "No starter workflow for this kind yet — Phase 2.",
  },
  install_pipeline_not_bound: {
    tone: "warn",
    label:
      "No activated repo yet — finish the onboarding wizard (or activate a repo in the Repos tab), then hit Install again.",
  },
  install_github_app_missing: {
    tone: "err",
    label: "GitHub App is gone — reinstall before installing the workflow.",
  },
  install_install_pr_failed: {
    tone: "err",
    label: "GitHub refused the install PR — check repo permissions.",
  },
  install_upstream: {
    tone: "err",
    label: "GitHub refused the install PR — see details below and re-check App perms.",
  },
  install_upstream_workflows_scope: {
    tone: "err",
    label:
      'GitHub App is missing the "Workflows" permission (Read & Write). Open the App settings → Permissions → Workflows, accept the update, then retry Install.',
  },
  install_upstream_contents_scope: {
    tone: "err",
    label:
      'GitHub App is missing "Contents" (Read & Write). Grant it in the App settings, accept the permissions update, then retry.',
  },
  install_upstream_pulls_scope: {
    tone: "err",
    label:
      'GitHub App is missing "Pull requests" (Read & Write). Grant it in the App settings, accept the permissions update, then retry.',
  },
  install_upstream_repo_not_selected: {
    tone: "warn",
    label:
      "The App isn't granted access to this repo. Open the App installation in GitHub, add the repo to the selected list, then retry.",
  },
};

export function DashboardLive({
  workspaceId,
  workspaceName,
  workspaceSlug,
  data,
  repos,
  banner,
}: {
  workspaceId: string;
  workspaceName: string;
  workspaceSlug: string;
  data: ApiDashboard;
  repos: ApiActivatedRepo[];
  banner?: { kind: string; reason: string; detail?: string };
}) {
  const bannerInfo = banner ? RUN_REASONS[banner.reason] ?? null : null;
  const setupComplete = data.counts.active_repos > 0;

  return (
    <>
      {bannerInfo && (
        <div
          className="mb-4 flex flex-col gap-1 rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm"
          role="status"
        >
          <div className="flex items-start gap-2">
            <Badge tone={bannerInfo.tone}>{banner!.kind}</Badge>
            <span className="text-white/80">{bannerInfo.label}</span>
          </div>
          {banner?.detail && (
            <p className="ml-[52px] break-words font-mono text-[11px] leading-snug text-white/55">
              {banner.detail}
            </p>
          )}
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

      <RepoStatusStrip
        repos={repos}
        pipelines={data.pipelines}
        workspaceId={workspaceId}
      />

      <RecommendedActions
        pipelines={data.pipelines}
        workspaceId={workspaceId}
      />

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

/**
 * Dashboard "Recommended actions" strip.
 *
 * Distinct from the dedicated Pipelines page (``/pipelines``): the
 * dashboard only ever surfaces pipelines you can fire *right now* —
 * a small, opinionated set of quick CTAs ("re-run PR gate on the
 * default repo", etc.) — so the operator doesn't have to read a
 * five-card matrix every time they open the home page. Anything that
 * needs an Install-PR or is a Phase-2 preset is a navigation away,
 * not in the operator's face.
 */
/**
 * Per-repo "Ship status" strip. For each activated repo we roll up:
 *
 *   preset · pipelines enabled/total · workflows installed · last run.
 *
 * This answers the operator's first question on the dashboard —
 * "is Ship actually live on my repo?" — without making them scan the
 * whole Recommended-actions grid or jump to ``/pipelines``.
 */
function RepoStatusStrip({
  repos,
  pipelines,
  workspaceId,
}: {
  repos: ApiActivatedRepo[];
  pipelines: ApiPipeline[];
  workspaceId: string;
}) {
  if (repos.length === 0) return null;
  const byRepo = new Map<string, ApiPipeline[]>();
  for (const p of pipelines) {
    if (!p.repo_id) continue;
    const bucket = byRepo.get(p.repo_id) ?? [];
    bucket.push(p);
    byRepo.set(p.repo_id, bucket);
  }
  return (
    <section className="mt-8">
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <h3 className="font-display text-base font-bold text-white">
            Ship status per repo
          </h3>
          <p className="mt-1 text-xs text-white/55">
            Roll-up of presets, installed workflows, and the most recent run
            for every activated repository.
          </p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {repos.map((repo) => {
          const laneRows = byRepo.get(repo.id) ?? [];
          const totalLanes = laneRows.length;
          const enabledLanes = laneRows.filter((p) => p.enabled).length;
          const installedLanes = laneRows.filter(
            (p) => p.workflow_installed === true,
          ).length;
          const needsInstallLanes = laneRows.filter(
            (p) => pipelineCardState(p) === "needs-install",
          ).length;
          const lastRunTs = laneRows.reduce<string | null>((acc, p) => {
            if (!p.last_run_at) return acc;
            if (!acc) return p.last_run_at;
            return p.last_run_at > acc ? p.last_run_at : acc;
          }, null);
          const lastStatus = (() => {
            if (!lastRunTs) return null;
            const hit = laneRows.find((p) => p.last_run_at === lastRunTs);
            return hit?.last_run_status ?? null;
          })();

          const setupComplete =
            totalLanes > 0 && installedLanes === totalLanes;
          const tone: BadgeTone = setupComplete
            ? "ok"
            : needsInstallLanes > 0
              ? "warn"
              : "err";
          const toneLabel = setupComplete
            ? "setup complete"
            : needsInstallLanes > 0
              ? `${needsInstallLanes} install pending`
              : "no lanes yet";

          return (
            <Card key={repo.id} className="flex flex-col gap-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-mono text-sm font-semibold text-white">
                    {repo.full_name}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge tone="neutral">
                      {repo.preset ?? "adoption-minimum"}
                    </Badge>
                    <Badge tone={tone}>{toneLabel}</Badge>
                    {repo.private && <Badge tone="neutral">private</Badge>}
                  </div>
                </div>
                <a
                  href={repo.html_url}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 text-[11px] font-semibold text-white/55 hover:text-aqua"
                >
                  github →
                </a>
              </div>

              <dl className="grid grid-cols-3 gap-2 text-[11px] uppercase tracking-wide text-white/45">
                <div>
                  <dt>Lanes</dt>
                  <dd className="mt-0.5 text-sm font-semibold text-white/85">
                    {enabledLanes}/{totalLanes}
                  </dd>
                </div>
                <div>
                  <dt>Workflows</dt>
                  <dd className="mt-0.5 text-sm font-semibold text-white/85">
                    {installedLanes}/{totalLanes}
                  </dd>
                </div>
                <div>
                  <dt>Last run</dt>
                  <dd className="mt-0.5 text-sm font-semibold text-white/85">
                    {lastRunTs ? relativeTime(lastRunTs) : "—"}
                    {lastStatus && (
                      <span className="ml-1 text-[10px] font-semibold text-white/55">
                        · {lastStatus}
                      </span>
                    )}
                  </dd>
                </div>
              </dl>

              {!setupComplete && (
                <form
                  action="/api/dashboard/install-bundle"
                  method="POST"
                  className="flex items-center justify-between gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2"
                >
                  <input type="hidden" name="ws" value={workspaceId} />
                  <input type="hidden" name="repo_id" value={repo.id} />
                  <div className="min-w-0 text-[11px] leading-snug text-white/65">
                    One PR adds every workflow + <code>.ship/</code> so lanes
                    come live at once.
                  </div>
                  <button
                    type="submit"
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3 py-1 text-[11px] font-bold text-ink shadow-glow transition hover:brightness-110"
                  >
                    Install everything →
                  </button>
                </form>
              )}

              <div className="mt-auto flex items-center justify-between text-[11px] font-semibold text-white/55">
                <Link
                  href={`/pipelines?repo=${encodeURIComponent(repo.full_name)}`}
                  className="hover:text-aqua"
                >
                  Open lanes →
                </Link>
                <span>
                  activated{" "}
                  {repo.activated_at ? relativeTime(repo.activated_at) : "?"}
                </span>
              </div>

              <details className="group rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-[11px] leading-snug">
                <summary className="cursor-pointer list-none text-white/55 hover:text-white">
                  Change preset →
                </summary>
                <form
                  action="/api/dashboard/update-preset"
                  method="POST"
                  className="mt-2 space-y-2"
                >
                  <input type="hidden" name="ws" value={workspaceId} />
                  <input type="hidden" name="repo_id" value={repo.id} />
                  <label className="block">
                    <span className="sr-only">Preset</span>
                    <select
                      name="preset"
                      defaultValue={repo.preset ?? ""}
                      className="w-full rounded-md border border-white/15 bg-black/30 px-2 py-1 text-[11px] text-white focus:border-aqua/60 focus:outline-none"
                    >
                      <option value="">(default shape)</option>
                      {PRESET_CHOICES.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex items-center gap-2 text-white/60">
                    <input
                      type="checkbox"
                      name="reshape"
                      className="h-3 w-3 accent-aqua"
                    />
                    Re-apply enabled/disabled to match preset (otherwise
                    additive — new lanes seed, existing stay as-is)
                  </label>
                  <button
                    type="submit"
                    className="inline-flex items-center gap-1.5 rounded-full border border-white/20 bg-white/5 px-3 py-1 text-[11px] font-semibold text-white/90 transition hover:border-aqua/60 hover:bg-aqua/10"
                  >
                    Save preset
                  </button>
                </form>
              </details>

              <details className="group rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-[11px] leading-snug">
                <summary className="cursor-pointer list-none text-white/55 hover:text-coral">
                  Danger zone →
                </summary>
                <div className="mt-2 space-y-2">
                  <p className="text-white/60">
                    Deletes Ship's state for <strong>{repo.full_name}</strong>:
                    every lane bound to this repo plus their run history.
                    Doesn't touch github.com — unlink the App there
                    separately.
                  </p>
                  <form
                    action="/api/dashboard/disconnect-repo"
                    method="POST"
                    className="flex flex-wrap items-center gap-2"
                  >
                    <input type="hidden" name="ws" value={workspaceId} />
                    <input type="hidden" name="repo_id" value={repo.id} />
                    <label className="flex-1 min-w-[140px]">
                      <span className="sr-only">
                        Type disconnect to confirm
                      </span>
                      <input
                        type="text"
                        name="confirm"
                        placeholder={"type: disconnect"}
                        required
                        pattern="disconnect"
                        className="w-full rounded-md border border-white/15 bg-black/30 px-2 py-1 font-mono text-[11px] text-white placeholder:text-white/35 focus:border-coral/60 focus:outline-none"
                      />
                    </label>
                    <button
                      type="submit"
                      className="inline-flex items-center gap-1.5 rounded-full border border-coral/40 bg-coral/10 px-3 py-1 text-[11px] font-semibold text-coral transition hover:border-coral/70 hover:bg-coral/20"
                    >
                      Disconnect
                    </button>
                  </form>
                </div>
              </details>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

function RecommendedActions({
  pipelines,
  workspaceId,
}: {
  pipelines: ApiPipeline[];
  workspaceId: string;
}) {
  const ready = pipelines.filter(
    (p) => pipelineCardState(p) === "run-ready" && p.enabled,
  );
  return (
    <section className="mt-8">
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <h3 className="font-display text-base font-bold text-white">
            Recommended actions
          </h3>
          <p className="mt-1 text-xs text-white/55">
            Quick triggers for the manual lanes that are wired up right now.
            Full lane catalog lives under{" "}
            <Link href="/pipelines" className="text-aqua hover:underline">
              Pipelines
            </Link>
            .
          </p>
        </div>
        <Link
          href="/pipelines"
          className="hidden whitespace-nowrap text-xs font-semibold text-aqua hover:underline sm:inline"
        >
          See all pipelines →
        </Link>
      </div>
      {ready.length === 0 ? (
        <RecommendedActionsEmpty pipelines={pipelines} />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {ready.map((p) => (
            <PipelineCard key={p.id} pipeline={p} workspaceId={workspaceId} />
          ))}
        </div>
      )}
    </section>
  );
}

function RecommendedActionsEmpty({ pipelines }: { pipelines: ApiPipeline[] }) {
  // Tell the operator *why* there's nothing to click: have we never
  // seeded any lanes (no repo activated), or are they all gated on an
  // Install PR? The two states want different next steps.
  const needsInstall = pipelines.some(
    (p) => pipelineCardState(p) === "needs-install",
  );
  return (
    <Card className="border-white/10">
      {pipelines.length === 0 ? (
        <p className="text-sm text-white/70">
          No pipelines yet. Pick at least one repo on the wizard and the
          default lanes appear automatically.
        </p>
      ) : needsInstall ? (
        <p className="text-sm text-white/70">
          Default lanes are seeded but their workflow YAMLs aren&rsquo;t in
          the repo yet. Open{" "}
          <Link href="/pipelines" className="text-aqua hover:underline">
            Pipelines
          </Link>{" "}
          to file the install PR for the lanes you want to use.
        </p>
      ) : (
        <p className="text-sm text-white/70">
          Nothing to fire manually right now. Open{" "}
          <Link href="/pipelines" className="text-aqua hover:underline">
            Pipelines
          </Link>{" "}
          for the full lane catalog.
        </p>
      )}
    </Card>
  );
}

type PipelineCardState = "run-ready" | "needs-install" | "coming-soon";

function pipelineCardState(p: ApiPipeline): PipelineCardState {
  if (!p.supports_run) return "coming-soon";
  if (p.workflow_installed === true) return "run-ready";
  // workflow_installed === false (probed, missing) OR null (unbound /
  // probe failed). Both surface the same "Install workflow" CTA — the
  // backend's 412 ``code`` will pick the right banner if anything
  // else is broken (unbound, app missing).
  return "needs-install";
}

function PipelineCard({
  pipeline,
  workspaceId,
}: {
  pipeline: ApiPipeline;
  workspaceId: string;
}) {
  const state = pipelineCardState(pipeline);
  const lastRunLabel = pipeline.last_run_at
    ? `${pipeline.last_run_status ?? "run"} · ${relativeTime(pipeline.last_run_at)}`
    : "no runs yet";
  const tone: BadgeTone =
    pipeline.last_run_status === "succeeded"
      ? "ok"
      : pipeline.last_run_status === "failed"
        ? "err"
        : pipeline.last_run_status === "running"
          ? "info"
          : pipeline.last_run_status
            ? "warn"
            : "neutral";

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone="info">{pipeline.kind.replace("_", " ")}</Badge>
            <Badge tone={pipeline.enabled ? "ok" : "neutral"}>
              {pipeline.enabled ? "enabled" : "disabled"}
            </Badge>
            {state === "coming-soon" && (
              <Badge tone="neutral">phase 2 preset</Badge>
            )}
            {state === "needs-install" && (
              <Badge tone="warn">workflow not installed</Badge>
            )}
            {state === "run-ready" && pipeline.workflow_installed && (
              <Badge tone="ok">workflow installed</Badge>
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
            {pipeline.repo_full_name && (
              <>
                {" · "}
                <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
                  {pipeline.repo_full_name}
                </code>
              </>
            )}
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
          <ToggleSwitch
            checked={pipeline.enabled}
            label={pipeline.enabled ? "Disable pipeline" : "Enable pipeline"}
          />
          <span className="text-[11px] font-semibold text-white/55">
            {pipeline.enabled ? "enabled" : "disabled"}
          </span>
        </form>

        <PipelineActionButton
          state={state}
          pipeline={pipeline}
          workspaceId={workspaceId}
        />
      </div>
    </Card>
  );
}

function PipelineActionButton({
  state,
  pipeline,
  workspaceId,
}: {
  state: PipelineCardState;
  pipeline: ApiPipeline;
  workspaceId: string;
}) {
  if (state === "coming-soon") {
    return (
      <button
        type="button"
        disabled
        title="Phase 2 ships a starter workflow for this pipeline kind."
        className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-1.5 text-xs font-bold text-white/40"
      >
        Coming with presets
      </button>
    );
  }
  if (state === "needs-install") {
    return (
      <form
        action="/api/dashboard/install-pipeline"
        method="POST"
        className="flex items-center gap-2"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="pipeline" value={pipeline.id} />
        {/* Always post the repo the card lives under — "Recommended actions"
         * only surfaces bound pipelines, so ``repo_id`` is always present
         * and the backend targets that exact repo (and rebinds if needed). */}
        {pipeline.repo_id && (
          <input type="hidden" name="repo_id" value={pipeline.repo_id} />
        )}
        <button
          type="submit"
          className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/[0.08] px-3.5 py-1.5 text-xs font-bold text-aqua hover:bg-aqua/[0.16]"
        >
          Install workflow PR →
        </button>
      </form>
    );
  }
  return (
    <form
      action="/api/dashboard/run-pipeline"
      method="POST"
      className="flex items-center gap-2"
    >
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="pipeline" value={pipeline.id} />
      {pipeline.repo_id && (
        <input type="hidden" name="repo_id" value={pipeline.repo_id} />
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
