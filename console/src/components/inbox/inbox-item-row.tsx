import Link from "next/link";

import { Badge, type BadgeTone } from "@/components/ui";
import { cn } from "@/lib/cn";
import { INBOX_TYPE_META, type InboxItem } from "@/lib/inbox-types";

import { InboxRowActions } from "./inbox-row-actions";
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

  // Dense single-row layout: type-dot · title (truncated) · meta · owner pill.
  // Summary collapses under the title only when it adds new info beyond the
  // title (no duplicate-text wraps). Each row is ~52px tall; the previous
  // 3-column grid with a 36px avatar and stacked badges took ~120px.
  const dotClass =
    item.type === "clarification"
      ? "bg-sun"
      : item.type === "approval"
        ? "bg-aqua"
        : item.type === "improvement"
          ? "bg-lilac"
          : item.type === "failure" || item.type === "blocker" || item.type === "exception"
            ? "bg-coral"
            : item.type === "stuck"
              ? "bg-sun/80"
              : "bg-white/40";

  const showSummary =
    item.summary && item.summary.trim().toLowerCase() !== item.title.trim().toLowerCase();

  const body = (
    <div
      className={cn(
        "group flex items-start gap-3 rounded-xl border px-3 py-2.5 transition",
        isTerminal
          ? "border-white/[0.06] bg-white/[0.015] opacity-65"
          : "border-white/10 bg-white/[0.03] hover:border-white/25 hover:bg-white/[0.06]",
        className,
      )}
    >
      {/* Type dot */}
      <span
        className={cn("mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full", dotClass)}
        title={meta.label}
        aria-hidden
      />

      {/* Main column */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-white/45">
            {meta.label.replace(/s$/, "")}
          </span>
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
            <Badge tone="warn" className="!normal-case" dot={false}>
              fallback routing
            </Badge>
          )}
        </div>
        <p
          className={cn(
            "mt-1 truncate text-sm font-semibold",
            isTerminal ? "text-white/55" : "text-white",
          )}
        >
          {item.title}
        </p>
        {showSummary && (
          <p className="mt-0.5 truncate text-xs text-white/50">{item.summary}</p>
        )}
      </div>

      {/* Right rail: actions + owner + play key. */}
      <div
        className="flex shrink-0 items-center gap-2 self-center"
        title={item.intake_reason ?? undefined}
      >
        {item.play_key && (
          <code className="hidden font-mono text-[10px] text-white/35 lg:inline">
            {item.play_key}
          </code>
        )}
        {workspaceId && href && !isTerminal && (
          <InboxRowActions
            workspaceId={workspaceId}
            item={{ id: item.id, type: item.type, status: item.status }}
            detailHref={href}
          />
        )}
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold",
            item.owner
              ? "border-aqua/35 bg-aqua/10 text-aqua"
              : "border-coral/35 bg-coral/10 text-coral",
          )}
        >
          <span className="font-mono text-[9px]">{ownerInitials(item.owner)}</span>
          <span>{ownerLabel(item.owner)}</span>
        </span>
      </div>
    </div>
  );

  if (!href) return body;
  return (
    <Link
      href={href}
      className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-aqua/60 focus-visible:ring-offset-2 focus-visible:ring-offset-ink rounded-2xl"
      aria-label={`${INBOX_TYPE_META[item.type].label}: ${item.title}`}
    >
      {body}
    </Link>
  );
}
