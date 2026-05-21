import Link from "next/link";

import { DashboardPrioritizer } from "@/components/dashboard/prioritizer";
import { TemplateUpdateAlert } from "@/components/template-update-alert";
import type {
  ApiActivatedRepo,
  ApiLiveSystem,
  ApiOpsBlocker,
  ApiOpsDashboard,
  ApiPrioritiesResponse,
  ApiPriorityLastAction,
} from "@/lib/api/client";
import { cn } from "@/lib/cn";
import type {
  InboxCountsResponse,
  InboxItem,
  InboxListResponse,
  InboxType,
} from "@/lib/inbox-types";
import {
  type OpsReportWindow,
  opsReportWindowShortLabel,
  OPS_REPORT_WINDOWS,
} from "@/lib/ops-window";

/**
 * Workspace home — editorial layout (post-PR-D1 simplification).
 *
 * The page is a single editorial column. The earlier 7/3/2 grid was
 * trying to be a Datadog-style mission-control panel and an editorial
 * brief at the same time; the two genres fought each other and
 * neither won. Telemetry (Live System, Recent activity, DORA) lives
 * on ``/analytics`` now — the home is just "what should I do next".
 *
 *   1. Status alerts (bundle stale + blockers) — only when something
 *      is non-ok, so a clean workspace renders without chrome.
 *   2. ``Needs you`` lede — h1-grade heading + decisions/PRs/shipped
 *      stat ribbon + first 4 decisions inline.
 *   3. Project prioritizer (Active / Drafts / Parked / Unprioritised).
 *   4. Active-tickets summary strip (one line).
 *
 * A single-line ``LiveSystemStrip`` sits under the alerts so the
 * operator can spot a system blip without leaving the page.
 *
 * Color discipline: ``aqua`` is the editorial-positive accent,
 * ``lilac`` human handoff, ``coral`` errors, ``sun`` paused/blocked,
 * ``white/40`` muted kickers. No bordered cards.
 */

export type WorkspaceHomeProps = {
  summary: ApiOpsDashboard;
  repos: ApiActivatedRepo[];
  workspaceId: string;
  multiWs?: boolean;
  inboxItems: InboxListResponse | null;
  inboxCounts: InboxCountsResponse | null;
  priorities: ApiPrioritiesResponse | null;
  liveSystem: ApiLiveSystem | null;
  opsWindow: OpsReportWindow;
  skipWizard: boolean;
};

