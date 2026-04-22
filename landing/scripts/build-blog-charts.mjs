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

console.log("build-blog-charts: done.");

/* ECharts chokes on re-declaring the same hex constant in newer releases;
 * the small QUOTE_OR helper lets us fall through to a literal without
 * having to introduce another top-level name above the chart block. */
function QUOTE_OR(hex) {
  return hex;
}
