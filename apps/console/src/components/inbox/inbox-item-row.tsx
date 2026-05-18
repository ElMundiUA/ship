import Link from "next/link";

import { cn } from "@/lib/cn";
import { INBOX_TYPE_META, type InboxItem } from "@/lib/inbox-types";

import { InboxRowActions } from "./inbox-row-actions";
import { StaleBadge } from "./stale-badge";

/**
 * One row in the unified Inbox list.
 *
 * Per design rec: no rounded card around the row. Per-kind colour
 * lives on a 3px left-edge **spine** (Linear's priority spine
 * idiom). Rows sit on a ``divide-y`` rhythm provided by the
 * parent — this component contributes zero borders / backgrounds
 * in the resting state. Hover gets a faint ``bg-white/[0.025]``
 * tint and reveals the inline action buttons (touch breakpoints
 * keep the actions visible at all times).
 */

const SPINE_TONE: Record<InboxItem["type"], string> = {
  clarification: "bg-sun",
  approval: "bg-aqua",
  improvement: "bg-lilac",
  failure: "bg-coral",
  blocker: "bg-coral",
  exception: "bg-coral/60",
  stuck: "bg-white/30",
  report: "bg-white/15",
};

const SPINE_WIDTH: Record<InboxItem["type"], string> = {
  clarification: "w-[3px]",
  approval: "w-[3px]",
  improvement: "w-[2px]",
  failure: "w-[3px]",
  blocker: "w-[4px]",
  exception: "w-[2px]",
  stuck: "w-px",
  report: "w-px",
};

function ownerLabel(owner: InboxItem["owner"]): string {
  if (!owner) return "Unassigned";
  return owner.display_name?.trim() || owner.email;
}

function ownerInitials(owner: InboxItem["owner"]): string {
  if (!owner) return "—";
  const source = owner.display_name?.trim() || owner.email;
  const tokens = source.split(/[\s@.]+/).filter(Boolean);
  if (tokens.length === 0) return "—";
  if (tokens.length === 1) return tokens[0].slice(0, 2).toUpperCase();
  return (tokens[0][0] + tokens[1][0]).toUpperCase();
}

export type InboxItemRowProps = {
  item: InboxItem;
  href?: string;
  /** Workspace id — required for inline actions. When omitted, action buttons hide. */
  workspaceId?: string;
  /** Override "now" for tests / SSR snapshots. */
  referenceDate?: Date;
  className?: string;
};

export function InboxItemRow({
  item,
  href,
  workspaceId,
  referenceDate,
  className,
}: InboxItemRowProps) {
  const meta = INBOX_TYPE_META[item.type];
  const isTerminal =
    item.status === "resolved" || item.status === "dismissed";
  const showSummary =
    item.summary &&
    item.summary.trim().toLowerCase() !== item.title.trim().toLowerCase();

  const body = (
    <div
      className={cn(
        "group relative flex items-start gap-4 py-3 pl-4 pr-2 transition",
        isTerminal ? "opacity-50" : "hover:bg-white/[0.025]",
        className,
      )}
    >
      <span
        aria-hidden
        title={meta.label}
        className={cn(
          "absolute inset-y-2 left-0 rounded-r-sm",
          SPINE_TONE[item.type],
          SPINE_WIDTH[item.type],
        )}
      />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-white/45">
          <span>{meta.label.replace(/s$/, "")}</span>
          {item.status !== "new" && (
            <>
              <span className="text-white/15">·</span>
              <span className="text-white/55">{item.status}</span>
            </>
          )}
          <StaleBadge
            createdAt={item.created_at}
            status={item.status}
            snoozedUntil={item.snoozed_until}
            referenceDate={referenceDate}
          />
          {item.intake_reason?.startsWith("fallback:") && (
            <>
              <span className="text-white/15">·</span>
              <span className="text-sun/80">fallback routing</span>
            </>
          )}
        </div>
        <p
          className={cn(
            "mt-1 truncate text-[15px] font-semibold",
            isTerminal ? "text-white/55" : "text-white",
          )}
        >
          {item.title}
        </p>
        {showSummary && (
          <p className="mt-0.5 truncate text-xs text-white/55">{item.summary}</p>
        )}
      </div>

      <div
        className="flex shrink-0 items-center gap-3 self-center"
        title={item.intake_reason ?? undefined}
      >
        {item.play_key && (
          <code className="hidden font-mono text-[10px] text-white/30 lg:inline">
            {item.play_key}
          </code>
        )}
        {workspaceId && href && !isTerminal && (
          <span className="inline-flex">
            <InboxRowActions
              workspaceId={workspaceId}
              item={{ id: item.id, type: item.type, status: item.status }}
              detailHref={href}
            />
          </span>
        )}
        <span
          className={cn(
            "inline-flex items-center gap-1.5 text-xs",
            item.owner ? "text-white/85" : "text-coral/85",
          )}
        >
          <span
            className={cn(
              "inline-flex h-5 w-5 items-center justify-center rounded-full font-mono text-[9px] font-bold",
              item.owner
                ? "bg-white/[0.08] text-white/75"
                : "bg-coral/15 text-coral",
            )}
          >
            {ownerInitials(item.owner)}
          </span>
          <span className="truncate">{ownerLabel(item.owner)}</span>
        </span>
      </div>
    </div>
  );

  if (!href) return body;
  return (
    <Link
      href={href}
      className="block focus:outline-none focus-visible:ring-1 focus-visible:ring-aqua/60 focus-visible:ring-offset-0"
      aria-label={`${INBOX_TYPE_META[item.type].label}: ${item.title}`}
    >
      {body}
    </Link>
  );
}
