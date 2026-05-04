import Link from "next/link";

import type {
  ApiActivatedRepo,
  ApiOpsBlocker,
  ApiOpsDashboard,
  ApiOpsShippedItem,
  ApiOpsWorkItem,
} from "@/lib/api/client";
import { cn } from "@/lib/cn";
import type {
  InboxCountsResponse,
  InboxItem,
  InboxListResponse,
  InboxType,
} from "@/lib/inbox-types";

/**
 * Workspace home — editorial two-column dashboard.
 *
 * Layout (≥ lg): 12-col grid.
 *   - Left rail (col-span-8): status alerts → "Needs you" lede with
 *     inline pulse counts → Decisions tier → Ready-to-merge tier →
 *     Work in flight 3-up (column-color spine, no per-column card).
 *   - Right rail (col-span-4, sticky): System pulse (single block of
 *     real KV from ``automation_health`` + ``system_status``), Recent
 *     activity timeline, Repo channel list.
 *
 * Mobile collapses to single column with the rail moving below.
 *
 * No ``Card`` wrappers. Section breaks come from
 * ``border-t border-white/10 mt-8 pt-8`` + tinted kickers.
 *
 * Data discipline (per design pass): every number rendered here maps
 * 1:1 to a real ApiOpsDashboard / InboxCounts field. Fake placeholders
 * (``Routines coming up = —``, ``Routines completed today = success_rate
 * * (failures + 1)``) are gone. If a field isn't real yet, the row
 * doesn't render rather than showing a synthetic value.
 */

export type WorkspaceHomeProps = {
  summary: ApiOpsDashboard;
  repos: ApiActivatedRepo[];
  workspaceId: string;
  inboxItems: InboxListResponse | null;
  inboxCounts: InboxCountsResponse | null;
};

