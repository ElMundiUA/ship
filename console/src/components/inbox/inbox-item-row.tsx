import Link from "next/link";

import { Badge, type BadgeTone } from "@/components/ui";
import { cn } from "@/lib/cn";
import {
  INBOX_TYPE_META,
  type InboxItem,
  type InboxType,
} from "@/lib/inbox-types";

import { StaleBadge } from "./stale-badge";

/**
 * One row in the unified Inbox list (RFC-0010 P2-12).
 *
 * Pure presentational — receives a fully-typed `InboxItem` from the
 * list page and renders:
 *   - type chip + age badge (escalates yellow at 2d, red at 7d)
 *   - title + summary preview (single-line truncated)
 *   - owner pill (or "Unassigned" warn pill when `owner` is null)
 *   - intake reason (debug-grade tooltip; surfaces routing path)
 *   - status side-marker for snoozed/resolved/dismissed rows
 *
 * Linking is delegated to the parent: provide `href` to make the
 * whole row a Link, or omit it for static demos. The full keyboard
 * a11y story (focus ring, aria-label) lives on the wrapping `<a>`.
 */

const TYPE_TONE: Record<InboxType, BadgeTone> = {
  clarification: "info",
  improvement: "ok",
  failure: "err",
  approval: "warn",
  exception: "err",
};

const STATUS_TONE: Record<InboxItem["status"], BadgeTone> = {
  new: "info",
  snoozed: "warn",
  resolved: "neutral",
  dismissed: "neutral",
};

function ownerLabel(owner: InboxItem["owner"]): string {
  if (!owner) return "Unassigned";
  return owner.display_name?.trim() || owner.email;
}

function ownerInitials(owner: InboxItem["owner"]): string {
  if (!owner) return "?";
  const source = owner.display_name?.trim() || owner.email;
  const tokens = source.split(/[\s@.]+/).filter(Boolean);
  if (tokens.length === 0) return "?";
  if (tokens.length === 1) return tokens[0].slice(0, 2).toUpperCase();
  return (tokens[0][0] + tokens[1][0]).toUpperCase();
}

export type InboxItemRowProps = {
  item: InboxItem;
  href?: string;
  /** Override "now" for tests / SSR snapshots. */
  referenceDate?: Date;
  className?: string;
  /** Callback when the row is clicked (in addition to navigation). */
  onSelect?: (id: string) => void;
};

export function InboxItemRow({
  item,
  href,
  referenceDate,
  className,
  onSelect,
}: InboxItemRowProps) {
  const meta = INBOX_TYPE_META[item.type];
  const isTerminal =
    item.status === "resolved" || item.status === "dismissed";

  const body = (
    <div
      className={cn(
        "grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-2xl border px-4 py-3 transition",
        isTerminal
          ? "border-white/5 bg-white/[0.02] opacity-70"
          : "border-white/10 bg-white/[0.04] hover:border-white/20 hover:bg-white/[0.07]",
        className,
      )}
    >
      <div
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[10px] font-bold uppercase tracking-wider",
          item.owner
            ? "bg-aqua/15 text-aqua border border-aqua/40"
            : "bg-coral/15 text-coral border border-coral/40",
        )}
        title={item.owner ? `Assigned to ${ownerLabel(item.owner)}` : "Unassigned — needs routing fix"}
      >
        {ownerInitials(item.owner)}
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={TYPE_TONE[item.type]} dot>
            {meta.label.replace(/s$/, "")}
          </Badge>
          {item.status !== "new" && (
            <Badge tone={STATUS_TONE[item.status]}>{item.status}</Badge>
          )}
          <StaleBadge
            createdAt={item.created_at}
            status={item.status}
            snoozedUntil={item.snoozed_until}
            referenceDate={referenceDate}
          />
          {item.intake_reason?.startsWith("fallback:") && (
            <Badge
              tone="warn"
              className="!normal-case"
              dot={false}
            >
              fallback routing
            </Badge>
          )}
        </div>
        <div
          className={cn(
            "mt-1.5 truncate text-sm font-semibold",
            isTerminal ? "text-white/55" : "text-white",
          )}
        >
          {item.title}
        </div>
        {item.summary && (
          <div className="mt-0.5 truncate text-xs text-white/55">
            {item.summary}
          </div>
        )}
      </div>

      <div
        className="flex shrink-0 flex-col items-end gap-1 text-right"
        title={item.intake_reason ?? undefined}
      >
        <div className="text-xs font-semibold text-white/85">
          {ownerLabel(item.owner)}
        </div>
        {item.play_key && (
          <code className="text-[10px] font-medium text-white/40">
            {item.play_key}
          </code>
        )}
      </div>
    </div>
  );

  if (!href) return body;
  return (
    <Link
      href={href}
      className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-aqua/60 focus-visible:ring-offset-2 focus-visible:ring-offset-ink rounded-2xl"
      onClick={() => onSelect?.(item.id)}
      aria-label={`${INBOX_TYPE_META[item.type].label}: ${item.title}`}
    >
      {body}
    </Link>
  );
}
