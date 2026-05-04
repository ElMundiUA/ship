/**
 * Unified Inbox list page.
 *
 * Editorial layout: a typographic stats ribbon (mine · unassigned ·
 * all open) replaces the segmented control; type chips sit on a
 * single line with no bordered container; items group into three
 * triage tiers (Tier 1 — needs you, Tier 2 — autonomy escapes,
 * Tier 3 — later) with section kickers and a hairline rule.
 *
 * Per design rec we drop the bordered Filters card, the
 * "X items" header, and the per-row card chrome. Per-row colour
 * lives on a left-edge spine inside ``InboxItemRow``.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { InboxFiltersControlled } from "@/components/inbox/inbox-filters-controlled";
import { InboxItemRow } from "@/components/inbox/inbox-item-row";
import { buildInboxUrl, countActiveFilters } from "@/components/inbox/inbox-url";
import { EmptyState } from "@/components/ui";
import { cn } from "@/lib/cn";
import {
  ApiHttpError,
  listInboxItems,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  DEFAULT_INBOX_FILTERS,
  INBOX_LIST_DEFAULT_STATUSES,
  INBOX_TIER_LABEL,
  INBOX_TYPES,
  inboxTier,
  isInboxType,
  type InboxFilterState,
  type InboxItem,
  type InboxListResponse,
  type InboxTier,
  type InboxType,
} from "@/lib/inbox-types";
import {
  pickWorkspace,
  withWorkspaceQuery,
} from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

const PAGE_LIMIT = 25;
const TIERS: InboxTier[] = [1, 2, 3];

const TIER_KICKER_TONE: Record<InboxTier, string> = {
  1: "text-sun/80",
  2: "text-coral/80",
  3: "text-white/40",
};

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
      workspaceId: string;
      multiWs: boolean;
      list: InboxListResponse;
      filters: InboxFilterState;
      cursor: string | null;
      repo: string | null;
      play: string | null;
    }
  | { source: "down"; reason: string };

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

  const cursorRaw = typeof raw.cursor === "string" ? raw.cursor : null;
  const cursor = cursorRaw && cursorRaw.length > 0 ? cursorRaw : null;
  const repoRaw = typeof raw.repo === "string" ? raw.repo : null;
  const repo = repoRaw && repoRaw.length > 0 ? repoRaw : null;
  const playRaw = typeof raw.play === "string" ? raw.play : null;
  const play = playRaw && playRaw.length > 0 ? playRaw : null;
  const errorCode = typeof raw.error === "string" ? raw.error : null;

  return {
    filters: { ownership, types },
    cursor,
    repo,
    play,
    errorCode,
  };
}

async function load(
  parsed: ParsedParams,
  searchParams: Record<string, string | string[] | undefined>,
): Promise<Mode> {
  const token = await getCachedSessionToken();
  if (!token) {
    redirect("/login?next=%2Finbox&reason=session_expired");
  }

  const ws = await getCachedWorkspaces();
  const resolved = await getResolvedWorkspaceId(searchParams, ws);
  const workspace = pickWorkspace(ws, resolved);

  try {
    const list = await listInboxItems(
      workspace.id,
      {
        ownership: parsed.filters.ownership,
        types: parsed.filters.types,
        statuses: INBOX_LIST_DEFAULT_STATUSES,
        repo_id: parsed.repo ?? undefined,
        play_key: parsed.play ?? undefined,
        cursor: parsed.cursor,
        limit: PAGE_LIMIT,
      },
      token,
    );
    return {
      source: "live",
      workspaceId: workspace.id,
      multiWs: ws.length > 1,
      list,
      filters: parsed.filters,
      cursor: parsed.cursor,
      repo: parsed.repo,
      play: parsed.play,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Finbox&reason=session_expired");
    }
    return {
      source: "down",
      reason: err instanceof Error ? err.message : "Could not load inbox items.",
    };
  }
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

function sumInboxTypeCounts(
  c: Partial<Record<InboxType, number>>,
): number {
  let n = 0;
  for (const t of INBOX_TYPES) n += c[t] ?? 0;
  return n;
}

function partitionByTier(items: InboxItem[]): Record<InboxTier, InboxItem[]> {
  const out: Record<InboxTier, InboxItem[]> = { 1: [], 2: [], 3: [] };
  for (const item of items) {
    out[inboxTier(item.type)].push(item);
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
  const data = await load(parsed, params);

  if (data.source === "down") {
    return (
      <>
        <PageHeader kicker="attention" title="Inbox" />
        <PageBody>
          {parsed.errorCode && (
            <div className="mb-5 text-xs text-coral/95">
              {errorMessage(parsed.errorCode)}
            </div>
          )}
          <ApiUnavailable scope="inbox" details={data.reason} />
        </PageBody>
      </>
    );
  }

  const { workspaceId, multiWs, list, filters, cursor, repo, play } = data;
  const typeCounts = pickTypeCounts(list.counts_by_type);
  const allTypesCount = sumInboxTypeCounts(typeCounts);
  const activeFilterCount = countActiveFilters(filters, { repo, play });
  const inboxWs = multiWs ? workspaceId : undefined;
  const tiers = partitionByTier(list.items);

  return (
    <>
      <PageHeader
        kicker="attention"
        title="Inbox"
        actions={
          <RefreshButton
            filters={filters}
            repo={repo}
            play={play}
            cursor={cursor}
            workspaceScope={inboxWs}
          />
        }
      />
      <PageBody>
        <div className="mx-auto max-w-5xl space-y-8">
          {parsed.errorCode && (
            <p className="text-xs text-coral/95">
              {errorMessage(parsed.errorCode)}
            </p>
          )}

          <OwnershipRibbon
            current={filters.ownership}
            filters={filters}
            repo={repo}
            play={play}
            workspaceScope={inboxWs}
          />

          <InboxFiltersControlled
            value={filters}
            counts={{ types: typeCounts, allTypes: allTypesCount }}
            repo={repo}
            play={play}
            workspaceScope={inboxWs}
          />

          {(repo || play) && (
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/55">
              {repo && (
                <span>
                  scope:{" "}
                  <code className="font-mono text-white/80">{repo}</code>
                </span>
              )}
              {play && (
                <span>
                  play:{" "}
                  <code className="font-mono text-white/80">{play}</code>
                </span>
              )}
              <a
                href={buildInboxUrl(filters, { workspaceScope: inboxWs })}
                className="font-semibold text-sun hover:text-white"
              >
                clear scope
              </a>
            </div>
          )}

          {list.items.length === 0 ? (
            <InboxEmpty
              filters={filters}
              activeFilterCount={activeFilterCount}
              repo={repo}
              play={play}
              workspaceScope={inboxWs}
            />
          ) : (
            <div className="space-y-10">
              {TIERS.map((tier) => {
                const items = tiers[tier];
                if (items.length === 0) return null;
                return (
                  <TierSection
                    key={tier}
                    tier={tier}
                    items={items}
                    workspaceScope={inboxWs}
                  />
                );
              })}
            </div>
          )}

          <Pager
            nextCursor={list.next_cursor}
            currentCursor={cursor}
            filters={filters}
            repo={repo}
            play={play}
            workspaceScope={inboxWs}
          />
        </div>
      </PageBody>
    </>
  );
}


// ---------------------------------------------------------------------------
// Ownership stats ribbon — typographic
// ---------------------------------------------------------------------------


function OwnershipRibbon({
  current,
  filters,
  repo,
  play,
  workspaceScope,
}: {
  current: InboxFilterState["ownership"];
  filters: InboxFilterState;
  repo: string | null;
  play: string | null;
  workspaceScope?: string;
}) {
  const lenses: { key: InboxFilterState["ownership"]; label: string }[] = [
    { key: "mine", label: "Mine" },
    { key: "unassigned", label: "Unassigned" },
    { key: "all", label: "All open" },
  ];
  return (
    <nav
      aria-label="Inbox ownership"
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm"
    >
      {lenses.map((lens, idx) => {
        const active = current === lens.key;
        const href = buildInboxUrl(
          { ...filters, ownership: lens.key },
          { repo, play, workspaceScope },
        );
        return (
          <span key={lens.key} className="flex items-baseline">
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
              {lens.label}
            </Link>
            {idx < lenses.length - 1 && (
              <span className="ml-3 text-white/15">·</span>
            )}
          </span>
        );
      })}
    </nav>
  );
}


// ---------------------------------------------------------------------------
// Tier section — kicker + divide-y rows
// ---------------------------------------------------------------------------


function TierSection({
  tier,
  items,
  workspaceScope,
}: {
  tier: InboxTier;
  items: InboxItem[];
  workspaceScope?: string;
}) {
  return (
    <section>
      <header className="mb-3 flex items-center gap-3">
        <h2
          className={cn(
            "text-[11px] font-bold uppercase tracking-[0.22em]",
            TIER_KICKER_TONE[tier],
          )}
        >
          {INBOX_TIER_LABEL[tier]}
        </h2>
        <div className="h-px flex-1 bg-white/[0.06]" />
        <span className="font-mono text-[11px] text-white/35">
          {items.length}
        </span>
      </header>
      <ul className="divide-y divide-white/[0.06]">
        {items.map((item) => (
          <li key={item.id}>
            <InboxItemRow
              item={item}
              workspaceId={workspaceScope}
              href={
                workspaceScope
                  ? `/inbox/${item.id}?ws=${encodeURIComponent(workspaceScope)}`
                  : `/inbox/${item.id}`
              }
            />
          </li>
        ))}
      </ul>
    </section>
  );
}


// ---------------------------------------------------------------------------
// Empty states
// ---------------------------------------------------------------------------


function InboxEmpty({
  filters,
  activeFilterCount,
  repo,
  play,
  workspaceScope,
}: {
  filters: InboxFilterState;
  activeFilterCount: number;
  repo: string | null;
  play: string | null;
  workspaceScope?: string;
}) {
  if (activeFilterCount > 0) {
    return (
      <EmptyState
        title="No items match these filters"
        body="Try widening the ownership lens to All, clearing the type chips, or dropping the repo/play scope."
        action={
          <Link
            href={buildInboxUrl(DEFAULT_INBOX_FILTERS, { workspaceScope })}
            className="text-xs font-semibold text-aqua hover:text-white"
          >
            Clear all filters
          </Link>
        }
      />
    );
  }
  if (filters.ownership === "mine") {
    return (
      <EmptyState
        title="Nothing on your plate"
        body="Either you're caught up or nobody's routing things your way. Switch to All open to see the workspace firehose."
        action={
          <a
            href={buildInboxUrl(
              { ...filters, ownership: "all" },
              { repo, play, workspaceScope },
            )}
            className="text-xs font-semibold text-white/85 hover:text-white"
          >
            Switch to All open
          </a>
        }
      />
    );
  }
  return (
    <EmptyState
      title="Inbox empty"
      body="Either nothing has fired or your team coverage needs attention — check who can answer under Settings → Members."
      action={
        <a
          href={withWorkspaceQuery(
            "/settings?tab=members",
            workspaceScope ?? "",
            Boolean(workspaceScope),
          )}
          className="text-xs font-semibold text-aqua hover:text-white"
        >
          Open Members
        </a>
      }
    />
  );
}


// ---------------------------------------------------------------------------
// Pager + Refresh
// ---------------------------------------------------------------------------


function Pager({
  nextCursor,
  currentCursor,
  filters,
  repo,
  play,
  workspaceScope,
}: {
  nextCursor: string | null;
  currentCursor: string | null;
  filters: InboxFilterState;
  repo: string | null;
  play: string | null;
  workspaceScope?: string;
}) {
  if (!nextCursor && !currentCursor) return null;
  return (
    <div className="flex items-center justify-between text-[11px]">
      {currentCursor ? (
        <a
          href={buildInboxUrl(filters, { repo, play, workspaceScope })}
          className="font-semibold text-white/55 hover:text-white"
        >
          ← First page
        </a>
      ) : (
        <span />
      )}
      {nextCursor && (
        <a
          href={buildInboxUrl(filters, {
            repo,
            play,
            cursor: nextCursor,
            workspaceScope,
          })}
          className="font-semibold text-white/55 hover:text-white"
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
  workspaceScope,
}: {
  filters: InboxFilterState;
  repo: string | null;
  play: string | null;
  cursor: string | null;
  workspaceScope?: string;
}) {
  return (
    <form action="/inbox" method="GET" className="contents">
      {filters.ownership !== DEFAULT_INBOX_FILTERS.ownership && (
        <input type="hidden" name="ownership" value={filters.ownership} />
      )}
      {filters.types.map((t) => (
        <input key={t} type="hidden" name="type" value={t} />
      ))}
      {repo && <input type="hidden" name="repo" value={repo} />}
      {play && <input type="hidden" name="play" value={play} />}
      {cursor && <input type="hidden" name="cursor" value={cursor} />}
      {workspaceScope && (
        <input type="hidden" name="ws" value={workspaceScope} />
      )}
      <button
        type="submit"
        className="text-xs font-semibold text-white/55 transition hover:text-white"
        title="Reload the current view"
      >
        ↻ Refresh
      </button>
    </form>
  );
}
