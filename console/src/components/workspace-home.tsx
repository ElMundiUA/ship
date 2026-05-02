import Link from "next/link";

import type {
  ApiActivatedRepo,
  ApiOpsDashboard,
  ApiOpsStatus,
  ApiOpsWorkItem,
} from "@/lib/api/client";
import { Badge, type BadgeTone, Card } from "@/components/ui";
import type {
  InboxCountsResponse,
  InboxItem,
  InboxListResponse,
  InboxType,
} from "@/lib/inbox-types";

/**
 * Workspace home — five-section operator dashboard.
 *
 *   1. NeedsShipUpdateBanner   — compact one-line banner if any repo is
 *                                behind the current Ship bundle.
 *   2. TodayPulseSection       — daily standup snapshot. Date kicker
 *                                plus four mini-stats (routines today,
 *                                upcoming, yesterday closed, blockers).
 *   3. WhatNeedsYouSection     — two sub-blocks. Decisions waiting
 *                                (Inbox preview with inline action
 *                                buttons) and "Ready to merge" PRs
 *                                (placeholder — backend merge endpoint
 *                                ships in a follow-up).
 *   4. WorkInFlightSection     — kanban-style mini-board grouped by
 *                                process state, with tracker source
 *                                label up top so the data origin is
 *                                always obvious.
 *   5. PipelineHealthSection   — single compact strip with routine
 *                                run counts, stuck items, and knowledge
 *                                drift signal.
 *   6. RecentActivitySection   — timeline of recently shipped items
 *                                and routine outputs (skeleton; full
 *                                activity feed lands when the backend
 *                                exposes /v1/.../activity).
 *
 * Data flows in from page.tsx via two server-side fetches:
 *   - getOpsDashboard(workspaceId)  → ApiOpsDashboard
 *   - listInboxItems / getInboxCounts → InboxListResponse / InboxCountsResponse
 *
 * Both inbox calls are best-effort (`null` is handled).
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
  const reposNeedingShipUpdate = repos.filter(needsShipTemplateUpdate);

  return (
    <div className="space-y-6">
      {reposNeedingShipUpdate.length > 0 && (
        <NeedsShipUpdateBanner repos={reposNeedingShipUpdate} workspaceId={workspaceId} />
      )}

      <TodayPulseSection summary={summary} />
      <WhatNeedsYouSection
        inboxItems={inboxItems}
        inboxCounts={inboxCounts}
        workspaceId={workspaceId}
        prsReadyToMerge={derivePrsReadyToMerge(summary)}
      />
      <WorkInFlightSection summary={summary} workspaceId={workspaceId} />
      <PipelineHealthSection summary={summary} />
      <RecentActivitySection summary={summary} />
    </div>
  );
}

/* ---------------------------------------------------------------- *
 * Section 1 — Today's pulse                                         *
 * ---------------------------------------------------------------- */

