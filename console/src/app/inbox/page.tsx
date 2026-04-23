/**
 * Unified Inbox list page (RFC-0010 ticket P2-12).
 *
 * Server component that wires the just-shipped foundation pieces
 * together: `listInboxItems` from the API client, `InboxItemRow` for
 * each row, `InboxFilters` (via the `InboxFiltersControlled` client
 * shell) for the three-axis filter chips. State lives in the URL so
 * admins can deep-link / share / refresh without losing context;
 * `searchParams` are parsed defensively through the type guards in
 * `inbox-types.ts` so a hostile URL can't smuggle bad enum values
 * into the API query.
 *
 * Two modes, mirroring `/members` and `/settings/groups`:
 *   - **live**: `SHIP_API_URL` set + valid session → real list +
 *     counts from `/v1/workspaces/{ws}/inbox`.
 *   - **mock**: API unconfigured, session missing/expired, or the
 *     backend is unreachable → static MockView with five sample
 *     items (one per inbox type) so the marketing-style preview
 *     deployment has something to show.
 *
 * The detail page (P2-13) and routing settings (P2-16) are sibling
 * tickets — this file links to `/inbox/{id}` but does not own that
 * route.
 */

import { AppShell } from "@/components/app-shell";
import { InboxFiltersControlled } from "@/components/inbox/inbox-filters-controlled";
import { InboxItemRow } from "@/components/inbox/inbox-item-row";
import {
  buildInboxUrl,
  countActiveFilters,
} from "@/components/inbox/inbox-url";
import {
  Card,
  CardHeader,
  EmptyState,
  LiveBanner,
  MockBanner,
} from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getMe,
  isApiConfigured,
  listInboxItems,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import type { ApiUser, ApiWorkspace } from "@/lib/api/types";
import {
  DEFAULT_INBOX_FILTERS,
  INBOX_STATUSES,
  INBOX_TYPES,
  INBOX_TYPE_META,
  isInboxStatus,
  isInboxType,
  type InboxFilterState,
  type InboxItem,
  type InboxListResponse,
  type InboxStatus,
  type InboxType,
} from "@/lib/inbox-types";
import { workspaces as mockWorkspaces } from "@/lib/mock/cloud";

export const dynamic = "force-dynamic";

const PAGE_LIMIT = 25;

type ParsedParams = {
  filters: InboxFilterState;
  cursor: string | null;
  repo: string | null;
  play: string | null;
  errorCode: string | null;
};

type Mode =
  | {
      source: "live";
      workspace: ApiWorkspace;
      me: ApiUser | null;
      list: InboxListResponse;
      filters: InboxFilterState;
      cursor: string | null;
      repo: string | null;
      play: string | null;
    }
  | { source: "mock"; reason: string };

function errorMessage(code: string): string {
  switch (code) {
    case "forbidden":
      return "You don't have permission to act on that inbox item.";
    case "not_found":
      return "That inbox item is gone — it may have been resolved in another tab.";
    case "bad_input":
      return "The action couldn't be applied — required fields were missing.";
    case "stale":
      return "Item changed since you opened it. Refresh and try again.";
    case "api_unavailable":
      return "Backend is unreachable. Try again in a moment.";
    default:
      return `Couldn't apply the change (${code}). Try again or refresh.`;
  }
}

