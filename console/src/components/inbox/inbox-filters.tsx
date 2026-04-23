"use client";

import { cn } from "@/lib/cn";
import {
  INBOX_STATUSES,
  INBOX_TYPE_META,
  INBOX_TYPES,
  type InboxFilterState,
  type InboxStatus,
  type InboxType,
} from "@/lib/inbox-types";

/**
 * Filter controls for the unified Inbox list (RFC-0010 P2-12).
 *
 * Three-axis filtering:
 *   - Ownership tabs:  Mine | Unassigned | All
 *   - Type chips:      Clarification | Improvement | Failure | Approval | Exception
 *   - Status pills:    new (default) | snoozed | resolved | dismissed
 *
 * State is owned by the parent (so it can mirror to the URL via
 * `useSearchParams` + `router.replace`). This component is the
 * "view" half of the controlled-pattern: it renders the current
 * state and emits `onChange(next)` for every interaction.
 *
 * Counts (next to each pill) are optional — pass `counts={...}` to
 * decorate, omit for static demos.
 */

const OWNERSHIP_OPTIONS: {
  key: InboxFilterState["ownership"];
  label: string;
  hint: string;
}[] = [
  { key: "mine", label: "Mine", hint: "Items routed to you" },
  {
    key: "unassigned",
    label: "Unassigned",
    hint: "Routing fallback failed — needs an admin",
  },
  { key: "all", label: "All", hint: "Workspace-wide firehose (admin)" },
];

const STATUS_LABEL: Record<InboxStatus, string> = {
  new: "Open",
  snoozed: "Snoozed",
  resolved: "Resolved",
  dismissed: "Dismissed",
};

export type InboxFiltersProps = {
  value: InboxFilterState;
  onChange: (next: InboxFilterState) => void;
  /** Optional count badges, keyed by filter dimension. */
  counts?: {
    ownership?: Partial<Record<InboxFilterState["ownership"], number>>;
    types?: Partial<Record<InboxType, number>>;
    statuses?: Partial<Record<InboxStatus, number>>;
  };
  className?: string;
};

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

export function InboxFilters({
  value,
  onChange,
  counts,
  className,
}: InboxFiltersProps) {
  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div
        role="tablist"
        aria-label="Inbox ownership filter"
        className="flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] p-1"
      >
        {OWNERSHIP_OPTIONS.map((opt) => {
          const active = value.ownership === opt.key;
          const count = counts?.ownership?.[opt.key];
          return (
            <button
              key={opt.key}
              type="button"
              role="tab"
              aria-selected={active}
              title={opt.hint}
              onClick={() => onChange({ ...value, ownership: opt.key })}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold transition",
                active
                  ? "bg-white/15 text-white shadow-sm"
                  : "text-white/65 hover:bg-white/[0.06] hover:text-white/85",
              )}
            >
              {opt.label}
              {count !== undefined && (
                <span
                  className={cn(
                    "rounded-full px-1.5 text-[10px] font-bold",
                    active ? "bg-white/20 text-white" : "bg-white/10 text-white/55",
                  )}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {INBOX_TYPES.map((t) => {
          const active = value.types.includes(t);
          const count = counts?.types?.[t];
          return (
            <button
              key={t}
              type="button"
              aria-pressed={active}
              onClick={() =>
                onChange({ ...value, types: toggle(value.types, t) })
              }
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-semibold transition",
                active
                  ? "border-aqua/50 bg-aqua/15 text-aqua"
                  : "border-white/10 bg-white/[0.04] text-white/65 hover:border-white/20 hover:text-white/85",
              )}
            >
              {INBOX_TYPE_META[t].label}
              {count !== undefined && (
                <span
                  className={cn(
                    "rounded-full px-1.5 text-[10px] font-bold",
                    active ? "bg-aqua/30 text-white" : "bg-white/10 text-white/55",
                  )}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
        {value.types.length > 0 && (
          <button
            type="button"
            onClick={() => onChange({ ...value, types: [] })}
            className="ml-1 text-[10px] font-semibold uppercase tracking-wider text-white/40 hover:text-white/70"
          >
            clear
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {INBOX_STATUSES.map((s) => {
          const active = value.statuses.includes(s);
          const count = counts?.statuses?.[s];
          return (
            <button
              key={s}
              type="button"
              aria-pressed={active}
              onClick={() =>
                onChange({ ...value, statuses: toggle(value.statuses, s) })
              }
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider transition",
                active
                  ? "border-lilac/50 bg-lilac/15 text-lilac"
                  : "border-white/10 bg-white/[0.02] text-white/55 hover:border-white/20 hover:text-white/75",
              )}
            >
              {STATUS_LABEL[s]}
              {count !== undefined && (
                <span
                  className={cn(
                    "rounded-full px-1 text-[9px] font-bold",
                    active ? "bg-lilac/30 text-white" : "bg-white/10 text-white/55",
                  )}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
