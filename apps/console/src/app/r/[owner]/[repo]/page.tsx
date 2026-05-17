import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import {
  Badge,
  type BadgeTone,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  StatTile,
} from "@/components/ui";
import {
  ApiHttpError,
  getRepoHome,
  isApiConfigured,
  type OpsReportWindow,
} from "@/lib/api/client";
import {
  OPS_REPORT_WINDOWS,
  opsReportWindowPhrase,
  opsReportWindowShortLabel,
  parseOpsReportWindow,
} from "@/lib/ops-window";
import { getSessionToken } from "@/lib/api/session";
import type {
  ApiActivatedRepo,
  ApiRepoHomeActivityKind,
  ApiRepoHomeActivityStatus,
  ApiRepoHomeReport,
} from "@/lib/api/client";
import { resolveRepoContext, type RepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";
import { toAppShellWorkspaces, withWorkspaceQuery } from "@/lib/workspace-scope";

/**
 * Repo home (``/r/<owner>/<repo>``).
 *
 * PR-4 puts Now/Trends back on the per-repo surface (RFC-0008 §F).
 * Two lightweight tabs over a single backend snapshot
 * (``/v1/workspaces/{ws}/repos/{id}/home``):
 * - **Now** — in-flight counters, last-24h activity, lane health,
 *   bundle/install flags, a newest-first activity feed.
 * - **Trends** — 30-day histogram bucketed by UTC day (success/fail/
 *   other stacked) plus a per-lane breakdown.
 *
 * Everything below the tabs (repo-facts card, quick-nav tiles) is
 * unchanged scope-setting from PR-1.
 */

export const dynamic = "force-dynamic";

type Search = Record<string, string | string[] | undefined>;
type Tab = "now" | "trends";

function parseTab(raw: string | string[] | undefined): Tab {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v === "trends" ? "trends" : "now";
}

export default async function RepoHomePage({
  params,
  searchParams,
}: {
  params: Promise<RepoRouteParams>;
  searchParams: Promise<Search>;
}) {
  const [resolved, search] = await Promise.all([params, searchParams]);
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const tab = parseTab(search.tab);
  const opsWindow = parseOpsReportWindow(search.window);

  if (!isApiConfigured()) {
    return renderMock(slug, tab, opsWindow);
  }

  const token = await getSessionToken();
  if (!token) {
    redirect(`/login?next=${encodeURIComponent(`/r/${slug}`)}`);
  }

  const result = await resolveRepoContext(token, slug, search);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(`/r/${slug}`)}`);
  }
  if (result.kind === "down") return renderDownState(slug);
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  const ctx = result.ctx;
  let report: ApiRepoHomeReport | null = null;
  try {
    report = await getRepoHome(ctx.workspace.id, ctx.repo.id, {
      token,
      window: opsWindow,
    });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(`/r/${slug}`)}`);
    }
    // Down / transient — let the page render with the tabs disabled
    // instead of 500-ing the shell.
    report = null;
  }

  return renderRepoHome(ctx, report, tab, opsWindow);
}

function renderRepoHome(
  ctx: RepoContext,
  report: ApiRepoHomeReport | null,
  tab: Tab,
  opsWindow: OpsReportWindow,
) {
  const { workspace, allWorkspaces, repo, repos } = ctx;
  const base = `/r/${repo.full_name}`;
  const multi = allWorkspaces.length > 1;
  const settingsHref = multi
    ? `${base}/settings?ws=${encodeURIComponent(workspace.id)}`
    : `${base}/settings`;
  const processHref = withWorkspaceQuery("/process", workspace.id, multi);
  return (
    <AppShell
      title={`${repo.full_name}`}
      kicker="repo"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={toAppShellWorkspaces(allWorkspaces)}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repo.id,
      }}
      actions={
        <>
          <Link
            href={settingsHref}
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            Repo settings
          </Link>
          <ButtonPrimary>
            <Link href={processHref}>Open process →</Link>
          </ButtonPrimary>
        </>
      }
    >
      <RepoHomeBody
        repo={repo}
        base={base}
        report={report}
        tab={tab}
        opsWindow={opsWindow}
        workspaceId={workspace.id}
        multiWs={multi}
      />
    </AppShell>
  );
}