function parseSearchParams(
  raw: Record<string, string | string[] | undefined>,
): ParsedParams {
  const ownershipRaw = typeof raw.ownership === "string" ? raw.ownership : null;
  const ownership: InboxFilterState["ownership"] =
    ownershipRaw === "mine" ||
    ownershipRaw === "unassigned" ||
    ownershipRaw === "all"
      ? ownershipRaw
      : DEFAULT_INBOX_FILTERS.ownership;

  const typeRaw = raw.type;
  const typeArr = Array.isArray(typeRaw)
    ? typeRaw
    : typeof typeRaw === "string"
      ? [typeRaw]
      : [];
  const types = typeArr.filter(
    (v): v is InboxType => typeof v === "string" && isInboxType(v),
  );

  const statusRaw = raw.status;
  const statusArr = Array.isArray(statusRaw)
    ? statusRaw
    : typeof statusRaw === "string"
      ? [statusRaw]
      : [];
  const statusesParsed = statusArr.filter(
    (v): v is InboxStatus => typeof v === "string" && isInboxStatus(v),
  );
  const statuses =
    statusArr.length > 0 ? statusesParsed : [...DEFAULT_INBOX_FILTERS.statuses];

  const cursorRaw = typeof raw.cursor === "string" ? raw.cursor : null;
  const cursor = cursorRaw && cursorRaw.length > 0 ? cursorRaw : null;
  const repoRaw = typeof raw.repo === "string" ? raw.repo : null;
  const repo = repoRaw && repoRaw.length > 0 ? repoRaw : null;
  const playRaw = typeof raw.play === "string" ? raw.play : null;
  const play = playRaw && playRaw.length > 0 ? playRaw : null;
  const errorCode = typeof raw.error === "string" ? raw.error : null;

  return {
    filters: { ownership, types, statuses },
    cursor,
    repo,
    play,
    errorCode,
  };
}

async function load(parsed: ParsedParams): Promise<Mode> {
  if (!isApiConfigured()) {
    return { source: "mock", reason: "SHIP_API_URL is not set" };
  }
  const token = await getSessionToken();
  if (!token) {
    return { source: "mock", reason: "Sign in to view your real inbox" };
  }
  let workspace: ApiWorkspace;
  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0) {
      return {
        source: "mock",
        reason: "Create a workspace first to use the inbox",
      };
    }
    workspace = ws[0];
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return { source: "mock", reason: "Session expired — sign in again" };
    }
    if (err instanceof ApiUnavailableError) {
      return { source: "mock", reason: "Backend unreachable" };
    }
    return {
      source: "mock",
      reason: err instanceof Error ? err.message : "Backend returned an error",
    };
  }

  try {
    const [list, me] = await Promise.all([
      listInboxItems(
        workspace.id,
        {
          ownership: parsed.filters.ownership,
          types: parsed.filters.types,
          statuses: parsed.filters.statuses,
          repo_id: parsed.repo ?? undefined,
          play_key: parsed.play ?? undefined,
          cursor: parsed.cursor,
          limit: PAGE_LIMIT,
        },
        token,
      ),
      getMe(token).catch(() => null as ApiUser | null),
    ]);
    return {
      source: "live",
      workspace,
      me,
      list,
      filters: parsed.filters,
      cursor: parsed.cursor,
      repo: parsed.repo,
      play: parsed.play,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return { source: "mock", reason: "Session expired — sign in again" };
    }
    if (err instanceof ApiUnavailableError) {
      return { source: "mock", reason: "Backend unreachable" };
    }
    return {
      source: "mock",
      reason: err instanceof Error ? err.message : "Backend returned an error",
    };
  }
}

function meToShellUser(me: ApiUser | null) {
  if (!me) return null;
  const name = me.display_name?.trim() || me.email;
  const parts = name.split(/[\s@.]+/).filter(Boolean);
  const initials =
    parts.length >= 2
      ? (parts[0][0] + parts[1][0]).toUpperCase()
      : (parts[0]?.slice(0, 2) ?? "??").toUpperCase();
  return { name, email: me.email, initials };
}

function pickTypeCounts(
  raw: Record<string, number>,
): Partial<Record<InboxType, number>> {
  const out: Partial<Record<InboxType, number>> = {};
  for (const t of INBOX_TYPES) {
    if (typeof raw[t] === "number") out[t] = raw[t];
  }
  return out;
}

function pickStatusCounts(
  raw: Record<string, number>,
): Partial<Record<InboxStatus, number>> {
  const out: Partial<Record<InboxStatus, number>> = {};
  for (const s of INBOX_STATUSES) {
    if (typeof raw[s] === "number") out[s] = raw[s];
  }
  return out;
}

