import Link from "next/link";

import { DashboardPrioritizer } from "@/components/dashboard/prioritizer";
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

export type TemplateUpdateState = {
  /** PR number returned by the seed call — if set, render the auto-merge confirm step. */
  seedPr: number | null;
  /** Repo id the seed PR was opened against (needed for the merge action). */
  seedRepo: string | null;
  /** Error code from a failed seed/activate call (renders inline banner). */
  seedError: string | null;
  /** PR number that just merged — renders a success banner that the operator can dismiss. */
  seedMerged: number | null;
};

export type WorkspaceHomeProps = {
  summary: ApiOpsDashboard;
  repos: ApiActivatedRepo[];
  workspaceId: string;
  multiWs?: boolean;
  inboxItems: InboxListResponse | null;
  inboxCounts: InboxCountsResponse | null;
  priorities: ApiPrioritiesResponse | null;
  liveSystem: ApiLiveSystem | null;
  templateUpdate?: TemplateUpdateState;
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
  templateUpdate,
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
  const inFlight = summary.work_in_progress.length;
  const inProgress = summary.work_in_progress.filter(
    (it) => it.status === "in_progress",
  ).length;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="space-y-10">
        {(reposNeedingUpdate.length > 0
          || summary.blockers.length > 0
          || templateUpdate?.seedPr
          || templateUpdate?.seedMerged
          || templateUpdate?.seedError) && (
          <StatusAlerts
            blockers={summary.blockers}
            reposNeedingUpdate={reposNeedingUpdate}
            workspaceId={workspaceId}
            multiWs={multiWs}
            templateUpdate={templateUpdate}
          />
        )}

        <LiveSystemStrip data={liveSystem} workspaceId={workspaceId} />

        <NeedsYouSection
          decisions={decisions}
          decisionsTotal={decisionsTotal}
          prsReadyToMerge={prsReadyToMerge}
          shippedTotal={totalShipped}
          blockerCount={blockerCount}
          workspaceId={workspaceId}
        />

        {priorities ? (
          <DashboardPrioritizer
            workspaceId={workspaceId}
            initial={priorities}
          />
        ) : null}

        <LastActionStrip lastAction={priorities?.last_action ?? null} />

        <ActiveTicketsStrip
          inFlight={inFlight}
          inProgress={inProgress}
          workspaceId={workspaceId}
        />
      </div>
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
          <span className="text-coral">
            {failures} failure{failures === 1 ? "" : "s"}
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
  templateUpdate,
}: {
  blockers: ApiOpsBlocker[];
  reposNeedingUpdate: ApiActivatedRepo[];
  workspaceId: string;
  multiWs: boolean;
  templateUpdate?: TemplateUpdateState;
}) {
  const wsScope = multiWs ? workspaceId : "";
  const seedPr = templateUpdate?.seedPr ?? null;
  const seedRepo = templateUpdate?.seedRepo ?? null;
  const seedMerged = templateUpdate?.seedMerged ?? null;
  const seedError = templateUpdate?.seedError ?? null;
  // Pick the first stale repo by default — the dashboard never has more
  // than one in practice for closed beta. Multi-repo case still works:
  // operator hits the alert per repo because each lands in
  // ``reposNeedingUpdate`` with its own row.
  const updateRepo =
    seedRepo
      ? reposNeedingUpdate.find((r) => r.id === seedRepo) ?? reposNeedingUpdate[0]
      : reposNeedingUpdate[0];

  return (
    <ul className="divide-y divide-white/[0.06]">
      {seedMerged !== null && (
        <li className="flex items-baseline justify-between gap-3 py-3">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/75">
              Ship template
            </p>
            <p className="mt-1 text-sm text-white/85">
              <span className="font-semibold text-white">Updated</span> · PR
              #{seedMerged} merged. Bundle live on next routine tick.
            </p>
          </div>
          <a
            href={
              wsScope
                ? `/?ws=${encodeURIComponent(wsScope)}`
                : "/"
            }
            className="shrink-0 text-xs font-semibold text-white/55 hover:text-white"
          >
            Dismiss
          </a>
        </li>
      )}
      {seedError !== null && (
        <li className="flex items-baseline justify-between gap-3 py-3">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-coral/85">
              Ship template
            </p>
            <p className="mt-1 text-sm text-coral/95">
              {seedErrorMessage(seedError)}
            </p>
          </div>
          <a
            href={
              wsScope
                ? `/?ws=${encodeURIComponent(wsScope)}`
                : "/"
            }
            className="shrink-0 text-xs font-semibold text-white/55 hover:text-white"
          >
            Dismiss
          </a>
        </li>
      )}
      {seedPr !== null && seedRepo && (
        <li className="py-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/75">
            Ship template
          </p>
          <p className="mt-1 text-sm text-white/85">
            <span className="font-semibold text-white">PR #{seedPr} opened</span>{" "}
            · auto-merge when CI passes?
          </p>
          <div className="mt-2 flex items-center gap-3">
            <form
              action="/api/template-update/activate"
              method="POST"
              className="contents"
            >
              <input type="hidden" name="ws" value={workspaceId} />
              <input type="hidden" name="repo_id" value={seedRepo} />
              <input type="hidden" name="pr_number" value={String(seedPr)} />
              <input type="hidden" name="action" value="merge" />
              <input type="hidden" name="ws_scope" value={wsScope} />
              <button
                type="submit"
                className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110"
              >
                Yes, auto-merge
              </button>
            </form>
            <form
              action="/api/template-update/activate"
              method="POST"
              className="contents"
            >
              <input type="hidden" name="ws" value={workspaceId} />
              <input type="hidden" name="repo_id" value={seedRepo} />
              <input type="hidden" name="pr_number" value={String(seedPr)} />
              <input type="hidden" name="action" value="skip" />
              <input type="hidden" name="ws_scope" value={wsScope} />
              <button
                type="submit"
                className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-[11px] font-semibold text-white/85 transition hover:bg-white/[0.08]"
              >
                I&rsquo;ll merge it myself
              </button>
            </form>
          </div>
        </li>
      )}
      {reposNeedingUpdate.length > 0
        && seedPr === null
        && seedMerged === null
        && updateRepo && (
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
          <form
            action="/api/template-update/seed"
            method="POST"
            className="contents"
          >
            <input type="hidden" name="ws" value={workspaceId} />
            <input type="hidden" name="repo_id" value={updateRepo.id} />
            <input type="hidden" name="ws_scope" value={wsScope} />
            <button
              type="submit"
              className="shrink-0 text-xs font-semibold text-aqua hover:text-white"
            >
              Update →
            </button>
          </form>
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
  prsReadyToMerge,
  shippedTotal,
  blockerCount,
  workspaceId,
}: {
  decisions: InboxItem[];
  decisionsTotal: number;
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


function seedErrorMessage(code: string): string {
  switch (code) {
    case "merge_blocked":
      return "PR opened but GitHub wouldn't merge — branch protection or required checks aren't satisfied. Open the PR on GitHub to finish.";
    case "github_app_missing":
      return "Ship's GitHub App isn't installed. Reinstall it and try again.";
    case "github_upstream_error":
      return "GitHub rejected the merge. Open the PR and merge by hand.";
    case "validation_failed":
      return "The seed call failed validation. Refresh and try again.";
    case "forbidden":
      return "You don't have permission to update the template here.";
    case "not_found":
      return "Repo or workspace went missing. Refresh and try again.";
    case "api_unavailable":
      return "Ship API is unreachable. Try again in a moment.";
    case "bad_input":
      return "The action couldn't be applied — required fields were missing.";
    default:
      return `Couldn't update the template (${code}).`;
  }
}
