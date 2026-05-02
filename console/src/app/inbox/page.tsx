/**
 * Unified Inbox list page (RFC-0010 ticket P2-12).
 *
 * Server component that wires the just-shipped foundation pieces
 * together: `listInboxItems` from the API client, `InboxItemRow` for
 * each row, `InboxFilters` (via the `InboxFiltersControlled` client
 * shell) for ownership + type chips. State lives in the URL so
 * admins can deep-link / share / refresh without losing context;
 * `searchParams` are parsed defensively through the type guards in
 * `inbox-types.ts` so a hostile URL can't smuggle bad enum values
 * into the API query.
 *
 * Three outcomes:
 *   - **live**: backend reachable + session valid → real list + counts
 *     from `/v1/workspaces/{ws}/inbox`.
 *   - **redirect**: missing session → `/login`; no workspaces → `/onboarding`.
 *   - **down**: API misconfigured or unreachable → `<ApiUnavailable>`.
 *
 * The detail page (P2-13) and routing settings (P2-16) are sibling
 * tickets — this file links to `/inbox/{id}` but does not own that
 * route.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import { InboxFiltersControlled } from "@/components/inbox/inbox-filters-controlled";
import { InboxItemRow } from "@/components/inbox/inbox-item-row";
import { buildInboxUrl, countActiveFilters } from "@/components/inbox/inbox-url";
import { Card, CardHeader, EmptyState } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getMe,
  isApiConfigured,
  listInboxItems,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import type { ApiUser, ApiWorkspace } from "@/lib/api/types";
import {
  DEFAULT_INBOX_FILTERS,
  INBOX_LIST_DEFAULT_STATUSES,
  INBOX_TYPES,
  isInboxType,
  type InboxFilterState,
  type InboxItem,
  type InboxListResponse,
  type InboxType,
} from "@/lib/inbox-types";
import {
  pickWorkspace,
  toAppShellWorkspaces,
  withWorkspaceQuery,
} from "@/lib/workspace-scope";

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
      allWorkspaces: ApiWorkspace[];
      me: ApiUser | null;
      list: InboxListResponse;
      filters: InboxFilterState;
      cursor: string | null;
      repo: string | null;
      play: string | null;
    }
  | { source: "redirect"; href: string }
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
  if (!isApiConfigured()) {
    return { source: "down", reason: "SHIP_API_URL is not set on this deployment." };
  }
  const token = await getSessionToken();
  if (!token) {
    return {
      source: "redirect",
      href: "/login?next=%2Finbox&reason=session_expired",
    };
  }
  let workspace: ApiWorkspace;
  let allWorkspaces: ApiWorkspace[];
  try {
    const ws = await listWorkspaces(token);
    if (ws.length === 0) {
      return { source: "redirect", href: "/onboarding?step=github" };
    }
    allWorkspaces = ws;
    const resolved = await getResolvedWorkspaceId(searchParams, ws);
    workspace = pickWorkspace(ws, resolved);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return {
        source: "redirect",
        href: "/login?next=%2Finbox&reason=session_expired",
      };
    }
    return {
      source: "down",
      reason: err instanceof Error ? err.message : "Could not load workspaces.",
    };
  }

  try {
    const [list, me] = await Promise.all([
      listInboxItems(
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
      ),
      getMe(token).catch(() => null as ApiUser | null),
    ]);
    return {
      source: "live",
      workspace,
      allWorkspaces,
      me,
      list,
      filters: parsed.filters,
      cursor: parsed.cursor,
      repo: parsed.repo,
      play: parsed.play,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return {
        source: "redirect",
        href: "/login?next=%2Finbox&reason=session_expired",
      };
    }
    return {
      source: "down",
      reason: err instanceof Error ? err.message : "Could not load inbox items.",
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

function sumInboxTypeCounts(
  c: Partial<Record<InboxType, number>>,
): number {
  let n = 0;
  for (const t of INBOX_TYPES) n += c[t] ?? 0;
  return n;
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

  if (data.source === "redirect") {
    redirect(data.href);
  }
  if (data.source === "down") {
    return (
      <AppShell kicker="attention" title="Inbox">
        {parsed.errorCode && (
          <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
            {errorMessage(parsed.errorCode)}
          </div>
        )}
        <ApiUnavailable scope="inbox" details={data.reason} />
      </AppShell>
    );
  }

  const { workspace, allWorkspaces, me, list, filters, cursor, repo, play } =
    data;
  const typeCounts = pickTypeCounts(list.counts_by_type);
  const allTypesCount = sumInboxTypeCounts(typeCounts);
  const activeFilterCount = countActiveFilters(filters, { repo, play });
  const multiWs = allWorkspaces.length > 1;
  const inboxWs = multiWs ? workspace.id : undefined;

  return (
    <AppShell
      kicker="attention"
      title="Inbox"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      allWorkspaces={toAppShellWorkspaces(allWorkspaces)}
      me={meToShellUser(me)}
      actions={
        <RefreshButton
          filters={filters}
          repo={repo}
          play={play}
          cursor={cursor}
          workspaceScope={inboxWs}
        />
      }
    >
      {parsed.errorCode && (
        <div className="mb-5 rounded-xl border border-coral/30 bg-coral/[0.06] px-3 py-2 text-xs text-coral/95">
          {errorMessage(parsed.errorCode)}
        </div>
      )}

      {/* Compact filter strip — replaces the old "Filters" card.
        * Single bordered row: ownership tabs + type chips + scope pills.
        * Avoids the chrome (CardHeader + subtitle) that ate a quarter of
        * the viewport before. */}
      <section className="rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
            Filters
          </p>
          <InboxFiltersControlled
            value={filters}
            counts={{ types: typeCounts, allTypes: allTypesCount }}
            repo={repo}
            play={play}
            workspaceScope={inboxWs}
          />
          {(repo || play) && (
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-white/55">
              {repo && (
                <span className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5">
                  repo: <code className="font-mono text-white/80">{repo}</code>
                </span>
              )}
              {play && (
                <span className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5">
                  play: <code className="font-mono text-white/80">{play}</code>
                </span>
              )}
              <a
                href={buildInboxUrl(filters, { workspaceScope: inboxWs })}
                className="text-[11px] font-semibold text-sun hover:text-white"
              >
                clear scope
              </a>
            </div>
          )}
        </div>
      </section>

      {/* Counts kicker for the items list — was buried inside a CardHeader. */}
      <div className="mt-5 flex flex-wrap items-baseline gap-3">
        <h2 className="font-display text-lg font-bold text-white">
          {list.items.length === 0 ? "No items" : `${list.items.length} item${list.items.length === 1 ? "" : "s"}`}
        </h2>
        <span className="text-[11px] text-white/45">
          Oldest-first; resolved / dismissed rows fade out so the queue keeps reading top-down.
        </span>
      </div>

      <div className="mt-3">
        <ItemsTable
          items={list.items}
          filters={filters}
          repo={repo}
          play={play}
          activeFilterCount={activeFilterCount}
          workspaceScope={inboxWs}
        />
      </div>

      <Pager
        nextCursor={list.next_cursor}
        currentCursor={cursor}
        filters={filters}
        repo={repo}
        play={play}
        workspaceScope={inboxWs}
      />
    </AppShell>
  );
}

