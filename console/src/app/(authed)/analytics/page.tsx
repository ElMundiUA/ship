/**
 * Analytics — DORA + live system telemetry surface.
 *
 * Server component that fetches the four DORA metrics over a rolling
 * window (30 / 90 days, switched via ``?days=…``) and the live-system
 * aggregator (relocated from the home page in PR-D1). Charts render
 * client-side via ``recharts`` in :module:`./_components/charts`.
 *
 * Layout: a 4-up grid of DORA cards (Deployment Frequency, Lead Time,
 * Change Failure Rate, MTTR) on top — each card carries a headline
 * number, a DORA performance band label, and a tiny chart. Below the
 * grid, a ``Live system`` block (Knowledge / Routines / Daily /
 * Specialists) for ambient health, and a ``Recent`` activity list.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { DashboardLiveSystem } from "@/components/dashboard/live-system";
import {
  CfrChart,
  DfChart,
  LtChart,
  MttrSparkline,
} from "@/components/analytics/charts";
import { cn } from "@/lib/cn";
import {
  ApiHttpError,
  getDoraAnalytics,
  getLiveSystem,
  getOpsDashboard,
  type ApiDoraResponse,
  type ApiLiveSystem,
  type ApiOpsDashboard,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

const ALLOWED_WINDOWS = [30, 90, 180] as const;
type WindowDays = (typeof ALLOWED_WINDOWS)[number];

type Mode =
  | {
      source: "live";
      workspaceId: string;
      multiWs: boolean;
      windowDays: WindowDays;
      dora: ApiDoraResponse;
      liveSystem: ApiLiveSystem | null;
      summary: ApiOpsDashboard | null;
    }
  | { source: "down"; reason: string };

function parseWindow(raw: string | string[] | undefined): WindowDays {
  const v = typeof raw === "string" ? Number.parseInt(raw, 10) : NaN;
  return ALLOWED_WINDOWS.includes(v as WindowDays) ? (v as WindowDays) : 30;
}

async function load(
  windowDays: WindowDays,
  searchParams: Record<string, string | string[] | undefined>,
): Promise<Mode> {
  const token = await getCachedSessionToken();
  if (!token) {
    redirect("/login?next=%2Fanalytics&reason=session_expired");
  }

  const ws = await getCachedWorkspaces();
  const resolved = await getResolvedWorkspaceId(searchParams, ws);
  const workspace = pickWorkspace(ws, resolved);

  let dora: ApiDoraResponse;
  try {
    dora = await getDoraAnalytics(workspace.id, windowDays, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fanalytics&reason=session_expired");
    }
    return {
      source: "down",
      reason:
        err instanceof Error
          ? err.message
          : "Could not load DORA analytics.",
    };
  }

  // Live system + ops summary are best-effort — a missing block
  // shouldn't block the DORA grid which is the headline content.
  const [liveSystem, summary] = await Promise.all([
    getLiveSystem(workspace.id, token).catch(() => null),
    getOpsDashboard(workspace.id, token).catch(() => null),
  ]);

  return {
    source: "live",
    workspaceId: workspace.id,
    multiWs: ws.length > 1,
    windowDays,
    dora,
    liveSystem,
    summary,
  };
}

export default async function AnalyticsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const windowDays = parseWindow(params.days);
  const data = await load(windowDays, params);

  if (data.source === "down") {
    return (
      <>
        <PageHeader kicker="data" title="Analytics" />
        <PageBody>
          <ApiUnavailable scope="analytics" details={data.reason} />
        </PageBody>
      </>
    );
  }

  const { workspaceId, multiWs, dora, liveSystem, summary } = data;
  const wsScope = multiWs ? `?ws=${encodeURIComponent(workspaceId)}` : "";

  return (
    <>
      <PageHeader
        kicker="data"
        title="Analytics"
        actions={<WindowPicker current={windowDays} workspaceScope={wsScope} />}
      />
      <PageBody>
        <div className="mx-auto max-w-6xl space-y-12">
          <DoraGrid dora={dora} />

          {liveSystem !== null && (
            <section className="space-y-4">
              <header className="flex items-baseline justify-between">
                <h2 className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/45">
                  Live system
                </h2>
                <p className="text-[11px] text-white/40">
                  Ambient health · refreshed on each render
                </p>
              </header>
              <DashboardLiveSystem data={liveSystem} />
            </section>
          )}

          {summary !== null && summary.shipped.items.length > 0 && (
            <RecentMerges summary={summary} />
          )}
        </div>
      </PageBody>
    </>
  );
}


// ---------------------------------------------------------------------------
// DORA grid — four cards, headline + band label + chart
// ---------------------------------------------------------------------------


function DoraGrid({ dora }: { dora: ApiDoraResponse }) {
  return (
    <section className="space-y-4">
      <header className="flex items-baseline justify-between">
        <h2 className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/45">
          DORA · last {dora.window_days} days
        </h2>
        <p className="text-[11px] text-white/40">
          Computed{" "}
          {new Date(dora.computed_at).toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
          })}
        </p>
      </header>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <DoraCard
          title="Deployment frequency"
          headline={formatPerDay(dora.deployment_frequency.per_day_median)}
          sub={`${dora.deployment_frequency.total_deploys} deploys total · p90 ${formatPerDay(dora.deployment_frequency.per_day_p90)}`}
          band={dora.deployment_frequency.label}
        >
          <DfChart data={dora.deployment_frequency.time_series} />
        </DoraCard>
        <DoraCard
          title="Lead time for changes"
          headline={formatHours(dora.lead_time.median_hours)}
          sub={`${dora.lead_time.sample_size} merged PRs · p90 ${formatHours(dora.lead_time.p90_hours)}`}
          band={dora.lead_time.label}
        >
          <LtChart data={dora.lead_time.time_series} />
        </DoraCard>
        <DoraCard
          title="Change failure rate"
          headline={formatRate(dora.change_failure_rate.rate)}
          sub={`${dora.change_failure_rate.failed_deploys} failed deploys · ${dora.change_failure_rate.hotfix_prs} hotfix PRs · ${dora.change_failure_rate.total_deploys} total`}
          band={dora.change_failure_rate.label}
        >
          <CfrChart data={dora.change_failure_rate.time_series} />
        </DoraCard>
        <DoraCard
          title="Mean time to recovery"
          headline={formatHours(dora.mttr.median_hours)}
          sub={
            dora.mttr.incidents.length === 0
              ? "no incidents in window"
              : `${dora.mttr.incidents.filter((i) => i.recovered_at).length} recovered · ${dora.mttr.incidents.filter((i) => i.recovered_at === null).length} open · p90 ${formatHours(dora.mttr.p90_hours)}`
          }
          band={dora.mttr.label}
        >
          <MttrSparkline incidents={dora.mttr.incidents} />
        </DoraCard>
      </div>
    </section>
  );
}


function DoraCard({
  title,
  headline,
  sub,
  band,
  children,
}: {
  title: string;
  headline: string;
  sub: string;
  band: string;
  children: React.ReactNode;
}) {
  return (
    <article className="space-y-4 rounded-xl border border-white/[0.08] bg-white/[0.02] p-5">
      <header className="space-y-1">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
          {title}
        </p>
        <p className="font-display text-3xl font-bold text-white">
          {headline}
        </p>
        <p className="text-[11px] text-white/55">{sub}</p>
        <p
          className={cn(
            "text-[11px] font-semibold",
            band.startsWith("Elite")
              ? "text-aqua/90"
              : band.startsWith("High")
                ? "text-aqua/70"
                : band.startsWith("Medium")
                  ? "text-sun/80"
                  : band.startsWith("Low")
                    ? "text-coral"
                    : "text-white/40",
          )}
        >
          {band}
        </p>
      </header>
      <div className="h-32">{children}</div>
    </article>
  );
}


// ---------------------------------------------------------------------------
// Window picker — 30 / 90 / 180 days
// ---------------------------------------------------------------------------


function WindowPicker({
  current,
  workspaceScope,
}: {
  current: WindowDays;
  workspaceScope: string;
}) {
  return (
    <nav className="flex items-baseline gap-3 text-xs">
      {ALLOWED_WINDOWS.map((d, idx) => {
        const active = d === current;
        const sep = workspaceScope ? "&" : "?";
        const href = `/analytics${workspaceScope}${sep}days=${d}`;
        return (
          <span key={d} className="flex items-baseline">
            <Link
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "transition",
                active
                  ? "font-semibold text-white"
                  : "text-white/45 hover:text-white",
              )}
            >
              {d}d
            </Link>
            {idx < ALLOWED_WINDOWS.length - 1 && (
              <span className="ml-3 text-white/15">·</span>
            )}
          </span>
        );
      })}
    </nav>
  );
}


// ---------------------------------------------------------------------------
// Recent merges — relocated from the old home right-rail
// ---------------------------------------------------------------------------


function RecentMerges({ summary }: { summary: ApiOpsDashboard }) {
  const items = summary.shipped.items.slice(0, 12);
  return (
    <section className="space-y-3">
      <header className="flex items-baseline justify-between">
        <h2 className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/45">
          Recent · last 24h
        </h2>
        <p className="text-[11px] text-white/40">
          {summary.shipped.features_shipped_count} features ·{" "}
          {summary.shipped.fixes_count} fixes ·{" "}
          {summary.shipped.rollbacks_count} rollbacks
        </p>
      </header>
      <ul className="divide-y divide-white/[0.06]">
        {items.map((item, idx) => (
          <li
            key={`${idx}-${item.name}`}
            className="flex items-baseline justify-between gap-4 py-3"
          >
            <div className="min-w-0">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
                {item.type}
                {item.repo && (
                  <>
                    <span className="mx-2 text-white/15">·</span>
                    <span className="font-normal normal-case tracking-normal">
                      {item.repo}
                    </span>
                  </>
                )}
              </p>
              <p className="mt-1 truncate text-sm text-white">{item.name}</p>
            </div>
            {item.href && (
              <a
                href={item.href}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 text-[11px] font-semibold text-white/55 hover:text-white"
              >
                Open →
              </a>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}


// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------


function formatPerDay(n: number): string {
  if (n >= 1) return `${n.toFixed(n >= 10 ? 0 : 1)}/day`;
  if (n === 0) return "—";
  // < 1 / day → flip to the canonical DORA cadence labels.
  const perWeek = n * 7;
  if (perWeek >= 1) return `${perWeek.toFixed(1)}/week`;
  const perMonth = n * 30;
  return `${perMonth.toFixed(1)}/month`;
}

function formatHours(n: number | null): string {
  if (n === null || Number.isNaN(n)) return "—";
  if (n < 1) return `${Math.round(n * 60)}m`;
  if (n < 24) return `${n.toFixed(1)}h`;
  const days = n / 24;
  if (days < 7) return `${days.toFixed(1)}d`;
  const weeks = days / 7;
  if (weeks < 4) return `${weeks.toFixed(1)}w`;
  const months = days / 30;
  return `${months.toFixed(1)}mo`;
}

function formatRate(n: number): string {
  return `${Math.round(n * 100)}%`;
}