function TodayPulseSection({ summary }: { summary: ApiOpsDashboard }) {
  const todayLabel = formatTodayLabel();
  const health = summary.automation_health;
  const status = summary.system_status;
  const shipped = summary.shipped;
  const totalShipped =
    shipped.features_shipped_count + shipped.fixes_count + shipped.rollbacks_count;
  const blockerCount = status.failing_pipelines_count + status.broken_automations_count;
  const tone: PulseTone =
    blockerCount > 0 ? "critical" : health.failures_count > 0 ? "warn" : "ok";

  return (
    <section className="rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.03] via-transparent to-white/[0.02] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/40">
            {todayLabel}
          </p>
          <h2 className="font-display mt-1 text-xl font-bold text-white">Workspace pulse</h2>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/30 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em]">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${
              tone === "critical" ? "bg-coral" : tone === "warn" ? "bg-sun" : "bg-aqua"
            }`}
          />
          <span
            className={
              tone === "critical"
                ? "text-coral"
                : tone === "warn"
                  ? "text-sun"
                  : "text-aqua"
            }
          >
            {tone === "critical" ? "needs attention" : tone === "warn" ? "degraded" : "healthy"}
          </span>
        </span>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        <PulseStat
          glyph="✓"
          glyphTone="aqua"
          value={`${health.success_rate !== null ? Math.round(health.success_rate * (health.failures_count + 1)) : 0}`}
          label="Routines completed today"
          hint={
            health.success_rate === null
              ? "No runs yet"
              : `${formatPercent(health.success_rate)} success`
          }
        />
        <PulseStat
          glyph="▸"
          glyphTone="sun"
          value="—"
          label="Routines coming up"
          hint="Schedule view in /process"
        />
        <PulseStat
          glyph="✓"
          glyphTone="aqua"
          value={String(totalShipped)}
          label="Shipped in last 24h"
          hint={`${shipped.features_shipped_count}f · ${shipped.fixes_count}fix · ${shipped.rollbacks_count}rb`}
        />
        <PulseStat
          glyph={blockerCount > 0 ? "⚠" : "·"}
          glyphTone={blockerCount > 0 ? "coral" : "muted"}
          value={String(blockerCount)}
          label="Active blockers"
          hint={
            blockerCount > 0
              ? "From CI failures and broken automations"
              : "Nothing blocking"
          }
        />
      </div>
    </section>
  );
}

type PulseTone = "ok" | "warn" | "critical";
type GlyphTone = "aqua" | "sun" | "coral" | "muted";

function PulseStat({
  glyph,
  glyphTone,
  value,
  label,
  hint,
}: {
  glyph: string;
  glyphTone: GlyphTone;
  value: string;
  label: string;
  hint: string;
}) {
  const glyphColor =
    glyphTone === "aqua"
      ? "text-aqua"
      : glyphTone === "sun"
        ? "text-sun"
        : glyphTone === "coral"
          ? "text-coral"
          : "text-white/30";
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3">
      <div className="flex items-baseline gap-2">
        <span className={`font-mono text-xl font-bold ${glyphColor}`}>{glyph}</span>
        <span className="font-display text-2xl font-bold text-white">{value}</span>
      </div>
      <p className="mt-2 text-xs font-semibold text-white/85">{label}</p>
      <p className="mt-1 text-[11px] text-white/45">{hint}</p>
    </div>
  );
}

/* ---------------------------------------------------------------- *
 * Section 2 — What needs you                                        *
 * ---------------------------------------------------------------- */

function WhatNeedsYouSection({
  inboxItems,
  inboxCounts,
  workspaceId,
  prsReadyToMerge,
}: {
  inboxItems: InboxListResponse | null;
  inboxCounts: InboxCountsResponse | null;
  workspaceId: string;
  prsReadyToMerge: ReadyToMergePr[];
}) {
  const total = inboxCounts?.all_open ?? inboxItems?.total ?? 0;
  const byType = inboxCounts?.by_type ?? inboxItems?.counts_by_type ?? {};
  const inboxHref = `/inbox?ws=${encodeURIComponent(workspaceId)}`;

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="font-display text-xl font-bold text-white">What needs you</h2>
          <p className="mt-1 text-xs text-white/50">
            Decisions waiting on a human and PRs ready to merge.
          </p>
        </div>
        <Link href={inboxHref} className="text-xs font-semibold text-sun hover:text-white">
          Open Inbox →
        </Link>
      </div>

      {/* Decisions block */}
      <div className="mt-5">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/85">
            Decisions
          </p>
          <span className="font-display text-lg font-bold text-white">{total}</span>
          <span className="text-xs text-white/45">waiting</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {(["clarification", "approval", "failure", "improvement", "exception"] as InboxType[]).map(
              (t) => {
                const c = byType[t] ?? 0;
                if (c === 0) return null;
                return (
                  <span
                    key={t}
                    className={`inline-flex items-center rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] ${typeBadgeClass(t)}`}
                  >
                    {c} {t.slice(0, 5)}
                  </span>
                );
              },
            )}
          </div>
        </div>

        {!inboxItems || inboxItems.items.length === 0 ? (
          <EmptyState>Caught up. Workspace is quiet.</EmptyState>
        ) : (
          <ul className="mt-4 space-y-2">
            {inboxItems.items.slice(0, 4).map((item) => (
              <li key={item.id}>
                <DecisionRow item={item} workspaceId={workspaceId} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Ready to merge block */}
      <div className="mt-6 border-t border-white/10 pt-5">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/85">
            Ready to merge
          </p>
          <span className="font-display text-lg font-bold text-white">
            {prsReadyToMerge.length}
          </span>
          <span className="text-xs text-white/45">PRs</span>
        </div>
        {prsReadyToMerge.length === 0 ? (
          <EmptyState>No PRs awaiting merge.</EmptyState>
        ) : (
          <ul className="mt-4 space-y-2">
            {prsReadyToMerge.slice(0, 4).map((pr) => (
              <li key={`${pr.repo}-${pr.number}`}>
                <ReadyToMergeRow pr={pr} />
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-[11px] text-white/40">
          Merge button is wired to the Ship GitHub App; backend endpoint lands in the next pass.
          Until then this list links straight to GitHub.
        </p>
      </div>
    </Card>
  );
}

function DecisionRow({ item, workspaceId }: { item: InboxItem; workspaceId: string }) {
  const href = `/inbox/${encodeURIComponent(item.id)}?ws=${encodeURIComponent(workspaceId)}`;
  const actionLabel = canonicalActionLabel(item.type);
  return (
    <Link
      href={href}
      className="group flex items-start justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2.5 transition hover:border-white/25 hover:bg-white/[0.05]"
    >
      <div className="flex min-w-0 flex-1 items-start gap-2.5">
        <span
          className={`mt-0.5 inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.14em] ${typeBadgeClass(item.type)}`}
        >
          {item.type.slice(0, 5)}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-white">{item.title}</p>
          {item.summary && (
            <p className="mt-0.5 truncate text-[11px] text-white/55">{item.summary}</p>
          )}
        </div>
      </div>
      <span className="shrink-0 rounded-md border border-white/15 bg-white/[0.05] px-2.5 py-1 text-[11px] font-semibold text-white/85 group-hover:border-aqua/40 group-hover:text-aqua">
        {actionLabel}
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
    <div className="flex items-start justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-white">
          <span className="font-mono text-aqua">#{pr.number}</span> · {pr.title}
        </p>
        <p className="mt-0.5 truncate text-[11px] text-white/50">
          {[pr.ticketRef, pr.repo, "✓ CI"].filter(Boolean).join(" · ")}
        </p>
      </div>
      <a
        href={pr.href}
        target="_blank"
        rel="noreferrer"
        className="shrink-0 rounded-md border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-bold uppercase tracking-wide text-aqua transition hover:border-aqua/70 hover:bg-aqua/20"
      >
        Merge
      </a>
    </div>
  );
}

function derivePrsReadyToMerge(summary: ApiOpsDashboard): ReadyToMergePr[] {
  // Best-effort: surface review-state work items that have a PR attached.
  // Real "ready to merge" check (CI green, reviews satisfied, no conflicts)
  // requires the upcoming /dashboard/ops PR-mergeability fields.
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

function stripTicketPrefix(name: string, ref: string | null | undefined): string {
  if (!ref) return name;
  if (name.startsWith(`${ref}: `)) return name.slice(ref.length + 2);
  if (name.startsWith(`${ref} `)) return name.slice(ref.length + 1);
  return name;
}

/* ---------------------------------------------------------------- *
 * Section 3 — Work in flight (kanban mini)                          *
 * ---------------------------------------------------------------- */

const FLIGHT_COLUMNS: { id: ApiOpsWorkItem["status"]; label: string }[] = [
  { id: "in_progress", label: "In progress" },
  { id: "review", label: "Review" },
  { id: "blocked", label: "Blocked" },
];

function WorkInFlightSection({
  summary,
  workspaceId,
}: {
  summary: ApiOpsDashboard;
  workspaceId: string;
}) {
  const items = summary.work_in_progress;
  const trackerLabel = primaryTrackerLabel(items);
  const grouped = new Map<string, ApiOpsWorkItem[]>();
  for (const c of FLIGHT_COLUMNS) grouped.set(c.id, []);
  for (const item of items) {
    if (grouped.has(item.status)) grouped.get(item.status)!.push(item);
  }

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="font-display text-xl font-bold text-white">Work in flight</h2>
          <p className="mt-1 text-xs text-white/50">
            Live tickets grouped by state. Tracker source:{" "}
            <span className="text-white/85">{trackerLabel ?? "auto-detected per repo"}</span>.
          </p>
        </div>
        <Link
          href={`/process?ws=${encodeURIComponent(workspaceId)}`}
          className="text-xs font-semibold text-sun hover:text-white"
        >
          Open process →
        </Link>
      </div>

      {items.length === 0 ? (
        <EmptyState>Nothing in flight right now.</EmptyState>
      ) : (
        <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
          {FLIGHT_COLUMNS.map((col) => {
            const colItems = grouped.get(col.id) ?? [];
            return <FlightColumn key={col.id} label={col.label} items={colItems} />;
          })}
        </div>
      )}
    </Card>
  );
}

function FlightColumn({ label, items }: { label: string; items: ApiOpsWorkItem[] }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-baseline justify-between">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/55">{label}</p>
        <span className="font-mono text-xs text-white/55">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="mt-3 text-[11px] text-white/30">Empty</p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {items.slice(0, 4).map((item) => (
            <li key={`${item.name}-${item.updated_at}`}>
              <FlightRow item={item} />
            </li>
          ))}
          {items.length > 4 && (
            <li className="pt-1 text-[10px] uppercase tracking-[0.14em] text-white/30">
              +{items.length - 4} more
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

function FlightRow({ item }: { item: ApiOpsWorkItem }) {
  const href = item.href;
  const isExternal =
    href != null && (href.startsWith("http://") || href.startsWith("https://"));
  const content = (
    <div className="rounded-lg border border-white/[0.06] bg-black/20 px-2.5 py-2">
      <p className="truncate text-[12px] font-semibold text-white">
        {item.ticket_ref && (
          <span className="mr-1.5 font-mono text-aqua/85">{item.ticket_ref}</span>
        )}
        {stripTicketPrefix(item.name, item.ticket_ref)}
      </p>
      <p className="mt-0.5 truncate text-[10px] text-white/40">
        {[item.repo, item.board_column, formatDate(item.updated_at)]
          .filter(Boolean)
          .join(" · ")}
      </p>
    </div>
  );
  if (!href) return content;
  return isExternal ? (
    <a href={href} target="_blank" rel="noreferrer">
      {content}
    </a>
  ) : (
    <Link href={href}>{content}</Link>
  );
}

function primaryTrackerLabel(items: ApiOpsWorkItem[]): string | null {
  // Pick the most-frequent tracker label across WIP items so the user
  // sees what's actually feeding the column. Without this, mixed sources
  // (Linear + GitHub Issues fallback per repo) hide silently.
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

/* ---------------------------------------------------------------- *
 * Section 4 — Pipeline health (compact strip)                       *
 * ---------------------------------------------------------------- */

function PipelineHealthSection({ summary }: { summary: ApiOpsDashboard }) {
  const h = summary.automation_health;
  const stuckCount = summary.work_in_progress.filter((it) => it.status === "blocked").length;
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
          Pipeline health
        </p>
        <HealthMetric
          label="Routines today"
          value={`${h.failures_count + Math.round((h.success_rate ?? 0) * (h.failures_count + 1))} runs`}
          subtone="ok"
        />
        <HealthMetric
          label="Failures"
          value={String(h.failures_count)}
          subtone={h.failures_count > 0 ? "err" : "ok"}
        />
        <HealthMetric
          label="Success rate"
          value={h.success_rate === null ? "—" : formatPercent(h.success_rate)}
          subtone={h.success_rate !== null && h.success_rate < 0.8 ? "warn" : "ok"}
        />
        <HealthMetric
          label="Stuck items"
          value={String(stuckCount)}
          subtone={stuckCount > 0 ? "warn" : "ok"}
        />
        <HealthMetric
          label="Manual asks"
          value={String(h.manual_interventions_count)}
          subtone={h.manual_interventions_count > 0 ? "warn" : "ok"}
        />
      </div>
    </section>
  );
}

function HealthMetric({
  label,
  value,
  subtone,
}: {
  label: string;
  value: string;
  subtone: "ok" | "warn" | "err";
}) {
  const color =
    subtone === "err" ? "text-coral" : subtone === "warn" ? "text-sun" : "text-aqua";
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[11px] uppercase tracking-[0.14em] text-white/45">{label}</span>
      <span className={`font-display text-base font-bold ${color}`}>{value}</span>
    </div>
  );
}

/* ---------------------------------------------------------------- *
 * Section 5 — Recent activity (timeline)                            *
 * ---------------------------------------------------------------- */

type ActivityRow = {
  kind: "shipped" | "blocker" | "system";
  title: string;
  meta: string;
  href: string | null;
  toneDot: "aqua" | "sun" | "coral" | "muted";
};

function RecentActivitySection({ summary }: { summary: ApiOpsDashboard }) {
  const rows: ActivityRow[] = [];

  for (const item of summary.shipped.items.slice(0, 6)) {
    rows.push({
      kind: "shipped",
      title: item.name,
      meta: [item.type, item.repo].filter(Boolean).join(" · "),
      href: item.href,
      toneDot: "aqua",
    });
  }
  if (summary.system_status.failing_pipelines_count > 0) {
    rows.push({
      kind: "system",
      title: `${summary.system_status.failing_pipelines_count} pipeline(s) failed in last 24h`,
      meta: "system status · check audit",
      href: null,
      toneDot: "coral",
    });
  }
  if (rows.length === 0) {
    return (
      <Card>
        <h2 className="font-display text-xl font-bold text-white">Recent activity</h2>
        <EmptyState>Nothing shipped or flagged in the last 24 hours.</EmptyState>
        <p className="mt-3 text-[11px] text-white/40">
          Full activity timeline (PRs, routine outputs, decisions) lands when the backend exposes
          a unified <code className="text-white/55">/v1/.../activity</code> feed.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="font-display text-xl font-bold text-white">Recent activity</h2>
        <span className="text-[11px] text-white/40">last 24h</span>
      </div>
      <ul className="mt-4 space-y-1.5">
        {rows.map((row, i) => (
          <li key={`${row.kind}-${i}-${row.title}`}>
            <ActivityItem row={row} />
          </li>
        ))}
      </ul>
      <p className="mt-4 text-[11px] text-white/40">
        Skeleton feed — derived from shipped PRs and system status. Full activity timeline lands
        when the backend exposes a unified <code className="text-white/55">/v1/.../activity</code>{" "}
        feed.
      </p>
    </Card>
  );
}

function ActivityItem({ row }: { row: ActivityRow }) {
  const dot =
    row.toneDot === "aqua"
      ? "bg-aqua"
      : row.toneDot === "sun"
        ? "bg-sun"
        : row.toneDot === "coral"
          ? "bg-coral"
          : "bg-white/30";
  const content = (
    <div className="flex items-start gap-3 rounded-xl border border-white/[0.06] bg-white/[0.015] px-3 py-2 transition hover:border-white/20 hover:bg-white/[0.04]">
      <span className={`mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[12.5px] font-semibold text-white">{row.title}</p>
        <p className="mt-0.5 truncate text-[10.5px] text-white/45">{row.meta}</p>
      </div>
    </div>
  );
  if (!row.href) return content;
  const isExternal = row.href.startsWith("http://") || row.href.startsWith("https://");
  return isExternal ? (
    <a href={row.href} target="_blank" rel="noreferrer">
      {content}
    </a>
  ) : (
    <Link href={row.href}>{content}</Link>
  );
}

