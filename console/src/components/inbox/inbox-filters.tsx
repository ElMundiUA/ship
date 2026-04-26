"use client";

import { cn } from "@/lib/cn";
import {
  INBOX_FILTER_TYPES,
  INBOX_TYPE_META,
  type InboxFilterState,
  type InboxType,
} from "@/lib/inbox-types";

/**
 * Filter controls for the unified Inbox list (RFC-0010 P2-12).
 *
 * Ownership: All (default) | Mine | Unassigned
 * Type row:  All, then Clarifications … Exceptions (stuck / blockers stay in the
 * list but are not in this chip row).
 *
 * List status slice (e.g. open + snoozed) is fixed in the data layer — not
 * user-configurable here.
 */

const OWNERSHIP_OPTIONS: {
  key: InboxFilterState["ownership"];
  label: string;
  hint: string;
}[] = [
  { key: "all", label: "All", hint: "Entire workspace queue" },
  { key: "mine", label: "Mine", hint: "Items routed to you" },
  {
    key: "unassigned",
    label: "Unassigned",
    hint: "No owner — needs assignment",
  },
];

export type InboxFiltersProps = {
  value: InboxFilterState;
  onChange: (next: InboxFilterState) => void;
  /** Optional count badges, keyed by filter dimension. */
  counts?: {
    ownership?: Partial<Record<InboxFilterState["ownership"], number>>;
    types?: Partial<Record<InboxType, number>>;
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
        <button
          type="button"
          aria-pressed={value.types.length === 0}
          onClick={() => onChange({ ...value, types: [] })}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-semibold transition",
            value.types.length === 0
              ? "border-aqua/50 bg-aqua/15 text-aqua"
              : "border-white/10 bg-white/[0.04] text-white/65 hover:border-white/20 hover:text-white/85",
          )}
        >
          All
        </button>
        {INBOX_FILTER_TYPES.map((t) => {
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
      </div>
    </div>
  );
}
