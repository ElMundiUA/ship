import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  Badge,
  type BadgeTone,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
  StatTile,
} from "@/components/ui";
import {
  ApiHttpError,
  getRepoHome,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import type {
  ApiActivatedRepo,
  ApiRepoHomeActivityKind,
  ApiRepoHomeActivityStatus,
  ApiRepoHomeReport,
} from "@/lib/api/client";
import { resolveRepoContext, type RepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";

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

  if (!isApiConfigured()) {
    return renderMock(slug, tab);
  }

  const token = await getSessionToken();
  if (!token) {
    redirect(`/login?next=${encodeURIComponent(`/r/${slug}`)}`);
  }

  const result = await resolveRepoContext(token, slug);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(`/r/${slug}`)}`);
  }
  if (result.kind === "down") return renderDownState(slug);
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  const ctx = result.ctx;
  let report: ApiRepoHomeReport | null = null;
  try {
    report = await getRepoHome(ctx.workspace.id, ctx.repo.id, { token });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(`/r/${slug}`)}`);
    }
    // Down / transient — let the page render with the tabs disabled
    // instead of 500-ing the shell.
    report = null;
  }

  return renderRepoHome(ctx, report, tab);
}

function renderRepoHome(ctx: RepoContext, report: ApiRepoHomeReport | null, tab: Tab) {
  const { workspace, repo, repos } = ctx;
  const base = `/r/${repo.full_name}`;
  return (
    <AppShell
      title={`${repo.full_name}`}
      kicker="repo"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repo.id,
      }}
      actions={
        <>
          <Link
            href={`${base}/settings`}
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            Repo settings
          </Link>
          <ButtonPrimary>
            <Link href={`/plays?scope=repo&repo=${encodeURIComponent(repo.id)}`}>
              Start a request →
            </Link>
          </ButtonPrimary>
        </>
      }
    >
      <RepoHomeBody repo={repo} base={base} report={report} tab={tab} />
    </AppShell>
  );
}

function renderMock(slug: string, tab: Tab) {
  const base = `/r/${slug}`;
  const ownerRepo = slug;
  return (
    <AppShell title={ownerRepo} kicker="repo · mock">
      <MockBanner />
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
            current_bundle_version: 0,
          } as ApiActivatedRepo
        }
        base={base}
        report={null}
        tab={tab}
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

function RepoHomeBody({
  repo,
  base,
  report,
  tab,
}: {
  repo: ApiActivatedRepo;
  base: string;
  report: ApiRepoHomeReport | null;
  tab: Tab;
}) {
  return (
    <>
      <HomeTabs base={base} tab={tab} disabled={report === null} />

      {report === null ? (
        <Card>
          <CardHeader
            title="Home rollup unavailable"
            subtitle="Couldn't load Now/Trends — try again in a few seconds."
          />
        </Card>
      ) : tab === "now" ? (
        <NowTab report={report} />
      ) : (
        <TrendsTab report={report} />
      )}

      <RepoFactsAndNav repo={repo} base={base} />
    </>
  );
}

function HomeTabs({
  base,
  tab,
  disabled,
}: {
  base: string;
  tab: Tab;
  disabled: boolean;
}) {
  const tabs: { key: Tab; label: string; hint: string }[] = [
    { key: "now", label: "Now", hint: "Live state + last 24h" },
    { key: "trends", label: "Trends", hint: "30-day histogram + per-lane" },
  ];
  return (
    <nav
      className="mb-4 inline-flex rounded-full border border-white/10 bg-white/[0.03] p-1 text-xs font-semibold"
      aria-label="Repo home tabs"
    >
      {tabs.map((t) => {
        const active = tab === t.key;
        const href = t.key === "now" ? base : `${base}?tab=${t.key}`;
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

function NowTab({ report }: { report: ApiRepoHomeReport }) {
  const n = report.now;
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
          label="Last 24h"
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
          Recent activity
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

function TrendsTab({ report }: { report: ApiRepoHomeReport }) {
  const t = report.trends;
  const rate =
    t.totals.success_rate === null
      ? null
      : Math.round(t.totals.success_rate * 100);

  return (
    <>
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
  base,
}: {
  repo: ApiActivatedRepo;
  base: string;
}) {
  const tiles: {
    href: string;
    label: string;
    body: string;
    tone: "ok" | "info" | "warn";
  }[] = [
    // RFC-0010: per-repo lanes/requests/clarifications/improvements/
    // artifact-feedback retired; tiles now point at the workspace
    // surfaces that own the data, with the repo's id pre-filtered
    // via ``?scope=repo&repo=<id>`` (or ``?type=`` for inbox folds).
    {
      href: `/automations?scope=repo&repo=${encodeURIComponent(repo.id)}`,
      label: "Automations",
      body: "Scheduled + event-driven patterns wired from .ship/config.yml.",
      tone: "info",
    },
    {
      href: `/runs?scope=repo&repo=${encodeURIComponent(repo.id)}`,
      label: "Runs",
      body: "Catalog of one-shot agent patterns dispatched from this repo.",
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
    {
      href: `${base}/knowledge`,
      label: "Knowledge",
      body: "Parsed runbooks, wiki pages, slide decks scoped to this repo.",
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
              <Link href={`/plays?scope=repo&repo=${encodeURIComponent(repo.id)}`}>
                Plays catalog
              </Link>
            </ButtonPrimary>
            <ButtonGhost>
              <Link href={`/automations?scope=repo&repo=${encodeURIComponent(repo.id)}`}>
                Review automations
              </Link>
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
