"use client";

import { useMemo, useState } from "react";

import { cn } from "@/lib/cn";
import {
  INBOX_LANE_META,
  INBOX_LANES,
  type InboxItem,
  type InboxLane,
} from "@/lib/inbox-types";

export type InboxLaneFilterValue = InboxLane | "all";

export function filterInboxByLane(
  items: InboxItem[],
  lane: InboxLaneFilterValue,
): InboxItem[] {
  if (lane === "all") return items;
  return items.filter((item) => item.lane === lane);
}

export function InboxLaneFilterChips({
  allCount,
  counts,
  value,
  onChange,
  className,
}: {
  allCount: number;
  counts: Record<InboxLane, number>;
  value: InboxLaneFilterValue;
  onChange: (lane: InboxLaneFilterValue) => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-white/[0.06] px-4 py-2",
        className,
      )}
      data-testid="inbox-lane-filters"
    >
      <LaneChip
        active={value === "all"}
        label="All"
        count={allCount}
        onClick={() => onChange("all")}
      />
      {INBOX_LANES.map((lane) => (
        <LaneChip
          key={lane}
          active={value === lane}
          label={INBOX_LANE_META[lane].label}
          count={counts[lane]}
          toneClass={INBOX_LANE_META[lane].tone}
          onClick={() => onChange(lane)}
        />
      ))}
    </div>
  );
}

function LaneChip({
  active,
  label,
  count,
  toneClass,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  toneClass?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      data-testid={`inbox-lane-${label.toLowerCase()}`}
      onClick={onClick}
      className={cn(
        "inline-flex items-baseline gap-1.5 text-[11px] font-semibold uppercase tracking-wider transition",
        active
          ? cn("text-aqua", toneClass)
          : "text-white/55 hover:text-white",
      )}
    >
      <span>{label}</span>
      {active && <span className="tabular-nums text-white/45">({count})</span>}
    </button>
  );
}

/** Controlled lane filter with internal state (inbox list pane). */
export function useInboxLaneFilter(items: InboxItem[]) {
  const [lane, setLane] = useState<InboxLaneFilterValue>("all");
  const visible = useMemo(() => filterInboxByLane(items, lane), [items, lane]);
  const counts = useMemo(() => {
    const map: Record<InboxLane, number> = { now: 0, today: 0, whenever: 0 };
    for (const item of items) {
      map[item.lane] += 1;
    }
    return map;
  }, [items]);
  return { lane, setLane, visible, counts };
}
