"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, Card, CardHeader } from "@/components/ui";
import type { ApiLane, ApiLaneCatalogEntry } from "@/lib/api/client";

import { humanizeCron, nextOccurrences } from "./cron";

/**
 * Weekly calendar view for the Active tab.
 *
 * Renders 7 days × 24 hours. For each lane with a cron schedule we
 * compute its occurrences in the displayed week and drop a block at
 * the matching hour/day cell. Clicking a block pops a detail drawer
 * on the right with the pattern's description + "Edit schedule" /
 * "Open Actions" links.
 *
 * Design decisions:
 *
 * - Week is **Monday-starting** because lanes skew toward working-
 *   week cadences (standups, retros) where starting on Sunday splits
 *   the weekly pattern visually.
 * - Navigation is ``Previous week / This week / Next week`` — we
 *   don't need infinite scrolling; 99% of the insight lives in the
 *   current week.
 * - Event-driven lanes (PR, push) live in a strip *below* the grid
 *   with a short description — they don't have a time, so forcing
 *   them onto a clock would lie to the user.
 * - Hours visible are compressed: we hide the 0–5 UTC band by default
 *   (toggleable) because most schedules cluster in 06–22 UTC.
 *
 * Time is UTC end-to-end (matches ``schedule:`` in GitHub Actions).
 */

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// Day indices as JS getUTCDay() returns them: 0 = Sun … 6 = Sat.
// Our layout is Mon..Sun, so we remap once here.
const JS_DAY_TO_COL: Record<number, number> = {
  1: 0, // Mon
  2: 1, // Tue
  3: 2, // Wed
  4: 3, // Thu
  5: 4, // Fri
  6: 5, // Sat
  0: 6, // Sun
};

type Occurrence = {
  lane: ApiLane;
  at: Date;
};

type OccurrenceCell = {
  col: number; // 0..6
  hour: number; // 0..23
  items: Occurrence[];
};

export function WeeklyCalendar({
  lanes,
  catalog,
  onEdit,
}: {
  lanes: ApiLane[];
  catalog: ApiLaneCatalogEntry[];
  onEdit?: (lane: ApiLane) => void;
}) {
  const [weekOffset, setWeekOffset] = useState(0);
  const [compact, setCompact] = useState(true);
  const [selected, setSelected] = useState<Occurrence | null>(null);

  const { weekStart, weekEnd } = useMemo(
    () => computeWeek(weekOffset),
    [weekOffset],
  );

  const scheduleLanes = useMemo(
    () => lanes.filter((l) => l.kind === "schedule" && l.enabled && l.cron),
    [lanes],
  );
  const eventLanes = useMemo(
    () => lanes.filter((l) => l.kind === "event" || l.kind === "once"),
    [lanes],
  );

  const cells = useMemo(
    () => buildCells(scheduleLanes, weekStart, weekEnd),
    [scheduleLanes, weekStart, weekEnd],
  );

  const catalogIndex = useMemo(
    () => new Map(catalog.map((c) => [c.kind, c])),
    [catalog],
  );

  // Which rows (hours) to show. Compact mode hides ``0..5``; if a
  // lane DOES fire in those hours we keep them visible anyway.
  const visibleHours = useMemo(() => {
    const fired = new Set<number>();
    for (const c of cells) fired.add(c.hour);
    const out: number[] = [];
    for (let h = 0; h < 24; h += 1) {
      if (!compact) out.push(h);
      else if (h >= 6 && h <= 22) out.push(h);
      else if (fired.has(h)) out.push(h);
    }
    return out;
  }, [cells, compact]);

  const weekLabel = `${fmtWeekHeader(weekStart)} – ${fmtWeekHeader(
    new Date(weekEnd.getTime() - 1),
  )} UTC`;

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setWeekOffset((w) => w - 1)}
            className="rounded-md border border-white/15 bg-white/[0.04] px-2.5 py-1 text-[11px] font-semibold text-white/70 hover:text-white"
          >
            ‹ Prev
          </button>
          <button
            type="button"
            onClick={() => setWeekOffset(0)}
            disabled={weekOffset === 0}
            className={
              "rounded-md border px-2.5 py-1 text-[11px] font-semibold transition " +
              (weekOffset === 0
                ? "cursor-default border-aqua/40 bg-aqua/10 text-aqua"
                : "border-white/15 bg-white/[0.04] text-white/70 hover:text-white")
            }
          >
            This week
          </button>
          <button
            type="button"
            onClick={() => setWeekOffset((w) => w + 1)}
            className="rounded-md border border-white/15 bg-white/[0.04] px-2.5 py-1 text-[11px] font-semibold text-white/70 hover:text-white"
          >
            Next ›
          </button>
          <span className="ml-2 font-mono text-[11px] text-white/55">
            {weekLabel}
          </span>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-white/55">
          <input
            type="checkbox"
            checked={compact}
            onChange={(e) => setCompact(e.target.checked)}
            className="h-3 w-3 rounded border-white/30 bg-white/10"
          />
          Compact (hide 00–05 UTC)
        </label>
      </div>

      {scheduleLanes.length === 0 ? (
        <Card>
          <CardHeader
            title="Nothing scheduled"
            subtitle="Your .ship/config.yml has no scheduled lanes yet. Add one from the Library tab."
          />
          <div className="mt-3">
            <Link
              href="/lanes?tab=library"
              className="inline-flex rounded border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua hover:bg-aqua/20"
            >
              Browse Library →
            </Link>
          </div>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
          <CalendarGrid
            visibleHours={visibleHours}
            cells={cells}
            onPickOccurrence={setSelected}
            selectedKey={selected ? occurrenceKey(selected) : null}
          />
          <DetailDrawer
            occurrence={selected}
            catalogIndex={catalogIndex}
            onClose={() => setSelected(null)}
            onEdit={onEdit}
          />
        </div>
      )}

      {eventLanes.length > 0 ? (
        <EventDrivenSection lanes={eventLanes} catalogIndex={catalogIndex} />
      ) : null}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Grid