export default async function InboxPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = ((await (searchParams ?? Promise.resolve({}))) ?? {}) as Record<
    string,
    string | string[] | undefined
  >;
  const parsed = parseSearchParams(params);
  const data = await load(parsed);

  if (data.source === "mock") {
    return (
      <MockView
        reason={data.reason}
        filters={parsed.filters}
        errorCode={parsed.errorCode}
      />
    );
  }

  const { workspace, me, list, filters, cursor, repo, play } = data;
  const typeCounts = pickTypeCounts(list.counts_by_type);
  const statusCounts = pickStatusCounts(list.counts_by_status);
  const activeFilterCount = countActiveFilters(filters, { repo, play });

  return (
    <AppShell
      kicker="attention"
      title="Inbox"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      me={meToShellUser(me)}
      actions={
        <RefreshButton
          filters={filters}
          repo={repo}
          play={play}
          cursor={cursor}
        />
      }
    >
      <LiveBanner workspace={workspace.slug} />

      {parsed.errorCode && (
        <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(parsed.errorCode)}
        </div>
      )}

      <CountsStats
        list={list}
        typeCounts={typeCounts}
        activeFilterCount={activeFilterCount}
      />

      <Card className="mt-5">
        <CardHeader
          title="Filters"
          subtitle="Three axes — ownership, type, status. Default view shows your open queue."
        />
        <InboxFiltersControlled
          value={filters}
          counts={{ types: typeCounts, statuses: statusCounts }}
          repo={repo}
          play={play}
        />
        {(repo || play) && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-white/55">
            {repo && (
              <span className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5">
                repo:{" "}
                <code className="font-mono text-white/80">{repo}</code>
              </span>
            )}
            {play && (
              <span className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5">
                play:{" "}
                <code className="font-mono text-white/80">{play}</code>
              </span>
            )}
            <a
              href={buildInboxUrl(filters, {})}
              className="text-[11px] font-semibold text-aqua/80 hover:text-aqua"
            >
              clear scope
            </a>
          </div>
        )}
      </Card>

      <ItemsTable
        items={list.items}
        filters={filters}
        repo={repo}
        play={play}
        activeFilterCount={activeFilterCount}
      />

      <Pager
        nextCursor={list.next_cursor}
        currentCursor={cursor}
        filters={filters}
        repo={repo}
        play={play}
      />
    </AppShell>
  );
}

function CountsStats({
  list,
  typeCounts,
  activeFilterCount,
}: {
  list: InboxListResponse;
  typeCounts: Partial<Record<InboxType, number>>;
  activeFilterCount: number;
}) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <Card>
        <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
          Total in view
        </div>
        <div className="mt-1 font-display text-2xl font-bold text-white">
          {list.total.toString()}
        </div>
        <div className="mt-1 text-[10px] text-white/45">
          Server-side count for the current filter set.
        </div>
      </Card>

      <Card>
        <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
          By type
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {INBOX_TYPES.map((t) => (
            <span
              key={t}
              title={INBOX_TYPE_META[t].blurb}
              className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-semibold text-white/65"
            >
              {INBOX_TYPE_META[t].label.replace(/s$/, "")}
              <span className="rounded-full bg-white/10 px-1 text-[9px] font-bold text-white/85">
                {typeCounts[t] ?? 0}
              </span>
            </span>
          ))}
        </div>
      </Card>

      <Card>
        <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
          Filters active
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <div className="font-display text-2xl font-bold text-white">
            {activeFilterCount.toString()}
          </div>
          {activeFilterCount > 0 && (
            <a
              href="/inbox"
              className="text-[11px] font-semibold text-aqua/80 hover:text-aqua"
            >
              clear all →
            </a>
          )}
        </div>
        <div className="mt-1 text-[10px] text-white/45">
          {activeFilterCount === 0
            ? "Showing the default view (your open queue)."
            : "Tap clear all to drop scope + filters back to defaults."}
        </div>
      </Card>
    </div>
  );
}

