"use client";

import Link from "next/link";

import {
  InboxLaneFilterChips,
  useInboxLaneFilter,
} from "@/components/inbox/inbox-lane-filters";
import { MailboxKeyboardNav } from "@/components/inbox/mailbox-keyboard-nav";
import { StaleBadge } from "@/components/inbox/stale-badge";
import { EmptyState } from "@/components/ui";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import {
  formatInboxHeadline,
  formatIntakeReasonTooltip,
} from "@/lib/inbox-copy";
import { ROW_KICKER, type InboxItem } from "@/lib/inbox-types";

type Ownership = "mine" | "unassigned" | "all";

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

  const hrefForId = (id: string) =>
    buildSelectHref({ id, ownership, workspaceScope });

  return (
    <div className="flex min-h-[60vh] flex-col rounded-xl border border-white/[0.08] bg-white/[0.015]">
      <MailboxKeyboardNav
        itemIds={visible.map((i) => i.id)}
        selectedId={selectedId}
        buildHref={hrefForId}
      />
      {ownershipTabs}
      <InboxLaneFilterChips
        allCount={items.length}
        counts={counts}
        value={lane}
        onChange={setLane}
      />

      {visible.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-4">
          {items.length === 0 ? (
            <EmptyState
              title="Inbox empty"
              body="Nothing waiting on you — agents working."
            />
          ) : (
            <p className="text-center text-sm text-white/55">
              No items in this lane — try another filter.
            </p>
          )}
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
  const kicker = ROW_KICKER[item.type];
  const isUnread = item.status === "new";
  const headline = formatInboxHeadline(item);
  const decisionCount = item.action_item_count ?? 0;
  const intakeTip = formatIntakeReasonTooltip(item.intake_reason);

  return (
    <Link
      href={buildSelectHref({ id: item.id, ownership, workspaceScope })}
      className={cn(
        "group relative flex items-center gap-2.5 px-4 py-2 transition",
        selected ? "bg-aqua/[0.08]" : "hover:bg-white/[0.03]",
      )}
      aria-current={selected ? "page" : undefined}
      title={intakeTip}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-2 w-2 shrink-0 rounded-full",
          isUnread ? kicker.tone : "bg-white/15",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-white/40">
          <span>
            <span aria-hidden>{kicker.glyph} </span>
            {kicker.label}
          </span>
          <span className="text-white/15">·</span>
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
          {decisionCount > 0 && (
            <span className="rounded-full border border-white/15 bg-white/[0.06] px-1.5 py-0.5 text-[9px] font-semibold text-white/70">
              {decisionCount} {decisionCount === 1 ? "decision" : "decisions"}
            </span>
          )}
        </div>
        <p
          className={cn(
            "truncate text-sm leading-tight",
            selected
              ? "font-semibold text-white"
              : isUnread
                ? "font-semibold text-white/95"
                : "text-white/70",
          )}
        >
          {headline}
        </p>
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
