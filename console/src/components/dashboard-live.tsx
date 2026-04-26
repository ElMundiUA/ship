import Link from "next/link";

import {
  Badge,
  Card,
  CardHeader,
  StatTile,
  type BadgeTone,
} from "@/components/ui";
import type {
  ApiDashboard,
  ApiDashboardPullRequest,
  ApiDashboardWorkflowRun,
  ApiWorkspaceNotification,
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
  banner,
}: {
  workspaceId: string;
  workspaceName: string;
  workspaceSlug: string;
  data: ApiDashboard;
  banner?: { kind: string; reason: string; detail?: string };
}) {
  const bannerInfo = banner ? RUN_REASONS[banner.reason] ?? null : null;
  const setupComplete = data.counts.active_repos > 0;
  const notifications = data.notifications ?? [];

  return (
    <>
      {notifications.length > 0 && (
        <NotificationRail
          workspaceId={workspaceId}
          notifications={notifications}
        />
      )}

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
          label="Open PRs (last 10)"
          value={String(data.counts.open_pull_requests)}
          hint="Updated by the GitHub webhook"
        />
        <StatTile
          label="Inbox"
          value={String(data.notifications.length)}
          hint="Current workspace notifications"
        />
      </section>

      <section className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Recent pull requests"
            subtitle="Live from your activated GitHub repos"
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

/**
 * A4 "PR merged" + A5 "self-heal dispatched" banners, rendered as a
 * dismissible rail above the dashboard. The rail is *authoritative*
 * for onboarding ping-backs: the query-string ``?reason=`` banner
 * above it is a one-shot navigation side effect, but these rows live
 * in the database and stay visible across refreshes until the user
 * clears them.
 *
 * Implemented as plain ``<form method="POST">`` so dismissing works
 * even with JS disabled (useful when the dashboard is framed inside
 * a restrictive CSP context, e.g. during enterprise evaluation).
 */
function NotificationRail({
  workspaceId,
  notifications,
}: {
  workspaceId: string;
  notifications: ApiWorkspaceNotification[];
}) {
  return (
    <div className="mb-4 flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-white/55">
          What’s new
        </h2>
        {notifications.length > 1 && (
          <form
            action="/api/dashboard/dismiss-notification"
            method="POST"
            className="inline"
          >
            <input type="hidden" name="ws" value={workspaceId} />
            <input type="hidden" name="all" value="1" />
            <button
              type="submit"
              className="text-[11px] font-semibold text-white/55 hover:text-white/80"
            >
              Clear all
            </button>
          </form>
        )}
      </div>
      {notifications.map((n) => (
        <NotificationCard
          key={n.id}
          workspaceId={workspaceId}
          notification={n}
        />
      ))}
    </div>
  );
}

const NOTIFICATION_TONES: Record<string, BadgeTone> = {
  pr_merged: "ok",
  self_heal_dispatched: "info",
  self_heal_skipped: "warn",
};

const NOTIFICATION_LABELS: Record<string, string> = {
  pr_merged: "PR merged",
  self_heal_dispatched: "Self-heal",
  self_heal_skipped: "Self-heal skipped",
};

function NotificationCard({
  workspaceId,
  notification,
}: {
  workspaceId: string;
  notification: ApiWorkspaceNotification;
}) {
  const tone = NOTIFICATION_TONES[notification.kind] ?? "neutral";
  const label = NOTIFICATION_LABELS[notification.kind] ?? notification.kind;
  // href can point at github.com (PR URL) or an internal route. We
  // open external links in a new tab so the dashboard stays put, but
  // internal routes (``/runs/...``) navigate in-place for a
  // normal app feel.
  const isExternal =
    !!notification.href && /^https?:\/\//i.test(notification.href);
  return (
    <div
      className="flex flex-col gap-2 rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4"
      role="status"
    >
      <div className="flex flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={tone}>{label}</Badge>
          <span className="text-sm font-semibold text-white">
            {notification.title}
          </span>
          <span className="text-[11px] text-white/45">
            {relativeTime(notification.created_at)}
          </span>
        </div>
        {notification.body && (
          <p className="text-xs leading-snug text-white/70">
            {notification.body}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {notification.href &&
          (isExternal ? (
            <a
              href={notification.href}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-semibold text-aqua hover:bg-aqua/20"
            >
              Open ↗
            </a>
          ) : (
            <Link
              href={notification.href}
              className="inline-flex items-center rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-semibold text-aqua hover:bg-aqua/20"
            >
              Open →
            </Link>
          ))}
        <form
          action="/api/dashboard/dismiss-notification"
          method="POST"
          className="inline"
        >
          <input type="hidden" name="ws" value={workspaceId} />
          <input type="hidden" name="notification" value={notification.id} />
          <button
            type="submit"
            aria-label="Dismiss notification"
            title="Dismiss"
            className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/10 text-white/55 hover:border-white/30 hover:text-white"
          >
            ×
          </button>
        </form>
      </div>
    </div>
  );
}