export function WorkspaceHome({
  summary,
  repos,
  workspaceId,
  multiWs = false,
  inboxItems,
  inboxCounts,
  priorities,
  liveSystem,
  opsWindow,
  skipWizard,
}: WorkspaceHomeProps) {
  const reposNeedingUpdate = repos.filter(needsShipTemplateUpdate);
  const decisions = (inboxItems?.items ?? []).slice(0, 4);
  // Decisions counter mirrors the /inbox page: only items in
  // ACTIONABLE_CATEGORIES (`decision_needed` + `failure`). Reports
  // and `dismiss_silently` rows live in /reports and must not pad
  // the "needs you" line. Pre-ELS-147 this used `all_open` which
  // swept in daily-digest and learning-capture rows as if they
  // were decisions — the operator saw "16 waiting" and clicked
  // through to a list of reports.
  const decisionsTotal =
    inboxCounts?.actionable_new ?? inboxItems?.total ?? 0;
  const prsReadyToMerge = derivePrsReadyToMerge(summary);
  const totalShipped =
    summary.shipped.features_shipped_count +
    summary.shipped.fixes_count +
    summary.shipped.rollbacks_count;
  const blockerCount =
    summary.blockers.length + reposNeedingUpdate.length;
  const inFlight = summary.work_in_progress.length;
  const inProgress = summary.work_in_progress.filter(
    (it) => it.status === "in_progress",
  ).length;
  const periodKicker = opsReportWindowShortLabel(opsWindow);

  return (
    <div className="mx-auto max-w-6xl">
      <div className="grid grid-cols-1 gap-x-10 gap-y-8 lg:grid-cols-12">
        {/* Left — what the operator acts on: blockers, the decisions
            waiting, and the project queue the agent picks from. */}
        <div className="space-y-8 lg:col-span-7">
          {(reposNeedingUpdate.length > 0 || summary.blockers.length > 0) && (
            <StatusAlerts
              blockers={summary.blockers}
              reposNeedingUpdate={reposNeedingUpdate}
              workspaceId={workspaceId}
              multiWs={multiWs}
            />
          )}

          <NeedsYouSection
            decisions={decisions}
            decisionsTotal={decisionsTotal}
            prsReadyToMerge={prsReadyToMerge}
            shippedTotal={totalShipped}
            blockerCount={blockerCount}
            workspaceId={workspaceId}
            periodKicker={periodKicker}
          />

          {priorities ? (
            <DashboardPrioritizer
              workspaceId={workspaceId}
              initial={priorities}
            />
          ) : null}
        </div>

        {/* Right — ambient context, recedes. Sticky so it stays put
            while the queue scrolls. */}
        <aside className="space-y-6 lg:col-span-5 lg:sticky lg:top-6 lg:self-start">
          <OpsWindowSegment
            workspaceId={workspaceId}
            multiWs={multiWs}
            current={opsWindow}
            skipWizard={skipWizard}
          />
          <LiveSystemStrip data={liveSystem} workspaceId={workspaceId} />
          <LastActionStrip lastAction={priorities?.last_action ?? null} />
          <ActiveTicketsStrip
            inFlight={inFlight}
            inProgress={inProgress}
            workspaceId={workspaceId}
          />
        </aside>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Ops period (workspace aggregates)
// ---------------------------------------------------------------------------

function OpsWindowSegment({
  workspaceId,
  multiWs,
  current,
  skipWizard,
}: {
  workspaceId: string;
  multiWs: boolean;
  current: OpsReportWindow;
  skipWizard: boolean;
}) {
  const labels: Record<OpsReportWindow, string> = {
    "24h": "24h",
    "7d": "7d",
    "30d": "30d",
    all: "All",
  };
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Workspace ops · UTC window
      </p>
      <nav
        className="inline-flex rounded-full border border-white/10 bg-white/[0.03] p-1 text-xs font-semibold"
        aria-label="Ops dashboard period"
      >
        {OPS_REPORT_WINDOWS.map((w) => {
          const active = w === current;
          const p = new URLSearchParams();
          p.set("window", w);
          if (multiWs) p.set("ws", workspaceId);
          if (skipWizard) p.set("skipWizard", "1");
          const href = `/?${p.toString()}`;
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


// ---------------------------------------------------------------------------
// Live system — single-line strip under the alerts, not a widget block
// ---------------------------------------------------------------------------


function LiveSystemStrip({
  data,
  workspaceId,
}: {
  data: ApiLiveSystem | null;
  workspaceId: string;
}) {
  const analyticsHref = `/analytics?ws=${encodeURIComponent(workspaceId)}`;
  if (data === null) {
    return (
      <p className="text-[11px] text-white/35">
        Live system · couldn&apos;t reach the aggregator on this render.
        <Link
          href={analyticsHref}
          className="ml-2 font-semibold text-white/55 hover:text-white"
        >
          Analytics →
        </Link>
      </p>
    );
  }
  const successPct =
    data.masthead.success_rate_7d !== null
      ? `${Math.round(data.masthead.success_rate_7d * 100)}%`
      : null;
  const last =
    data.masthead.last_run_at !== null
      ? `${formatRelative(data.masthead.last_run_at)} last run`
      : "no runs yet";
  const failures = data.masthead.failures_7d ?? 0;
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[11px] text-white/55">
      <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        System
      </span>
      {successPct && (
        <span
          className={
            data.masthead.last_run_status === "error"
              ? "text-coral"
              : "text-aqua/90"
          }
        >
          {successPct}
        </span>
      )}
      <span className="text-white/15">·</span>
      <span>{last}</span>
      {failures > 0 && (
        <>
          <span className="text-white/15">·</span>
          {/* 7-day cumulative — label it so it doesn't read as "N on
              fire right now" or fight the ops-window toggle. Coral only
              when the latest run actually errored. */}
          <span
            className={
              data.masthead.last_run_status === "error"
                ? "text-coral"
                : "text-white/45"
            }
          >
            {failures} failure{failures === 1 ? "" : "s"}{" "}
            <span className="text-white/30">7d</span>
          </span>
        </>
      )}
      <span className="ml-auto">
        <Link
          href={analyticsHref}
          className="font-semibold text-white/55 hover:text-white"
        >
          Analytics →
        </Link>
      </span>
    </p>
  );
}


function formatRelative(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const diffMs = Date.now() - date.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}


// ---------------------------------------------------------------------------
// Status alerts — bundle-stale + blockers, only when something is non-ok
// ---------------------------------------------------------------------------


function StatusAlerts({
  blockers,
  reposNeedingUpdate,
  workspaceId,
  multiWs,
}: {
  blockers: ApiOpsBlocker[];
  reposNeedingUpdate: ApiActivatedRepo[];
  workspaceId: string;
  multiWs: boolean;
}) {
  return (
    <ul className="divide-y divide-white/[0.06]">
      {reposNeedingUpdate.length > 0 && (
        <TemplateUpdateAlert
          workspaceId={workspaceId}
          reposNeedingUpdate={reposNeedingUpdate}
          multiWs={multiWs}
        />
      )}
      {blockers.map((blocker, idx) => (
        <li
          key={`${blocker.type}-${idx}-${blocker.title}`}
          className="flex items-baseline justify-between gap-3 py-3"
        >
          <div className="min-w-0">
            <p
              className={cn(
                "text-[10px] font-bold uppercase tracking-[0.18em]",
                blocker.impact === "high"
                  ? "text-coral/85"
                  : blocker.impact === "medium"
                    ? "text-sun/80"
                    : "text-white/45",
              )}
            >
              {blocker.type}
            </p>
            <p className="mt-1 truncate text-sm text-white/85">
              <span className="font-semibold text-white">{blocker.title}</span>
              {(blocker.repo || blocker.scope) && (
                <>
                  <span className="mx-2 text-white/20">·</span>
                  <span className="text-white/55">
                    {[blocker.repo, blocker.scope].filter(Boolean).join(" / ")}
                  </span>
                </>
              )}
              <span className="mx-2 text-white/20">·</span>
              <span className="text-white/55">{formatAge(blocker.age_seconds)}</span>
            </p>
          </div>
          {blocker.href && (
            <a
              href={blocker.href}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 text-xs font-semibold text-white/55 hover:text-white"
            >
              Open →
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}


// ---------------------------------------------------------------------------
// "Needs you" — lede + decisions + ready-to-merge
// ---------------------------------------------------------------------------


function NeedsYouSection({
  decisions,
  decisionsTotal,
  prsReadyToMerge,
  shippedTotal,
  blockerCount,
  workspaceId,
  periodKicker,
}: {
  decisions: InboxItem[];
  decisionsTotal: number;
  prsReadyToMerge: ReadyToMergePr[];
  shippedTotal: number;
  blockerCount: number;
  workspaceId: string;
  periodKicker: string;
}) {
  // Lede subtitle — built from real fields only. Each clause renders
  // only when its number is meaningful, otherwise it's omitted.
  const subtitleParts: React.ReactNode[] = [];
  if (decisionsTotal > 0) {
    subtitleParts.push(
      <span key="decisions" className="text-sun">
        <span className="font-display font-bold">{decisionsTotal}</span>{" "}
        decision{decisionsTotal === 1 ? "" : "s"} waiting
      </span>,
    );
  }
  if (prsReadyToMerge.length > 0) {
    subtitleParts.push(
      <span key="prs">
        <span className="font-display font-bold text-white">
          {prsReadyToMerge.length}
        </span>{" "}
        PR{prsReadyToMerge.length === 1 ? "" : "s"} ready to merge
      </span>,
    );
  }
  if (shippedTotal > 0) {
    subtitleParts.push(
      <span key="shipped">
        <span className="font-display font-bold text-aqua">{shippedTotal}</span>{" "}
        shipped{" "}
        <span className="font-mono text-[11px] font-semibold text-white/50">
          ({periodKicker})
        </span>
      </span>,
    );
  }
  if (subtitleParts.length === 0) {
    subtitleParts.push(
      <span key="quiet" className="italic">
        Workspace is quiet — no decisions waiting, no PRs queued.
      </span>,
    );
  }

  const inboxHref = `/inbox?ws=${encodeURIComponent(workspaceId)}`;

  return (
    <section className="space-y-8">
      <header className="space-y-2">
        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
          Needs you
        </p>
        <h2 className="font-display text-2xl font-bold leading-tight text-white">
          {decisionsTotal > 0
            ? `${decisionsTotal} ${decisionsTotal === 1 ? "decision needs" : "decisions need"} you.`
            : prsReadyToMerge.length + blockerCount > 0
              ? "A few things to look at."
              : "Nothing on your plate."}
        </h2>
        <p className="text-sm text-white/65">
          {subtitleParts.flatMap((node, idx) =>
            idx === 0
              ? [node]
              : [
                  <span key={`sep-${idx}`} className="mx-2 text-white/20">
                    ·
                  </span>,
                  node,
                ],
          )}
        </p>
      </header>

      {decisions.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <h3 className="text-[10px] font-bold uppercase tracking-[0.22em] text-sun/80">
              Decisions · {decisionsTotal} waiting
            </h3>
            <div className="h-px flex-1 bg-white/[0.06]" />
          </div>
          <ul className="divide-y divide-white/[0.06]">
            {decisions.map((item) => (
              <li key={item.id}>
                <DecisionRow item={item} workspaceId={workspaceId} />
              </li>
            ))}
          </ul>
          {decisionsTotal > decisions.length && (
            <p className="text-[11px]">
              <Link
                href={inboxHref}
                className="font-semibold text-white/55 hover:text-white"
              >
                +{decisionsTotal - decisions.length} more in Inbox →
              </Link>
            </p>
          )}
        </div>
      )}

      {prsReadyToMerge.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <h3 className="text-[10px] font-bold uppercase tracking-[0.22em] text-aqua/75">
              Ready to merge · {prsReadyToMerge.length}
            </h3>
            <div className="h-px flex-1 bg-white/[0.06]" />
          </div>
          <ul className="divide-y divide-white/[0.06]">
            {prsReadyToMerge.slice(0, 4).map((pr) => (
              <li key={`${pr.repo}-${pr.number}`}>
                <ReadyToMergeRow pr={pr} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}


function DecisionRow({
  item,
  workspaceId,
}: {
  item: InboxItem;
  workspaceId: string;
}) {
  const href = `/inbox/${encodeURIComponent(item.id)}?ws=${encodeURIComponent(workspaceId)}`;
  return (
    <Link
      href={href}
      className="group relative flex items-baseline justify-between gap-4 py-3 pl-4 transition hover:bg-white/[0.025]"
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-2 left-0 rounded-r-sm",
          INBOX_SPINE[item.type],
          INBOX_SPINE_WIDTH[item.type],
        )}
      />
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-white/45">
          <span>{item.type}</span>
          {item.summary && (
            <>
              <span className="text-white/15">·</span>
              <span className="truncate text-white/55 normal-case tracking-normal">
                {item.summary}
              </span>
            </>
          )}
        </p>
        <p className="mt-1 truncate text-[15px] font-semibold text-white">
          {item.title}
        </p>
      </div>
      <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-aqua opacity-0 transition group-hover:opacity-100">
        {canonicalActionLabel(item.type)}
      </span>
    </Link>
  );
}


type ReadyToMergePr = {
  number: number;
  title: string;
  repo: string | null;
  ticketRef: string | null;
  href: string;
};


function ReadyToMergeRow({ pr }: { pr: ReadyToMergePr }) {
  return (
    <a
      href={pr.href}
      target="_blank"
      rel="noreferrer"
      className="group flex items-baseline justify-between gap-4 py-3 transition hover:bg-white/[0.025]"
    >
      <div className="min-w-0">
        <p className="flex items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-white/45">
          <span className="font-mono text-aqua/85">#{pr.number}</span>
          {pr.ticketRef && (
            <>
              <span className="text-white/15">·</span>
              <span className="font-mono">{pr.ticketRef}</span>
            </>
          )}
          {pr.repo && (
            <>
              <span className="text-white/15">·</span>
              <span>{pr.repo}</span>
            </>
          )}
        </p>
        <p className="mt-1 truncate text-[15px] font-semibold text-white">
          {pr.title}
        </p>
      </div>
      <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-aqua opacity-0 transition group-hover:opacity-100">
        Merge
      </span>
    </a>
  );
}


function derivePrsReadyToMerge(summary: ApiOpsDashboard): ReadyToMergePr[] {
  const out: ReadyToMergePr[] = [];
  for (const item of summary.work_in_progress) {
    if (item.status !== "review" || !item.pull_request) continue;
    out.push({
      number: item.pull_request.number,
      title: stripTicketPrefix(item.name, item.ticket_ref),
      repo: item.repo ?? null,
      ticketRef: item.ticket_ref ?? null,
      href: item.pull_request.href,
    });
  }
  return out;
}


// ---------------------------------------------------------------------------
// Active-tickets summary strip (replaces the 3-col Work-in-flight grid)
// ---------------------------------------------------------------------------


function ActiveTicketsStrip({
  inFlight,
  inProgress,
  workspaceId,
}: {
  inFlight: number;
  inProgress: number;
  workspaceId: string;
}) {
  if (inFlight === 0) return null;
  return (
    <p className="flex items-baseline justify-between gap-4 border-t border-white/[0.06] pt-4 text-[12px] text-white/55">
      <span>
        <span className="font-display font-bold text-white">{inFlight}</span>{" "}
        active ticket{inFlight === 1 ? "" : "s"}
        <span className="mx-2 text-white/20">·</span>
        <span>{inProgress} in flight</span>
      </span>
      <Link
        href={`/process?ws=${encodeURIComponent(workspaceId)}`}
        className="shrink-0 text-[11px] font-semibold uppercase tracking-widest text-white/45 hover:text-white"
      >
        Open process →
      </Link>
    </p>
  );
}


// ---------------------------------------------------------------------------
// Right rail — Last-action · Recent activity · Repos
// ---------------------------------------------------------------------------


function LastActionStrip({
  lastAction,
}: {
  lastAction: ApiPriorityLastAction | null;
}) {
  if (!lastAction) return null;
  const time = formatTime(lastAction.ts);
  const inner = (
    <p className="flex items-baseline gap-2 text-[11px] text-white/65">
      <span aria-hidden className="text-aqua">·</span>
      <span className="truncate">
        {lastAction.label}
        <span className="mx-2 text-white/20">·</span>
        <span className="text-white/45">{time}</span>
      </span>
    </p>
  );
  if (lastAction.href) {
    return (
      <a
        href={lastAction.href}
        target="_blank"
        rel="noreferrer"
        className="block hover:text-aqua"
      >
        {inner}
      </a>
    );
  }
  return inner;
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


const INBOX_SPINE: Record<InboxType, string> = {
  clarification: "bg-sun",
  approval: "bg-aqua",
  improvement: "bg-lilac",
  failure: "bg-coral",
  blocker: "bg-coral",
  exception: "bg-coral/60",
  stuck: "bg-white/30",
  report: "bg-white/15",
};


const INBOX_SPINE_WIDTH: Record<InboxType, string> = {
  clarification: "w-[3px]",
  approval: "w-[3px]",
  improvement: "w-[2px]",
  failure: "w-[3px]",
  blocker: "w-[4px]",
  exception: "w-[2px]",
  stuck: "w-px",
  report: "w-px",
};


function canonicalActionLabel(type: InboxType): string {
  switch (type) {
    case "clarification":
      return "Answer";
    case "approval":
      return "Approve";
    case "improvement":
      return "Accept";
    case "failure":
      return "Retry";
    case "exception":
    case "blocker":
    case "stuck":
      return "Acknowledge";
    case "report":
      return "Acknowledge";
  }
}


function stripTicketPrefix(name: string, ref: string | null | undefined): string {
  if (!ref) return name;
  if (name.startsWith(`${ref}: `)) return name.slice(ref.length + 2);
  if (name.startsWith(`${ref} `)) return name.slice(ref.length + 1);
  return name;
}


function needsShipTemplateUpdate(repo: ApiActivatedRepo): boolean {
  const installed = repo.installed_bundle_version;
  const current = repo.current_bundle_version;
  if (installed == null) return true;
  return compareBundleVersions(installed, current) < 0;
}


function compareBundleVersions(left: string, right: string): number {
  const a = left.split(".").map((part) => Number.parseInt(part, 10) || 0);
  const b = right.split(".").map((part) => Number.parseInt(part, 10) || 0);
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}


function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}


function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}


