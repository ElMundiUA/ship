"use client";

import { cn } from "@/lib/cn";
import {
  INBOX_FILTER_TYPES,
  INBOX_TYPE_META,
  type InboxFilterState,
  type InboxType,
} from "@/lib/inbox-types";

/**
 * Type chip row for the unified Inbox list.
 *
 * Ownership lives in the page-level stats ribbon now, so this
 * component only renders the type filter strip. Chips are
 * transparent in the resting state with a hairline border, lit up
 * via the section accent when active. Counts only render on the
 * active chip + the All chip — inactive chips stay quiet.
 */

export type InboxFiltersProps = {
  value: InboxFilterState;
  onChange: (next: InboxFilterState) => void;
  /** Optional count badges, keyed by filter dimension. */
  counts?: {
    types?: Partial<Record<InboxType, number>>;
    /** Total across types for this view (no type filter); matches sum of per-type counts. */
    allTypes?: number;
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
    <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-2", className)}>
      <ChipButton
        active={value.types.length === 0}
        count={counts?.allTypes}
        showCount
        onClick={() => onChange({ ...value, types: [] })}
      >
        All
      </ChipButton>
      {INBOX_FILTER_TYPES.map((t) => {
        const active = value.types.includes(t);
        const count = counts?.types?.[t];
        return (
          <ChipButton
            key={t}
            active={active}
            count={count}
            showCount={active}
            onClick={() =>
              onChange({ ...value, types: toggle(value.types, t) })
            }
          >
            {INBOX_TYPE_META[t].label}
          </ChipButton>
        );
      })}
    </div>
  );
}


function ChipButton({
  active,
  count,
  showCount,
  onClick,
  children,
}: {
  active: boolean;
  count?: number;
  showCount?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "group inline-flex items-baseline gap-1.5 text-[11px] font-semibold uppercase tracking-wider transition",
        active
          ? "text-aqua"
          : "text-white/55 hover:text-white",
      )}
    >
      <span>{children}</span>
      {showCount && count !== undefined && (
        <span
          className={cn(
            "font-mono text-[10px] font-bold",
            active ? "text-aqua/70" : "text-white/35",
          )}
        >
          {count}
        </span>
      )}
    </button>
  );
}
