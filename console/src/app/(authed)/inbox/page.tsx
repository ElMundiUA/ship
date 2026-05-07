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
 * letters and click answer." Deep-link ``/inbox/[id]`` still renders
 * the bigger detail surface (events timeline, snooze controls,
 * reassign) for power-user flows.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { MailboxFooter } from "@/components/inbox/mailbox-footer";
import { StaleBadge } from "@/components/inbox/stale-badge";
import { MarkdownBlock } from "@/components/markdown-block";
import { cn } from "@/lib/cn";
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
  INBOX_LIST_DEFAULT_STATUSES,
  INBOX_TYPE_META,
  type InboxItem,
  type InboxItemDetail,
  type InboxListResponse,
  type InboxType,
} from "@/lib/inbox-types";
import { pickWorkspace, withWorkspaceQuery } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

const PAGE_LIMIT = 50;

type Ownership = "mine" | "unassigned" | "all";

type ParsedParams = {
  ownership: Ownership;
  selectedId: string | null;
  errorCode: string | null;
};

type Mode =
  | {
      source: "live";
      workspaceId: string;
      multiWs: boolean;
      list: InboxListResponse;
      detail: InboxItemDetail | null;
      detailError: string | null;
      ownership: Ownership;
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
): ParsedParams {
  const ownershipRaw = typeof raw.ownership === "string" ? raw.ownership : null;
  const ownership: Ownership =
    ownershipRaw === "mine" ||
    ownershipRaw === "unassigned" ||
    ownershipRaw === "all"
      ? ownershipRaw
      : "all";

  const selectedRaw = typeof raw.selected === "string" ? raw.selected : null;
  const selectedId = selectedRaw && selectedRaw.length > 0 ? selectedRaw : null;
  const errorCode = typeof raw.error === "string" ? raw.error : null;

  return { ownership, selectedId, errorCode };
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

  let list: InboxListResponse;
  try {
    list = await listInboxItems(
      workspace.id,
      {
        ownership: parsed.ownership,
        statuses: INBOX_LIST_DEFAULT_STATUSES,
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
    ownership: parsed.ownership,
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

  const { workspaceId, multiWs, list, detail, detailError, ownership, selectedId } =
    data;
  const wsScope = multiWs ? workspaceId : undefined;

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
          <MailboxList
            items={list.items}
            ownership={ownership}
            selectedId={selectedId}
            workspaceScope={wsScope}
          />

          <MailboxPreview
            detail={detail}
            detailError={detailError}
            workspaceId={workspaceId}
            workspaceScope={wsScope}
            empty={list.items.length === 0}
          />
        </div>
      </PageBody>
    </>
  );
}

// ---------------------------------------------------------------------------
// Left pane: the list
// ---------------------------------------------------------------------------

function MailboxList({
  items,
  ownership,
  selectedId,
  workspaceScope,
}: {
  items: InboxItem[];
  ownership: Ownership;
  selectedId: string | null;
  workspaceScope?: string;
}) {
  return (
    <div className="flex min-h-[60vh] flex-col rounded-xl border border-white/[0.08] bg-white/[0.015]">
      <OwnershipTabs current={ownership} workspaceScope={workspaceScope} />

      {items.length === 0 ? (
        <div className="flex-1 px-4 py-10 text-center text-sm text-white/55">
          {ownership === "mine"
            ? "Nothing on your plate."
            : "Inbox empty."}
        </div>
      ) : (
        <ul className="divide-y divide-white/[0.06] overflow-y-auto">
          {items.map((item) => (
            <li key={item.id}>
              <MailboxRow
                item={item}
                selected={item.id === selectedId}
                ownership={ownership}
                workspaceScope={workspaceScope}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function buildSelectHref({
  id,
  ownership,
  workspaceScope,
}: {
  id: string;
  ownership: Ownership;
  workspaceScope?: string;
}): string {
  const params = new URLSearchParams();
  params.set("selected", id);
  if (ownership !== "all") params.set("ownership", ownership);
  if (workspaceScope) params.set("ws", workspaceScope);
  const qs = params.toString();
  return qs ? `/inbox?${qs}` : "/inbox";
}

function buildOwnershipHref({
  ownership,
  workspaceScope,
}: {
  ownership: Ownership;
  workspaceScope?: string;
}): string {
  const params = new URLSearchParams();
  if (ownership !== "all") params.set("ownership", ownership);
  if (workspaceScope) params.set("ws", workspaceScope);
  const qs = params.toString();
  return qs ? `/inbox?${qs}` : "/inbox";
}

function OwnershipTabs({
  current,
  workspaceScope,
}: {
  current: Ownership;
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

const ROW_TONE: Record<InboxType, string> = {
  clarification: "bg-sun",
  approval: "bg-aqua",
  improvement: "bg-lilac",
  failure: "bg-coral",
  blocker: "bg-coral",
  exception: "bg-coral/60",
  stuck: "bg-white/30",
  report: "bg-white/15",
};

function MailboxRow({
  item,
  selected,
  ownership,
  workspaceScope,
}: {
  item: InboxItem;
  selected: boolean;
  ownership: Ownership;
  workspaceScope?: string;
}) {
  const meta = INBOX_TYPE_META[item.type];
  const isUnread = item.status === "new";
  return (
    <Link
      href={buildSelectHref({ id: item.id, ownership, workspaceScope })}
      className={cn(
        "group relative flex items-start gap-3 px-4 py-3 transition",
        selected
          ? "bg-aqua/[0.08]"
          : "hover:bg-white/[0.03]",
      )}
      aria-current={selected ? "true" : undefined}
    >
      <span
        aria-hidden
        className={cn(
          "mt-1 inline-block h-2 w-2 shrink-0 rounded-full",
          isUnread ? ROW_TONE[item.type] : "bg-white/15",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-white/40">
          <span>{meta.label.replace(/s$/, "")}</span>
          <StaleBadge
            createdAt={item.created_at}
            status={item.status}
            snoozedUntil={item.snoozed_until}
          />
        </div>
        <p
          className={cn(
            "mt-1 truncate text-sm",
            selected ? "font-semibold text-white" : isUnread ? "font-semibold text-white/95" : "text-white/70",
          )}
        >
          {item.title}
        </p>
        {item.summary && item.summary.trim().toLowerCase() !== item.title.trim().toLowerCase() && (
          <p className="mt-0.5 truncate text-xs text-white/50">{item.summary}</p>
        )}
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Right pane: the preview
// ---------------------------------------------------------------------------

function MailboxPreview({
  detail,
  detailError,
  workspaceId,
  workspaceScope,
  empty,
}: {
  detail: InboxItemDetail | null;
  detailError: string | null;
  workspaceId: string;
  workspaceScope?: string;
  empty: boolean;
}) {
  if (empty) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.015] px-6 text-center text-sm text-white/55">
        Pick a workspace lens on the left — there&rsquo;s nothing here right now.
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

  const meta = INBOX_TYPE_META[detail.type as InboxType];
  const body = readBody(detail);
  const isClosed = detail.status === "resolved" || detail.status === "dismissed";

  return (
    <div className="flex min-h-[60vh] flex-col rounded-xl border border-white/[0.08] bg-white/[0.015]">
      <header className="border-b border-white/[0.06] px-6 py-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] uppercase tracking-[0.18em] text-white/45">
          <span>{meta?.label ?? detail.type}</span>
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
        <h1 className="mt-1 text-lg font-semibold text-white">{detail.title}</h1>
        {detail.owner && (
          <p className="mt-1 text-xs text-white/55">
            Owner:{" "}
            <span className="text-white/75">
              {detail.owner.display_name?.trim() || detail.owner.email}
            </span>
          </p>
        )}
        <p className="mt-1 text-xs text-white/40">
          <Link
            href={withWorkspaceQuery(
              `/inbox/${encodeURIComponent(detail.id)}`,
              workspaceScope ?? "",
              Boolean(workspaceScope),
            )}
            className="underline-offset-2 hover:text-white hover:underline"
          >
            Open full detail →
          </Link>
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {body ? (
          <MarkdownBlock>{body}</MarkdownBlock>
        ) : (
          <p className="text-sm italic text-white/40">No body.</p>
        )}
      </div>

      <footer className="border-t border-white/[0.06] px-6 py-4">
        <MailboxFooter detail={detail} workspaceId={workspaceId} />
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
