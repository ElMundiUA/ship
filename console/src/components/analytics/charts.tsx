"use client";

/**
 * Recharts-backed sparklines for the DORA cards on /analytics.
 *
 * Kept terse: these are auxiliary renderings — the headline number on
 * the card is the actual story. Charts give the operator a quick
 * "trend up / trend down / lumpy" read without forcing them to scan
 * a table.
 */

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type {
  ApiDoraCfrPoint,
  ApiDoraDfPoint,
  ApiDoraIncident,
  ApiDoraLtPoint,
} from "@/lib/api/client";


const TOOLTIP_STYLE: React.CSSProperties = {
  background: "rgba(10, 10, 14, 0.95)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 6,
  padding: "6px 10px",
  fontSize: 11,
  color: "rgba(255,255,255,0.85)",
};


export function DfChart({ data }: { data: ApiDoraDfPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis
          dataKey="date"
          tick={{ fontSize: 9, fill: "rgba(255,255,255,0.4)" }}
          interval="preserveStartEnd"
          minTickGap={32}
          tickFormatter={shortDate}
        />
        <YAxis hide allowDecimals={false} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(value) => {
            const n = typeof value === "number" ? value : Number(value);
            return [`${n} deploy${n === 1 ? "" : "s"}`, "count"];
          }}
          labelFormatter={(label) => fullDate(String(label))}
        />
        <Bar dataKey="count" fill="#5eead4" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}


export function LtChart({ data }: { data: ApiDoraLtPoint[] }) {
  // Drop weeks with no samples so the line doesn't dive to 0 and look
  // like a regression. The chart's job is to show trend, not zero-fill.
  const filtered = data.filter((d) => d.median_hours !== null);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart
        data={filtered}
        margin={{ top: 4, right: 4, bottom: 0, left: 0 }}
      >
        <XAxis
          dataKey="week_start"
          tick={{ fontSize: 9, fill: "rgba(255,255,255,0.4)" }}
          interval="preserveStartEnd"
          minTickGap={32}
          tickFormatter={shortDate}
        />
        <YAxis hide />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(value) => {
            const n = typeof value === "number" ? value : Number(value);
            return [`${n.toFixed(1)} h`, "median"];
          }}
          labelFormatter={(label) => `Week of ${shortDate(String(label))}`}
        />
        <Line
          type="monotone"
          dataKey="median_hours"
          stroke="#a78bfa"
          strokeWidth={2}
          dot={{ r: 2, fill: "#a78bfa" }}
          activeDot={{ r: 4 }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}


export function CfrChart({ data }: { data: ApiDoraCfrPoint[] }) {
  // Stack failed bars on top of total to read failures as a portion
  // of activity rather than an absolute. Keep colours editorial — sun
  // for total volume, coral for failures so a bad day jumps out.
  const merged = data.map((p) => ({
    date: p.date,
    success: Math.max(p.total - p.failed, 0),
    failed: p.failed,
  }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        data={merged}
        margin={{ top: 4, right: 4, bottom: 0, left: 0 }}
      >
        <XAxis
          dataKey="date"
          tick={{ fontSize: 9, fill: "rgba(255,255,255,0.4)" }}
          interval="preserveStartEnd"
          minTickGap={32}
          tickFormatter={shortDate}
        />
        <YAxis hide allowDecimals={false} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={(label) => fullDate(String(label))}
          formatter={(value, name) => [
            `${value}`,
            String(name) === "success" ? "succeeded" : "failed",
          ]}
        />
        <Bar
          dataKey="success"
          stackId="a"
          fill="#5eead4"
          radius={[0, 0, 0, 0]}
        />
        <Bar
          dataKey="failed"
          stackId="a"
          fill="#f87171"
          radius={[2, 2, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}


export function MttrSparkline({ incidents }: { incidents: ApiDoraIncident[] }) {
  const series = incidents
    .filter((i) => i.hours !== null)
    .map((i, idx) => ({
      idx,
      hours: i.hours ?? 0,
      label: i.workflow_name ?? "deploy",
    }));
  if (series.length === 0) {
    return (
      <p className="flex h-full items-center justify-center text-[11px] italic text-white/30">
        {incidents.length === 0
          ? "No incidents in window."
          : `${incidents.length} open · no recoveries yet.`}
      </p>
    );
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart
        data={series}
        margin={{ top: 4, right: 4, bottom: 0, left: 0 }}
      >
        <XAxis hide dataKey="idx" />
        <YAxis hide />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(value) => {
            const n = typeof value === "number" ? value : Number(value);
            return [`${n.toFixed(2)} h`, "recovery"];
          }}
          labelFormatter={(_, payload) => {
            if (payload && payload.length > 0) {
              const item = payload[0]?.payload as { label?: string } | undefined;
              return item?.label ?? "incident";
            }
            return "incident";
          }}
        />
        <Area
          type="monotone"
          dataKey="hours"
          stroke="#f87171"
          fill="#f87171"
          fillOpacity={0.2}
          strokeWidth={2}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}


function shortDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function fullDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}