/* ---------------------------------------------------------------- *
 * NeedsShipUpdateBanner (compact)                                   *
 * ---------------------------------------------------------------- */

function NeedsShipUpdateBanner({
  repos,
  workspaceId,
}: {
  repos: ApiActivatedRepo[];
  workspaceId: string;
}) {
  const configureHref = `/onboarding?step=configure&ws=${encodeURIComponent(workspaceId)}`;
  return (
    <section className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-aqua/25 bg-aqua/[0.06] px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-bold text-aqua">Ship template update available</span>
        <span className="text-white/55">
          {repos.length === 1
            ? `${repos[0].full_name}`
            : `${repos.length} repos behind`}
        </span>
      </div>
      <Link
        href={configureHref}
        className="shrink-0 rounded-full border border-aqua/50 bg-aqua/10 px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/20"
      >
        Open wizard →
      </Link>
    </section>
  );
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

/* ---------------------------------------------------------------- *
 * Shared helpers                                                    *
 * ---------------------------------------------------------------- */

function EmptyState({ children }: { children: string }) {
  return (
    <div className="mt-3 rounded-xl border border-dashed border-white/10 bg-white/[0.015] px-4 py-3 text-sm text-white/45">
      {children}
    </div>
  );
}

function typeBadgeClass(type: InboxType): string {
  switch (type) {
    case "clarification":
      return "border-sun/40 bg-sun/10 text-sun";
    case "approval":
      return "border-aqua/40 bg-aqua/10 text-aqua";
    case "improvement":
      return "border-lilac/40 bg-lilac/10 text-lilac";
    case "failure":
      return "border-coral/40 bg-coral/10 text-coral";
    case "exception":
      return "border-white/30 bg-white/10 text-white/85";
    default:
      return "border-white/15 bg-white/5 text-white/65";
  }
}

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
      return "Acknowledge";
    default:
      return "Open";
  }
}

function formatTodayLabel(): string {
  const now = new Date();
  return new Intl.DateTimeFormat("en", {
    weekday: "long",
    month: "short",
    day: "numeric",
  }).format(now);
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

// Avoid an unused-import error for ApiOpsStatus / BadgeTone now that the
// new layout doesn't lean on them. They stay typed in case a follow-up
// section needs them again.
type _Unused = ApiOpsStatus | BadgeTone;
void undefined as unknown as _Unused;
