"use client";

import Link from "next/link";

import {
  InboxLaneFilterChips,
  useInboxLaneFilter,
} from "@/components/inbox/inbox-lane-filters";
import { StaleBadge } from "@/components/inbox/stale-badge";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import {
  INBOX_TYPE_META,
  type InboxItem,
  type InboxType,
} from "@/lib/inbox-types";

type Ownership = "mine" | "unassigned" | "all";

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

export function InboxMailboxListClient({
  items,
  ownership,
  selectedId,
  workspaceScope,
  ownershipTabs,
}: {
  items: InboxItem[];
  ownership: Ownership;
  selectedId: string | null;
  workspaceScope?: string;
  ownershipTabs: React.ReactNode;
}) {
  const { lane, setLane, visible, counts } = useInboxLaneFilter(items);

  return (
    <div className="flex min-h-[60vh] flex-col rounded-xl border border-white/[0.08] bg-white/[0.015]">
      {ownershipTabs}
      <InboxLaneFilterChips
        allCount={items.length}
        counts={counts}
        value={lane}
        onChange={setLane}
      />

      {visible.length === 0 ? (
        <div className="flex-1 px-4 py-10 text-center text-sm text-white/55">
          {ownership === "mine"
            ? "Nothing on your plate."
            : "Inbox empty."}
        </div>
      ) : (
        <ul
          className="divide-y divide-white/[0.06] overflow-y-auto"
          data-testid="inbox-mailbox-rows"
        >
          {visible.map((item) => (
            <li key={item.id} data-lane={item.lane}>
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
        selected ? "bg-aqua/[0.08]" : "hover:bg-white/[0.03]",
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
        <span className="text-white/15">·</span>
        {/* Received-time on the list row, not just the preview header.
            Operator scans the list left-to-right and used to have no
            recency signal without clicking each letter. Title attr
            carries the absolute local time for hover. */}
        <time
          className="normal-case tracking-normal text-white/55"
          dateTime={item.created_at}
          title={new Date(item.created_at).toLocaleString()}
        >
          {relativeTime(item.created_at)}
        </time>
        <StaleBadge
          createdAt={item.created_at}
          status={item.status}
          snoozedUntil={item.snoozed_until}
        />
      </div>
      <p
        className={cn(
          "mt-1 truncate text-sm",
          selected
            ? "font-semibold text-white"
            : isUnread
              ? "font-semibold text-white/95"
              : "text-white/70",
        )}
      >
        {item.title}
      </p>
      {item.summary &&
        item.summary.trim().toLowerCase() !== item.title.trim().toLowerCase() && (
          <p className="mt-0.5 truncate text-xs text-white/50">{item.summary}</p>
        )}
      </div>
    </Link>
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