function ItemsTable({
  items,
  filters,
  repo,
  play,
  activeFilterCount,
  workspaceScope,
}: {
  items: InboxItem[];
  filters: InboxFilterState;
  repo: string | null;
  play: string | null;
  activeFilterCount: number;
  workspaceScope?: string;
}) {
  if (items.length === 0) {
    if (activeFilterCount > 0) {
      return (
        <div className="mt-5">
          <EmptyState
            title="No items match these filters"
            body="Try widening the ownership tab to All, clearing the type chips, or dropping the repo/play scope."
            action={
              <Link
                href={buildInboxUrl(DEFAULT_INBOX_FILTERS, {
                  workspaceScope,
                })}
                className="inline-flex items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
              >
                Clear all filters
              </Link>
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
                  { repo, play, workspaceScope },
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
          body="Either nothing has fired or your team coverage needs attention — check who can answer under Settings → Members."
            action={
            <a
              href={withWorkspaceQuery(
                "/settings?tab=members",
                workspaceScope ?? "",
                Boolean(workspaceScope),
              )}
              className="inline-flex items-center gap-1 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
            >
              Open Members
            </a>
          }
        />
      </div>
    );
  }

  // Bare ul — the surrounding heading + helper text live in the page now,
  // so the Card+CardHeader wrapper that used to live here would just
  // duplicate the chrome.
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li key={item.id}>
          <InboxItemRow
            item={item}
            href={
              workspaceScope
                ? `/inbox/${item.id}?ws=${encodeURIComponent(workspaceScope)}`
                : `/inbox/${item.id}`
            }
          />
        </li>
      ))}
    </ul>
  );
}

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
    <div className="mt-5 flex items-center justify-between">
      {currentCursor ? (
        <a
          href={buildInboxUrl(filters, { repo, play, workspaceScope })}
          className="text-[11px] font-semibold text-white/55 hover:text-white"
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
        className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
        title="Reload the current view"
      >
        ↻ Refresh
      </button>
    </form>
  );
}

