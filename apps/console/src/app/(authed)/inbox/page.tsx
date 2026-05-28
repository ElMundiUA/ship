/**
 * Inbox — mailbox layout.
 *
 * Outlook-style split pane: list on the left, preview on the right.
 * URL drives selection (``?selected=<id>``); the preview pane
 * server-fetches that item's full detail and renders the body plus a
 * type-aware footer (acknowledge / quick-reply / decision buttons).
 *
 * The earlier tier-sectioned editorial layout was simplified per
 * operator feedback: less chrome, fewer cards, "I just want to read
 * letters and click answer." The standalone ``/inbox/[id]`` detail
 * route was retired — this preview is the single surface, rendering
 * both resolution systems: structured ``action_items`` via
 * ``InboxActionPanel``, and the type-driven accept/dismiss/answer
 * dispositions via ``InboxDispositionPanel``.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { InboxMailboxListClient } from "@/components/inbox/inbox-mailbox-list-client";
import { InboxActionPanel } from "@/components/inbox/inbox-action-panel";
import { InboxDispositionPanel } from "@/components/inbox/inbox-disposition-panel";
import { MailboxFooter } from "@/components/inbox/mailbox-footer";
import { buildInboxUrl, parseInboxSearchParams } from "@/components/inbox/inbox-url";
import { MarkdownBlock } from "@/components/markdown-block";
import { EmptyState } from "@/components/ui";
import { formatInboxHeadline } from "@/lib/inbox-copy";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import {
  ApiHttpError,
  getInboxItem,
  listInboxItems,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  INBOX_ACTIONABLE_CATEGORIES,
  INBOX_LIST_DEFAULT_STATUSES,
  INBOX_TYPE_META,
  inboxRowKicker,
  isInboxType,
  parseChecklistActionItems,
  type InboxItemDetail,
  type InboxListResponse,
  type InboxType,
} from "@/lib/inbox-types";
import { pickWorkspace } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

const PAGE_LIMIT = 50;

type Ownership = "mine" | "unassigned" | "all";

type Mode =
  | {
      source: "live";
      workspaceId: string;
      multiWs: boolean;
      list: InboxListResponse;
      detail: InboxItemDetail | null;
      detailError: string | null;
      ownership: Ownership;
      typeFilters: InboxType[];
      selectedId: string | null;
    }
  | { source: "down"; reason: string };

function errorMessage(code: string): string {
  switch (code) {
    case "forbidden":
      return "You don't have permission to act on that item.";
    case "not_found":
      return "That item is gone — it may have been resolved in another tab.";
    case "validation_failed":
      return "Reply was empty — write something before sending.";
    case "state_invalid":
      return "Item state changed since you opened it. Refresh and try again.";
    case "api_unavailable":
      return "Backend is unreachable. Try again in a moment.";
    default:
      return `Couldn't apply the change (${code}).`;
  }
}

function parseSearchParams(
  raw: Record<string, string | string[] | undefined>,
) {
  return parseInboxSearchParams(raw);
}

async function load(
  parsed: ReturnType<typeof parseInboxSearchParams>,
  searchParams: Record<string, string | string[] | undefined>,
): Promise<Mode> {
  const token = await getCachedSessionToken();
  if (!token) {
    redirect("/login?next=%2Finbox&reason=session_expired");
  }

  const ws = await getCachedWorkspaces();
  const resolved = await getResolvedWorkspaceId(searchParams, ws);
  if (ws.length === 0) {
    redirect("/onboarding?step=github");
  }
  const workspace = pickWorkspace(ws, resolved);

  let list: InboxListResponse;
  try {
    list = await listInboxItems(
      workspace.id,
      {
        ownership: parsed.filters.ownership,
        types:
          parsed.filters.types.length > 0 ? parsed.filters.types : undefined,
        categories: INBOX_ACTIONABLE_CATEGORIES,
        statuses: INBOX_LIST_DEFAULT_STATUSES,
        sort: "priority_desc_created_asc",
        limit: PAGE_LIMIT,
      },
      token,
    );
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Finbox&reason=session_expired");
    }
    return {
      source: "down",
      reason: err instanceof Error ? err.message : "Could not load inbox items.",
    };
  }

  // If nothing is explicitly selected but the list is non-empty, auto-
  // select the first row so the right pane is never blank when there's
  // a letter ready to read. The operator can always click another row.
  const effectiveSelected =
    parsed.selectedId ?? (list.items[0]?.id ?? null);

  let detail: InboxItemDetail | null = null;
  let detailError: string | null = null;
  if (effectiveSelected) {
    try {
      detail = await getInboxItem(workspace.id, effectiveSelected, token);
    } catch (err) {
      detailError =
        err instanceof ApiHttpError && err.status === 404
          ? "not_found"
          : "load_failed";
    }
  }

  return {
    source: "live",
    workspaceId: workspace.id,
    multiWs: ws.length > 1,
    list,
    detail,
    detailError,
    ownership: parsed.filters.ownership,
    typeFilters: parsed.filters.types,
    selectedId: effectiveSelected,
  };
}

export default async function InboxMailboxPage({
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
            <p className="mb-5 text-xs text-coral/95">
              {errorMessage(parsed.errorCode)}
            </p>
          )}
          <ApiUnavailable scope="inbox" details={data.reason} />
        </PageBody>
      </>
    );
  }

  const { workspaceId, multiWs, list, detail, detailError, ownership, typeFilters, selectedId } =
    data;
  const wsScope = multiWs ? workspaceId : undefined;
  const inboxFilters = { ownership, types: typeFilters };

  return (
    <>
      <PageHeader kicker="attention" title="Inbox" />
      <PageBody>
        {parsed.errorCode && (
          <p className="mb-4 text-xs text-coral/95">
            {errorMessage(parsed.errorCode)}
          </p>
        )}

        <div className="grid gap-4 lg:grid-cols-[26rem_minmax(0,1fr)]">
          <InboxMailboxListClient
            items={list.items}
            ownership={ownership}
            selectedId={selectedId}
            workspaceScope={wsScope}
            typeFilters={typeFilters}
            ownershipTabs={
              <OwnershipTabs
                current={ownership}
                filters={inboxFilters}
                workspaceScope={wsScope}
              />
            }
          />

          <MailboxPreview
            detail={detail}
            detailError={detailError}
            workspaceId={workspaceId}
            workspaceScope={wsScope}
            filters={inboxFilters}
            selectedId={selectedId}
            empty={list.items.length === 0}
          />
        </div>
      </PageBody>
    </>
  );
}


function buildOwnershipHref({
  ownership,
  filters,
  workspaceScope,
}: {
  ownership: Ownership;
  filters: { ownership: Ownership; types: InboxType[] };
  workspaceScope?: string;
}): string {
  return buildInboxUrl(
    { ownership, types: filters.types },
    { workspaceScope },
  );
}

function OwnershipTabs({
  current,
  filters,
  workspaceScope,
}: {
  current: Ownership;
  filters: { ownership: Ownership; types: InboxType[] };
  workspaceScope?: string;
}) {
  const tabs: { key: Ownership; label: string }[] = [
    { key: "mine", label: "Mine" },
    { key: "unassigned", label: "Unassigned" },
    { key: "all", label: "All open" },
  ];
  return (
    <div className="flex items-center gap-3 border-b border-white/[0.06] px-4 py-3 text-xs">
      {tabs.map((tab, idx) => {
        const active = current === tab.key;
        return (
          <span key={tab.key} className="flex items-baseline">
            <Link
              href={buildOwnershipHref({
                ownership: tab.key,
                filters,
                workspaceScope,
              })}
              aria-current={active ? "page" : undefined}
              className={cn(
                "transition",
                active
                  ? "font-semibold text-white"
                  : "text-white/50 hover:text-white",
              )}
            >
              {tab.label}
            </Link>
            {idx < tabs.length - 1 && (
              <span className="ml-3 text-white/15">·</span>
            )}
          </span>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right pane: the preview
// ---------------------------------------------------------------------------

function buildMailboxReturnTo({
  selectedId,
  filters,
  workspaceScope,
}: {
  selectedId: string;
  filters: { ownership: Ownership; types: InboxType[] };
  workspaceScope?: string;
}): string {
  return buildInboxUrl(filters, { selected: selectedId, workspaceScope });
}

function MailboxPreview({
  detail,
  detailError,
  workspaceId,
  workspaceScope,
  filters,
  empty,
}: {
  detail: InboxItemDetail | null;
  detailError: string | null;
  workspaceId: string;
  workspaceScope?: string;
  filters: { ownership: Ownership; types: InboxType[] };
  selectedId: string | null;
  empty: boolean;
}) {
  if (empty) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.015] p-6">
        <EmptyState
          title="Inbox empty"
          body="Nothing waiting on you — agents working."
        />
      </div>
    );
  }
  if (detailError === "not_found") {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.015] px-6 text-center text-sm text-white/55">
        That item is gone — it was resolved in another tab.
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.015] px-6 text-center text-sm text-white/55">
        Pick a letter on the left to read it.
      </div>
    );
  }

  const meta = isInboxType(detail.type)
    ? INBOX_TYPE_META[detail.type]
    : null;
  const kicker = inboxRowKicker(detail.type);
  const body = readBody(detail);
  const isClosed = detail.status === "resolved" || detail.status === "dismissed";
  const previewHeadline = formatInboxHeadline(detail);
  const checklistItems = parseChecklistActionItems(detail.payload);
  const rawActionItems = (detail.payload as Record<string, unknown> | undefined)?.[
    "action_items"
  ];
  const hasActionItems =
    Array.isArray(rawActionItems) && rawActionItems.length > 0;
  const returnTo = buildMailboxReturnTo({
    selectedId: detail.id,
    filters,
    workspaceScope,
  });

  return (
    // Self-sized: header + body + footer stack tightly. Short letters
    // (a 3-line refire-cap blocker) no longer leave a 600px void
    // between the body and the action buttons. Long bodies cap at
    // ~70vh and scroll internally so the footer stays in the
    // viewport without forcing the operator to scroll past empty
    // space first.
    <div
      className="flex flex-col rounded-xl border border-white/[0.08] bg-white/[0.015]"
      data-mailbox-preview
    >
      <header className="border-b border-white/[0.06] px-6 py-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] uppercase tracking-[0.18em] text-white/45">
          <span>{meta?.label ?? kicker.label}</span>
          <span className="text-white/15">·</span>
          <span
            className="text-white/55 normal-case tracking-normal"
            title={new Date(detail.created_at).toLocaleString()}
          >
            received {relativeTime(detail.created_at)}
          </span>
          {isClosed && (
            <>
              <span className="text-white/15">·</span>
              <span className="text-white/55">{detail.status}</span>
              {detail.resolution && (
                <>
                  <span className="text-white/15">·</span>
                  <span className="text-white/55">{detail.resolution}</span>
                </>
              )}
            </>
          )}
        </div>
        <h1 className="mt-1 text-lg font-semibold text-white">{previewHeadline}</h1>
        {detail.owner && (
          <p className="mt-1 text-xs text-white/55">
            Owner:{" "}
            <span className="text-white/75">
              {detail.owner.display_name?.trim() || detail.owner.email}
            </span>
          </p>
        )}
      </header>

      <div className="max-h-[70vh] overflow-y-auto px-6 py-5">
        {body ? (
          <MarkdownBlock collapseFromHeadings={["Technical details", "Details"]}>
            {body}
          </MarkdownBlock>
        ) : (
          <p className="text-sm italic text-white/40">No body.</p>
        )}
      </div>

      <footer className="space-y-3 border-t border-white/[0.06] px-6 py-4">
        {isClosed ? (
          <p className="text-xs italic text-white/40">
            This letter is {detail.status}.
          </p>
        ) : checklistItems.length > 0 ? (
          <MailboxFooter
            detail={detail}
            workspaceId={workspaceId}
            returnTo={returnTo}
          />
        ) : hasActionItems ? (
          <InboxActionPanel workspaceId={workspaceId} item={detail} />
        ) : (
          <InboxDispositionPanel
            workspaceId={workspaceId}
            itemId={detail.id}
            itemType={
              isInboxType(detail.type) ? detail.type : "clarification"
            }
          />
        )}
      </footer>
    </div>
  );
}

function readBody(detail: InboxItemDetail): string {
  // Agent-filed reports stash the markdown body under
  // ``payload.body``; legacy items keep the human-readable text in
  // ``summary``. Fall through gracefully.
  const payloadBody = detail.payload?.["body"];
  if (typeof payloadBody === "string" && payloadBody.trim().length > 0) {
    return payloadBody;
  }
  if (detail.summary && detail.summary.trim().length > 0) return detail.summary;
  return "";
}
