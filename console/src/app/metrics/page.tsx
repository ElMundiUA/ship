import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  type ApiMetricsOverview,
  type MetricsWindow,
  ApiHttpError,
  ApiUnavailableError,
  getMetricsOverview,
  isApiConfigured,
  listActivatedRepos,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * SHIP-book metrics dashboard (D11).
 *
 * One aggregator endpoint on the backend (`/metrics/overview`) feeds
 * every panel here — we never fan out to per-resource GETs. The page
 * is static-rendered per request so the numbers match the moment you
 * hit it; no client-side revalidation yet (add it when someone opens
 * a support issue about drift).
 *
 * **DORA honesty:** the DORA panel is an approximation. Deploy
 * frequency is merged-PR velocity (we don't track deploys
 * explicitly), MTTR is null (needs failure→recovery linking we
 * don't have yet). Labelled as such in the UI so the customer
 * doesn't read it as gospel.
 */

export const dynamic = "force-dynamic";

const VALID_WINDOWS: readonly MetricsWindow[] = ["7d", "30d", "90d"];

export default async function MetricsPage({
  searchParams,
}: {
  searchParams: Promise<{ window?: string }>;
}) {
  const params = await searchParams;
  if (!isApiConfigured()) {
    return (
      <AppShell title="Metrics">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load SHIP-book metrics."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fmetrics");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fmetrics");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];
  const windowValue: MetricsWindow =
    VALID_WINDOWS.includes(params.window as MetricsWindow)
      ? (params.window as MetricsWindow)
      : "30d";

  let overview: ApiMetricsOverview;
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  try {
    [overview, repos] = await Promise.all([
      getMetricsOverview(workspace.id, windowValue, { token }),
      listActivatedRepos(workspace.id, token).catch(() => []),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fmetrics");
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Metrics"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repos[0]?.id ?? null,
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
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs text-white/55">
          Workspace-wide pulse: delivery flow (DORA-style), pipeline
          health, and agent-surface throughput. All numbers are
          snapshotted when the page loads and cover the selected
          window.
        </p>
        <div className="flex gap-2 rounded-md border border-white/10 bg-white/5 p-1 text-xs">
          {VALID_WINDOWS.map((w) => (
            <Link
              key={w}
              href={`/metrics?window=${w}`}
              className={`rounded px-3 py-1 font-semibold transition ${
                windowValue === w
                  ? "bg-white/15 text-white"
                  : "text-white/60 hover:text-white"
              }`}
            >
              {w}
            </Link>
          ))}
        </div>
      </div>

      <DoraSection overview={overview} />
      <div className="mt-6">
        <AgentSection overview={overview} />
      </div>
      <div className="mt-6">
        <PipelinesSection overview={overview} />
      </div>
    </AppShell>
  );
}

// ---------------------------------------------------------------------------
// Panels
// ---------------------------------------------------------------------------

function DoraSection({ overview }: { overview: ApiMetricsOverview }) {
  const d = overview.dora;
  return (
    <section>
      <SectionHeader
        title="Delivery flow (DORA-ish)"
        hint={`Window: ${overview.window_days}d · approximations — deploys = merged PRs`}
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Deploy frequency"
          value={formatRate(d.deploy_frequency_per_day, "/day")}
          sub={`${d.prs_merged} merged PRs`}
        />
        <Stat
          label="Lead time for change"
          value={formatHours(d.avg_lead_time_hours)}
          sub="opened → merged (mean)"
        />
        <Stat
          label="Change failure rate"
          value={formatPct(d.change_failure_rate)}
          sub={`${d.workflow_runs_failed}/${d.workflow_runs_total} workflow runs failed`}
        />
        <Stat
          label="MTTR"
          value={d.mttr_hours === null ? "—" : formatHours(d.mttr_hours)}
          sub={d.mttr_hours === null ? "coming soon" : "mean time to recovery"}
          muted={d.mttr_hours === null}
        />
      </div>
    </section>
  );
}

function AgentSection({ overview }: { overview: ApiMetricsOverview }) {
  const c = overview.clarifications;
  const i = overview.improvements;
  const ch = overview.chat;
  return (
    <section>
      <SectionHeader
        title="Agent surface"
        hint="How much work the agent surfaces vs. how much a human closes out"
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader
            title="Clarifications"
            subtitle={`Median turnaround ${formatHours(c.median_resolution_hours)}`}
          />
          <BreakdownRow label="Open" value={c.open} tone="warn" />
          <BreakdownRow label="Answered" value={c.answered} tone="ok" />
          <BreakdownRow label="Skipped" value={c.skipped} tone="muted" />
          <BreakdownRow label="Stale" value={c.stale} tone="err" />
          <div className="mt-3 border-t border-white/5 pt-3 text-[11px] text-white/60">
            Answer rate: <strong>{formatPct(c.answer_rate)}</strong>
          </div>
        </Card>
        <Card>
          <CardHeader
            title="Improvements"
            subtitle={`${i.total} proposals · ${formatPct(i.accept_rate)} accepted`}
          />
          <BreakdownRow label="Pending" value={i.pending} tone="warn" />
          <BreakdownRow label="Accepted" value={i.accepted} tone="ok" />
          <BreakdownRow label="Declined" value={i.declined} tone="err" />
          <BreakdownRow label="Later" value={i.deferred} tone="muted" />
        </Card>
        <Card>
          <CardHeader
            title="Chat threads"
            subtitle={`${ch.messages_total} messages total`}
          />
          <BreakdownRow label="Active" value={ch.threads_active} tone="warn" />
          <BreakdownRow label="Resolved" value={ch.threads_resolved} tone="ok" />
          <BreakdownRow label="Archived" value={ch.threads_archived} tone="muted" />
          <div className="mt-3 border-t border-white/5 pt-3 text-[11px] text-white/60">
            Thread → ticket rate: <strong>{formatPct(ch.ticket_rate)}</strong>
          </div>
        </Card>
      </div>
    </section>
  );
}

function PipelinesSection({ overview }: { overview: ApiMetricsOverview }) {
  const r = overview.runs;
  const p = overview.pipelines;
  return (
    <section>
      <SectionHeader
        title="Pipeline health"
        hint={`${p.enabled}/${p.total} lanes enabled · ${r.total} runs in window`}
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Success rate"
          value={formatPct(r.success_rate)}
          sub={`${r.succeeded} succeeded / ${r.failed} failed`}
        />
        <Stat
          label="Runs in window"
          value={r.total.toLocaleString()}
          sub={`${r.running} still running`}
        />
        <Stat
          label="Avg duration"
          value={formatDuration(r.avg_duration_seconds)}
          sub="terminal runs only"
        />
        <Stat
          label="Lanes configured"
          value={`${p.enabled}/${p.total}`}
          sub={`${p.disabled} disabled`}
        />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader title="Runs by kind" subtitle="Which lanes are actually busy" />
          {r.by_kind.length === 0 ? (
            <EmptyState />
          ) : (
            <ul className="divide-y divide-white/5 text-[12px]">
              {r.by_kind.map((k) => (
                <li
                  key={k.kind}
                  className="flex items-center justify-between py-2"
                >
                  <span className="font-semibold text-white/80">{k.kind}</span>
                  <span className="flex items-center gap-2 text-white/60">
                    <span>{k.total} runs</span>
                    <Badge>{formatPct(k.success_rate)}</Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader
            title="Runs by trigger"
            subtitle="Manual vs. webhook vs. auto-seeded"
          />
          {r.by_trigger.length === 0 ? (
            <EmptyState />
          ) : (
            <ul className="divide-y divide-white/5 text-[12px]">
              {r.by_trigger.map((b) => (
                <li
                  key={b.key}
                  className="flex items-center justify-between py-2"
                >
                  <span className="font-semibold text-white/80">{b.key}</span>
                  <span className="text-white/60">{b.value}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Small primitives
// ---------------------------------------------------------------------------

function SectionHeader({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="text-sm font-semibold tracking-tight text-white">
        {title}
      </h2>
      {hint ? (
        <span className="text-[11px] text-white/45">{hint}</span>
      ) : null}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  muted,
}: {
  label: string;
  value: string;
  sub?: string;
  muted?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border border-white/10 bg-white/[0.03] p-4 ${
        muted ? "opacity-70" : ""
      }`}
    >
      <div className="text-[10px] uppercase tracking-wider text-white/50">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold text-white">{value}</div>
      {sub ? (
        <div className="mt-1 text-[11px] text-white/55">{sub}</div>
      ) : null}
    </div>
  );
}

function BreakdownRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "ok" | "warn" | "err" | "muted";
}) {
  const toneClass =
    tone === "ok"
      ? "text-emerald-300"
      : tone === "warn"
        ? "text-amber-200"
        : tone === "err"
          ? "text-rose-300"
          : "text-white/50";
  return (
    <div className="flex items-center justify-between py-1 text-[12px]">
      <span className="text-white/70">{label}</span>
      <span className={`font-mono font-semibold ${toneClass}`}>{value}</span>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="py-4 text-center text-[11px] text-white/40">
      No data in this window yet.
    </div>
  );
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatPct(ratio: number | null): string {
  if (ratio === null || Number.isNaN(ratio)) return "—";
  return `${Math.round(ratio * 100)}%`;
}

function formatRate(rate: number | null, suffix: string): string {
  if (rate === null || Number.isNaN(rate)) return "—";
  return `${rate.toFixed(2)}${suffix}`;
}

function formatHours(hours: number | null): string {
  if (hours === null || Number.isNaN(hours)) return "—";
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 48) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(2)}h`;
}

function renderUnavailable(err: unknown) {
  const message =
    err instanceof ApiUnavailableError
      ? "Backend unreachable — spin up the API and refresh."
      : err instanceof ApiHttpError
        ? `Backend returned ${err.status}.`
        : "Something went sideways loading metrics.";
  return (
    <AppShell title="Metrics">
      <Card>
        <CardHeader title="Metrics unavailable" subtitle={message} />
      </Card>
    </AppShell>
  );
}