function ItemsTable({
  items,
  filters,
  repo,
  play,
  activeFilterCount,
}: {
  items: InboxItem[];
  filters: InboxFilterState;
  repo: string | null;
  play: string | null;
  activeFilterCount: number;
}) {
  if (items.length === 0) {
    if (activeFilterCount > 0) {
      return (
        <div className="mt-5">
          <EmptyState
            title="No items match these filters"
            body="Try widening the ownership tab to All, clearing the type chips, or dropping the repo/play scope."
            action={
              <a
                href="/inbox"
                className="inline-flex items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
              >
                Clear all filters
              </a>
            }
          />
        </div>
      );
    }
    if (filters.ownership === "mine") {
      return (
        <div className="mt-5">
          <EmptyState
            title="Nothing on your plate"
            body="Either you're caught up or nobody's routing things your way. Switch to All to see the workspace firehose."
            action={
              <a
                href={buildInboxUrl(
                  { ...filters, ownership: "all" },
                  { repo, play },
                )}
                className="inline-flex items-center gap-1 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 hover:border-white/30 hover:bg-white/[0.08]"
              >
                Switch to All
              </a>
            }
          />
        </div>
      );
    }
    return (
      <div className="mt-5">
        <EmptyState
          title="Inbox empty"
          body="Either nothing has fired or the routing rules need attention — check Settings → Inbox routing."
          action={
            <a
              href="/settings/inbox-routing"
              className="inline-flex items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
            >
              Open Inbox routing
            </a>
          }
        />
      </div>
    );
  }

  return (
    <Card padded={false} className="mt-5 overflow-hidden">
      <CardHeader
        className="px-5 pt-5"
        title={`Items (${items.length})`}
        subtitle="Oldest-first ranking; resolved/dismissed rows fade out so the queue keeps reading top-down."
      />
      <ul className="space-y-2 px-3 pb-3">
        {items.map((item) => (
          <li key={item.id}>
            <InboxItemRow item={item} href={`/inbox/${item.id}`} />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Pager({
  nextCursor,
  currentCursor,
  filters,
  repo,
  play,
}: {
  nextCursor: string | null;
  currentCursor: string | null;
  filters: InboxFilterState;
  repo: string | null;
  play: string | null;
}) {
  if (!nextCursor && !currentCursor) return null;
  return (
    <div className="mt-5 flex items-center justify-between">
      {currentCursor ? (
        <a
          href={buildInboxUrl(filters, { repo, play })}
          className="text-[11px] font-semibold text-white/55 hover:text-white"
        >
          ← First page
        </a>
      ) : (
        <span />
      )}
      {nextCursor && (
        <a
          href={buildInboxUrl(filters, { repo, play, cursor: nextCursor })}
          className="rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
        >
          Load older →
        </a>
      )}
    </div>
  );
}

function RefreshButton({
  filters,
  repo,
  play,
  cursor,
}: {
  filters: InboxFilterState;
  repo: string | null;
  play: string | null;
  cursor: string | null;
}) {
  return (
    <form action="/inbox" method="GET" className="contents">
      {filters.ownership !== DEFAULT_INBOX_FILTERS.ownership && (
        <input type="hidden" name="ownership" value={filters.ownership} />
      )}
      {filters.types.map((t) => (
        <input key={t} type="hidden" name="type" value={t} />
      ))}
      {filters.statuses.map((s) => (
        <input key={s} type="hidden" name="status" value={s} />
      ))}
      {repo && <input type="hidden" name="repo" value={repo} />}
      {play && <input type="hidden" name="play" value={play} />}
      {cursor && <input type="hidden" name="cursor" value={cursor} />}
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
        title="Reload the current view"
      >
        ↻ Refresh
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// MockView — static preview deployment fallback (no Math.random / Date.now).
// ---------------------------------------------------------------------------

const MOCK_REFERENCE_DATE = new Date("2026-04-22T12:00:00Z");

function isoDaysAgo(days: number): string {
  const d = new Date(MOCK_REFERENCE_DATE);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString();
}

function mockItems(workspaceId: string): InboxItem[] {
  return [
    {
      id: "inbox_clarification_demo",
      workspace_id: workspaceId,
      repo_id: "repo_billing",
      type: "clarification",
      status: "new",
      title: "Confirm rotated key for stripe-prod",
      summary:
        "Pipeline blocked: which env should the rotated STRIPE_KEY land in?",
      intake_handle: "secops",
      intake_reason: "rule:secops",
      owner: {
        user_id: "u_mira",
        email: "mira@helio.dev",
        display_name: "Mira Tan",
      },
      play_key: "rotate-secrets",
      run_id: "run_001",
      created_at: isoDaysAgo(1),
      due_at: null,
      snoozed_until: null,
      resolved_at: null,
      resolution: null,
    },
    {
      id: "inbox_approval_demo",
      workspace_id: workspaceId,
      repo_id: "repo_api",
      type: "approval",
      status: "new",
      title: "Approve schema migration to v17",
      summary:
        "Add nullable `last_seen_at` column to users — non-blocking, but requires owner sign-off.",
      intake_handle: "release_manager",
      intake_reason: "rule:release_manager",
      owner: {
        user_id: "u_dk",
        email: "denis@helio.dev",
        display_name: "Denis K.",
      },
      play_key: "schema-migrate",
      run_id: "run_002",
      created_at: isoDaysAgo(3),
      due_at: null,
      snoozed_until: null,
      resolved_at: null,
      resolution: null,
    },
    {
      id: "inbox_failure_demo",
      workspace_id: workspaceId,
      repo_id: "repo_user_svc",
      type: "failure",
      status: "new",
      title: "scan-secrets failed on user-svc",
      summary:
        "3 consecutive failures on the nightly scan. Workflow log shows a 503 from GitHub Advanced Security.",
      intake_handle: "code_owner",
      intake_reason: "rule:code_owner",
      owner: null,
      play_key: "scan-secrets",
      run_id: "run_003",
      created_at: isoDaysAgo(8),
      due_at: null,
      snoozed_until: null,
      resolved_at: null,
      resolution: null,
    },
    {
      id: "inbox_improvement_demo",
      workspace_id: workspaceId,
      repo_id: "repo_api",
      type: "improvement",
      status: "snoozed",
      title: "Bundle bump available for api-backend preset",
      summary:
        "v18 ships richer FSM hints and an updated lint rule for inbox payloads.",
      intake_handle: "pr_author",
      intake_reason: "fallback:workspace_admin",
      owner: {
        user_id: "u_jordan",
        email: "jordan@helio.dev",
        display_name: "Jordan Lee",
      },
      play_key: "bundle-upgrade",
      run_id: null,
      created_at: isoDaysAgo(2),
      due_at: null,
      snoozed_until: isoDaysAgo(-1),
      resolved_at: null,
      resolution: null,
    },
    {
      id: "inbox_exception_demo",
      workspace_id: workspaceId,
      repo_id: "repo_billing",
      type: "exception",
      status: "new",
      title: "Waiver requested: deploy outside change window",
      summary:
        "Charlie wants to push the billing hotfix Saturday 23:00; needs an explicit override.",
      intake_handle: "secops",
      intake_reason: "rule:secops",
      owner: {
        user_id: "u_asha",
        email: "asha@helio.dev",
        display_name: "Asha Verma",
      },
      play_key: "deploy-billing",
      run_id: "run_005",
      created_at: isoDaysAgo(5),
      due_at: null,
      snoozed_until: null,
      resolved_at: null,
      resolution: null,
    },
  ];
}

function MockView({
  reason,
  filters,
  errorCode,
}: {
  reason: string;
  filters: InboxFilterState;
  errorCode: string | null;
}) {
  const ws = mockWorkspaces[0];
  const items = mockItems(ws.id);
  const typeCounts: Partial<Record<InboxType, number>> = {};
  for (const item of items) {
    typeCounts[item.type] = (typeCounts[item.type] ?? 0) + 1;
  }
  const fakeList: InboxListResponse = {
    items,
    total: items.length,
    counts_by_type: typeCounts as Record<string, number>,
    counts_by_status: { new: 4, snoozed: 1, resolved: 0, dismissed: 0 },
    next_cursor: null,
  };
  return (
    <AppShell kicker="attention" title="Inbox">
      <MockBanner reason={reason} />
      {errorCode && (
        <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(errorCode)}
        </div>
      )}

      <CountsStats
        list={fakeList}
        typeCounts={typeCounts}
        activeFilterCount={countActiveFilters(filters, {})}
      />

      <Card padded={false} className="mt-5 overflow-hidden">
        <CardHeader
          className="px-5 pt-5"
          title="Items (sample)"
          subtitle="Sign in to view the real queue. The five rows below preview the type distribution + stale-age badge."
        />
        <ul className="space-y-2 px-3 pb-3">
          {items.map((item) => (
            <li key={item.id}>
              <InboxItemRow
                item={item}
                referenceDate={MOCK_REFERENCE_DATE}
              />
            </li>
          ))}
        </ul>
      </Card>
    </AppShell>
  );
}