function renderMock(slug: string, tab: Tab, opsWindow: OpsReportWindow = "24h") {
  const base = `/r/${slug}`;
  const ownerRepo = slug;
  return (
    <AppShell title={ownerRepo} kicker="repo · mock">
      <ApiUnavailable scope="repo" details="SHIP_API_URL not set" />
      <RepoHomeBody
        repo={
          {
            id: "mock",
            external_id: 0,
            full_name: ownerRepo,
            default_branch: "main",
            private: false,
            html_url: `https://github.com/${ownerRepo}`,
            description: "Mock repo — backend not configured.",
            activated_at: null,
            provider: "github",
            preset: null,
            installed_bundle_version: null,
            current_bundle_version: "0.6",
          } as ApiActivatedRepo
        }
        base={base}
        report={null}
        tab={tab}
        opsWindow={opsWindow}
        workspaceId="mock-ws"
        multiWs={false}
      />
    </AppShell>
  );
}

function renderDownState(slug: string) {
  return (
    <AppShell title={slug} kicker="repo">
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle="The repo view couldn't load live data."
        />
        <p className="text-sm text-white/70">
          Try again in a few seconds. If this keeps happening, check the
          backend service in your hosting console.
        </p>
      </Card>
    </AppShell>
  );
}

function repoNavHref(
  base: string,
  tab: Tab,
  opsWindow: OpsReportWindow,
  workspaceId: string,
  multiWs: boolean,
): string {
  const p = new URLSearchParams();
  p.set("window", opsWindow);
  if (tab === "trends") p.set("tab", "trends");
  if (multiWs) p.set("ws", workspaceId);
  return `${base}?${p.toString()}`;
}

function RepoHomeBody({
  repo,
  base,
  report,
  tab,
  opsWindow,
  workspaceId,
  multiWs,
}: {
  repo: ApiActivatedRepo;
  base: string;
  report: ApiRepoHomeReport | null;
  tab: Tab;
  opsWindow: OpsReportWindow;
  workspaceId: string;
  multiWs: boolean;
}) {
  return (
    <>
      <RepoOpsWindowStrip
        base={base}
        current={opsWindow}
        workspaceId={workspaceId}
        multiWs={multiWs}
      />
      <HomeTabs
        base={base}
        tab={tab}
        disabled={report === null}
        opsWindow={opsWindow}
        workspaceId={workspaceId}
        multiWs={multiWs}
      />

      {report === null ? (
        <Card>
          <CardHeader
            title="Home rollup unavailable"
            subtitle="Couldn't load Now/Trends — try again in a few seconds."
          />
        </Card>
      ) : tab === "now" ? (
        <NowTab report={report} opsWindow={opsWindow} />
      ) : (
        <TrendsTab report={report} opsWindow={opsWindow} />
      )}

      <RepoFactsAndNav repo={repo} />
    </>
  );
}