export function WorkspaceHome({
  summary,
  repos,
  workspaceId,
  inboxItems,
  inboxCounts,
}: WorkspaceHomeProps) {
  const reposNeedingUpdate = repos.filter(needsShipTemplateUpdate);
  const decisions = (inboxItems?.items ?? []).slice(0, 4);
  const decisionsTotal = inboxCounts?.all_open ?? inboxItems?.total ?? 0;
  const prsReadyToMerge = derivePrsReadyToMerge(summary);
  const totalShipped =
    summary.shipped.features_shipped_count +
    summary.shipped.fixes_count +
    summary.shipped.rollbacks_count;
  const blockerCount =
    summary.blockers.length + reposNeedingUpdate.length;

  return (
    <div className="mx-auto max-w-6xl 2xl:max-w-screen-2xl">
      <div className="grid grid-cols-1 gap-x-12 gap-y-10 lg:grid-cols-12 2xl:gap-x-16">
        <div className="space-y-10 lg:col-span-8 2xl:col-span-7">
          {(reposNeedingUpdate.length > 0 || summary.blockers.length > 0) && (
            <StatusAlerts
              blockers={summary.blockers}
              reposNeedingUpdate={reposNeedingUpdate}
              workspaceId={workspaceId}
            />
          )}

          <NeedsYouSection
            decisions={decisions}
            decisionsTotal={decisionsTotal}
            decisionsByType={inboxCounts?.by_type ?? inboxItems?.counts_by_type ?? {}}
            prsReadyToMerge={prsReadyToMerge}
            shippedTotal={totalShipped}
            blockerCount={blockerCount}
            workspaceId={workspaceId}
          />

          <WorkInFlightSection
            items={summary.work_in_progress}
            workspaceId={workspaceId}
          />
        </div>

        {/*
          ``2xl:contents`` dissolves this wrapper at 2xl so the two
          inner ``<aside>`` elements become direct grid children with
          their own col-spans. Below 2xl the wrapper acts as a normal
          col-span-4 sticky right rail with both panels stacked
          inside.
        */}
        <div className="space-y-10 lg:col-span-4 lg:sticky lg:top-20 lg:self-start 2xl:contents">
          <aside className="space-y-10 2xl:col-span-3 2xl:sticky 2xl:top-20 2xl:self-start">
            <SystemPulse summary={summary} />
          </aside>
          <aside className="space-y-10 2xl:col-span-2 2xl:sticky 2xl:top-20 2xl:self-start">
            <RecentActivity summary={summary} />
            <RepoChannelList repos={repos} workspaceId={workspaceId} />
          </aside>
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Status alerts — bundle-stale + blockers, only when something is non-ok
// ---------------------------------------------------------------------------


function StatusAlerts({
  blockers,
  reposNeedingUpdate,
  workspaceId,
}: {
  blockers: ApiOpsBlocker[];
  reposNeedingUpdate: ApiActivatedRepo[];
  workspaceId: string;
}) {
  const configureHref = `/onboarding?step=configure&ws=${encodeURIComponent(workspaceId)}`;
  return (
    <ul className="divide-y divide-white/[0.06]">
      {reposNeedingUpdate.length > 0 && (
        <li className="flex items-baseline justify-between gap-3 py-3">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/75">
              Ship template
            </p>
            <p className="mt-1 text-sm text-white/85">
              <span className="font-semibold text-white">
                Update available
              </span>{" "}
              ·{" "}
              {reposNeedingUpdate.length === 1
                ? reposNeedingUpdate[0].full_name
                : `${reposNeedingUpdate.length} repos behind`}
            </p>
          </div>
          <Link
            href={configureHref}
            className="shrink-0 text-xs font-semibold text-aqua hover:text-white"
          >
            Open wizard →
          </Link>
        </li>
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
  decisionsByType,
  prsReadyToMerge,
  shippedTotal,
  blockerCount,
  workspaceId,
}: {
  decisions: InboxItem[];
  decisionsTotal: number;
  decisionsByType: Record<string, number>;
  prsReadyToMerge: ReadyToMergePr[];
  shippedTotal: number;
  blockerCount: number;
  workspaceId: string;
}) {
  // Lede subtitle — built from real fields only. Each clause renders
  // only when its number is meaningful, otherwise it's omitted.
  const subtitleParts: React.ReactNode[] = [];
  if (decisionsTotal > 0) {
    subtitleParts.push(
      <span key="decisions">
        <span className="font-display font-bold text-white">
          {decisionsTotal}
        </span>{" "}
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
        shipped
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
        <h2 className="font-display text-3xl font-bold leading-tight text-white">
          {decisionsTotal + prsReadyToMerge.length + blockerCount > 0
            ? "Three things to look at."
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
            <DecisionTypeBreakdown counts={decisionsByType} />
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


function DecisionTypeBreakdown({ counts }: { counts: Record<string, number> }) {
  const order: InboxType[] = [
    "clarification",
    "approval",
    "failure",
    "improvement",
    "exception",
  ];
  const parts = order
    .map((t) => ({ t, n: counts[t] ?? 0 }))
    .filter((x) => x.n > 0);
  if (parts.length === 0) return null;
  return (
    <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/45">
      {parts.map((p, idx) => (
        <span key={p.t}>
          {idx > 0 && <span className="mx-1.5 text-white/15">·</span>}
          {p.n} {p.t.slice(0, 5)}
        </span>
      ))}
    </p>
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
// Work in flight — 3-col grid with column spine
// ---------------------------------------------------------------------------


const FLIGHT_COLUMNS: {
  id: ApiOpsWorkItem["status"];
  label: string;
  spine: string;
  kicker: string;
}[] = [
  {
    id: "in_progress",
    label: "In progress",
    spine: "bg-white/15",
    kicker: "text-white/55",
  },
  {
    id: "review",
    label: "Review",
    spine: "bg-aqua/30",
    kicker: "text-aqua/75",
  },
  {
    id: "blocked",
    label: "Blocked",
    spine: "bg-sun/40",
    kicker: "text-sun/80",
  },
];


function WorkInFlightSection({
  items,
  workspaceId,
}: {
  items: ApiOpsWorkItem[];
  workspaceId: string;
}) {
  if (items.length === 0) return null;
  const grouped = new Map<string, ApiOpsWorkItem[]>();
  for (const c of FLIGHT_COLUMNS) grouped.set(c.id, []);
  for (const item of items) {
    if (grouped.has(item.status)) grouped.get(item.status)!.push(item);
  }
  const tracker = primaryTrackerLabel(items);
  return (
    <section className="space-y-4">
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
            Work in flight
          </p>
          <p className="mt-1 text-xs text-white/55">
            Live tickets · {tracker ?? "auto-detected"}
          </p>
        </div>
        <Link
          href={`/process?ws=${encodeURIComponent(workspaceId)}`}
          className="shrink-0 text-xs font-semibold text-white/55 hover:text-white"
        >
          Open process →
        </Link>
      </header>
      <div className="grid grid-cols-1 gap-y-6 sm:grid-cols-3 sm:divide-x sm:divide-white/[0.06]">
        {FLIGHT_COLUMNS.map((col, idx) => {
          const colItems = grouped.get(col.id) ?? [];
          return (
            <div
              key={col.id}
              className={cn("min-w-0 space-y-3", idx > 0 && "sm:pl-4")}
            >
              <div className="flex items-baseline justify-between">
                <h3
                  className={cn(
                    "text-[10px] font-bold uppercase tracking-[0.22em]",
                    col.kicker,
                  )}
                >
                  {col.label}
                </h3>
                <span className="font-mono text-[11px] text-white/35">
                  {colItems.length}
                </span>
              </div>
              {colItems.length === 0 ? (
                <p className="text-[11px] italic text-white/30">empty</p>
              ) : (
                <ul className="divide-y divide-white/[0.06]">
                  {colItems.slice(0, 4).map((item) => (
                    <li key={`${item.name}-${item.updated_at}`}>
                      <FlightRow item={item} spineClass={col.spine} />
                    </li>
                  ))}
                  {colItems.length > 4 && (
                    <li className="pt-2 text-[10px] uppercase tracking-[0.14em] text-white/30">
                      +{colItems.length - 4} more
                    </li>
                  )}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}


function FlightRow({
  item,
  spineClass,
}: {
  item: ApiOpsWorkItem;
  spineClass: string;
}) {
  const href = item.href;
  const isExternal =
    href != null && (href.startsWith("http://") || href.startsWith("https://"));
  const inner = (
    <div className="group relative flex items-baseline justify-between gap-2 py-2 pl-3">
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-2 left-0 w-px rounded-r-sm",
          spineClass,
        )}
      />
      <div className="min-w-0">
        <p className="truncate text-[12.5px] font-semibold text-white group-hover:text-aqua">
          {item.ticket_ref && (
            <span className="mr-1.5 font-mono text-aqua/85">
              {item.ticket_ref}
            </span>
          )}
          {stripTicketPrefix(item.name, item.ticket_ref)}
        </p>
        <p className="mt-0.5 truncate text-[10px] text-white/40">
          {[item.repo, item.board_column, formatRelative(item.updated_at)]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
    </div>
  );
  if (!href) return inner;
  return isExternal ? (
    <a href={href} target="_blank" rel="noreferrer">
      {inner}
    </a>
  ) : (
    <Link href={href}>{inner}</Link>
  );
}


// ---------------------------------------------------------------------------
// Right rail — System pulse · Recent activity · Repos
// ---------------------------------------------------------------------------


function SystemPulse({ summary }: { summary: ApiOpsDashboard }) {
  const h = summary.automation_health;
  const stuck = summary.work_in_progress.filter((it) => it.status === "blocked").length;
  const lastDeploy = summary.system_status.last_deploy?.time ?? null;

  return (
    <section className="space-y-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Today
      </p>
      <dl className="space-y-2 text-sm">
        <PulseRow
          label="Success rate"
          value={h.success_rate === null ? null : formatPercent(h.success_rate)}
          tone={
            h.success_rate === null
              ? "muted"
              : h.success_rate < 0.8
                ? "warn"
                : "ok"
          }
        />
        <PulseRow
          label="Failures"
          value={String(h.failures_count)}
          tone={h.failures_count > 0 ? "err" : "ok"}
        />
        <PulseRow
          label="Stuck"
          value={String(stuck)}
          tone={stuck > 0 ? "warn" : "ok"}
        />
        <PulseRow
          label="Manual asks"
          value={String(h.manual_interventions_count)}
          tone={h.manual_interventions_count > 0 ? "warn" : "ok"}
        />
        {h.automation_coverage !== null && (
          <PulseRow
            label="Automation coverage"
            value={formatPercent(h.automation_coverage)}
            tone="muted"
          />
        )}
        {lastDeploy && (
          <PulseRow
            label="Last deploy"
            value={formatRelative(lastDeploy)}
            tone="muted"
          />
        )}
      </dl>
    </section>
  );
}


function PulseRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | null;
  tone: "ok" | "warn" | "err" | "muted";
}) {
  const valueColor =
    tone === "err"
      ? "text-coral"
      : tone === "warn"
        ? "text-sun"
        : tone === "ok"
          ? "text-white/85"
          : "text-white/45";
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-white/55">{label}</dt>
      <dd className={cn("font-mono text-sm tabular-nums", valueColor)}>
        {value ?? "—"}
      </dd>
    </div>
  );
}


function RecentActivity({ summary }: { summary: ApiOpsDashboard }) {
  const rows: {
    title: string;
    meta: string;
    href: string | null;
    tone: "aqua" | "coral" | "muted";
  }[] = [];

  for (const item of summary.shipped.items.slice(0, 6)) {
    rows.push({
      title: item.name,
      meta: [shippedTypeLabel(item.type), item.repo].filter(Boolean).join(" · "),
      href: item.href,
      tone: "aqua",
    });
  }
  if (summary.system_status.failing_pipelines_count > 0) {
    rows.push({
      title: `${summary.system_status.failing_pipelines_count} pipeline failure${summary.system_status.failing_pipelines_count === 1 ? "" : "s"} today`,
      meta: "system status",
      href: null,
      tone: "coral",
    });
  }

  if (rows.length === 0) {
    return (
      <section className="space-y-2">
        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
          Recent
        </p>
        <p className="text-xs italic text-white/35">
          Nothing shipped or flagged in the last 24h.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Recent · last 24h
      </p>
      <ul className="space-y-3 border-l border-white/10 pl-4">
        {rows.map((row, idx) => {
          const dot =
            row.tone === "aqua"
              ? "bg-aqua"
              : row.tone === "coral"
                ? "bg-coral"
                : "bg-white/30";
          const inner = (
            <div className="relative">
              <span
                aria-hidden
                className={cn(
                  "absolute -left-[1.1rem] top-1.5 h-1.5 w-1.5 rounded-full",
                  dot,
                )}
              />
              <p className="truncate text-[12.5px] font-semibold text-white">
                {row.title}
              </p>
              <p className="mt-0.5 truncate text-[10.5px] text-white/45">
                {row.meta}
              </p>
            </div>
          );
          return (
            <li key={`${idx}-${row.title}`}>
              {row.href ? (
                row.href.startsWith("http") ? (
                  <a
                    href={row.href}
                    target="_blank"
                    rel="noreferrer"
                    className="block hover:text-aqua"
                  >
                    {inner}
                  </a>
                ) : (
                  <Link href={row.href} className="block hover:text-aqua">
                    {inner}
                  </Link>
                )
              ) : (
                inner
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}


function RepoChannelList({
  repos,
  workspaceId,
}: {
  repos: ApiActivatedRepo[];
  workspaceId: string;
}) {
  if (repos.length === 0) return null;
  return (
    <section className="space-y-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Repos
      </p>
      <ul className="space-y-1.5">
        {repos.map((repo) => {
          const stale = needsShipTemplateUpdate(repo);
          return (
            <li key={repo.id}>
              <Link
                href={`/r/${encodeURIComponent(repo.full_name.split("/")[0])}/${encodeURIComponent(repo.full_name.split("/")[1])}?ws=${encodeURIComponent(workspaceId)}`}
                className="group flex items-baseline gap-2 text-[11px]"
              >
                <span
                  aria-hidden
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    stale ? "bg-sun/70" : "bg-white/25",
                  )}
                />
                <span className="truncate text-white/75 group-hover:text-white">
                  {repo.full_name}
                </span>
                {stale && (
                  <span className="ml-auto shrink-0 text-[10px] uppercase tracking-widest text-sun/80">
                    behind
                  </span>
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
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
};


const INBOX_SPINE_WIDTH: Record<InboxType, string> = {
  clarification: "w-[3px]",
  approval: "w-[3px]",
  improvement: "w-[2px]",
  failure: "w-[3px]",
  blocker: "w-[4px]",
  exception: "w-[2px]",
  stuck: "w-px",
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
  }
}


function shippedTypeLabel(type: ApiOpsShippedItem["type"]): string {
  if (type === "feature") return "feature";
  if (type === "fix") return "fix";
  return "rollback";
}


function primaryTrackerLabel(items: ApiOpsWorkItem[]): string | null {
  const counts = new Map<string, number>();
  for (const it of items) {
    if (!it.tracker) continue;
    counts.set(it.tracker, (counts.get(it.tracker) ?? 0) + 1);
  }
  if (counts.size === 0) return null;
  let best: [string, number] | null = null;
  for (const entry of counts) {
    if (!best || entry[1] > best[1]) best = entry;
  }
  return best ? best[0] : null;
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


function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}


function formatRelative(value: string): string {
  const date = Date.parse(value);
  if (Number.isNaN(date)) return "—";
  const diff = Date.now() - date;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < hour) return `${Math.max(1, Math.round(diff / minute))}m`;
  if (diff < day) return `${Math.round(diff / hour)}h`;
  if (diff < 30 * day) return `${Math.round(diff / day)}d`;
  return new Date(date).toLocaleDateString();
}


function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}