// ----------------------------------------------------------------------------

function CalendarGrid({
  visibleHours,
  cells,
  onPickOccurrence,
  selectedKey,
}: {
  visibleHours: number[];
  cells: OccurrenceCell[];
  onPickOccurrence: (o: Occurrence) => void;
  selectedKey: string | null;
}) {
  // Index cells by ``col:hour`` for fast lookup.
  const index = useMemo(() => {
    const m = new Map<string, OccurrenceCell>();
    for (const c of cells) m.set(`${c.col}:${c.hour}`, c);
    return m;
  }, [cells]);

  return (
    <Card className="overflow-hidden !p-0">
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-[11px]">
          <thead>
            <tr className="border-b border-white/10 text-white/55">
              <th className="w-14 bg-white/[0.03] px-2 py-2 text-left font-semibold uppercase tracking-wider">
                UTC
              </th>
              {DAY_LABELS.map((d) => (
                <th
                  key={d}
                  className="min-w-[88px] bg-white/[0.03] px-2 py-2 text-left font-semibold uppercase tracking-wider"
                >
                  {d}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleHours.map((hour, rowIdx) => (
              <tr
                key={hour}
                className={
                  rowIdx % 2 === 0 ? "bg-white/[0.00]" : "bg-white/[0.015]"
                }
              >
                <td className="w-14 border-r border-white/5 px-2 py-1.5 align-top font-mono text-[10px] text-white/45">
                  {pad(hour)}:00
                </td>
                {DAY_LABELS.map((_, col) => {
                  const cell = index.get(`${col}:${hour}`);
                  return (
                    <td
                      key={col}
                      className="border-r border-white/5 px-1 py-1 align-top"
                    >
                      {cell ? (
                        <div className="space-y-1">
                          {cell.items.map((o) => {
                            const key = occurrenceKey(o);
                            const isSelected = key === selectedKey;
                            return (
                              <button
                                key={key}
                                type="button"
                                onClick={() => onPickOccurrence(o)}
                                title={`${o.lane.lane_id} — ${o.lane.repo_full_name}`}
                                className={
                                  "block w-full truncate rounded border px-1.5 py-1 text-left font-mono text-[10px] font-semibold transition " +
                                  (isSelected
                                    ? "border-aqua/60 bg-aqua/25 text-aqua"
                                    : "border-aqua/30 bg-aqua/10 text-aqua/90 hover:bg-aqua/20")
                                }
                              >
                                <span className="block truncate">
                                  {o.lane.lane_id}
                                </span>
                                <span className="block text-[9px] font-normal text-aqua/70">
                                  {pad(o.at.getUTCHours())}:
                                  {pad(o.at.getUTCMinutes())}
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ----------------------------------------------------------------------------
// Detail drawer
// ----------------------------------------------------------------------------

function DetailDrawer({
  occurrence,
  catalogIndex,
  onClose,
  onEdit,
}: {
  occurrence: Occurrence | null;
  catalogIndex: Map<string, ApiLaneCatalogEntry>;
  onClose: () => void;
  onEdit?: (lane: ApiLane) => void;
}) {
  if (!occurrence) {
    return (
      <Card>
        <CardHeader
          title="Lane details"
          subtitle="Click any block in the calendar to see what runs, when, and why."
        />
        <div className="mt-3 rounded-lg border border-dashed border-white/10 bg-white/[0.015] p-4 text-center text-[11px] text-white/45">
          Nothing selected.
        </div>
      </Card>
    );
  }
  const { lane, at } = occurrence;
  const recipe = catalogIndex.get(lane.lane_id);

  return (
    <Card className="sticky top-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <Badge tone="info">{lane.kind}</Badge>
          <h3 className="mt-1 font-display text-sm font-bold text-white">
            {recipe?.title ?? lane.lane_id}
          </h3>
          <p className="text-[11px] text-white/55">{lane.repo_full_name}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[11px] text-white/55 hover:text-white"
        >
          ×
        </button>
      </div>

      <div className="mt-3 space-y-2 text-[11px] text-white/75">
        <Row label="Next run">
          <span className="font-mono">{fmtFull(at)}</span>
        </Row>
        <Row label="Schedule">
          <span>{humanizeCron(lane.cron)}</span>
        </Row>
        {lane.pattern ? (
          <Row label="Pattern">
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[10px]">
              {lane.pattern}
            </code>
          </Row>
        ) : null}
        {recipe?.summary ? (
          <Row label="Description">
            <span className="text-white/65">{recipe.summary}</span>
          </Row>
        ) : null}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {onEdit ? (
          <button
            type="button"
            onClick={() => onEdit(lane)}
            className="rounded-md border border-aqua/40 bg-aqua/10 px-3 py-1 text-[11px] font-semibold text-aqua hover:bg-aqua/20"
          >
            Edit schedule
          </button>
        ) : null}
        <Link
          href={`/lanes/${lane.id}`}
          className="rounded-md border border-white/15 bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-white/70 hover:text-white"
        >
          Run history →
        </Link>
      </div>
    </Card>
  );
}

// ----------------------------------------------------------------------------
// Event-driven section
// ----------------------------------------------------------------------------

function EventDrivenSection({
  lanes,
  catalogIndex,
}: {
  lanes: ApiLane[];
  catalogIndex: Map<string, ApiLaneCatalogEntry>;
}) {
  return (
    <Card>
      <CardHeader
        title="Event-driven lanes"
        subtitle="These don't live on a clock — they fire when a pull request opens, a push lands, or on a one-off trigger."
      />
      <ul className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
        {lanes.map((lane) => {
          const recipe = catalogIndex.get(lane.lane_id);
          return (
            <li
              key={lane.id}
              className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="neutral">{lane.kind}</Badge>
                <span className="font-semibold text-white">
                  {recipe?.title ?? lane.lane_id}
                </span>
                {lane.enabled ? null : <Badge tone="warn">disabled</Badge>}
              </div>
              <p className="mt-1 text-[11px] text-white/55">
                {lane.repo_full_name}
              </p>
              {lane.pattern ? (
                <p className="mt-1 truncate font-mono text-[10px] text-white/45">
                  {lane.pattern}
                </p>
              ) : null}
              {recipe?.summary ? (
                <p className="mt-1 text-[11px] text-white/65">
                  {recipe.summary}
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

function buildCells(
  lanes: ApiLane[],
  weekStart: Date,
  weekEnd: Date,
): OccurrenceCell[] {
  const byKey = new Map<string, OccurrenceCell>();
  for (const lane of lanes) {
    if (!lane.cron) continue;
    const occs = nextOccurrences(lane.cron, weekStart, weekEnd);
    for (const at of occs) {
      const col = JS_DAY_TO_COL[at.getUTCDay()] ?? 0;
      const hour = at.getUTCHours();
      const key = `${col}:${hour}`;
      let cell = byKey.get(key);
      if (!cell) {
        cell = { col, hour, items: [] };
        byKey.set(key, cell);
      }
      cell.items.push({ lane, at });
    }
  }
  // Sort items within each cell by time so stacking is stable.
  for (const c of byKey.values()) {
    c.items.sort((a, b) => a.at.getTime() - b.at.getTime());
  }
  return [...byKey.values()];
}

function computeWeek(offset: number): { weekStart: Date; weekEnd: Date } {
  // Anchor on "today, Monday 00:00 UTC".
  const now = new Date();
  // JS weekday: 0 = Sun … 6 = Sat. We want ``days back to Monday``.
  const dow = now.getUTCDay();
  const daysBack = dow === 0 ? 6 : dow - 1;
  const monday = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()),
  );
  monday.setUTCDate(monday.getUTCDate() - daysBack + offset * 7);
  const next = new Date(monday.getTime());
  next.setUTCDate(monday.getUTCDate() + 7);
  return { weekStart: monday, weekEnd: next };
}

function fmtWeekHeader(d: Date): string {
  return `${d.toLocaleDateString("en-GB", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  })}`;
}

function fmtFull(d: Date): string {
  return `${d.toLocaleDateString("en-GB", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  })} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function occurrenceKey(o: Occurrence): string {
  return `${o.lane.id}@${o.at.getTime()}`;
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[96px_1fr] items-start gap-2">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-white/45">
        {label}
      </span>
      <div>{children}</div>
    </div>
  );
}
