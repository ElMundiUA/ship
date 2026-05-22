import Link from "next/link";

import { DashboardPrioritizer } from "@/components/dashboard/prioritizer";
import { TemplateUpdateAlert } from "@/components/template-update-alert";
import type {
  ApiActivatedRepo,
  ApiLiveSystem,
  ApiOpsBlocker,
  ApiOpsDashboard,
  ApiOpsFlow,
  ApiOpsShippedItem,
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
 * Workspace home — FSM pipeline + three gate columns (Variant A).
 *
 * Agentic-SDLC tickets don't dwell in "in progress" — an agent moves
 * a ticket through planning → dev → validation → review in minutes.
 * The only places work *waits on a human* are the two FSM gates:
 * ``auto_merge`` (a green PR awaiting merge consent) and ``parked``
 * (a project awaiting promote). So the home maps onto the FSM:
 *
 *   1. Status alerts — only when something is non-ok.
 *   2. ``FlowStrip`` (full width) — per-stage throughput in the window
 *      as a left-to-right pipeline, with the ``auto_merge`` gate
 *      highlighted when PRs await consent and a ``stuck loop`` badge
 *      for the dev↔review cycle.
 *   3. Three columns:
 *        AWAITING YOU  — decisions + PRs ready to merge + parked count.
 *        PROJECT QUEUE — the prioritizer (the agent's pick order).
 *        SHIPPED       — what merged in the window.
 *   4. Footer strips — ops window toggle, live-system, last action.
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
  const decisions = (inboxItems?.items ?? []).slice(0, 5);
  // Decisions counter mirrors the /inbox page: only items in
  // ACTIONABLE_CATEGORIES (`decision_needed` + `failure`). Reports
  // and `dismiss_silently` rows live in /reports and must not pad
  // the "awaiting you" line. Pre-ELS-147 this used `all_open` which
  // swept in daily-digest and learning-capture rows as if they
  // were decisions — the operator saw "16 waiting" and clicked
  // through to a list of reports.
  const decisionsTotal =
    inboxCounts?.actionable_new ?? inboxItems?.total ?? 0;
  const prsReadyToMerge = derivePrsReadyToMerge(summary);
  const parkedCount = (priorities?.projects ?? []).filter(
    (p) => p.priority_state === "parked",
  ).length;
  const periodKicker = opsReportWindowShortLabel(opsWindow);

  return (
    <div className="mx-auto w-full max-w-[1600px] space-y-10">
      {(reposNeedingUpdate.length > 0 || summary.blockers.length > 0) && (
        <StatusAlerts
          blockers={summary.blockers}
          reposNeedingUpdate={reposNeedingUpdate}
          workspaceId={workspaceId}
          multiWs={multiWs}
        />
      )}

      {/* HERO — full-width FSM pipeline + the window toggle that drives
          it. Hidden entirely when the window carries no throughput so a
          quiet day never shows a row of dead zeros. */}
      <FlowStrip
        flow={summary.flow}
        periodKicker={periodKicker}
        windowToggle={
          <OpsWindowSegment
            workspaceId={workspaceId}
            multiWs={multiWs}
            current={opsWindow}
            skipWizard={skipWizard}
          />
        }
      />

      <div className="grid grid-cols-1 gap-x-12 gap-y-12 lg:grid-cols-12">
        {/* PROJECT QUEUE — the order the agent picks work in. The widest,
            tallest column: it's the thing the operator manages daily. */}
        <section className="lg:col-span-8">
          {priorities ? (
            <DashboardPrioritizer
              workspaceId={workspaceId}
              initial={priorities}
            />
          ) : (
            <p className="text-base text-white/45">No projects yet.</p>
          )}
        </section>

        {/* Right rail — what needs you, then what shipped. Stacked so the
            column runs tall next to the queue instead of leaving a void. */}
        <aside className="space-y-12 lg:col-span-4">
          <AwaitingYouColumn
            decisions={decisions}
            decisionsTotal={decisionsTotal}
            prsReadyToMerge={prsReadyToMerge}
            parkedCount={parkedCount}
            workspaceId={workspaceId}
          />
          <ShippedColumn
            items={summary.shipped.items}
            featuresCount={summary.shipped.features_shipped_count}
            fixesCount={summary.shipped.fixes_count}
            rollbacksCount={summary.shipped.rollbacks_count}
            periodKicker={periodKicker}
            workspaceId={workspaceId}
          />
        </aside>
      </div>

      {/* Footer — ambient system strips. */}
      <div className="space-y-3 border-t border-white/[0.06] pt-6">
        <LiveSystemStrip data={liveSystem} workspaceId={workspaceId} />
        <LastActionStrip lastAction={priorities?.last_action ?? null} />
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// FlowStrip — FSM pipeline throughput (full width)
// ---------------------------------------------------------------------------


const FLOW_STAGE_LABEL: Record<string, string> = {
  planning: "Planning",
  dev_implementation: "Dev",
  validation: "Validation",
  code_review: "Review",
  auto_merge: "Merge gate",
};


function FlowStrip({
  flow,
  periodKicker,
  windowToggle,
}: {
  flow: ApiOpsFlow | null | undefined;
  periodKicker: string;
  windowToggle: React.ReactNode;
}) {
  const stages = flow?.stages ?? [];
  const hasThroughput = stages.some((s) => s.count > 0);
  const hasGates = (flow?.awaiting_merge ?? 0) + (flow?.stuck_loop ?? 0) > 0;
  // A quiet window reads all-zero — don't paint a dead row of zeros.
  // Keep the toggle visible so the operator can widen the window.
  if (!flow || (!hasThroughput && !hasGates)) {
    return (
      <section className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/40">
          Pipeline · no throughput in the last {periodKicker.toLowerCase()}
        </p>
        {windowToggle}
      </section>
    );
  }
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/45">
            Pipeline · throughput{" "}
            <span className="font-mono normal-case tracking-normal text-white/30">
              ({periodKicker})
            </span>
          </h3>
          {flow.stuck_loop > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-coral/40 bg-coral/10 px-2.5 py-1 text-xs font-semibold text-coral">
              <span aria-hidden>↺</span> {flow.stuck_loop} stuck in dev↔review
            </span>
          )}
        </div>
        {windowToggle}
      </div>
      <ol className="flex flex-col items-stretch gap-2 sm:flex-row">
        {stages.map((stage, idx) => {
          const isGate = stage.stage === "auto_merge";
          return (
            <li
              key={stage.stage}
              className="flex flex-1 items-stretch"
            >
              <FlowNode
                label={FLOW_STAGE_LABEL[stage.stage] ?? stage.stage}
                count={stage.count}
                isGate={isGate}
                gateCount={isGate ? flow.awaiting_merge : 0}
              />
              {idx < stages.length - 1 && (
                <span
                  aria-hidden
                  className="hidden items-center px-2 text-lg text-white/20 sm:flex"
                >
                  →
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}


function FlowNode({
  label,
  count,
  isGate,
  gateCount,
}: {
  label: string;
  count: number;
  isGate: boolean;
  gateCount: number;
}) {
  const gateHot = isGate && gateCount > 0;
  return (
    <div
      className={cn(
        "flex flex-1 flex-col justify-between rounded-xl border px-4 py-4 transition",
        gateHot
          ? "border-sun/50 bg-sun/[0.08]"
          : "border-white/[0.08] bg-white/[0.02]",
      )}
    >
      <p
        className={cn(
          "text-xs font-bold uppercase tracking-[0.16em]",
          gateHot ? "text-sun" : "text-white/50",
        )}
      >
        {label}
      </p>
      <p className="mt-3 flex items-baseline gap-2">
        <span
          className={cn(
            "font-display text-4xl font-bold leading-none",
            count > 0 ? "text-white" : "text-white/25",
          )}
        >
          {count}
        </span>
        <span className="text-xs text-white/35">done</span>
      </p>
      {gateHot && (
        <p className="mt-2 text-xs font-semibold text-sun">
          {gateCount} awaiting you
        </p>
      )}
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
    <nav
      className="inline-flex rounded-full border border-white/10 bg-white/[0.03] p-1 text-xs font-semibold"
      aria-label="Ops dashboard period (UTC)"
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
// AWAITING YOU — the two FSM human gates + inbox decisions
// ---------------------------------------------------------------------------


function AwaitingYouColumn({
  decisions,
  decisionsTotal,
  prsReadyToMerge,
  parkedCount,
  workspaceId,
}: {
  decisions: InboxItem[];
  decisionsTotal: number;
  prsReadyToMerge: ReadyToMergePr[];
  parkedCount: number;
  workspaceId: string;
}) {
  const inboxHref = `/inbox?ws=${encodeURIComponent(workspaceId)}`;
  const total = decisionsTotal + prsReadyToMerge.length + parkedCount;
  const nothing = total === 0;

  return (
    <div className="space-y-7">
      <header className="space-y-2">
        <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-sun/80">
          Awaiting you
        </p>
        <h2 className="font-display text-2xl font-bold leading-tight text-white">
          {nothing
            ? "Nothing on your plate."
            : `${total} thing${total === 1 ? "" : "s"} need${total === 1 ? "s" : ""} you.`}
        </h2>
      </header>

      {nothing && (
        <p className="text-[15px] italic leading-relaxed text-white/45">
          No decisions, no PRs at the merge gate, no parked projects.
          The pipeline is running itself.
        </p>
      )}

      {prsReadyToMerge.length > 0 && (
        <div className="space-y-2.5">
          <GateHeading
            label="Merge gate"
            count={prsReadyToMerge.length}
            tone="aqua"
          />
          <ul className="divide-y divide-white/[0.06]">
            {prsReadyToMerge.slice(0, 6).map((pr) => (
              <li key={`${pr.repo}-${pr.number}`}>
                <ReadyToMergeRow pr={pr} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {decisions.length > 0 && (
        <div className="space-y-2.5">
          <GateHeading
            label="Decisions"
            count={decisionsTotal}
            tone="sun"
          />
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

      {parkedCount > 0 && (
        <div className="space-y-2.5">
          <GateHeading label="Parked" count={parkedCount} tone="white" />
          <p className="text-[15px] leading-relaxed text-white/60">
            {parkedCount} project{parkedCount === 1 ? "" : "s"} on hold —
            promote {parkedCount === 1 ? "it" : "them"} in the queue to let
            the agent pick {parkedCount === 1 ? "it" : "them"} up.
          </p>
        </div>
      )}
    </div>
  );
}


function GateHeading({
  label,
  count,
  tone,
}: {
  label: string;
  count: number;
  tone: "sun" | "aqua" | "white";
}) {
  const toneClass =
    tone === "sun"
      ? "text-sun/80"
      : tone === "aqua"
        ? "text-aqua/75"
        : "text-white/45";
  return (
    <div className="flex items-center gap-3">
      <h3
        className={cn(
          "text-[11px] font-bold uppercase tracking-[0.22em]",
          toneClass,
        )}
      >
        {label} · {count}
      </h3>
      <div className="h-px flex-1 bg-white/[0.06]" />
    </div>
  );
}


// ---------------------------------------------------------------------------
// SHIPPED — what merged in the window
// ---------------------------------------------------------------------------


const SHIPPED_TONE: Record<ApiOpsShippedItem["type"], string> = {
  feature: "text-aqua/85",
  fix: "text-lilac/85",
  rollback: "text-coral/85",
};


function ShippedColumn({
  items,
  featuresCount,
  fixesCount,
  rollbacksCount,
  periodKicker,
  workspaceId,
}: {
  items: ApiOpsShippedItem[];
  featuresCount: number;
  fixesCount: number;
  rollbacksCount: number;
  periodKicker: string;
  workspaceId: string;
}) {
  const total = featuresCount + fixesCount + rollbacksCount;
  const analyticsHref = `/analytics?ws=${encodeURIComponent(workspaceId)}`;
  return (
    <div className="space-y-3.5">
      <div className="flex items-center gap-3">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.22em] text-aqua/75">
          Shipped{" "}
          <span className="font-mono normal-case tracking-normal text-white/30">
            ({periodKicker})
          </span>
        </h3>
        <div className="h-px flex-1 bg-white/[0.06]" />
      </div>

      {total === 0 ? (
        <p className="text-[15px] italic text-white/45">
          Nothing merged in this window.
        </p>
      ) : (
        <>
          <p className="text-[15px] text-white/60">
            <span className="font-display text-xl font-bold text-aqua">
              {total}
            </span>{" "}
            merged
            {featuresCount > 0 && ` · ${featuresCount} feat`}
            {fixesCount > 0 && ` · ${fixesCount} fix`}
            {rollbacksCount > 0 && ` · ${rollbacksCount} rollback`}
          </p>
          <ul className="divide-y divide-white/[0.06]">
            {items.slice(0, 8).map((item, idx) => (
              <li key={`${item.repo}-${idx}-${item.name}`}>
                <ShippedRow item={item} />
              </li>
            ))}
          </ul>
          {total > items.slice(0, 8).length && (
            <p className="text-xs">
              <Link
                href={analyticsHref}
                className="font-semibold text-white/55 hover:text-white"
              >
                Full history in Analytics →
              </Link>
            </p>
          )}
        </>
      )}
    </div>
  );
}


function ShippedRow({ item }: { item: ApiOpsShippedItem }) {
  const inner = (
    <div className="py-3">
      <p className="flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-white/45">
        <span className={SHIPPED_TONE[item.type]}>{item.type}</span>
        {item.repo && (
          <>
            <span className="text-white/15">·</span>
            <span className="truncate normal-case tracking-normal text-white/45">
              {item.repo}
            </span>
          </>
        )}
      </p>
      <p className="mt-1 truncate text-[14px] text-white/85">{item.name}</p>
    </div>
  );
  if (item.href) {
    return (
      <a
        href={item.href}
        target="_blank"
        rel="noreferrer"
        className="block transition hover:bg-white/[0.025]"
      >
        {inner}
      </a>
    );
  }
  return inner;
}


function DecisionRow({
  item,
  workspaceId,
}: {
  item: InboxItem;
  workspaceId: string;
}) {
  const href = `/inbox?selected=${encodeURIComponent(item.id)}&ws=${encodeURIComponent(workspaceId)}`;
  return (
    <Link
      href={href}
      className="group relative flex items-baseline justify-between gap-4 py-3.5 pl-4 transition hover:bg-white/[0.025]"
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
        <p className="flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-white/45">
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
        <p className="mt-1 truncate text-base font-semibold text-white">
          {item.title}
        </p>
      </div>
      <span className="shrink-0 text-xs font-semibold uppercase tracking-wide text-aqua opacity-0 transition group-hover:opacity-100">
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
      className="group flex items-baseline justify-between gap-4 py-3.5 transition hover:bg-white/[0.025]"
    >
      <div className="min-w-0">
        <p className="flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-white/45">
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
        <p className="mt-1 truncate text-base font-semibold text-white">
          {pr.title}
        </p>
      </div>
      <span className="shrink-0 text-xs font-semibold uppercase tracking-wide text-aqua opacity-0 transition group-hover:opacity-100">
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
// Footer strips — Last-action · Live system
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