function RepoOpsWindowStrip({
  base,
  current,
  workspaceId,
  multiWs,
}: {
  base: string;
  current: OpsReportWindow;
  workspaceId: string;
  multiWs: boolean;
}) {
  const labels: Record<OpsReportWindow, string> = {
    "24h": "24h",
    "7d": "7d",
    "30d": "30d",
    all: "All",
  };
  return (
    <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Activity window · UTC
      </p>
      <nav
        className="inline-flex rounded-full border border-white/10 bg-white/[0.03] p-1 text-xs font-semibold"
        aria-label="Repo activity period"
      >
        {OPS_REPORT_WINDOWS.map((w) => {
          const active = w === current;
          const href = repoNavHref(base, "now", w, workspaceId, multiWs);
          return (
            <Link
              key={w}
              href={href}
              aria-current={active ? "page" : undefined}
              className={
                active
                  ? "rounded-full bg-white/15 px-3 py-1.5 text-white"
                  : "rounded-full px-3 py-1.5 text-white/60 transition hover:text-white"
              }
            >
              {labels[w]}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

function HomeTabs({
  base,
  tab,
  disabled,
  opsWindow,
  workspaceId,
  multiWs,
}: {
  base: string;
  tab: Tab;
  disabled: boolean;
  opsWindow: OpsReportWindow;
  workspaceId: string;
  multiWs: boolean;
}) {
  const tabs: { key: Tab; label: string; hint: string }[] = [
    {
      key: "now",
      label: "Now",
      hint: `Live state · ${opsReportWindowPhrase(opsWindow)}`,
    },
    {
      key: "trends",
      label: "Trends",
      hint: "UTC day histogram · span from window_days",
    },
  ];
  return (
    <nav
      className="mb-4 inline-flex rounded-full border border-white/10 bg-white/[0.03] p-1 text-xs font-semibold"
      aria-label="Repo home tabs"
    >
      {tabs.map((t) => {
        const active = tab === t.key;
        const href = repoNavHref(base, t.key, opsWindow, workspaceId, multiWs);
        return (
          <Link
            key={t.key}
            href={href}
            aria-disabled={disabled}
            title={t.hint}
            className={
              active
                ? "rounded-full bg-white/15 px-4 py-1.5 text-white"
                : "rounded-full px-4 py-1.5 text-white/60 transition hover:text-white"
            }
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Now tab
// ---------------------------------------------------------------------------

function NowTab({
  report,
  opsWindow,
}: {
  report: ApiRepoHomeReport;
  opsWindow: OpsReportWindow;
}) {
  const n = report.now;
  const winShort = opsReportWindowShortLabel(opsWindow);
  const successRate =
    n.runs_last_24h > 0
      ? Math.round((n.successes_last_24h / n.runs_last_24h) * 100)
      : null;

  const flags: { tone: BadgeTone; label: string }[] = [];
  if (n.install_missing) flags.push({ tone: "err", label: "install missing" });
  if (n.install_suspended)
    flags.push({ tone: "err", label: "install suspended" });
  if (n.bundle_drift) flags.push({ tone: "warn", label: "bundle out of date" });

  return (
    <>
      <section className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          label="In flight"
          value={String(n.runs_in_flight)}
          hint={
            n.dispatches_in_flight > 0
              ? `${n.dispatches_in_flight} agent · ${n.runs_in_flight - n.dispatches_in_flight} lane`
              : "lane + agent runs currently open"
          }
        />
        <StatTile
          label={`Runs (${winShort})`}
          value={String(n.runs_last_24h)}
          hint={
            successRate === null
              ? "no activity"
              : `${n.successes_last_24h} ok · ${n.failures_last_24h} fail · ${successRate}% success`
          }
        />
        <StatTile
          label="Lanes wired"
          value={`${n.lanes_enabled}/${n.lanes_total}`}
          hint="enabled / total"
        />
        <StatTile
          label="Last run"
          value={n.last_run_at ? formatRelative(n.last_run_at) : "—"}
          hint={
            n.last_success_at
              ? `success ${formatRelative(n.last_success_at)}`
              : "no success yet"
          }
        />
      </section>

      {flags.length > 0 ? (
        <Card className="mb-4">
          <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
            Needs attention
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {flags.map((f) => (
              <Badge key={f.label} tone={f.tone} dot>
                {f.label}
              </Badge>
            ))}
          </div>
        </Card>
      ) : null}

      <Card padded={false} className="mb-6">
        <div className="border-b border-white/[0.08] px-5 py-3 text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
          Recent activity · {opsReportWindowPhrase(opsWindow)}
        </div>
        {n.recent_activity.length === 0 ? (
          <div className="px-5 py-6 text-center text-sm text-white/50">
            No runs yet. Kick one off from{" "}
            <span className="font-mono text-aqua">Requests</span> or wire a
            lane.
          </div>
        ) : (
          <ul className="divide-y divide-white/[0.06]">
            {n.recent_activity.map((a, i) => (
              <li
                key={`${a.kind}-${i}-${a.at}`}
                className="flex flex-wrap items-center gap-3 px-5 py-3"
              >
                <Badge tone={statusTone(a.status)} dot>
                  {a.status}
                </Badge>
                <span className="font-mono text-[11px] uppercase tracking-wider text-white/50">
                  {kindLabel(a.kind)}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm text-white">
                  {a.title}
                </span>
                <span className="text-[11px] text-white/45">
                  {formatRelative(a.at)}
                </span>
                {a.html_url ? (
                  <a
                    href={a.html_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11px] font-semibold text-aqua hover:underline"
                  >
                    open ↗
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

// ---------------------------------------------------------------------------
// Trends tab
// ---------------------------------------------------------------------------

function TrendsTab({
  report,
  opsWindow,
}: {
  report: ApiRepoHomeReport;
  opsWindow: OpsReportWindow;
}) {
  const t = report.trends;
  const rate =
    t.totals.success_rate === null
      ? null
      : Math.round(t.totals.success_rate * 100);

  return (
    <>
      <p className="mb-3 text-[11px] leading-relaxed text-white/45">
        Histogram buckets cover the last{" "}
        <span className="font-semibold text-white/70">{t.window_days}</span>{" "}
        UTC days (the <span className="font-mono">window_days</span> query
        param). The Now tab uses the{" "}
        <span className="font-mono text-white/60">
          {opsReportWindowShortLabel(opsWindow)}
        </span>{" "}
        selector for counters.
      </p>
      <section className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          label={`Runs · ${t.window_days}d`}
          value={String(t.totals.runs)}
          hint="all sources combined"
        />
        <StatTile
          label="Success"
          value={String(t.totals.successes)}
          hint={rate === null ? "no runs" : `${rate}% rate`}
        />
        <StatTile
          label="Fail"
          value={String(t.totals.failures)}
          hint={t.totals.other > 0 ? `${t.totals.other} other` : "0 other"}
        />
        <StatTile
          label="Active lanes"
          value={String(t.lanes.filter((l) => l.runs > 0).length)}
          hint="fired inside window"
        />
      </section>

      <Card className="mb-4">
        <div className="mb-3 flex items-baseline justify-between">
          <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
            Daily activity
          </div>
          <div className="text-[11px] text-white/45">
            success · fail · other
          </div>
        </div>
        <Histogram buckets={t.buckets} />
      </Card>

      <Card padded={false}>
        <div className="border-b border-white/[0.08] px-5 py-3 text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
          Lanes
        </div>
        {t.lanes.length === 0 ? (
          <div className="px-5 py-6 text-center text-sm text-white/50">
            No lane activity in the last {t.window_days} days.
          </div>
        ) : (
          <ul className="divide-y divide-white/[0.06]">
            {t.lanes.map((lane) => {
              const laneRate =
                lane.runs > 0
                  ? Math.round((lane.successes / lane.runs) * 100)
                  : null;
              return (
                <li
                  key={lane.lane_id}
                  className="flex flex-wrap items-center gap-3 px-5 py-3"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-sm text-white">
                    {lane.lane_id}
                  </span>
                  <span className="font-mono text-[11px] text-white/65">
                    {lane.runs} runs
                  </span>
                  <Badge
                    tone={
                      laneRate === null
                        ? "neutral"
                        : laneRate >= 80
                          ? "ok"
                          : laneRate >= 50
                            ? "warn"
                            : "err"
                    }
                  >
                    {laneRate === null ? "—" : `${laneRate}% ok`}
                  </Badge>
                  <span className="text-[11px] text-white/45">
                    {lane.last_run_at
                      ? formatRelative(lane.last_run_at)
                      : "never"}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </>
  );
}

function Histogram({
  buckets,
}: {
  buckets: ApiRepoHomeReport["trends"]["buckets"];
}) {
  const max = buckets.reduce((m, b) => Math.max(m, b.total), 0);
  return (
    <div className="flex items-end gap-[3px]">
      {buckets.map((b) => {
        const h = max === 0 ? 0 : Math.max(2, Math.round((b.total / max) * 96));
        const ok = b.total === 0 ? 0 : (b.successes / b.total) * h;
        const fail = b.total === 0 ? 0 : (b.failures / b.total) * h;
        const other = h - ok - fail;
        return (
          <div
            key={b.day}
            title={`${b.day}: ${b.total} (${b.successes} ok, ${b.failures} fail, ${b.other} other)`}
            className="flex h-24 w-full max-w-[18px] flex-1 flex-col justify-end rounded-sm bg-white/[0.04]"
          >
            {other > 0 ? (
              <div
                className="bg-sky-500/60"
                style={{ height: `${other}px` }}
              />
            ) : null}
            {fail > 0 ? (
              <div
                className="bg-coral/80"
                style={{ height: `${fail}px` }}
              />
            ) : null}
            {ok > 0 ? (
              <div
                className="bg-emerald-500/80"
                style={{ height: `${ok}px` }}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Repo facts + nav (unchanged scope-setting block)
// ---------------------------------------------------------------------------

function RepoFactsAndNav({
  repo,
}: {
  repo: ApiActivatedRepo;
}) {
  const tiles: {
    href: string;
    label: string;
    body: string;
    tone: "ok" | "info" | "warn";
  }[] = [
    {
      href: `/process?repo=${encodeURIComponent(repo.id)}`,
      label: "Process",
      body: "Specialists, states, handoffs, and active work for this repo.",
      tone: "info",
    },
    {
      href: `/inbox?type=clarification`,
      label: "Clarifications",
      body: "Tracker-projected items waiting on a human decision.",
      tone: "warn",
    },
    {
      href: `/inbox?type=improvement`,
      label: "Improvements",
      body: "Agent-proposed refactors, housekeeping, follow-ups.",
      tone: "info",
    },
  ];

  return (
    <>
      <section className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader
            title="Repo facts"
            subtitle={repo.private ? "Private · GitHub" : "Public · GitHub"}
          />
          <dl className="space-y-2 text-sm">
            <Field label="Default branch" value={repo.default_branch} />
            <Field
              label="Preset"
              value={repo.preset ?? "adoption-minimum (implicit)"}
            />
            <Field
              label="Bundle"
              value={
                repo.installed_bundle_version == null
                  ? "never seeded"
                  : `v${repo.installed_bundle_version} / v${repo.current_bundle_version}`
              }
            />
            <Field
              label="Activated"
              value={repo.activated_at ? relativeDate(repo.activated_at) : "—"}
            />
          </dl>
          <div className="mt-4 flex gap-2">
            <a
              href={repo.html_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs font-semibold text-aqua hover:underline"
            >
              View on GitHub ↗
            </a>
          </div>
        </Card>
        <Card>
          <CardHeader
            title="Jump to"
            subtitle="Common repo surfaces."
          />
          <div className="flex flex-wrap gap-2">
            <ButtonPrimary>
              <Link href={`/process?repo=${encodeURIComponent(repo.id)}`}>
                Open process
              </Link>
            </ButtonPrimary>
            <ButtonGhost>
              <Link href="/knowledge">Knowledge</Link>
            </ButtonGhost>
          </div>
        </Card>
      </section>

      <section className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {tiles.map((tile) => (
          <Link
            key={tile.href}
            href={tile.href}
            className="group rounded-xl border border-white/10 bg-white/[0.03] p-4 transition hover:border-white/25 hover:bg-white/[0.06]"
          >
            <div className="mb-2 flex items-center gap-2">
              <Badge tone={tile.tone}>{tile.label}</Badge>
              <span className="ml-auto text-white/30 transition group-hover:text-white">
                →
              </span>
            </div>
            <p className="text-xs leading-relaxed text-white/60">{tile.body}</p>
          </Link>
        ))}
      </section>
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-white/5 pb-1.5 last:border-b-0">
      <dt className="text-[11px] font-semibold uppercase tracking-widest text-white/45">
        {label}
      </dt>
      <dd className="truncate text-right font-mono text-[11px] text-white/80">
        {value}
      </dd>
    </div>
  );
}

function relativeDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 10);
}

function statusTone(status: ApiRepoHomeActivityStatus): BadgeTone {
  switch (status) {
    case "succeeded":
      return "ok";
    case "failed":
      return "err";
    case "running":
      return "info";
    case "cancelled":
      return "neutral";
    case "other":
      return "neutral";
  }
}

function kindLabel(kind: ApiRepoHomeActivityKind): string {
  switch (kind) {
    case "pipeline":
      return "lane";
    case "workflow":
      return "gh";
    case "agent":
      return "request";
  }
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
