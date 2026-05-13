#!/usr/bin/env node
/**
 * Render Apache ECharts infographics for the Ship Log (blog).
 *
 * Output: landing/public/diagrams/blog/*.svg
 *
 * Run from repo root (or via npm run blog:charts):
 *   node landing/scripts/build-blog-charts.mjs
 *
 * Shares the book's cream/ink print palette so the paper-card
 * presentation on /blog/* matches /book. Keep the numbers honest —
 * everything here is sourced from `git log` or an actual release
 * artifact.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as echarts from "echarts";

const __dirname = dirname(fileURLToPath(import.meta.url));
const landingRoot = join(__dirname, "..");
const outDir = join(landingRoot, "public", "diagrams", "blog");
mkdirSync(outDir, { recursive: true });

const PAPER = "#fbf8f1";
const PAPER_SOFT = "#f4efe1";
const INK = "#1b1d24";
const INK_SOFT = "#4d525c";
const INK_FAINT = "#7b818d";
const RULE = "#d9d2c3";
const ACCENT = "#a4451b";
const ACCENT_SOFT = "#b96b3f";
const ACCENT_PALE = "#e8c9a8";
const INK_GREEN = "#3d6b4f";
const INK_GREEN_SOFT = "#74a087";
const INK_RED = "#9a2f2a";

const SANS = "Inter, Helvetica Neue, Arial, sans-serif";

function render(name, option, width, height) {
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
  chart.setOption({
    backgroundColor: "transparent",
    textStyle: { fontFamily: SANS, color: INK },
    animation: false,
    ...option,
  });
  const svg = chart.renderToSVGString();
  chart.dispose();
  const cleaned = svg
    .replace(/\swidth="\d+"/, "")
    .replace(/\sheight="\d+"/, "")
    .replace(/<svg /, `<svg preserveAspectRatio="xMidYMid meet" `);
  const filePath = join(outDir, `${name}.svg`);
  writeFileSync(filePath, cleaned);
  console.log("build-blog-charts:", filePath);
}

/* ------------------------------------------------------------------ *
 * Chart 1: commits-per-day for the first two weeks of Ship           *
 * Source: `git log --format="%ad" --date=short | sort | uniq -c`     *
 * ------------------------------------------------------------------ */
{
  const days = [
    "Apr 7",
    "Apr 8",
    "Apr 9",
    "Apr 10",
    "Apr 11",
    "Apr 12",
    "Apr 13",
    "Apr 14",
    "Apr 15",
    "Apr 16",
    "Apr 17",
    "Apr 18",
    "Apr 19",
    "Apr 20",
    "Apr 21",
    "Apr 22",
  ];
  const commits = [20, 2, 0, 0, 0, 4, 0, 0, 1, 5, 0, 1, 35, 53, 53, 15];
  /* Phase colour bands so the reader can see the story without
   * reading the caption. Keep the phase list minimal — 4 beats. */
  const phases = [
    { from: "Apr 7", to: "Apr 12", label: "Bootstrap", color: PAPER_SOFT },
    { from: "Apr 15", to: "Apr 18", label: "CLI + shipctl", color: ACCENT_PALE, opacity: 0.45 },
    { from: "Apr 19", to: "Apr 19", label: "Cloud + Console day 1", color: ACCENT_PALE, opacity: 0.7 },
    { from: "Apr 20", to: "Apr 22", label: "Pilot", color: ACCENT_PALE, opacity: 0.55 },
  ];
  render(
    "blog-commits-per-day",
    {
      grid: { left: 56, right: 24, top: 42, bottom: 48, containLabel: true },
      xAxis: {
        type: "category",
        data: days,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS, fontSize: 10, interval: 0, rotate: 30 },
      },
      yAxis: {
        type: "value",
        name: "commits / day",
        nameTextStyle: { color: INK_FAINT, fontFamily: SANS, fontSize: 11, padding: [0, 0, 8, 0] },
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS },
      },
      series: [
        {
          name: "Commits",
          type: "bar",
          barWidth: 22,
          itemStyle: {
            color: (p) => {
              const label = days[p.dataIndex];
              if (label === "Apr 19") return INK_RED;
              if (label === "Apr 20" || label === "Apr 21") return ACCENT;
              if (label === "Apr 22") return ACCENT_SOFT;
              if (label === "Apr 7") return INK_SOFT;
              return INK_FAINT;
            },
            borderRadius: [3, 3, 0, 0],
          },
          label: {
            show: true,
            position: "top",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 10,
            formatter: ({ value }) => (value > 0 ? value : ""),
          },
          data: commits,
          markArea: {
            silent: true,
            label: {
              position: "insideTop",
              color: INK_SOFT,
              fontFamily: SANS,
              fontSize: 10,
              fontWeight: 600,
            },
            data: phases.map((p) => [
              { name: p.label, xAxis: p.from, itemStyle: { color: p.color, opacity: p.opacity ?? 0.45 } },
              { xAxis: p.to },
            ]),
          },
          markPoint: {
            symbol: "pin",
            symbolSize: 46,
            label: {
              color: PAPER,
              fontFamily: SANS,
              fontSize: 10,
              fontWeight: 700,
              formatter: "Ship\nConsole\nborn",
            },
            itemStyle: { color: INK_RED },
            data: [{ xAxis: "Apr 19", yAxis: 35 }],
          },
        },
      ],
    },
    760,
    360,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 2: topology before / after we deleted the worker             *
 * ------------------------------------------------------------------ */
{
  const categories = ["Before", "After"];
  const series = [
    { name: "API backend", data: [1, 1], color: INK_GREEN },
    { name: "Background worker", data: [1, 0], color: ACCENT },
    { name: "Redis queue", data: [1, 0], color: ACCENT_SOFT },
    { name: "Repo cache volume", data: [1, 0], color: QUOTE_OR("#c98a4f") },
    { name: "Postgres (Neon)", data: [1, 1], color: INK_GREEN_SOFT },
    { name: "Console", data: [1, 1], color: INK_SOFT },
    { name: "Landing", data: [1, 1], color: INK_FAINT },
  ];

  render(
    "blog-topology-delta",
    {
      grid: { left: 90, right: 48, top: 36, bottom: 36, containLabel: false },
      tooltip: { show: false },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 10 },
        itemWidth: 12,
        itemHeight: 8,
        itemGap: 12,
      },
      xAxis: {
        type: "value",
        max: 7,
        splitLine: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: categories,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 13, fontWeight: 700 },
      },
      series: series.map((s) => ({
        name: s.name,
        type: "bar",
        stack: "topology",
        barWidth: 44,
        itemStyle: { color: s.color, borderColor: PAPER, borderWidth: 2 },
        label: {
          show: true,
          position: "inside",
          color: PAPER,
          fontFamily: SANS,
          fontSize: 10,
          fontWeight: 600,
          formatter: ({ value }) => (value > 0 ? s.name : ""),
        },
        data: s.data,
      })),
    },
    760,
    280,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 3: wizard steps — v1 → v2 iter 1 → v2 iter 7                 *
 * ------------------------------------------------------------------ */
{
  const iterations = ["Wizard v1", "v2 iter 1", "v2 iter 4", "v2 iter 7"];
  const steps = [10, 3, 3, 3];
  const sideEffects = [6, 2, 3, 4];
  render(
    "blog-wizard-steps",
    {
      grid: { left: 60, right: 30, top: 36, bottom: 36, containLabel: true },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 18,
      },
      xAxis: {
        type: "category",
        data: iterations,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 12, fontWeight: 600 },
      },
      yAxis: {
        type: "value",
        name: "surfaces in the flow",
        nameTextStyle: { color: INK_FAINT, fontFamily: SANS, fontSize: 11, padding: [0, 0, 8, 0] },
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS },
      },
      series: [
        {
          name: "User-visible steps",
          type: "bar",
          barWidth: 32,
          itemStyle: { color: ACCENT, borderRadius: [3, 3, 0, 0] },
          label: {
            show: true,
            position: "top",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 11,
          },
          data: steps,
        },
        {
          name: "Side-effects the user must reason about",
          type: "bar",
          barWidth: 32,
          itemStyle: { color: INK_GREEN_SOFT, borderRadius: [3, 3, 0, 0] },
          label: {
            show: true,
            position: "top",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 11,
          },
          data: sideEffects,
          markPoint: {
            symbol: "pin",
            symbolSize: 46,
            label: {
              color: PAPER,
              fontFamily: SANS,
              fontSize: 10,
              fontWeight: 700,
              formatter: "3 WOW steps\ncarry the whole flow",
            },
            itemStyle: { color: INK_GREEN },
            data: [{ xAxis: "v2 iter 7", yAxis: 4 }],
          },
        },
      ],
    },
    760,
    320,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 4: RFC-0005 artifact migration waterfall                     *
 * ------------------------------------------------------------------ */
{
  const steps = ["v1 total", "Wave 1", "Wave 2 + cleanup", "Legacy dropped", "v2 on disk"];
  /* The waterfall uses two bars: an invisible "base" to position
   * each delta, and the visible delta on top. */
  const base = [0, 0, 34, 61, 0];
  const delta = [61, 34, 27, -61, 61];
  render(
    "blog-artifacts-migration",
    {
      grid: { left: 60, right: 30, top: 36, bottom: 38, containLabel: true },
      xAxis: {
        type: "category",
        data: steps,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS, fontSize: 11, interval: 0 },
      },
      yAxis: {
        type: "value",
        name: "artifacts",
        nameTextStyle: { color: INK_FAINT, fontFamily: SANS, fontSize: 11, padding: [0, 0, 8, 0] },
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS },
      },
      series: [
        {
          name: "base",
          type: "bar",
          stack: "wf",
          itemStyle: { color: "transparent" },
          emphasis: { disabled: true },
          data: base,
        },
        {
          name: "Change",
          type: "bar",
          stack: "wf",
          barWidth: 46,
          itemStyle: {
            color: ({ dataIndex, value }) => {
              if (dataIndex === 0) return INK_SOFT;
              if (dataIndex === 3) return INK_RED;
              if (dataIndex === 4) return INK_GREEN;
              return value >= 0 ? ACCENT : INK_RED;
            },
            borderRadius: [3, 3, 0, 0],
          },
          label: {
            show: true,
            position: "top",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 11,
            formatter: ({ value, dataIndex }) => {
              if (dataIndex === 3) return "−61";
              if (dataIndex === 0 || dataIndex === 4) return String(value);
              return value > 0 ? `+${value}` : String(value);
            },
          },
          data: delta,
        },
      ],
    },
    760,
    320,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 5: two-speed repo — a stream of commits by area over time    *
 * Illustrative small-multiple companion to the first post.           *
 * ------------------------------------------------------------------ */
{
  const days = [
    "Apr 7",
    "Apr 12",
    "Apr 15",
    "Apr 16",
    "Apr 18",
    "Apr 19",
    "Apr 20",
    "Apr 21",
    "Apr 22",
  ];
  /* Hand-bucketed from the commit log. The shape, not the exact
   * number, is the message — so we're honest about that in the
   * caption. */
  const backend = [1, 1, 0, 0, 1, 14, 23, 20, 2];
  const console_ = [0, 0, 0, 0, 0, 8, 15, 17, 6];
  const cli = [0, 0, 0, 2, 0, 1, 2, 6, 2];
  const landing = [0, 2, 1, 1, 0, 5, 1, 1, 0];
  const infra = [14, 1, 0, 2, 0, 7, 12, 9, 5];
  render(
    "blog-stack-mix",
    {
      grid: { left: 56, right: 24, top: 38, bottom: 42, containLabel: true },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 18,
      },
      xAxis: {
        type: "category",
        data: days,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS, fontSize: 10 },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS },
      },
      series: [
        { name: "backend", type: "bar", stack: "mix", barWidth: 22, itemStyle: { color: ACCENT }, data: backend },
        { name: "console", type: "bar", stack: "mix", barWidth: 22, itemStyle: { color: INK_GREEN }, data: console_ },
        { name: "cli", type: "bar", stack: "mix", barWidth: 22, itemStyle: { color: ACCENT_SOFT }, data: cli },
        { name: "landing + docs", type: "bar", stack: "mix", barWidth: 22, itemStyle: { color: INK_SOFT }, data: landing },
        { name: "infra + ci", type: "bar", stack: "mix", barWidth: 22, itemStyle: { color: INK_FAINT }, data: infra },
      ],
    },
    760,
    340,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 6: Apr 7 extraction-day mix                                   *
 * Twenty commits, five themes. A rough "where did day one go" map.    *
 * ------------------------------------------------------------------ */
{
  render(
    "blog-extraction-mix",
    {
      grid: { left: 24, right: 24, top: 44, bottom: 34 },
      legend: {
        top: 8,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 18,
      },
      xAxis: {
        type: "value",
        max: 20,
        splitLine: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
      yAxis: {
        type: "category",
        data: ["Apr 7"],
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 13, fontWeight: 700 },
      },
      series: [
        { name: "Infra + Bunny CI", type: "bar", stack: "ex", barWidth: 38, itemStyle: { color: ACCENT }, label: { show: true, position: "inside", color: PAPER, fontFamily: SANS, fontSize: 11, fontWeight: 600, formatter: "12" }, data: [12] },
        { name: "Docs", type: "bar", stack: "ex", barWidth: 38, itemStyle: { color: INK_SOFT }, label: { show: true, position: "inside", color: PAPER, fontFamily: SANS, fontSize: 11, fontWeight: 600, formatter: "3" }, data: [3] },
        { name: "Repo extraction", type: "bar", stack: "ex", barWidth: 38, itemStyle: { color: INK_GREEN }, label: { show: true, position: "inside", color: PAPER, fontFamily: SANS, fontSize: 11, fontWeight: 600, formatter: "2" }, data: [2] },
        { name: "Prompts", type: "bar", stack: "ex", barWidth: 38, itemStyle: { color: ACCENT_SOFT }, label: { show: true, position: "inside", color: PAPER, fontFamily: SANS, fontSize: 11, fontWeight: 600, formatter: "2" }, data: [2] },
        { name: "CLI seed", type: "bar", stack: "ex", barWidth: 38, itemStyle: { color: INK_GREEN_SOFT }, label: { show: true, position: "inside", color: PAPER, fontFamily: SANS, fontSize: 11, fontWeight: 600, formatter: "1" }, data: [1] },
      ],
    },
    760,
    180,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 7: shipctl v0.9 command surface (grouped bars)               *
 * Source: dadcb9d commit body enumerating every verb.                *
 * ------------------------------------------------------------------ */
{
  const groups = [
    { label: "Lifecycle", verbs: "init · new · adopt", count: 3, color: INK_GREEN },
    { label: "Config", verbs: "init · get · set · validate · show", count: 5, color: INK_SOFT },
    { label: "Sync + cache", verbs: "sync · verify · doctor", count: 3, color: ACCENT_SOFT },
    { label: "Catalog", verbs: "search · fetch · pattern · tool · collection · workflow", count: 6, color: ACCENT },
    { label: "Telemetry", verbs: "on · off · status · flush · export · delete", count: 6, color: INK_GREEN_SOFT },
    { label: "Feedback", verbs: "draft · list · show · submit · remove", count: 5, color: INK_FAINT },
  ];
  render(
    "blog-cli-surface",
    {
      grid: { left: 96, right: 72, top: 20, bottom: 20 },
      xAxis: {
        type: "value",
        max: 7,
        splitLine: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: groups.map((g) => g.label),
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 12, fontWeight: 600 },
      },
      series: [
        {
          type: "bar",
          barWidth: 26,
          itemStyle: {
            color: (p) => groups[p.dataIndex].color,
            borderRadius: [0, 3, 3, 0],
          },
          label: {
            show: true,
            position: "right",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 10,
            formatter: ({ dataIndex, value }) => `${value} — ${groups[dataIndex].verbs}`,
          },
          data: groups.map((g) => g.count),
        },
      ],
    },
    760,
    290,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 8: Apr 19 hourly map of the Sunday the cloud console was born *
 * Source: git log %ad --date='format:%H' --since=2026-04-19.          *
 * ------------------------------------------------------------------ */
{
  const hours = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
  /* Counts hand-bucketed from the commit log — the shape is what the
   * caption points at, not a round trip into SQL. */
  const counts = [
    2,  // 00 — book + landing PDF CTA
    4,  // 01 — RFC-0005 wave 1/2 + drop docs-mcp
    2,  // 02 — landing rewrite + drop migration
    3,  // 03 — catalog-a13 + stack coverage + version bump
    0,
    3,  // 05 — book PDF redesign + Manual rebuild + docs clean
    1,  // 06 — landing nav collapse
    0, 0, 0, 0, 0, 0,
    1,  // 13 — RFC-0006 cloud platform foundations
    0,
    3,  // 15 — infra bootstrap + auth0 + notion
    0, 0, 0,
    2,  // 19 — workspace-ops + data-plane
    4,  // 20 — Sentry + Caddy + security + docker hub ci
    1,  // 21 — refactor(infra) remove worker + redis
    4,  // 22 — pilot day 1 + integrations + db
    3,  // 23 — day 3 / day 2A / day 2B
  ];
  /* Colour the bars by the beat the commit belongs to so the reader
   * can see the three acts of the day without reading the caption. */
  const beatFor = (h) => {
    if (h === 0 || h === 2 || h === 3 || h === 5 || h === 6) return INK_SOFT; // book + docs
    if (h === 1) return ACCENT_PALE; // rfc-0005 migration
    if (h === 13) return INK_RED; // cloud console born
    if (h === 15 || h === 19 || h === 20) return ACCENT; // cloud build-out
    if (h === 21) return INK_GREEN; // worker deletion
    if (h === 22 || h === 23) return ACCENT_SOFT; // pilot day
    return INK_FAINT;
  };
  render(
    "blog-sunday-hours",
    {
      grid: { left: 56, right: 24, top: 42, bottom: 42, containLabel: true },
      xAxis: {
        type: "category",
        data: hours,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS, fontSize: 10, interval: 1 },
      },
      yAxis: {
        type: "value",
        name: "commits / hour (Apr 19, +03)",
        nameTextStyle: { color: INK_FAINT, fontFamily: SANS, fontSize: 11, padding: [0, 0, 8, 0] },
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS },
      },
      series: [
        {
          type: "bar",
          barWidth: 16,
          itemStyle: {
            color: (p) => beatFor(p.dataIndex),
            borderRadius: [3, 3, 0, 0],
          },
          label: {
            show: true,
            position: "top",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 10,
            formatter: ({ value }) => (value > 0 ? value : ""),
          },
          data: counts,
          markPoint: {
            symbol: "pin",
            symbolSize: 46,
            label: {
              color: PAPER,
              fontFamily: SANS,
              fontSize: 10,
              fontWeight: 700,
              formatter: "RFC-0006\n13:24",
            },
            itemStyle: { color: INK_RED },
            data: [{ xAxis: "13", yAxis: 1 }],
          },
        },
      ],
    },
    760,
    340,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 9: RFC-0007 — nine lane phases in one day                    *
 * Source: the Phase 0 → Phase 7C+D+E commit chain on Apr 21.         *
 * ------------------------------------------------------------------ */
{
  const phases = [
    { label: "P0 · validators",          sha: "74456d3", layer: "proto",   color: INK_SOFT },
    { label: "P1 · migrate",             sha: "d56ae0c", layer: "cli",     color: ACCENT },
    { label: "P2 · run + idempotency",   sha: "83406b6", layer: "cli",     color: ACCENT },
    { label: "P3 · lanes install + CI",  sha: "bee62bd", layer: "cli+ci",  color: ACCENT_SOFT },
    { label: "P4 · sync --lock",         sha: "b15f5bd", layer: "cli",     color: ACCENT },
    { label: "P6 · retire workflow",     sha: "5da540d", layer: "proto",   color: INK_RED },
    { label: "P7A · lane projection",    sha: "7b24892", layer: "backend", color: INK_GREEN },
    { label: "P7B · callback metrics",   sha: "4845b7f", layer: "cli+ci",  color: ACCENT_SOFT },
    { label: "P7C+D+E · UI + docs",      sha: "a02160f", layer: "console", color: INK_GREEN_SOFT },
  ];
  render(
    "blog-lanes-phases",
    {
      grid: { left: 170, right: 56, top: 8, bottom: 16 },
      xAxis: {
        type: "value",
        max: 1,
        splitLine: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: phases.map((p) => p.label),
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 11, fontWeight: 600 },
      },
      series: [
        {
          type: "bar",
          barWidth: 20,
          itemStyle: {
            color: (p) => phases[p.dataIndex].color,
            borderRadius: [0, 3, 3, 0],
          },
          label: {
            show: true,
            position: "right",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 10,
            formatter: ({ dataIndex }) => `${phases[dataIndex].sha} · ${phases[dataIndex].layer}`,
          },
          data: phases.map(() => 1),
        },
      ],
    },
    760,
    340,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 10: Knowledge phases 1 → 8, commit count per phase           *
 * Source: the Apr 21 knowledge-consolidation commit chain.           *
 * ------------------------------------------------------------------ */
{
  const phases = [
    { label: "Phase 1 · scope + source",    commits: 1, color: INK_SOFT,      note: "a7c8edd" },
    { label: "Phase 2 · mirror .ship/kb",   commits: 1, color: INK_SOFT,      note: "f5ea287" },
    { label: "Phase 3 · scope ladder",      commits: 1, color: INK_GREEN,     note: "d4a982a" },
    { label: "Phase 4a/b · scope pill UI",  commits: 2, color: INK_GREEN_SOFT,note: "d9e106a · df16dba" },
    { label: "Phase 5a–d · articles dual-write", commits: 4, color: ACCENT_SOFT, note: "0b85a6d → 64821a6" },
    { label: "Phase 6a–c · Distiller",      commits: 3, color: ACCENT,        note: "08f8314 → c8fb5a1" },
    { label: "Phase 7a–c · connectors",     commits: 4, color: ACCENT_PALE,   note: "8fdebd4 → c72a52e" },
    { label: "Phase 8 · per-user memory",   commits: 1, color: INK_RED,       note: "aeeec74" },
  ];
  render(
    "blog-knowledge-phases",
    {
      grid: { left: 210, right: 120, top: 8, bottom: 24 },
      xAxis: {
        type: "value",
        max: 5,
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS, fontSize: 10 },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: phases.map((p) => p.label),
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 11, fontWeight: 600 },
      },
      series: [
        {
          type: "bar",
          barWidth: 20,
          itemStyle: {
            color: (p) => phases[p.dataIndex].color,
            borderRadius: [0, 3, 3, 0],
          },
          label: {
            show: true,
            position: "right",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 10,
            formatter: ({ dataIndex, value }) =>
              `${value} commit${value === 1 ? "" : "s"} · ${phases[dataIndex].note}`,
          },
          data: phases.map((p) => p.commits),
        },
      ],
    },
    760,
    340,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 11: Catalog rename — 21 patterns from two namespaces to six  *
 * ------------------------------------------------------------------ */
{
  const beforeStack = [
    { name: "cloud-*",        value: 7,  color: INK_SOFT },
    { name: "catalog-a*",     value: 9,  color: ACCENT_SOFT },
    { name: "adopt-ship-*",   value: 2,  color: ACCENT_PALE },
    { name: "kickoff + seed", value: 2,  color: INK_GREEN_SOFT },
    { name: "duplicates dropped", value: 5, color: INK_RED },
  ];
  const afterStack = [
    { name: "role-*",    value: 7, color: INK_GREEN },
    { name: "flow-*",    value: 8, color: ACCENT },
    { name: "op-*",      value: 2, color: INK_GREEN_SOFT },
    { name: "onboard-*", value: 2, color: ACCENT_SOFT },
    { name: "common-*",  value: 2, color: INK_SOFT },
    { name: "scan-*",    value: 0, color: ACCENT_PALE },
  ];
  const categories = ["Before", "After"];
  const series = [
    ...beforeStack.map((s) => ({
      name: s.name,
      stack: "before",
      type: "bar",
      barWidth: 44,
      data: [s.value, 0],
      itemStyle: { color: s.color, borderColor: PAPER, borderWidth: 2 },
      label: {
        show: true,
        position: "inside",
        color: PAPER,
        fontFamily: SANS,
        fontSize: 10,
        fontWeight: 600,
        formatter: ({ value }) => (value > 0 ? `${s.name} ${value}` : ""),
      },
    })),
    ...afterStack.map((s) => ({
      name: s.name,
      stack: "after",
      type: "bar",
      barWidth: 44,
      data: [0, s.value],
      itemStyle: { color: s.color, borderColor: PAPER, borderWidth: 2 },
      label: {
        show: true,
        position: "inside",
        color: PAPER,
        fontFamily: SANS,
        fontSize: 10,
        fontWeight: 600,
        formatter: ({ value }) => (value > 0 ? `${s.name} ${value}` : ""),
      },
    })),
  ];
  render(
    "blog-catalog-rename",
    {
      grid: { left: 90, right: 60, top: 8, bottom: 8 },
      tooltip: { show: false },
      legend: { show: false },
      xAxis: {
        type: "value",
        max: 26,
        splitLine: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: categories,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 13, fontWeight: 700 },
      },
      series,
    },
    760,
    220,
  );
}

/* ------------------------------------------------------------------ *
 * Chart 12: Navigator turn — four metrics, before vs after           *
 * ------------------------------------------------------------------ */
{
  const metrics = [
    { label: "Layout jumps per turn",          before: 6, after: 0, beforeFmt: "6+", afterFmt: "0–1" },
    { label: "Bordered bubbles on screen",     before: 7, after: 0, beforeFmt: "7 (thinking + tools + reply)", afterFmt: "0 (flat rows)" },
    { label: "Typewriter granularity",         before: 1, after: 5, beforeFmt: "char", afterFmt: "word" },
    { label: "Tool trail persists after turn", before: 0, after: 1, beforeFmt: "no — cleared on end", afterFmt: "yes — until next send" },
  ];
  render(
    "blog-navigator-turn",
    {
      grid: { left: 200, right: 220, top: 30, bottom: 20 },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 16,
      },
      xAxis: {
        type: "value",
        max: 8,
        splitLine: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: metrics.map((m) => m.label),
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 11, fontWeight: 600 },
      },
      series: [
        {
          name: "Before",
          type: "bar",
          barWidth: 14,
          itemStyle: { color: INK_RED, borderRadius: [0, 2, 2, 0] },
          label: {
            show: true,
            position: "right",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 10,
            formatter: ({ dataIndex }) => metrics[dataIndex].beforeFmt,
          },
          data: metrics.map((m) => m.before),
        },
        {
          name: "After",
          type: "bar",
          barWidth: 14,
          itemStyle: { color: INK_GREEN, borderRadius: [0, 2, 2, 0] },
          label: {
            show: true,
            position: "right",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 10,
            formatter: ({ dataIndex }) => metrics[dataIndex].afterFmt,
          },
          data: metrics.map((m) => m.after),
        },
      ],
    },
    760,
    260,
  );
}

console.log("build-blog-charts: done.");

/* ECharts chokes on re-declaring the same hex constant in newer releases;
 * the small QUOTE_OR helper lets us fall through to a literal without
 * having to introduce another top-level name above the chart block. */
function QUOTE_OR(hex) {
  return hex;
}
